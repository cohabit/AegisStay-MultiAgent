"""Bounded background execution with session queues and atomic, versioned commits.

Workers only change snapshots of the local hotel simulation. They never call an
external PMS. Cancellation invalidates a run immediately; an uninterruptible LLM
call may finish later, but cannot publish its snapshot.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_protocol import DeliveryCommand, TaskInterrupted, parse_delivery_command
from hotel_core import (
    HotelOrchestrator, HotelState, HousekeepingAgent, OpenAICompatibleClient,
    PMS_ROOMS, _room_no, _rules_extract, run_pms_night_audit,
)


class RuntimeBusy(RuntimeError):
    pass


class CommitConflict(RuntimeError):
    pass


@dataclass
class RunOutcome:
    flow: HotelOrchestrator
    reply: str
    run_id: str
    task_id: str
    version: int
    status: str


@dataclass
class TaskRun:
    session_id: str
    owner: HotelOrchestrator
    message: str
    force_rules: bool
    kind: str
    task_id: str
    version: int
    room_hint: str | None
    origin_detail: str = ""
    request_id: str | None = None
    target_ticket_id: str | None = None
    run_id: str = field(default_factory=lambda: f"RUN-{uuid.uuid4().hex[:12]}")
    status: str = "queued"
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: Future = field(default_factory=Future)
    worker_finished: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "task_id": self.task_id, "version": self.version,
            "status": self.status, "created_at": self.created_at,
        }


class SessionManager:
    """Short shared-state locks; independent sessions execute concurrently."""

    def __init__(self, ttl_seconds: int = 1800, kiosk_ttl_seconds: int = 30,
                 max_workers: int = 8, max_pending: int = 128):
        self.ttl_seconds, self.kiosk_ttl_seconds = ttl_seconds, kiosk_ttl_seconds
        self.sessions: dict[str, HotelOrchestrator] = {}
        self.last_active: dict[str, float] = {}
        self.session_ttls: dict[str, int] = {}
        self.lock = threading.RLock()
        self.shared_pms = deepcopy(PMS_ROOMS)
        self.shared_tickets: list[dict[str, Any]] = []
        self.shared_checkout_requests: list[dict[str, Any]] = []
        self.shared_voucher_ledger: list[dict[str, Any]] = []
        self.housekeeping = HousekeepingAgent()
        self.current_date = "2026-10-01"
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aegis-agent")
        self._runs: dict[str, TaskRun] = {}
        self._active: dict[str, str] = {}
        self._queues: dict[str, deque[str]] = {}
        self._requests: dict[tuple[str, str], str] = {}
        self._outstanding, self.max_pending = 0, max_pending
        self._closed = False

    def _cleanup_expired(self) -> None:
        now = time.time()
        for sid, timestamp in list(self.last_active.items()):
            if sid not in self._active and now - timestamp > self.session_ttls.get(sid, self.ttl_seconds):
                self.reset(sid)

    def cleanup_expired(self) -> list[str]:
        with self.lock:
            before = set(self.sessions)
            self._cleanup_expired()
            return sorted(before - set(self.sessions))

    def get_or_create(self, session_id: str, force_rules: bool = False,
                      client_type: str = "guest") -> HotelOrchestrator:
        with self.lock:
            self._cleanup_expired()
            if session_id not in self.sessions:
                state = HotelState(pms_rooms=self.shared_pms, tickets=self.shared_tickets,
                                   checkout_requests=self.shared_checkout_requests,
                                   voucher_ledger=self.shared_voucher_ledger, current_date=self.current_date)
                flow = HotelOrchestrator(state, force_rules=force_rules)
                flow.housekeeping = self.housekeeping
                flow.vertical_agents[flow.housekeeping.name] = self.housekeeping
                self.sessions[session_id] = flow
                self.session_ttls[session_id] = self.kiosk_ttl_seconds if client_type.lower() == "kiosk" else self.ttl_seconds
                self._queues[session_id] = deque()
            self.last_active[session_id] = time.time()
            return self.sessions[session_id]

    def adopt(self, session_id: str, flow: HotelOrchestrator) -> None:
        """Attach the existing Streamlit demo state without losing its business data."""
        with self.lock:
            if self.sessions:
                raise ValueError("仅空执行器可以接管已有演示状态")
            self.shared_pms, self.shared_tickets = flow.state.pms_rooms, flow.state.tickets
            self.shared_checkout_requests = flow.state.checkout_requests
            self.shared_voucher_ledger = flow.state.voucher_ledger
            self.housekeeping = flow.housekeeping
            self.current_date = flow.state.current_date
            self.sessions[session_id] = flow
            self.last_active[session_id] = time.time()
            self.session_ttls[session_id] = self.ttl_seconds
            self._queues[session_id] = deque()

    def _snapshot(self, flow: HotelOrchestrator, force_rules: bool | None = None) -> HotelOrchestrator:
        cloned = copy(flow)
        cloned.state = deepcopy(flow.state)
        cloned.model = copy(flow.model)
        cloned.force_rules = flow.force_rules if force_rules is None else force_rules
        if cloned.force_rules or os.getenv("AEGIS_FORCE_RULES", "").lower() in {"1", "true", "yes"}:
            cloned.model.disabled, cloned.model.client = True, None
        elif flow.model.disabled:
            cloned.model = OpenAICompatibleClient()
        cloned.reception = copy(flow.reception)
        cloned.reception.model = cloned.model
        cloned.reception.pms_rooms = cloned.state.pms_rooms
        cloned.housekeeping = copy(self.housekeeping)
        cloned.housekeeping.lock = threading.RLock()
        cloned.housekeeping.inventory = deepcopy(self.housekeeping.inventory)
        cloned.engineering = copy(flow.engineering)
        cloned.concierge = copy(flow.concierge)
        cloned.manager_escalation = copy(flow.manager_escalation)
        cloned.dispatcher = copy(flow.dispatcher)
        cloned.dispatcher.model = cloned.model
        cloned.vertical_agents = {agent.name: agent for agent in (
            cloned.housekeeping, cloned.engineering, cloned.concierge, cloned.manager_escalation,
        )}
        return cloned

    def snapshot(self, session_id: str) -> HotelOrchestrator | None:
        with self.lock:
            flow = self.get_session(session_id)
            return self._snapshot(flow) if flow else None

    def submit(self, session_id: str, message: str, room_no: str | None = None,
               force_rules: bool = False, client_type: str = "guest", request_id: str | None = None) -> TaskRun:
        message = message.strip()
        if not message:
            raise ValueError("消息不能为空")
        with self.lock:
            if self._closed:
                raise RuntimeBusy("执行器已关闭")
            self._cleanup_expired()
            if request_id and (session_id, request_id) in self._requests:
                previous = self._runs[self._requests[(session_id, request_id)]]
                if previous.message != message or previous.room_hint != (room_no or _room_no(message) or previous.room_hint):
                    raise ValueError("同一 request_id 不能用于不同请求")
                return previous
            if self._outstanding >= self.max_pending:
                raise RuntimeBusy("任务队列已满，请稍后重试")
            flow = self.get_or_create(session_id, force_rules, client_type)
            active = self._runs.get(self._active.get(session_id, ""))
            command = parse_delivery_command(message)
            control = command.action in {"replace", "cancel"}
            explicit_delivery = bool(command.references)
            old_items = set(command.references) - {command.item}
            active_item = parse_delivery_command(flow.state.tasks[active.task_id].get("goal_detail", active.message)).item if active else None
            target_active = active if control and not (command.unresolved_target or command.ambiguous) and active and (
                not explicit_delivery or active.kind == "送物" and (not old_items or active_item in old_items)
            ) else None
            origin = ""
            target_ticket = None
            kind = _rules_extract(message, flow.knowledge)["intent"]
            if target_active:
                kind = target_active.kind
                previous = flow.state.tasks[target_active.task_id]
                origin = previous.get("goal_detail", target_active.message)
            room_hint = room_no or _room_no(message) or flow.state.room_no or (active.room_hint if active else None)
            if room_hint not in self.shared_pms:
                room_hint = flow.state.room_no
            task_id = target_active.task_id if target_active else f"TASK-{uuid.uuid4().hex[:12]}"
            version = target_active.version + 1 if target_active else 1
            if control and not target_active:
                target_ticket, target_error = flow.delivery_target(message, command)
                latest = flow.state.tasks.get(target_ticket.get("task_id")) if target_ticket else None
                if target_ticket or target_error or explicit_delivery:
                    kind = "送物"
                if not latest and not target_ticket and not target_error and flow.state.pending and flow.state.pending.get("intent") == "送物":
                    previous_tasks = [t for t in flow.state.tasks.values() if t.get("kind") == "送物" and t.get("status") == "awaiting_input"]
                    latest = previous_tasks[-1] if previous_tasks else None
                if latest:
                    task_id, version = latest["task_id"], latest["version"] + 1
                    origin, kind = latest.get("goal_detail", latest.get("message", "")), "送物"
                elif target_ticket:
                    origin = target_ticket["item_detail"]
            goal_detail = message
            if kind == "送物" and command.action == "replace" and command.item:
                previous_quantity = parse_delivery_command(origin).quantity or 1
                quantity = command.quantity if command.quantity is not None else previous_quantity
                goal_detail = f"送 {quantity} 件{command.item}"
            run = TaskRun(session_id, flow, message, force_rules, kind, task_id, version,
                          room_hint, origin, request_id,
                          target_ticket_id=target_ticket["ticket_id"] if target_ticket else None)
            self._runs[run.run_id] = run
            self._outstanding += 1
            if request_id:
                self._requests[(session_id, request_id)] = run.run_id
            flow.state.messages.append({"role": "user", "content": message,
                                        "room_no": room_hint, "run_id": run.run_id})
            flow.state.tasks[task_id] = {**run.public(), "message": message, "goal_detail": goal_detail, "kind": kind}
            if target_active:
                self._supersede(target_active)
                self._active.pop(session_id, None)
            if session_id in self._active:
                self._queues[session_id].append(run.run_id)
            else:
                self._start(run)
            self._prune(session_id)
            return run

    def _start(self, run: TaskRun) -> None:
        self._active[run.session_id] = run.run_id
        run.status = "running"
        run.owner.state.tasks[run.task_id]["status"] = "running"
        self._executor.submit(self._execute, run)

    def _supersede(self, run: TaskRun, status: str = "superseded") -> None:
        run.cancelled.set()
        run.status = status
        if not run.result.done():
            snapshot = self._snapshot(run.owner)
            run.result.set_result(RunOutcome(snapshot, "此前任务已停止提交，正在处理最新请求。",
                                             run.run_id, run.task_id, run.version, status))

    def _is_current(self, run: TaskRun) -> bool:
        return (not run.cancelled.is_set() and self.sessions.get(run.session_id) is run.owner
                and self._active.get(run.session_id) == run.run_id
                and run.owner.state.tasks.get(run.task_id, {}).get("version") == run.version)

    @staticmethod
    def _row_key(collection: str, row: dict[str, Any]) -> Any:
        if collection == "tickets":
            return row["ticket_id"]
        if collection == "checkout_requests":
            return row["room_no"], row["requested_time"]
        return row["voucher_id"]

    def _commit(self, run: TaskRun, baseline: HotelState, stock: dict[str, int], worker: HotelOrchestrator) -> None:
        """Validate every delta before applying any of them under the shared lock."""
        if not self._is_current(run):
            raise TaskInterrupted()
        state, candidate = run.owner.state, worker.state
        if state.current_date != baseline.current_date:
            raise CommitConflict("业务日期已变化，需要重新核验")
        changes: list[tuple[Any, Any, Any]] = []
        additions: list[tuple[list, dict[str, Any]]] = []
        private_changes: dict[str, Any] = {}
        for name in ("room_no", "intent", "sentiment", "active_agent", "request_detail", "pending",
                     "late_checkout", "current_date", "borrowed_items", "audit_logs", "agent_conflicts"):
            before, proposed = getattr(baseline, name), getattr(candidate, name)
            if before != proposed:
                if getattr(state, name) != before:
                    raise CommitConflict("会话业务状态已由运营端更新")
                private_changes[name] = deepcopy(proposed)
        for room, proposed in candidate.pms_rooms.items():
            before = baseline.pms_rooms.get(room, {})
            for key, value in proposed.items():
                if before.get(key) == value:
                    continue
                live = self.shared_pms[room]
                if room != candidate.room_no or live.get(key) != before.get(key):
                    raise CommitConflict("房态或权益记录已变化")
                changes.append((live, key, deepcopy(value)))
        for name in ("tickets", "checkout_requests", "voucher_ledger"):
            original = {self._row_key(name, row): row for row in getattr(baseline, name)}
            live_list = getattr(state, name)
            live_rows = {self._row_key(name, row): row for row in live_list}
            for row in getattr(candidate, name):
                key = self._row_key(name, row)
                before = original.get(key)
                if row == before:
                    continue
                if row.get("room_no") != candidate.room_no:
                    raise CommitConflict("拒绝修改其他房间的业务数据")
                if before is None:
                    if key in live_rows:
                        raise CommitConflict("业务记录已经存在")
                    additions.append((live_list, deepcopy(row)))
                elif live_rows.get(key) != before:
                    raise CommitConflict("工单状态已变化，需要重新核验")
                else:
                    changes.append((live_rows[key], None, deepcopy(row)))
        for item, value in worker.housekeeping.inventory.items():
            if value != stock[item]:
                if self.housekeeping.inventory[item] != stock[item] or value < 0:
                    raise CommitConflict("物资库存已变化")
                changes.append((self.housekeeping.inventory, item, value))
        for container, key, value in changes:
            if key is None:
                container.clear()
                container.update(value)
            else:
                container[key] = value
        for target, row in additions:
            target.append(row)
        for name, value in private_changes.items():
            setattr(state, name, value)
        state.guest_profile = self.shared_pms.get(state.room_no)
        if state.late_checkout:
            state.late_checkout = next((r for r in self.shared_checkout_requests
                                       if r["room_no"] == state.room_no and r["requested_time"] == state.late_checkout["requested_time"]), state.late_checkout)
        state.model_mode, state.last_trace = candidate.model_mode, deepcopy(candidate.last_trace)
        state.last_trace.insert(0, {"agent": "Supervisor", "event": "version_checked",
                                   "detail": f"{run.task_id} / v{run.version}"})
        run.owner.model, run.owner.force_rules = worker.model, worker.force_rules
        run.owner.reception.model = run.owner.model
        run.owner.dispatcher.model = run.owner.model

    def _execute(self, run: TaskRun) -> None:
        try:
            for attempt in range(3):
                with self.lock:
                    if not self._is_current(run):
                        raise TaskInterrupted()
                    worker = self._snapshot(run.owner, run.force_rules)
                    baseline, stock = deepcopy(worker.state), deepcopy(worker.housekeeping.inventory)
                    if run.room_hint and not worker.state.room_no:
                        worker._bind_room(run.room_hint)
                    has_committed_task = any(t.get("task_id") == run.task_id or t["ticket_id"] == run.target_ticket_id for t in worker.state.tickets)
                    if run.origin_detail and not worker.state.pending and run.kind == "送物" and not has_committed_task:
                        worker.state.pending = {"intent": "送物", "detail": run.origin_detail,
                                                "sentiment": "正常", "requested_time": None, "nights": 1,
                                                "uncommitted_delivery": True}
                reply = worker.process(run.message, record_user=False, cancellation_check=run.cancelled.is_set)
                with self.lock:
                    originals = {t["ticket_id"]: t for t in baseline.tickets}
                    for ticket in worker.state.tickets:
                        if ticket != originals.get(ticket["ticket_id"]):
                            ticket.update(task_id=run.task_id, task_version=run.version)
                    try:
                        self._commit(run, baseline, stock, worker)
                    except CommitConflict:
                        if attempt < 2:
                            continue
                        raise
                    state = run.owner.state
                    state.messages.append({"role": "assistant", "content": reply,
                                           "room_no": state.room_no, "run_id": run.run_id})
                    run.owner.compact_messages()
                    run.status = "needs_review" if len(state.agent_conflicts) > len(baseline.agent_conflicts) else "completed"
                    command = parse_delivery_command(run.message)
                    task_status = "awaiting_input" if state.pending else "completed"
                    if run.kind == "送物":
                        task_status = "awaiting_input" if state.pending else "submitted"
                        if any(t.get("change_request", {}).get("status") == "待部门确认" for t in state.tickets if t.get("room_no") == state.room_no):
                            task_status = "awaiting_confirmation"
                        elif any(step["event"] == "delivery_target_required" for step in state.last_trace):
                            task_status = "awaiting_input"
                        elif command.action == "cancel" and (
                            any(t.get("task_id") == run.task_id and t["status"] == "已取消" for t in state.tickets)
                            or any(step["event"] == "task_cancelled" for step in state.last_trace)
                        ):
                            task_status = "cancelled"
                    if run.status == "needs_review":
                        task_status = "needs_review"
                    state.tasks[run.task_id].update(status=task_status, run_status=run.status)
                    self.last_active[run.session_id] = time.time()
                    if not run.result.done():
                        run.result.set_result(RunOutcome(self._snapshot(run.owner), reply, run.run_id,
                                                        run.task_id, run.version, run.status))
                    break
        except TaskInterrupted:
            with self.lock:
                self._supersede(run, run.status if run.cancelled.is_set() else "superseded")
        except Exception as error:
            with self.lock:
                if self._is_current(run):
                    run.status = "failed"
                    run.owner.state.tasks[run.task_id].update(status="failed", reason=type(error).__name__)
                    reply = "任务处理未完成，暂未提交本次操作，请稍后重试或联系前台。"
                    run.owner.state.messages.append({"role": "assistant", "content": reply, "run_id": run.run_id})
                    run.owner.compact_messages()
                    run.result.set_result(RunOutcome(self._snapshot(run.owner), reply, run.run_id,
                                                    run.task_id, run.version, run.status))
                else:
                    self._supersede(run)
        finally:
            with self.lock:
                run.worker_finished = True
                self._outstanding -= 1
                if self._active.get(run.session_id) == run.run_id:
                    self._active.pop(run.session_id, None)
                    queue = self._queues.get(run.session_id)
                    if queue:
                        next_run = self._runs[queue.popleft()]
                        self._start(next_run)
                if self.sessions.get(run.session_id) is not run.owner:
                    self._runs.pop(run.run_id, None)
                else:
                    self._prune(run.session_id)

    def wait(self, session_id: str, run_id: str, timeout: float = 30) -> RunOutcome:
        with self.lock:
            run = self.get_run(session_id, run_id)
        return run.result.result(timeout=timeout)

    def chat_result(self, session_id: str, message: str, room_no: str | None = None,
                    force_rules: bool = False, client_type: str = "guest", request_id: str | None = None) -> RunOutcome:
        run = self.submit(session_id, message, room_no, force_rules, client_type, request_id)
        return self.wait(session_id, run.run_id)

    def chat(self, session_id: str, message: str, room_no: str | None = None,
             force_rules: bool = False, client_type: str = "guest", request_id: str | None = None) -> HotelOrchestrator:
        return self.chat_result(session_id, message, room_no, force_rules, client_type, request_id).flow

    def get_run(self, session_id: str, run_id: str) -> TaskRun:
        with self.lock:
            run = self._runs.get(run_id)
            if not run or run.session_id != session_id or self.sessions.get(session_id) is not run.owner:
                raise KeyError("任务不存在或不属于该会话")
            return run

    def get_session(self, session_id: str) -> HotelOrchestrator | None:
        with self.lock:
            self._cleanup_expired()
            flow = self.sessions.get(session_id)
            if flow:
                self.last_active[session_id] = time.time()
            return flow

    def dispatch_delivery(self, ticket_id: str) -> dict[str, Any]:
        with self.lock:
            ticket = next((t for t in self.shared_tickets if t["ticket_id"] == ticket_id), None)
            if not ticket:
                raise KeyError(ticket_id)
            if ticket.get("category") != "送物" or ticket.get("status") != "处理中":
                raise ValueError("只有未完成的送物工单可以派送")
            if ticket.get("reserved_quantity", 0) != ticket.get("quantity", 1):
                raise ValueError("库存尚未预留，不能确认派送")
            if ticket.get("delivery_status") not in {"待派送", "配送中"}:
                raise ValueError("当前送物状态不允许派送")
            ticket.update(delivery_status="配送中", dispatched_at=datetime.now().isoformat(timespec="seconds"))
            return deepcopy(ticket)

    def confirm_delivery_change(self, ticket_id: str, approved: bool, returned: bool = False) -> dict[str, Any]:
        with self.lock:
            ticket = next((t for t in self.shared_tickets if t["ticket_id"] == ticket_id), None)
            if not ticket:
                raise KeyError(ticket_id)
            change = ticket.get("change_request")
            if not change:
                raise ValueError("该工单没有待确认的变更")
            if change["status"] != "待部门确认":
                return {"ticket": deepcopy(ticket), "reply": "该变更已处理，无需重复执行。"}
            if not approved:
                change["status"] = "已拒绝"
                reply = "保洁确认原物品无法撤回或更换，原工单继续按实际进度处理。"
                self._notify_change(ticket, reply)
                return {"ticket": deepcopy(ticket), "reply": reply}
            if (ticket.get("delivery_status") == "已送达" or ticket["status"] == "已结单") and not returned:
                raise ValueError("已送达物品需先确认取回，不能直接释放库存或宣称取消")
            staged = deepcopy(ticket)
            staged.update(delivery_status="待派送", status="处理中")
            department = copy(self.housekeeping)
            department.lock = threading.RLock()
            department.inventory = deepcopy(self.housekeeping.inventory)
            command = DeliveryCommand(change["action"], change.get("item"), change.get("quantity"))
            reply = department.revise(staged, command)
            if command.action == "cancel":
                valid = staged["status"] == "已取消"
            else:
                valid = staged.get("inventory_item") == command.item and staged.get("quantity") == command.quantity
            if not valid:
                raise ValueError(reply)
            staged["change_request"]["status"] = "已确认"
            self.housekeeping.inventory.clear()
            self.housekeeping.inventory.update(department.inventory)
            ticket.clear()
            ticket.update(staged)
            self._notify_change(ticket, reply)
            return {"ticket": deepcopy(ticket), "reply": reply}

    def _notify_change(self, ticket: dict[str, Any], reply: str) -> None:
        for flow in self.sessions.values():
            task = flow.state.tasks.get(ticket.get("task_id"))
            if task:
                task["status"] = "cancelled" if ticket["status"] == "已取消" else "submitted"
                flow.sync_borrowed_ticket(ticket)
                flow.state.messages.append({"role": "assistant", "content": f"【保洁确认】{reply}", "room_no": ticket["room_no"]})
                flow.state.last_trace.append({"agent": "HousekeepingAgent", "event": "delivery_change_confirmed", "detail": ticket["change_request"]["status"]})
                flow.compact_messages()

    def night_audit(self) -> dict[str, Any]:
        with self.lock:
            previous_date = self.current_date
            self.current_date, actions = run_pms_night_audit(self.shared_pms, previous_date)
            record = {"date": self.current_date, "actions_count": len(actions), "actions": actions,
                      "audited_at": datetime.now().isoformat(timespec="seconds")}
            for flow in self.sessions.values():
                flow.state.current_date = self.current_date
                flow.state.audit_logs.append(deepcopy(record))
            return {"previous_date": previous_date, **record}

    def reset(self, session_id: str) -> bool:
        with self.lock:
            flow = self.sessions.get(session_id)
            for run in list(self._runs.values()):
                if run.owner is flow and not run.result.done():
                    was_queued = run.status == "queued"
                    self._supersede(run, "cancelled")
                    if was_queued:
                        run.worker_finished = True
                        self._outstanding -= 1
            self._active.pop(session_id, None)
            self._queues.pop(session_id, None)
            self.last_active.pop(session_id, None)
            self.session_ttls.pop(session_id, None)
            self._requests = {key: value for key, value in self._requests.items() if key[0] != session_id}
            for run_id, run in list(self._runs.items()):
                if run.session_id == session_id and run.worker_finished:
                    self._runs.pop(run_id, None)
            return self.sessions.pop(session_id, None) is not None

    def reset_all(self) -> None:
        with self.lock:
            for sid in list(self.sessions):
                self.reset(sid)
            self.shared_pms.clear()
            self.shared_pms.update(deepcopy(PMS_ROOMS))
            self.shared_tickets.clear()
            self.shared_checkout_requests.clear()
            self.shared_voucher_ledger.clear()
            self.housekeeping = HousekeepingAgent()
            self.current_date = "2026-10-01"

    def _prune(self, session_id: str) -> None:
        finished = [r for r in self._runs.values() if r.session_id == session_id and r.worker_finished]
        for run in finished[:-100]:
            self._runs.pop(run.run_id, None)
            if run.request_id:
                self._requests.pop((session_id, run.request_id), None)

    def close(self) -> None:
        with self.lock:
            self._closed = True
            self.reset_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def active_session_count(self) -> int:
        with self.lock:
            self._cleanup_expired()
            return len(self.sessions)
