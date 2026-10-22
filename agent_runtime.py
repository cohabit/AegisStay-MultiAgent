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
from contextlib import contextmanager
from functools import wraps
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from agent_protocol import DeliveryCommand, TaskInterrupted, parse_delivery_command
from hotel_core import (
    HotelOrchestrator, HotelState, HousekeepingAgent, OpenAICompatibleClient,
    PMS_ROOMS, _room_no, _rules_extract, run_pms_night_audit,
)
from persistence import SQLiteStateStore


class RuntimeBusy(RuntimeError):
    pass


class CommitConflict(RuntimeError):
    pass


def durable(action: str):
    def decorate(method):
        @wraps(method)
        def call(self, *args, **kwargs):
            actor = kwargs.pop("actor", "operator")
            with self.mutation(actor, action, str(args[0]) if args else "hotel"):
                return method(self, *args, **kwargs)
        return call
    return decorate


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
    reply: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "task_id": self.task_id, "version": self.version,
            "status": self.status, "created_at": self.created_at,
        }


class SessionManager:
    """Short shared-state locks; independent sessions execute concurrently."""

    def __init__(self, ttl_seconds: int = 1800, kiosk_ttl_seconds: int = 30,
                 max_workers: int = 8, max_pending: int = 128,
                 db_path: str | None = None):
        initial_date = os.getenv("AEGIS_BUSINESS_DATE") or (date.today().isoformat() if os.getenv("AEGIS_DEPLOYED") == "1" else "2026-10-01")
        date.fromisoformat(initial_date)
        self.ttl_seconds, self.kiosk_ttl_seconds = ttl_seconds, kiosk_ttl_seconds
        self.sessions: dict[str, HotelOrchestrator] = {}
        self.last_active: dict[str, float] = {}
        self.session_ttls: dict[str, int] = {}
        self.lock = threading.RLock()
        configured_db = db_path if db_path is not None else os.getenv("AEGIS_DB_PATH")
        self.store = SQLiteStateStore(configured_db) if configured_db else None
        persisted = self.store.load_business() if self.store else None
        self.shared_pms = deepcopy((persisted or {}).get("pms_rooms") or PMS_ROOMS)
        self.shared_tickets: list[dict[str, Any]] = deepcopy((persisted or {}).get("tickets") or [])
        self.shared_checkout_requests: list[dict[str, Any]] = deepcopy((persisted or {}).get("checkout_requests") or [])
        for request in self.shared_checkout_requests:
            request.setdefault("request_id", "LATE-" + uuid.uuid4().hex)
        self.shared_voucher_ledger: list[dict[str, Any]] = deepcopy((persisted or {}).get("voucher_ledger") or [])
        self.housekeeping = HousekeepingAgent()
        persisted_inventory = (persisted or {}).get("inventory") or {}
        if persisted_inventory:
            self.housekeeping.inventory.update(persisted_inventory)
        self.current_date = (persisted or {}).get("current_date") or initial_date
        self.grants = deepcopy((persisted or {}).get("grants", {}))
        self.quarantine = deepcopy((persisted or {}).get("quarantine", {}))
        self.site_profile = os.getenv("AEGIS_SITE_PROFILE", "standard")
        if self.site_profile == "academic" and not persisted:
            for room in self.shared_pms.values():
                if room.get("room_status") == "在住":
                    room["member_level"] = "学术接待住客"
        for room in self.shared_pms.values():
            room.setdefault("stay_id", "STAY-" + uuid.uuid4().hex)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aegis-agent")
        self._runs: dict[str, TaskRun] = {}
        self._active: dict[str, str] = {}
        self._queues: dict[str, deque[str]] = {}
        self._requests: dict[tuple[str, str], str] = {}
        self._outstanding, self.max_pending = 0, max_pending
        self._closed = False
        self.storage_error: str | None = None
        self._audit: list[dict[str, Any]] = []
        if self.store and not persisted:
            self.persist()

    _PERSISTED_SESSION_FIELDS = (
        "messages", "room_no", "intent", "sentiment", "active_agent", "request_detail",
        "last_trace", "model_mode", "guest_profile", "pending", "late_checkout",
        "current_date", "borrowed_items", "audit_logs", "rolling_summary", "tasks",
        "agent_conflicts",
        "authorized_room", "stay_id", "session_id",
    )

    def _business_payload(self) -> dict[str, Any]:
        return {
            "pms_rooms": self.shared_pms,
            "tickets": self.shared_tickets,
            "checkout_requests": self.shared_checkout_requests,
            "voucher_ledger": self.shared_voucher_ledger,
            "inventory": self.housekeeping.inventory,
            "current_date": self.current_date,
            "grants": self.grants,
            "quarantine": self.quarantine,
        }

    def _session_payload(self, flow: HotelOrchestrator) -> dict[str, Any]:
        payload = {name: deepcopy(getattr(flow.state, name)) for name in self._PERSISTED_SESSION_FIELDS}
        return payload

    def persist(self, session_id: str | None = None, *, event: dict | None = None,
                deleted: tuple[str, ...] = ()) -> None:
        """Flush the current business state and optional session to SQLite."""
        with self.lock:
            if self.store:
                for flow in self.sessions.values():
                    for message in flow.state.messages:
                        message.setdefault("message_id", uuid.uuid4().hex)
                sessions = [{"session_id": sid, "state": self._session_payload(flow),
                             "client_type": "kiosk" if self.session_ttls[sid] == self.kiosk_ttl_seconds else "guest",
                             "ttl_seconds": self.session_ttls[sid], "updated_at": self.last_active[sid]}
                            for sid, flow in self.sessions.items()]
                runs = [{**r.public(), "session_id": r.session_id, "message": r.message,
                         "force_rules": r.force_rules, "kind": r.kind, "room_hint": r.room_hint,
                         "request_id": r.request_id, "reply": r.reply}
                        for r in self._runs.values() if self.sessions.get(r.session_id) is r.owner]
                try:
                    self.store.checkpoint(self._business_payload(), sessions, runs, deleted, event)
                except Exception as error:
                    self.storage_error = type(error).__name__
                    raise
                self.storage_error = None
            if event:
                self._audit.append({**event, "timestamp": time.time()})
                self._audit = self._audit[-500:]

    @contextmanager
    def mutation(self, actor: str, action: str, target: str = ""):
        """Do not acknowledge a write until the durable checkpoint has succeeded."""
        with self.lock:
            business = deepcopy(self._business_payload())
            private = {sid: self._session_payload(flow) for sid, flow in self.sessions.items()}
            try:
                yield
                self.persist(event={"actor": actor, "action": action, "target": target})
            except Exception:
                for name, container in (("pms_rooms", self.shared_pms), ("tickets", self.shared_tickets),
                                        ("checkout_requests", self.shared_checkout_requests), ("voucher_ledger", self.shared_voucher_ledger),
                                        ("inventory", self.housekeeping.inventory), ("grants", self.grants),
                                        ("quarantine", self.quarantine)):
                    if isinstance(container, dict):
                        for key in list(container):
                            if key not in business[name]:
                                container.pop(key)
                        for key, value in business[name].items():
                            if isinstance(value, dict) and isinstance(container.get(key), dict):
                                container[key].clear(); container[key].update(value)
                            else:
                                container[key] = value
                    else:
                        rows = {self._row_key(name, r): r for r in container}
                        restored_rows = []
                        for row in business[name]:
                            live = rows.get(self._row_key(name, row))
                            if live is not None:
                                live.clear(); live.update(row)
                            restored_rows.append(live if live is not None else row)
                        container[:] = restored_rows
                self.current_date = business["current_date"]
                for sid, payload in private.items():
                    flow = self.sessions[sid]
                    for name, value in payload.items():
                        setattr(flow.state, name, value)
                    flow.state.guest_profile = self.shared_pms.get(flow.state.room_no)
                for sid in set(self.sessions) - set(private):
                    self.sessions.pop(sid, None)
                    self.last_active.pop(sid, None)
                    self.session_ttls.pop(sid, None)
                    self._queues.pop(sid, None)
                    for run_id, run in list(self._runs.items()):
                        if run.session_id == sid:
                            self._runs.pop(run_id)
                            if run.request_id:
                                self._requests.pop((sid, run.request_id), None)
                raise

    def audit(self, limit: int = 100) -> list[dict]:
        with self.lock:
            return self.store.audit(limit) if self.store else deepcopy(self._audit[-limit:][::-1])

    def persistence_enabled(self) -> bool:
        return self.store is not None

    def _cleanup_expired(self) -> None:
        now = time.time()
        for sid, timestamp in list(self.last_active.items()):
            if sid not in self._active and now - timestamp > self.session_ttls.get(sid, self.ttl_seconds):
                if self.session_ttls.get(sid) == self.kiosk_ttl_seconds:
                    self.reset(sid)
                elif self.store:
                    self.persist(sid)
                    self.sessions.pop(sid, None)
                    self.last_active.pop(sid, None)
                    self.session_ttls.pop(sid, None)
                    self._queues.pop(sid, None)
                    for run_id, run in list(self._runs.items()):
                        if run.session_id == sid:
                            self._runs.pop(run_id)
                            if run.request_id:
                                self._requests.pop((sid, run.request_id), None)
                else:
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
                if len(self.sessions) >= 256:
                    raise RuntimeBusy("活跃会话过多，请稍后重试或联系前台")
                restored = self.store.load_session(session_id) if self.store else None
                state = HotelState(pms_rooms=self.shared_pms, tickets=self.shared_tickets,
                                   checkout_requests=self.shared_checkout_requests,
                                   voucher_ledger=self.shared_voucher_ledger, current_date=self.current_date)
                if restored:
                    for name in self._PERSISTED_SESSION_FIELDS:
                        if name in restored:
                            setattr(state, name, restored[name])
                    state.pms_rooms = self.shared_pms
                    state.tickets = self.shared_tickets
                    state.checkout_requests = self.shared_checkout_requests
                    state.voucher_ledger = self.shared_voucher_ledger
                    state.current_date = self.current_date
                    for task in state.tasks.values():
                        if task.get("status") in {"running", "queued"}:
                            task.update(status="interrupted", reason="服务进程重启，需重新提交")
                state.session_id = session_id
                flow = HotelOrchestrator(state, force_rules=force_rules)
                flow.housekeeping = self.housekeeping
                flow.vertical_agents[flow.housekeeping.name] = self.housekeeping
                self.sessions[session_id] = flow
                self.session_ttls[session_id] = int((restored or {}).get("ttl_seconds") or (self.kiosk_ttl_seconds if client_type.lower() == "kiosk" else self.ttl_seconds))
                self._queues[session_id] = deque()
                if self.store and restored:
                    for data in self.store.load_runs(session_id):
                        if data["run_id"] in self._runs:
                            continue
                        self._restore_run(session_id, flow, data)
            self.last_active[session_id] = time.time()
            return self.sessions[session_id]

    def _restore_run(self, session_id: str, flow: HotelOrchestrator, data: dict) -> TaskRun:
        run = TaskRun(session_id, flow, data["message"], data["force_rules"], data["kind"],
                      data["task_id"], data["version"], data["room_hint"], request_id=data.get("request_id"),
                      run_id=data["run_id"], status=data["status"], created_at=data["created_at"],
                      worker_finished=True, reply=data.get("reply", ""))
        if run.status in {"running", "queued"}:
            run.status, run.reply = "interrupted", "服务重启，本次执行未确认完成；请检查服务进度后重新提交。"
        run.result.set_result(RunOutcome(self._snapshot(flow), run.reply, run.run_id, run.task_id, run.version, run.status))
        self._runs[run.run_id] = run
        if run.request_id:
            self._requests[(session_id, run.request_id)] = run.run_id
        return run

    def adopt(self, session_id: str, flow: HotelOrchestrator) -> None:
        """Attach the existing Streamlit demo state without losing its business data."""
        with self.lock:
            if self.sessions:
                raise ValueError("仅空执行器可以接管已有演示状态")
            flow.state.session_id = session_id
            restored = self.store.load_session(session_id) if self.store else None
            flow.state.pms_rooms = self.shared_pms
            flow.state.tickets = self.shared_tickets
            flow.state.checkout_requests = self.shared_checkout_requests
            flow.state.voucher_ledger = self.shared_voucher_ledger
            flow.state.current_date = self.current_date
            if restored:
                for name in self._PERSISTED_SESSION_FIELDS:
                    if name in restored:
                        setattr(flow.state, name, restored[name])
                flow.state.pms_rooms = self.shared_pms
                flow.state.tickets = self.shared_tickets
                flow.state.checkout_requests = self.shared_checkout_requests
                flow.state.voucher_ledger = self.shared_voucher_ledger
                flow.state.current_date = self.current_date
            self.shared_pms, self.shared_tickets = flow.state.pms_rooms, flow.state.tickets
            self.shared_checkout_requests = flow.state.checkout_requests
            self.shared_voucher_ledger = flow.state.voucher_ledger
            if self.store:
                flow.housekeeping = self.housekeeping
                flow.vertical_agents[flow.housekeeping.name] = flow.housekeeping
            else:
                self.housekeeping = flow.housekeeping
            self.current_date = flow.state.current_date
            self.sessions[session_id] = flow
            self.last_active[session_id] = time.time()
            self.session_ttls[session_id] = self.ttl_seconds
            self._queues[session_id] = deque()
            flow.reception.pms_rooms = self.shared_pms

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

    def conversation_history(self, session_id: str, before: int | None = None, limit: int = 30) -> list[dict]:
        with self.lock:
            if self.store:
                return self.store.messages(session_id, before, limit)
            flow = self.get_session(session_id)
            return deepcopy(flow.state.messages[-limit:]) if flow else []

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
            if self.storage_error:
                raise RuntimeBusy("存储写入异常，暂停售理新请求，请联系值班人员")
            self._cleanup_expired()
            flow = self.get_or_create(session_id, force_rules, client_type)
            if request_id and (session_id, request_id) not in self._requests and self.store:
                record = self.store.find_run(session_id, request_id=request_id)
                if record:
                    self._restore_run(session_id, flow, record)
            if request_id and (session_id, request_id) in self._requests:
                previous = self._runs[self._requests[(session_id, request_id)]]
                if previous.message != message or previous.room_hint != (room_no or _room_no(message) or previous.room_hint):
                    raise ValueError("同一 request_id 不能用于不同请求")
                return previous
            if self._outstanding >= self.max_pending:
                raise RuntimeBusy("任务队列已满，请稍后重试")
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
            try:
                self.persist(session_id)
            except Exception as error:
                self.storage_error = type(error).__name__
                run.status, run.worker_finished = "failed", True
                self._outstanding -= 1
                flow.state.tasks[task_id]["status"] = "failed"
                raise RuntimeBusy("请求未受理：持久化失败，请联系前台") from error
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
        run.reply = "此前任务已停止提交，正在处理最新请求。"
        task = run.owner.state.tasks.get(run.task_id)
        if task and task.get("run_id") == run.run_id:
            task["status"] = status
        if not run.result.done():
            snapshot = self._snapshot(run.owner)
            run.result.set_result(RunOutcome(snapshot, "此前任务已停止提交，正在处理最新请求。",
                                             run.run_id, run.task_id, run.version, status))

    def _is_current(self, run: TaskRun) -> bool:
        state = run.owner.state
        if state.authorized_room:
            room = self.shared_pms.get(state.authorized_room, {})
            valid_pass = any(g["session_id"] == run.session_id and not g.get("revoked") and g["expires_at"] > time.time()
                             for g in self.grants.values())
            if room.get("stay_id") != state.stay_id or room.get("room_status") != "在住" or not valid_pass:
                return False
        return (not run.cancelled.is_set() and self.sessions.get(run.session_id) is run.owner
                and self._active.get(run.session_id) == run.run_id
                and run.owner.state.tasks.get(run.task_id, {}).get("version") == run.version)

    @staticmethod
    def _row_key(collection: str, row: dict[str, Any]) -> Any:
        if collection == "tickets":
            return row["ticket_id"]
        if collection == "checkout_requests":
            return row.get("request_id") or (row["room_no"], row["requested_time"])
        return row["voucher_id"]

    def _commit(self, run: TaskRun, baseline: HotelState, stock: dict[str, int], worker: HotelOrchestrator) -> None:
        """Validate every delta before applying any of them under the shared lock."""
        if not self._is_current(run):
            raise TaskInterrupted()
        state, candidate = run.owner.state, worker.state
        if state.authorized_room and candidate.room_no != state.authorized_room:
            raise ValueError("拒绝提交不属于入住凭证的房间")
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
                if before is not None and state.authorized_room and (
                    before.get("stay_id") != state.stay_id or before.get("owner_session_id") != run.session_id
                ):
                    raise ValueError("拒绝修改不属于本次入住会话的业务记录")
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
                                       if self._row_key("checkout_requests", r) == self._row_key("checkout_requests", state.late_checkout)), state.late_checkout)
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
                    if worker.state.authorized_room:
                        for collection in ("tickets", "checkout_requests", "voucher_ledger"):
                            rows = getattr(worker.state, collection)
                            setattr(worker.state, collection, [row for row in rows
                                    if row.get("stay_id") == worker.state.stay_id
                                    and row.get("owner_session_id") == run.session_id])
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
                            if ticket["ticket_id"] not in originals:
                                ticket.update(owner_session_id=run.session_id,
                                              stay_id=worker.state.stay_id or self.shared_pms.get(ticket["room_no"], {}).get("stay_id"))
                    baseline_requests = {self._row_key("checkout_requests", r): r for r in baseline.checkout_requests}
                    for request in worker.state.checkout_requests:
                        if request != baseline_requests.get(self._row_key("checkout_requests", request)):
                            request.update(owner_session_id=run.session_id,
                                           stay_id=worker.state.stay_id or self.shared_pms.get(request["room_no"], {}).get("stay_id"))
                    baseline_vouchers = {v["voucher_id"] for v in baseline.voucher_ledger}
                    for voucher in worker.state.voucher_ledger:
                        if voucher["voucher_id"] not in baseline_vouchers:
                            voucher.update(owner_session_id=run.session_id,
                                           stay_id=worker.state.stay_id or self.shared_pms.get(voucher["room_no"], {}).get("stay_id"))
                    try:
                        with self.mutation(run.session_id, "task.commit", run.task_id):
                            self._commit(run, baseline, stock, worker)
                            self._finish_task(run, baseline, reply)
                    except CommitConflict:
                        if attempt < 2:
                            continue
                        raise
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
                    run.reply = reply
                    run.owner.state.messages.append({"role": "assistant", "content": reply, "run_id": run.run_id})
                    run.owner.compact_messages()
                    try:
                        self.persist(run.session_id)
                    except Exception as storage_error:
                        self.storage_error = type(storage_error).__name__
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

    def _finish_task(self, run: TaskRun, baseline: HotelState, reply: str) -> None:
        state = run.owner.state
        state.messages.append({"role": "assistant", "content": reply, "room_no": state.room_no, "run_id": run.run_id})
        run.owner.compact_messages()
        run.status = "needs_review" if len(state.agent_conflicts) > len(baseline.agent_conflicts) else "completed"
        run.reply = reply
        command = parse_delivery_command(run.message)
        owned_tickets = [t for t in state.tickets if not state.authorized_room or
                         t.get("stay_id") == state.stay_id and t.get("owner_session_id") == run.session_id]
        task_status = "awaiting_input" if state.pending else "completed"
        if run.kind == "送物":
            task_status = "awaiting_input" if state.pending else "submitted"
            if any(t.get("change_request", {}).get("status") == "待部门确认" for t in owned_tickets if t.get("room_no") == state.room_no):
                task_status = "awaiting_confirmation"
            elif any(step["event"] == "delivery_target_required" for step in state.last_trace):
                task_status = "awaiting_input"
            elif command.action == "cancel" and (
                any(t.get("task_id") == run.task_id and t["status"] == "已取消" for t in owned_tickets)
                or any(step["event"] == "task_cancelled" for step in state.last_trace)):
                task_status = "cancelled"
        if run.status == "needs_review":
            task_status = "needs_review"
        state.tasks[run.task_id].update(status=task_status, run_status=run.status)
        self.last_active[run.session_id] = time.time()

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
            self.get_session(session_id)
            run = self._runs.get(run_id)
            if run is None and self.store and session_id in self.sessions:
                data = self.store.find_run(session_id, run_id=run_id)
                if data:
                    run = self._restore_run(session_id, self.sessions[session_id], data)
            if not run or run.session_id != session_id or self.sessions.get(session_id) is not run.owner:
                raise KeyError("任务不存在或不属于该会话")
            return run

    def get_session(self, session_id: str) -> HotelOrchestrator | None:
        with self.lock:
            self._cleanup_expired()
            flow = self.sessions.get(session_id)
            if flow is None and self.store and self.store.load_session(session_id):
                flow = self.get_or_create(session_id)
            if flow:
                self.last_active[session_id] = time.time()
            return flow

    @durable("delivery.dispatch")
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
            if ticket.get("delivery_status") == "配送中":
                return deepcopy(ticket)
            ticket.update(delivery_status="配送中", dispatched_at=datetime.now().isoformat(timespec="seconds"))
            return deepcopy(ticket)

    @durable("delivery.confirm_change")
    def confirm_delivery_change(self, ticket_id: str, approved: bool, returned: bool = False,
                                expected_change_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            ticket = next((t for t in self.shared_tickets if t["ticket_id"] == ticket_id), None)
            if not ticket:
                raise KeyError(ticket_id)
            change = ticket.get("change_request")
            if not change:
                raise ValueError("该工单没有待确认的变更")
            if expected_change_id is not None and change.get("change_id") != expected_change_id:
                raise ValueError("变更申请已更新，请刷新并重新核对")
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
            was_delivered = ticket.get("delivery_status") == "已送达" or ticket["status"] == "已结单"
            if was_delivered:
                # Returned linen/consumables require inspection; never make them
                # available simply because a guest asked for cancellation.
                staged["reserved_quantity"] = 0
            command = DeliveryCommand(change["action"], change.get("item"), change.get("quantity"))
            reply = department.revise(staged, command)
            if command.action == "cancel":
                valid = staged["status"] == "已取消"
            else:
                valid = staged.get("inventory_item") == command.item and staged.get("quantity") == command.quantity
            if not valid:
                raise ValueError(reply)
            staged["change_request"]["status"] = "已确认"
            if was_delivered:
                item = ticket.get("inventory_item")
                self.quarantine[item] = self.quarantine.get(item, 0) + ticket.get("reserved_quantity", 0)
                staged["return_status"] = "已回收待检验"
            self.housekeeping.inventory.clear()
            self.housekeeping.inventory.update(department.inventory)
            ticket.clear()
            ticket.update(staged)
            self._notify_change(ticket, reply)
            return {"ticket": deepcopy(ticket), "reply": reply}

    def _notify_change(self, ticket: dict[str, Any], reply: str) -> None:
        if ticket.get("owner_session_id"):
            self.get_session(ticket["owner_session_id"])
        for flow in self.sessions.values():
            task = flow.state.tasks.get(ticket.get("task_id"))
            if task:
                task["status"] = "cancelled" if ticket["status"] == "已取消" else "submitted"
                flow.sync_borrowed_ticket(ticket)
                flow.state.messages.append({"role": "assistant", "content": f"【保洁确认】{reply}", "room_no": ticket["room_no"]})
                flow.state.last_trace.append({"agent": "HousekeepingAgent", "event": "delivery_change_confirmed", "detail": ticket["change_request"]["status"]})
                flow.compact_messages()

    @durable("pms.night_audit")
    def night_audit(self, expected_date: str | None = None) -> dict[str, Any]:
        with self.lock:
            if expected_date is not None and expected_date != self.current_date:
                raise ValueError("业务日期已变化，请刷新后再执行，避免重复跨日")
            previous_date = self.current_date
            self.current_date, actions = run_pms_night_audit(self.shared_pms, previous_date)
            record = {"date": self.current_date, "actions_count": len(actions), "actions": actions,
                      "audited_at": datetime.now().isoformat(timespec="seconds")}
            for flow in self.sessions.values():
                flow.state.current_date = self.current_date
                flow.state.audit_logs.append(deepcopy(record))
            return {"previous_date": previous_date, **record}

    @durable("ticket.complete")
    def complete_ticket(self, ticket_id: str) -> dict:
        ticket = next((t for t in self.shared_tickets if t["ticket_id"] == ticket_id), None)
        if ticket is None:
            raise KeyError(ticket_id)
        if ticket.get("change_request", {}).get("status") == "待部门确认":
            raise ValueError("待确认的变更必须先处理，不能直接结单")
        if ticket["status"] == "已结单":
            return deepcopy(ticket)
        flow = self.get_session(ticket.get("owner_session_id", ""))
        if flow is None:
            flow = HotelOrchestrator(HotelState(pms_rooms=self.shared_pms, tickets=self.shared_tickets), force_rules=True)
            flow.housekeeping = self.housekeeping
        flow.complete_ticket(ticket_id)
        for session in self.sessions.values():
            task = session.state.tasks.get(ticket.get("task_id"))
            if task:
                task["status"] = "completed"
                session.state.messages.append({"role": "assistant", "content": f"【部门回执】{ticket['category']}服务已完成，如仍有问题请联系前台。"})
                session.compact_messages()
        return deepcopy(ticket)

    @durable("checkout.review")
    def review_checkout(self, room_no: str, requested_time: str, approved: bool | None,
                        expected_request_id: str | None = None) -> None:
        request = next((r for r in reversed(self.shared_checkout_requests)
                        if r["room_no"] == room_no and r["requested_time"] == requested_time
                        and (not expected_request_id or r.get("request_id") == expected_request_id)
                        and (expected_request_id or not r.get("stay_id") or r["stay_id"] == self.shared_pms[room_no].get("stay_id"))), None)
        if request is None:
            raise KeyError("延退申请不存在")
        if request.get("stay_id") and request["stay_id"] != self.shared_pms[room_no].get("stay_id"):
            raise ValueError("入住记录已变化，不能审批上一住客的申请")
        if self.shared_pms[room_no].get("room_status") != "在住":
            raise ValueError("房间已非在住，请前台先核对入住状态")
        if request["status"] == ({True: "已批准", False: "未批准", None: "需前台审核"}[approved]):
            return
        if request.get("owner_session_id"):
            self.get_session(request["owner_session_id"])
        flows = [f for sid, f in self.sessions.items() if f.state.room_no == room_no
                 and (not request.get("owner_session_id") or sid == request["owner_session_id"])]
        if not flows:
            raise ValueError("申请所属会话不存在，请人工核对")
        for flow in flows:
            flow.review_checkout(room_no, requested_time, approved, request.get("request_id"))
            flow.compact_messages()

    @durable("conflict.review")
    def resolve_conflict(self, conflict_id: str) -> None:
        for sid in self.store.session_ids() if self.store else []:
            data = self.store.load_session(sid)
            if any(c["conflict_id"] == conflict_id for c in data.get("agent_conflicts", [])):
                self.get_session(sid)
        for flow in self.sessions.values():
            if any(c["conflict_id"] == conflict_id for c in flow.state.agent_conflicts):
                flow.resolve_agent_conflict(conflict_id)
                return
        raise KeyError(conflict_id)

    @durable("guest_access.issue")
    def create_guest_access(self, room_no: str, ttl_seconds: int = 86400) -> dict:
        room = self.shared_pms.get(room_no)
        if not room or room.get("room_status") != "在住":
            raise ValueError("只能为核验为在住的房间签发凭证")
        if not 60 <= ttl_seconds <= 7 * 86400:
            raise ValueError("凭证有效期需要在 1 分钟至 7 天之间")
        grant = {"grant_id": "PASS-" + uuid.uuid4().hex, "session_id": "guest-" + uuid.uuid4().hex,
                 "room_no": room_no, "stay_id": room["stay_id"], "expires_at": int(time.time()) + ttl_seconds,
                 "revoked": False}
        self.grants[grant["grant_id"]] = grant
        flow = self.get_or_create(grant["session_id"])
        flow.state.authorized_room, flow.state.stay_id = room_no, room["stay_id"]
        flow._bind_room(room_no)
        return deepcopy(grant)

    @durable("guest_access.revoke")
    def revoke_guest_access(self, grant_id: str) -> None:
        if grant_id not in self.grants:
            raise KeyError(grant_id)
        self.grants[grant_id]["revoked"] = True

    def validate_guest_access(self, principal) -> None:
        with self.lock:
            grant = self.grants.get(principal.grant_id)
            room = self.shared_pms.get(principal.room_no, {})
            if (not grant or grant.get("revoked") or grant["expires_at"] <= time.time()
                    or room.get("room_status") != "在住" or room.get("stay_id") != principal.stay_id
                    or any(grant.get(k) != getattr(principal, k) for k in ("session_id", "room_no", "stay_id"))):
                raise ValueError("入住凭证已失效，请联系前台")

    @durable("stay.register")
    def register_stay(self, room_no: str, guest_alias: str, checkout_date: str, order_reference: str,
                      replace_existing: bool = False) -> dict:
        from datetime import date
        room = self.shared_pms.get(room_no)
        if room is None:
            raise KeyError(room_no)
        if date.fromisoformat(checkout_date) < date.today():
            raise ValueError("退房日期不能早于今天")
        if not guest_alias.strip() or not order_reference.strip():
            raise ValueError("需要核验的住客称呼和接待订单编号")
        if room.get("room_status") == "在住" and not replace_existing:
            raise ValueError("该房间仍在住，必须核对原入住并明确确认更替")
        if any(t["room_no"] == room_no and t["status"] == "处理中" for t in self.shared_tickets):
            raise ValueError("该房间仍有未完成工单，请先结清或取消原服务")
        old_stay = room.get("stay_id")
        room.update(stay_id="STAY-" + uuid.uuid4().hex, guest_name=guest_alias.strip(), room_status="在住",
                    checkout_date=checkout_date, checkout_time="12:00", member_level="学术接待住客",
                    order_reference=order_reference.strip(), daily_voucher_used=0)
        for grant in self.grants.values():
            if grant["room_no"] == room_no:
                grant["revoked"] = True
        return {"room_no": room_no, "stay_id": room["stay_id"], "previous_stay_id": old_stay}

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
            removed = self.sessions.pop(session_id, None) is not None
            if removed and self.store:
                self.persist(deleted=(session_id,))
            return removed

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
            self.grants.clear()
            if self.store:
                self.persist(deleted=tuple(self.store.session_ids()))

    def _prune(self, session_id: str) -> None:
        finished = [r for r in self._runs.values() if r.session_id == session_id and r.worker_finished]
        for run in finished[:-100]:
            self._runs.pop(run.run_id, None)
            if run.request_id:
                self._requests.pop((session_id, run.request_id), None)

    def close(self) -> None:
        with self.lock:
            if self._closed:
                return
            self._closed = True
            for run in list(self._runs.values()):
                if not run.result.done():
                    self._supersede(run, "interrupted")
                    task = run.owner.state.tasks.get(run.task_id)
                    if task and task.get("run_id") == run.run_id:
                        task.update(status="interrupted")
        # Let cooperative workers finish before closing the SQLite connection.
        self._executor.shutdown(wait=True, cancel_futures=True)
        if self.store:
            try:
                self.persist()
            finally:
                self.store.close()

    @property
    def active_session_count(self) -> int:
        with self.lock:
            self._cleanup_expired()
            return len(self.sessions)
