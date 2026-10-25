"""Single-writer API for an academic hotel; UI clients never open the database."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from collections import deque
import threading
from dataclasses import asdict
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent_runtime import RunOutcome, RuntimeBusy, SessionManager
from auth import (Principal, authenticate, enabled, issue_guest_pass, require_session,
                  require_staff, validate_configuration)
from hotel_core import HotelOrchestrator, HotelState, PUBLIC_ASSISTANT_NAME, _room_no
from persistence import StorageConflict
from ticket_details import delivery_fulfillment

session_manager = SessionManager()
_rate_lock = threading.Lock()
_recent_requests: dict[str, deque] = {}


@asynccontextmanager
async def lifespan(_app):
    validate_configuration()
    yield
    session_manager.close()


app = FastAPI(title="AegisStay · 学术酒店服务运营", version="3.0.0", lifespan=lifespan)
origins = [v.strip() for v in os.getenv("AEGIS_ALLOWED_ORIGINS", "http://localhost:8501").split(",") if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def operational_errors(request: Request, call_next):
    from starlette.responses import JSONResponse
    content_length = request.headers.get("content-length", "0")
    if not content_length.isdigit():
        return JSONResponse({"detail": "请求长度无效"}, status_code=400)
    if int(content_length) > 32768:
        return JSONResponse({"detail": "请求内容过长"}, status_code=413)
    try:
        response = await call_next(request)
    except (OSError, sqlite3.Error, StorageConflict, RuntimeBusy):
        return JSONResponse({"detail": "服务暂不可写入，请联系前台，勿重复承诺操作成功"}, status_code=503)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def identity(principal: Principal = Depends(authenticate)) -> Principal:
    if principal.role == "guest":
        try:
            session_manager.validate_guest_access(principal)
        except ValueError as error:
            raise HTTPException(401, str(error)) from error
    return principal


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    room_no: str | None = Field(default=None, pattern=r"^\d{3,4}$")
    client_type: Literal["guest", "kiosk"] = "guest"
    force_rules: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class SessionResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)


class ChangeConfirmation(BaseModel):
    approved: bool
    returned: bool = False
    expected_change_id: str | None = None


class CheckoutReview(BaseModel):
    requested_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    approved: bool | None = None
    expected_request_id: str | None = Field(default=None, max_length=80)


class GuestAccess(BaseModel):
    room_no: str = Field(pattern=r"^\d{3,4}$")
    ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    identity_checked: bool


class NightAudit(BaseModel):
    expected_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class StayRegistration(BaseModel):
    guest_alias: str = Field(min_length=1, max_length=40)
    checkout_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    order_reference: str = Field(min_length=1, max_length=80)
    identity_checked: bool
    replace_existing: bool = False


def _safe_flow(outcome: RunOutcome, principal: Principal) -> HotelOrchestrator:
    flow = copy(outcome.flow)
    if principal.staff:
        return flow
    flow.state = deepcopy(outcome.flow.state)
    flow.state.tickets = [t for t in flow.state.tickets if t.get("stay_id") == principal.stay_id
                          and t.get("owner_session_id") == principal.session_id]
    flow.state.checkout_requests = [r for r in flow.state.checkout_requests if r.get("stay_id") == principal.stay_id
                                    and r.get("owner_session_id") == principal.session_id]
    flow.state.voucher_ledger = [v for v in flow.state.voucher_ledger if v.get("stay_id") == principal.stay_id
                                and v.get("owner_session_id") == principal.session_id]
    return flow


def _chat_response(outcome: RunOutcome, principal: Principal) -> dict:
    flow = _safe_flow(outcome, principal)
    state = flow.state
    return {"session_id": principal.session_id or "", "reply": outcome.reply,
            "active_agent": state.active_agent, "room_no": state.room_no,
            "guest_name": (state.guest_profile or {}).get("guest_name"),
            "intent": state.intent, "sentiment": state.sentiment,
            "model_mode": state.model_mode if principal.staff else "酒店服务助手",
            "model_name": flow.model.model if principal.staff else PUBLIC_ASSISTANT_NAME,
            "trace": state.last_trace if principal.staff else [],
            "progress_items": flow.guest_progress_items(), "run_id": outcome.run_id,
            "task_id": outcome.task_id, "version": outcome.version, "run_status": outcome.status}


def _submit_request(req: ChatRequest, principal: Principal):
    require_session(principal, req.session_id, req.room_no)
    if principal.role == "guest":
        with _rate_lock:
            now = time.monotonic()
            for key in list(_recent_requests):
                if not _recent_requests[key] or now - _recent_requests[key][-1] > 60:
                    _recent_requests.pop(key, None)
            recent = _recent_requests.setdefault(principal.subject, deque())
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= 20 or len(_recent_requests) > 1000:
                raise HTTPException(429, "请求过于频繁，请稍候或联系前台")
            recent.append(now)
        # The pass establishes the room. Neither user wording nor model output
        # may rebind it. Explicit mismatches are rejected before model calls.
        mentioned = _room_no(req.message)
        if mentioned and mentioned != principal.room_no:
            raise HTTPException(403, "请求涉及非本次入住的房间，请联系前台")
        flow = session_manager.get_or_create(req.session_id, req.force_rules)
        flow.state.authorized_room, flow.state.stay_id = principal.room_no, principal.stay_id
        flow._bind_room(principal.room_no)
    try:
        return session_manager.submit(req.session_id, req.message, principal.room_no or req.room_no,
                                      req.force_rules, req.client_type, req.request_id)
    except RuntimeBusy as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/health")
def health():
    return {"status": "degraded" if session_manager.storage_error else "healthy", "service": "AegisStay"}


@app.get("/ready")
def ready():
    if session_manager.storage_error or session_manager._closed:
        raise HTTPException(503, "存储或执行器不可用")
    return {"ready": True, "persistent": session_manager.persistence_enabled()}


@app.get("/api/me")
def me(principal: Principal = Depends(identity)):
    return {**principal.public(), "site_profile": session_manager.site_profile,
            "persistent": session_manager.persistence_enabled()}


@app.post("/api/chat")
def guest_chat(req: ChatRequest, principal: Principal = Depends(identity)):
    run = _submit_request(req, principal)
    try:
        response = _chat_response(session_manager.wait(req.session_id, run.run_id), principal)
    except TimeoutError:
        raise HTTPException(504, {"message": "请求仍在处理，请查询状态，不要更换 request_id 重发", "run_id": run.run_id}) from None
    response["session_id"] = req.session_id
    return response


@app.post("/api/chat/submit", status_code=202)
def submit_chat(req: ChatRequest, principal: Principal = Depends(identity)):
    return {"session_id": req.session_id, **_submit_request(req, principal).public()}


@app.get("/api/tasks/{run_id}")
def get_task(run_id: str, session_id: str = Query(min_length=1, max_length=128), principal: Principal = Depends(identity)):
    require_session(principal, session_id)
    with session_manager.lock:
        try:
            run = session_manager.get_run(session_id, run_id)
        except KeyError:
            raise HTTPException(404, "任务不存在或不属于该会话") from None
        response = {"session_id": session_id, **run.public()}
        if run.result.done():
            response["result"] = _chat_response(run.result.result(), principal)
        return response


@app.post("/api/session/reset")
def reset_session(req: SessionResetRequest, principal: Principal = Depends(identity)):
    require_session(principal, req.session_id)
    removed = session_manager.reset(req.session_id)
    return {"status": "success" if removed else "not_found", "session_id": req.session_id}


@app.get("/api/session/{session_id}")
def session_detail(session_id: str, principal: Principal = Depends(identity)):
    require_session(principal, session_id)
    flow = session_manager.snapshot(session_id)
    if flow is None:
        raise HTTPException(404, "会话不存在")
    state = flow.state
    tickets = [t for t in state.tickets if t.get("room_no") == state.room_no and
               (principal.staff or t.get("stay_id") == principal.stay_id and t.get("owner_session_id") == session_id)]
    return {"session_id": session_id, "room_no": state.room_no, "guest_name": (state.guest_profile or {}).get("guest_name"),
            "member_level": (state.guest_profile or {}).get("member_level"), "active_agent": state.active_agent,
            "messages_count": len(state.messages), "messages": state.messages, "tickets": tickets,
            "tasks": list(state.tasks.values()), "agent_conflicts": state.agent_conflicts if principal.staff else [],
            "checkout_requests": [r for r in state.checkout_requests if r["room_no"] == state.room_no and
                                   (principal.staff or r.get("owner_session_id") == session_id and r.get("stay_id") == principal.stay_id)],
            "progress_items": _safe_flow(RunOutcome(flow, "", "", "", 0, ""), principal).guest_progress_items()}


@app.get("/api/session/{session_id}/messages")
def history(session_id: str, before: int | None = None, limit: int = Query(default=30, ge=1, le=100),
            principal: Principal = Depends(identity)):
    require_session(principal, session_id)
    return {"messages": session_manager.conversation_history(session_id, before, limit)}


def _department(principal: Principal) -> str | None:
    return {"housekeeping": "楼层保洁", "engineering": "工程部"}.get(principal.role)


def _ticket(ticket_id: str, principal: Principal) -> dict:
    require_staff(principal)
    ticket = next((t for t in session_manager.shared_tickets if t["ticket_id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(404, "工单不存在")
    if _department(principal) and ticket["assigned_dept"] != _department(principal):
        raise HTTPException(403, "当前岗位不能办理其他部门工单")
    return ticket


def _business_call(call):
    try:
        return call()
    except KeyError:
        raise HTTPException(404, "业务记录不存在") from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/operations/state")
def operations(principal: Principal = Depends(identity)):
    require_staff(principal)
    with session_manager.lock:
        conflicts = [deepcopy(c) for f in session_manager.sessions.values() for c in f.state.agent_conflicts]
        if session_manager.store:
            for sid in session_manager.store.session_ids():
                if sid not in session_manager.sessions:
                    conflicts.extend(session_manager.store.load_session(sid).get("agent_conflicts", []))
        tickets = [t for t in session_manager.shared_tickets if not _department(principal) or t["assigned_dept"] == _department(principal)]
        state = HotelState(pms_rooms=deepcopy(session_manager.shared_pms), tickets=deepcopy(tickets),
                           current_date=session_manager.current_date,
                           checkout_requests=deepcopy(session_manager.shared_checkout_requests) if not _department(principal) else [],
                           agent_conflicts=conflicts if not _department(principal) else [])
        for ticket in state.tickets:
            ticket["fulfillment"] = delivery_fulfillment(ticket, session_manager.housekeeping.inventory)
        if _department(principal):
            state.pms_rooms = {number: {key: room[key] for key in ("room_no", "room_status", "checkout_time") if key in room}
                               for number, room in state.pms_rooms.items()}
        return {"state": asdict(state), "inventory": deepcopy(session_manager.housekeeping.inventory),
                "quarantine": deepcopy(session_manager.quarantine), "site_profile": session_manager.site_profile}


@app.get("/api/pms/overview")
def overview(principal: Principal = Depends(identity)):
    payload = operations(principal)
    state = HotelState(**payload["state"])
    flow = HotelOrchestrator(state, force_rules=True)
    return {"total_rooms": len(state.pms_rooms), "occupied": sum(r.get("room_status") == "在住" for r in state.pms_rooms.values()),
            "clean": sum(r.get("room_status") == "空净房" for r in state.pms_rooms.values()),
            "pending_tickets": sum(t["status"] == "处理中" for t in state.tickets),
            "department_agents": {r["name"]: r for r in flow.agent_monitoring()}}


@app.get("/api/tickets")
def tickets(room_no: str | None = None, status: str | None = None, category: str | None = None,
            principal: Principal = Depends(identity)):
    require_staff(principal)
    with session_manager.lock:
        rows = [t for t in session_manager.shared_tickets if (not room_no or t["room_no"] == room_no)
                and (not status or t["status"] == status) and (not category or t["category"] == category)
                and (not _department(principal) or t["assigned_dept"] == _department(principal))]
        projected = deepcopy(rows)
        for ticket in projected:
            ticket["fulfillment"] = delivery_fulfillment(ticket, session_manager.housekeeping.inventory)
        return {"total": len(rows), "tickets": projected}


@app.post("/api/tickets/{ticket_id}/complete")
def complete(ticket_id: str, principal: Principal = Depends(identity)):
    _ticket(ticket_id, principal)
    _business_call(lambda: session_manager.complete_ticket(ticket_id, actor=principal.subject))
    return {"status": "success", "ticket_id": ticket_id}


@app.post("/api/tickets/{ticket_id}/dispatch")
def dispatch(ticket_id: str, principal: Principal = Depends(identity)):
    _ticket(ticket_id, principal)
    return _business_call(lambda: session_manager.dispatch_delivery(ticket_id, actor=principal.subject))


@app.post("/api/tickets/{ticket_id}/change/confirm")
def confirm_change(ticket_id: str, request: ChangeConfirmation, principal: Principal = Depends(identity)):
    _ticket(ticket_id, principal)
    if enabled() and not request.expected_change_id:
        raise HTTPException(400, "请提交当前变更标识，刷新工单后重试")
    return _business_call(lambda: session_manager.confirm_delivery_change(ticket_id, request.approved, request.returned,
                                                                          request.expected_change_id, actor=principal.subject))


@app.post("/api/checkout/{room_no}/review")
def checkout_review(room_no: str, request: CheckoutReview, principal: Principal = Depends(identity)):
    require_staff(principal, {"frontdesk", "manager"})
    if enabled() and not request.expected_request_id:
        raise HTTPException(400, "请提交当前延退申请标识，刷新申请后重试")
    _business_call(lambda: session_manager.review_checkout(room_no, request.requested_time, request.approved,
                                                          request.expected_request_id, actor=principal.subject))
    return {"status": "success"}


@app.post("/api/conflicts/{conflict_id}/review")
def conflict_review(conflict_id: str, principal: Principal = Depends(identity)):
    require_staff(principal, {"frontdesk", "manager"})
    _business_call(lambda: session_manager.resolve_conflict(conflict_id, actor=principal.subject))
    return {"status": "success"}


@app.post("/api/pms/night-audit")
def night_audit(request: NightAudit, principal: Principal = Depends(identity)):
    require_staff(principal, {"frontdesk", "manager"})
    return _business_call(lambda: session_manager.night_audit(request.expected_date, actor=principal.subject))


@app.post("/api/guest-access", status_code=201)
def guest_access(request: GuestAccess, principal: Principal = Depends(identity)):
    require_staff(principal, {"frontdesk", "manager"})
    if not request.identity_checked:
        raise HTTPException(400, "必须由前台先核验入住身份")
    record = _business_call(lambda: session_manager.create_guest_access(request.room_no, request.ttl_seconds, actor=principal.subject))
    guest = Principal("guest", record["grant_id"], **{k: record[k] for k in ("session_id", "room_no", "stay_id", "grant_id", "expires_at")})
    return {**record, "access_token": issue_guest_pass(guest)}


@app.post("/api/guest-access/{grant_id}/revoke")
def revoke_access(grant_id: str, principal: Principal = Depends(identity)):
    require_staff(principal, {"frontdesk", "manager"})
    _business_call(lambda: session_manager.revoke_guest_access(grant_id, actor=principal.subject))
    return {"status": "revoked"}


@app.post("/api/rooms/{room_no}/stay")
def register_stay(room_no: str, request: StayRegistration, principal: Principal = Depends(identity)):
    require_staff(principal, {"frontdesk", "manager"})
    if not request.identity_checked:
        raise HTTPException(400, "必须先核验入住订单和身份")
    return _business_call(lambda: session_manager.register_stay(room_no, request.guest_alias, request.checkout_date,
                                                               request.order_reference, request.replace_existing,
                                                               actor=principal.subject))


@app.get("/api/audit")
def audit(limit: int = Query(default=100, ge=1, le=500), principal: Principal = Depends(identity)):
    require_staff(principal, {"manager"})
    return {"events": session_manager.audit(limit)}


@app.post("/api/admin/reset")
def reset_all(principal: Principal = Depends(identity)):
    require_staff(principal, {"admin"})
    if enabled():
        raise HTTPException(403, "部署模式禁止清空全店；请使用离线备份与维护流程")
    session_manager.reset_all()
    return {"status": "ok"}
