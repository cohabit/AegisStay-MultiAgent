"""AegisStay FastAPI gateway with isolated conversation sessions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent_runtime import RunOutcome, RuntimeBusy, SessionManager


session_manager = SessionManager()


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="唯一会话ID")
    message: str = Field(..., min_length=1, description="住客自然语言消息")
    room_no: Optional[str] = Field(None, description="可选的预填房间号")
    client_type: str = Field("guest", description="guest 或 kiosk；kiosk 空闲 30 秒自动销毁")
    force_rules: bool = Field(False, description="压测或验收时禁用云端模型")
    request_id: Optional[str] = Field(None, min_length=1, max_length=128, description="可选的重试去重标识")


class ProgressItem(BaseModel):
    label: str
    status: str
    type: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    active_agent: str
    room_no: Optional[str] = None
    guest_name: Optional[str] = None
    intent: str
    sentiment: str
    model_mode: str
    model_name: str
    trace: list[dict[str, str]]
    progress_items: list[ProgressItem]
    run_id: str
    task_id: str
    version: int
    run_status: str


class SessionResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class SessionDetailResponse(BaseModel):
    session_id: str
    room_no: Optional[str] = None
    guest_name: Optional[str] = None
    member_level: Optional[str] = None
    active_agent: str
    messages_count: int
    messages: list[dict[str, Any]]
    tickets: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    agent_conflicts: list[dict[str, Any]]


class ChangeConfirmation(BaseModel):
    approved: bool
    returned: bool = False


class DepartmentStatus(BaseModel):
    name: str
    status: str
    active_tasks: int


class PMSOverviewResponse(BaseModel):
    total_rooms: int
    occupied: int
    clean: int
    pending_tickets: int
    department_agents: dict[str, DepartmentStatus]


app = FastAPI(
    title="AegisStay Hotel Multi-Agent Gateway",
    description="高并发、多会话隔离、Multi-Agent 协作的智慧酒店运营中枢服务",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, Any]:
    with session_manager.lock:
        session_manager._cleanup_expired()
        model_online = any(bool(flow.model.client) for flow in session_manager.sessions.values())
        return {
            "status": "healthy",
            "service": "AegisStay Gateway",
            "model_online": model_online,
            "active_sessions": len(session_manager.sessions),
            "timestamp": datetime.now().isoformat(),
        }


def _chat_response(outcome: RunOutcome) -> ChatResponse:
    flow = outcome.flow
    state = flow.state
    profile = state.guest_profile or {}
    return ChatResponse(
        session_id="",
        reply=outcome.reply,
        active_agent=state.active_agent,
        room_no=state.room_no,
        guest_name=profile.get("guest_name"),
        intent=state.intent,
        sentiment=state.sentiment,
        model_mode=state.model_mode,
        model_name=(
            flow.model.model
            if flow.model.client
            else "规则引擎 (force_rules=True)"
            if flow.force_rules
            else f"{flow.model.model} (云端未连接，规则降级)"
        ),
        trace=state.last_trace,
        progress_items=[ProgressItem(**item) for item in flow.guest_progress_items()],
        run_id=outcome.run_id,
        task_id=outcome.task_id,
        version=outcome.version,
        run_status=outcome.status,
    )


def _submit_request(req: ChatRequest):
    try:
        return session_manager.submit(req.session_id, req.message, req.room_no, req.force_rules,
                                      req.client_type, req.request_id)
    except RuntimeBusy as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/chat", response_model=ChatResponse, tags=["Guest Chat"])
def guest_chat(req: ChatRequest) -> ChatResponse:
    run = _submit_request(req)
    try:
        outcome = session_manager.wait(req.session_id, run.run_id)
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail={"message": "处理仍在进行，请查询任务状态", "run_id": run.run_id}) from error
    response = _chat_response(outcome)
    response.session_id = req.session_id
    return response


@app.post("/api/chat/submit", status_code=202, tags=["Guest Chat"])
def submit_chat(req: ChatRequest) -> dict[str, Any]:
    run = _submit_request(req)
    return {"session_id": req.session_id, **run.public()}


@app.get("/api/tasks/{run_id}", tags=["Guest Chat"])
def get_task(run_id: str, session_id: str = Query(..., min_length=1)) -> dict[str, Any]:
    with session_manager.lock:
        try:
            run = session_manager.get_run(session_id, run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="任务不存在或不属于该会话") from error
        result: dict[str, Any] = {"session_id": session_id, **run.public()}
        if run.result.done():
            response = _chat_response(run.result.result())
            response.session_id = session_id
            result["result"] = response
        return result


@app.post("/api/session/reset", tags=["Session"])
def reset_session(req: SessionResetRequest) -> dict[str, str]:
    success = session_manager.reset(req.session_id)
    return {
        "status": "success" if success else "not_found",
        "session_id": req.session_id,
        "message": "会话已重置并安全销毁历史上下文" if success else "会话不存在或已过期",
    }


@app.post("/api/admin/reset", tags=["Session"])
def reset_all_sessions() -> dict[str, str]:
    session_manager.reset_all()
    return {"status": "ok", "message": "所有会话已重置并恢复初始状态"}


@app.get("/api/session/{session_id}", response_model=SessionDetailResponse, tags=["Session"])
def get_session_detail(session_id: str) -> SessionDetailResponse:
    flow = session_manager.snapshot(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="会话不存在或已超时")
    state = flow.state
    profile = state.guest_profile or {}
    with session_manager.lock:
        messages = deepcopy(state.messages)
        tickets = deepcopy([t for t in state.tickets if t.get("room_no") == state.room_no])
    return SessionDetailResponse(
        session_id=session_id,
        room_no=state.room_no,
        guest_name=profile.get("guest_name"),
        member_level=profile.get("member_level"),
        active_agent=state.active_agent,
        messages_count=len(messages),
        messages=messages,
        tickets=tickets,
        tasks=list(state.tasks.values()),
        agent_conflicts=state.agent_conflicts,
    )


@app.get("/api/pms/overview", response_model=PMSOverviewResponse, tags=["Operations & PMS"])
def get_pms_overview() -> PMSOverviewResponse:
    with session_manager.lock:
        pms = session_manager.shared_pms
        tickets = session_manager.shared_tickets
        occupied = sum(1 for room in pms.values() if room.get("room_status") == "在住")
        clean = sum(1 for room in pms.values() if room.get("room_status") in {"空房", "干净"} or room.get("clean_status") == "已保洁")
        pending = [ticket for ticket in tickets if ticket.get("status") == "处理中"]

        def count_agent(name: str) -> int:
            return sum(1 for ticket in pending if ticket.get("agent") == name)

        return PMSOverviewResponse(
            total_rooms=len(pms),
            occupied=occupied,
            clean=clean,
            pending_tickets=len(pending),
            department_agents={
                "HousekeepingAgent": DepartmentStatus(name="客房保洁智能体", status="在线", active_tasks=count_agent("HousekeepingAgent")),
                "EngineeringAgent": DepartmentStatus(name="工程安防智能体", status="在线", active_tasks=count_agent("EngineeringAgent")),
                "ConciergeAgent": DepartmentStatus(name="礼宾接待智能体", status="在线", active_tasks=count_agent("ConciergeAgent")),
                "ManagerEscalationAgent": DepartmentStatus(
                    name="大堂经理危机响应",
                    status="在线" if count_agent("ManagerEscalationAgent") else "待命",
                    active_tasks=count_agent("ManagerEscalationAgent"),
                ),
            },
        )


@app.get("/api/tickets", tags=["Operations & PMS"])
def list_tickets(
    room_no: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
) -> dict[str, Any]:
    with session_manager.lock:
        tickets = list(session_manager.shared_tickets)
        if room_no:
            tickets = [ticket for ticket in tickets if ticket.get("room_no") == room_no]
        if status:
            tickets = [ticket for ticket in tickets if ticket.get("status") == status]
        if category:
            tickets = [ticket for ticket in tickets if ticket.get("category") == category]
        return {"total": len(tickets), "tickets": deepcopy(tickets)}


@app.post("/api/tickets/{ticket_id}/complete", tags=["Operations & PMS"])
def complete_ticket(ticket_id: str) -> dict[str, str]:
    with session_manager.lock:
        ticket = next((item for item in session_manager.shared_tickets if item.get("ticket_id") == ticket_id), None)
        if not ticket:
            raise HTTPException(status_code=404, detail="工单未找到")
        if ticket["status"] == "已取消":
            raise HTTPException(status_code=409, detail="已取消工单不能标记为完成")
        if ticket.get("inventory_item") in session_manager.housekeeping.inventory and ticket.get("reserved_quantity", 0) != ticket.get("quantity", 1):
            raise HTTPException(status_code=409, detail="库存尚未预留，不能确认送达")
        ticket["status"] = "已结单"
        if ticket.get("category") == "送物":
            ticket["delivery_status"] = "已送达"
        ticket["completed_at"] = datetime.now().isoformat(timespec="seconds")
        return {"status": "success", "ticket_id": ticket_id, "ticket_status": "已结单"}


@app.post("/api/tickets/{ticket_id}/dispatch", tags=["Operations & PMS"])
def dispatch_delivery(ticket_id: str) -> dict[str, Any]:
    try:
        return session_manager.dispatch_delivery(ticket_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="工单未找到") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/tickets/{ticket_id}/change/confirm", tags=["Operations & PMS"])
def confirm_delivery_change(ticket_id: str, confirmation: ChangeConfirmation) -> dict[str, Any]:
    try:
        return session_manager.confirm_delivery_change(ticket_id, confirmation.approved, confirmation.returned)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="工单未找到") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/pms/night-audit", tags=["Operations & PMS"])
def trigger_night_audit() -> dict[str, Any]:
    record = session_manager.night_audit()
    return {"status": "success", "current_date": record.pop("date"), **record}


@app.post("/api/harness/evolve", tags=["Harness Evolution"])
def trigger_evolution_cycle() -> dict[str, Any]:
    from harness.self_evolution import EvolutionDeltaRunner
    runner = EvolutionDeltaRunner()
    report = runner.run_evolution_cycle()
    return report
