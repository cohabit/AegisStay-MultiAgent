"""AegisStay FastAPI gateway with isolated conversation sessions."""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from hotel_core import HotelOrchestrator, HotelState, PMS_ROOMS


class SessionManager:
    """Own session-private blackboards while sharing the hotel data bus."""

    def __init__(self, ttl_seconds: int = 1800, kiosk_ttl_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self.kiosk_ttl_seconds = kiosk_ttl_seconds
        self.sessions: dict[str, HotelOrchestrator] = {}
        self.last_active: dict[str, float] = {}
        self.session_ttls: dict[str, int] = {}
        self.lock = threading.RLock()

        self.shared_pms = deepcopy(PMS_ROOMS)
        self.shared_tickets: list[dict[str, Any]] = []
        self.shared_checkout_requests: list[dict[str, Any]] = []
        self.shared_voucher_ledger: list[dict[str, Any]] = []

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            sid for sid, timestamp in self.last_active.items()
            if now - timestamp > self.session_ttls.get(sid, self.ttl_seconds)
        ]
        for sid in expired:
            self.sessions.pop(sid, None)
            self.last_active.pop(sid, None)
            self.session_ttls.pop(sid, None)

    def cleanup_expired(self) -> list[str]:
        with self.lock:
            before = set(self.sessions)
            self._cleanup_expired()
            return sorted(before - set(self.sessions))

    def get_or_create(
        self,
        session_id: str,
        force_rules: bool = False,
        client_type: str = "guest",
    ) -> HotelOrchestrator:
        with self.lock:
            self._cleanup_expired()
            if session_id not in self.sessions:
                state = HotelState(
                    pms_rooms=self.shared_pms,
                    tickets=self.shared_tickets,
                    checkout_requests=self.shared_checkout_requests,
                    voucher_ledger=self.shared_voucher_ledger,
                )
                self.sessions[session_id] = HotelOrchestrator(state=state, force_rules=force_rules)
                self.session_ttls[session_id] = (
                    self.kiosk_ttl_seconds if client_type.lower() == "kiosk" else self.ttl_seconds
                )
            self.last_active[session_id] = time.time()
            return self.sessions[session_id]

    def chat(
        self,
        session_id: str,
        message: str,
        room_no: str | None = None,
        force_rules: bool = False,
        client_type: str = "guest",
    ) -> HotelOrchestrator:
        """Process one turn atomically, preventing concurrent state interleaving."""
        with self.lock:
            flow = self.get_or_create(session_id, force_rules=force_rules, client_type=client_type)
            if force_rules:
                flow.force_rules = True
                flow.model.disabled = True
            else:
                flow.force_rules = False
                flow.model.disabled = False
            if room_no and not flow.state.room_no:
                flow._bind_room(room_no)
            flow.process(message)
            self.last_active[session_id] = time.time()
            return flow

    def get_session(self, session_id: str) -> Optional[HotelOrchestrator]:
        with self.lock:
            self._cleanup_expired()
            flow = self.sessions.get(session_id)
            if flow:
                self.last_active[session_id] = time.time()
            return flow

    def reset(self, session_id: str) -> bool:
        with self.lock:
            existed = session_id in self.sessions
            self.sessions.pop(session_id, None)
            self.last_active.pop(session_id, None)
            self.session_ttls.pop(session_id, None)
            return existed

    def reset_all(self) -> None:
        """Reset in-memory state; used by tests and controlled process resets."""
        with self.lock:
            self.sessions.clear()
            self.last_active.clear()
            self.session_ttls.clear()
            self.shared_pms.clear()
            self.shared_pms.update(deepcopy(PMS_ROOMS))
            self.shared_tickets.clear()
            self.shared_checkout_requests.clear()
            self.shared_voucher_ledger.clear()

    @property
    def active_session_count(self) -> int:
        with self.lock:
            self._cleanup_expired()
            return len(self.sessions)


session_manager = SessionManager()


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="唯一会话ID")
    message: str = Field(..., min_length=1, description="住客自然语言消息")
    room_no: Optional[str] = Field(None, description="可选的预填房间号")
    client_type: str = Field("guest", description="guest 或 kiosk；kiosk 空闲 30 秒自动销毁")
    force_rules: bool = Field(False, description="压测或验收时禁用云端模型")


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


@app.post("/api/chat", response_model=ChatResponse, tags=["Guest Chat"])
def guest_chat(req: ChatRequest) -> ChatResponse:
    flow = session_manager.chat(
        session_id=req.session_id,
        message=req.message,
        room_no=req.room_no,
        force_rules=req.force_rules,
        client_type=req.client_type,
    )
    state = flow.state
    profile = state.guest_profile or {}
    reply = (
        state.messages[-1]["content"]
        if state.messages and state.messages[-1].get("role") == "assistant"
        else "请告诉我您需要的服务。"
    )
    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
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
    )


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
    flow = session_manager.get_session(session_id)
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
        ticket["status"] = "已结单"
        ticket["completed_at"] = datetime.now().isoformat(timespec="seconds")
        return {"status": "success", "ticket_id": ticket_id, "ticket_status": "已结单"}
