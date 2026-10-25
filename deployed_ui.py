"""Role-based web views. No runtime, model keys, or SQLite connections here."""
from __future__ import annotations

import os
import html
import uuid
from hashlib import sha256
from functools import wraps
from datetime import date, timedelta

import streamlit as st

from api_client import ApiError, HotelApi
from ticket_details import delivery_fulfillment


def render_ticket_fulfillment(ticket: dict, inventory: dict) -> dict | None:
    """Render the shared staff projection without treating guest text as markup."""
    if ticket.get("category") != "送物":
        st.text(str(ticket.get("item_detail", "")))
        return None
    fulfillment = ticket.get("fulfillment")
    if not isinstance(fulfillment, dict):
        fulfillment = delivery_fulfillment(ticket, inventory)
    label, message = fulfillment["label"], fulfillment["message"]
    if fulfillment["needs_verification"]:
        st.warning(html.escape(f"待人工核验 · {message}"))
    elif fulfillment["needs_restock"]:
        st.warning(html.escape(f"{label}，未确认派送。{message}"))
    elif fulfillment["state"] == "awaiting_reservation":
        st.info(html.escape(f"{label} · {message}"))
    else:
        st.caption(html.escape(f"{label} · {message}"))
    st.markdown("**执行清单**")
    for item in fulfillment["items"]:
        unit = item["unit"]
        restock = "需补货：请协调补足库存缺口" if item["needs_restock"] else "无需补货"
        st.text(
            f"{item['name']} · 申请 {item['requested_quantity']} {unit}\n"
            f"已预留 {item['reserved_quantity']} {unit} · 当前可用 {item['available_quantity']} {unit}\n"
            f"缺口 {item['shortage_quantity']} {unit} · {restock}",
            width="stretch",
        )
    if not fulfillment["items"]:
        st.caption("执行清单尚不完整，请人工核验物品、单位与数量。")
    with st.expander("原始需求"):
        st.text(str(ticket.get("item_detail", "")), width="stretch")
    return fulfillment


def guest_history_page(fetch_page, *, scope: tuple, key: str) -> dict:
    """Accumulate the authorized archive without evicting records already read."""
    page = st.session_state.get(key)
    initial = page is None or page["scope"] != scope
    if initial:
        page = {"scope": scope, "messages": [], "latest": True, "has_older": False,
                "action": None, "scroll_revision": 0}
        st.session_state[key] = page
    page.setdefault("scroll_revision", 0)

    action = page["action"]
    existing = page["messages"]

    def ordered(records):
        return sorted({record["message_id"]: record for record in records}.values(),
                      key=lambda record: (record["sequence"], record["message_id"]))

    # The extra entry is a lookahead, never a replacement for the retained
    # timeline. All reads stay below the backend's unchanged limit of 100.
    if action == "older" and page["messages"]:
        before = existing[0]["sequence"]
        records = ordered(fetch_page(before=before, limit=61))
        incoming = records[-60:]
        page["has_older"] = len(records) > 60
        page["latest"] = False
    else:
        records = ordered(fetch_page(before=None, limit=61))
        incoming = records[-60:]
        if not existing:
            page["has_older"] = len(records) > 60
        # A tab can sleep through many replies. Bridge back to the newest
        # cached record rather than leave an invisible gap in the timeline.
        while existing and incoming and incoming[0]["sequence"] > existing[-1]["sequence"]:
            before = incoming[0]["sequence"]
            bridge = ordered(fetch_page(before=before, limit=61))
            bridge = [record for record in bridge if record["sequence"] < before][-60:]
            if not bridge:
                break
            incoming = bridge + incoming
        if action == "latest":
            page["latest"] = True

    page["messages"] = ordered(existing + incoming)
    if action == "latest":
        # Native autoscroll reacts to content changes. An explicit jump with
        # unchanged messages needs a new container key (and thus identity).
        # Only the viewport remounts; the retained timeline stays in this page.
        page["scroll_revision"] += 1
    # Keep the native observer enabled throughout latest mode. Disabling it
    # on the next fragment poll cancels a still-running native animation.
    # Streamlit's observer suspends following on manual wheel/pointer scroll;
    # explicit older-history mode additionally disables it until latest.
    page["autoscroll"] = page["latest"]
    page["action"] = None
    return page


def _navigate_guest_history(key: str, scope: tuple, action: str) -> None:
    page = st.session_state.get(key)
    if page is not None and page["scope"] == scope:
        page["action"] = action


def guest_history_controls(page: dict, *, key: str) -> None:
    """Keep navigation above the scrolling history so it remains discoverable."""
    with st.container(key=key + "-controls", horizontal=True, wrap=False,
                      vertical_alignment="center", gap="xsmall"):
        st.button("加载更早记录", key=key + "-older", disabled=not page["has_older"],
                  on_click=_navigate_guest_history, args=(key, page["scope"], "older"))
        st.button("回到最新消息", key=key + "-latest",
                  on_click=_navigate_guest_history, args=(key, page["scope"], "latest"))
        st.caption(f"{len(page['messages'])} 条", width="content", wrap=False,
                   help="已加载的对话条数；新回复自动更新，阅读历史时保持滚动位置。")


def show_api_error(function):
    @wraps(function)
    def safe(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ApiError as error:
            st.error(str(error))
    return safe


@st.fragment(run_every=2)
@show_api_error
def guest_services(api: HotelApi, person: dict) -> None:
    sid = person["session_id"]
    detail = api.request("GET", f"/api/session/{sid}")

    def fetch_history(*, before, limit):
        params = {"limit": limit}
        if before is not None:
            params["before"] = before
        return api.request("GET", f"/api/session/{sid}/messages", params=params)["messages"]

    history_key = "deployed-guest-history"
    history = guest_history_page(fetch_history, key=history_key,
                                 scope=(api.base_url, sid, person.get("stay_id"), person.get("grant_id"),
                                        sha256(api.headers.get("Authorization", "").encode()).hexdigest()))
    st.subheader("住客服务", icon=":material/room_service:")
    st.caption(f"房间 {person['room_no']} · 已核验本次入住 · 服务请求由前台与相应部门办理")
    chat, progress = st.columns([2.2, 1])
    with chat:
        guest_history_controls(history, key=history_key)
        panel_key = (f"deployed-history-panel-jump-{history['scroll_revision']}"
                     if history["scroll_revision"] else "deployed-history-panel")
        with st.container(height=380, border=True, key=panel_key, autoscroll=history["autoscroll"]):
            if not history["messages"]:
                st.write("您好，您可以咨询会务安排、申请送物或报修。")
            for message in history["messages"]:
                with st.chat_message(message["role"]):
                    st.write(message["content"])
        prompt = st.chat_input("例如：送两条毛巾；也可以补充、修改或取消", key="deployed-prompt", max_chars=4000)
        if prompt:
            # Keep the same ID if a network timeout makes acceptance uncertain.
            pending = st.session_state.get("pending_submission")
            if not pending or pending["message"] != prompt:
                pending = {"message": prompt, "request_id": uuid.uuid4().hex}
                st.session_state.pending_submission = pending
            api.request("POST", "/api/chat/submit", json={"session_id": sid, **pending})
            st.session_state.pop("pending_submission", None)
            _navigate_guest_history(history_key, history["scope"], "latest")
            st.rerun(scope="fragment")
    with progress:
        st.markdown("#### 我的服务进度")
        for item in detail["progress_items"]:
            st.info(item["label"])
        for task in detail["tasks"][-5:]:
            if task["status"] in {"queued", "running"}:
                st.caption("正在处理，可继续输入修改同一请求。")
            if task["status"] in {"failed", "interrupted"}:
                st.warning("有请求未确认完成，请查看服务进度后重试或联系前台。")
        if not detail["progress_items"]:
            st.caption("暂无进行中的服务")


@st.fragment(run_every=3)
@show_api_error
def operations(api: HotelApi, person: dict) -> None:
    payload = api.request("GET", "/api/operations/state")
    state = payload["state"]
    st.subheader("学术酒店运营台", icon=":material/dashboard:")
    role_name = {"admin": "管理员", "frontdesk": "前台", "housekeeping": "客房", "engineering": "工程", "manager": "经理", "local": "前台"}[person["role"]]
    st.caption(f"业务日期 {state['current_date']} · 当前岗位 {role_name} · 部门办理状态实时同步")
    rooms, tickets = state["pms_rooms"], state["tickets"]
    active = [t for t in tickets if t["status"] == "处理中"]
    cols = st.columns(4)
    cols[0].metric("客房", len(rooms), border=True)
    cols[1].metric("在住", sum(r.get("room_status") == "在住" for r in rooms.values()), border=True)
    cols[2].metric("待办工单", len(active), border=True)
    cols[3].metric("紧急待办", sum(t.get("risk_level") == "红色" for t in active), border=True)

    left, right = st.columns([1, 1.3])
    with left:
        st.markdown("#### 房态与接待")
        search = st.text_input("搜索房号", key="op-search")
        rows = [{"房号": r["room_no"], "房态": r.get("room_status"), "退房": r.get("checkout_time"),
                 "接待类别": r.get("member_level", "—")} for r in rooms.values() if search in r["room_no"]]
        st.dataframe(rows, hide_index=True, height=300, width="stretch")
        st.markdown("#### 物资")
        st.dataframe([{"物品": k, "可用": v, "回收待检": payload["quarantine"].get(k, 0)}
                      for k, v in payload["inventory"].items()], hide_index=True, height=200)
        if person["role"] in {"local", "admin", "manager", "frontdesk"}:
            with st.expander("夜审跨日"):
                confirmed = st.checkbox("已完成当日对账，确认推进业务日期", key="audit-confirm")
                if st.button("执行夜审", disabled=not confirmed):
                    api.request("POST", "/api/pms/night-audit", json={"expected_date": state["current_date"]})
                    st.rerun(scope="fragment")
    with right:
        st.markdown("#### 部门工单与变更")
        selection = st.selectbox("查看工单", ["待处理", "全部"], key="op-status")
        visible = active if selection == "待处理" else tickets
        for ticket in sorted(visible, key=lambda t: t.get("risk_level") != "红色")[:30]:
            tid = ticket["ticket_id"]
            with st.container(border=True, key="operation-ticket-" + tid, gap="xsmall"):
                st.markdown(f"**{html.escape(str(ticket['room_no']))} 房 · {html.escape(str(ticket['category']))}**")
                st.caption(html.escape(f"{ticket['assigned_dept']} · {ticket['status']} · {tid}"))
                fulfillment = render_ticket_fulfillment(ticket, payload["inventory"])
                change = ticket.get("change_request", {})
                if change.get("status") == "待部门确认":
                    action_text = "取消原服务" if change["action"] == "cancel" else f"更换为 {change.get('quantity', 1)} 件{change.get('item', '')}"
                    st.warning(html.escape(f"住客申请{action_text}，请核对实际派送情况。"))
                    delivered = ticket.get("delivery_status") == "已送达"
                    returned = st.checkbox("已确认取回（进入待检库存）", key="returned-" + tid) if delivered else False
                    yes, no = st.columns(2)
                    body = {"returned": returned, "expected_change_id": change["change_id"]}
                    if yes.button("确认撤回并变更", key="confirm-" + tid):
                        api.request("POST", f"/api/tickets/{tid}/change/confirm", json={"approved": True, **body})
                        st.rerun(scope="fragment")
                    if no.button("无法撤回", key="reject-" + tid):
                        api.request("POST", f"/api/tickets/{tid}/change/confirm", json={"approved": False, **body})
                        st.rerun(scope="fragment")
                else:
                    a, b = st.columns(2)
                    if fulfillment is not None and fulfillment["state"] != "in_transit" and ticket["status"] == "处理中":
                        if a.button("开始派送", key="dispatch-" + tid, disabled=not fulfillment["can_dispatch"]):
                            api.request("POST", f"/api/tickets/{tid}/dispatch")
                            st.rerun(scope="fragment")
                    if ticket["status"] == "处理中" and b.button("确认完成", key="complete-" + tid,
                                                              disabled=fulfillment is not None and not fulfillment["can_complete"]):
                        api.request("POST", f"/api/tickets/{tid}/complete")
                        st.rerun(scope="fragment")
        for req in state["checkout_requests"]:
            if req["status"] == "需前台审核":
                with st.container(border=True):
                    st.write(f"{req['room_no']} 房申请延退至 {req['requested_time']}")
                    st.caption(req["reason"])
                    yes, no = st.columns(2)
                    for col, approved, label in ((yes, True, "批准"), (no, False, "拒绝")):
                        if col.button(label, key=f"late-{req['request_id']}-{approved}"):
                            api.request("POST", f"/api/checkout/{req['room_no']}/review", json={"requested_time": req["requested_time"],
                                        "expected_request_id": req["request_id"], "approved": approved})
                            st.rerun(scope="fragment")
        for conflict in state["agent_conflicts"]:
            if conflict["status"] == "需人工核验":
                st.warning(conflict["reason"])
                if st.button("记录已核验（不自动建单）", key="conflict-" + conflict["conflict_id"]):
                    api.request("POST", f"/api/conflicts/{conflict['conflict_id']}/review")
                    st.rerun(scope="fragment")


def access_management(api: HotelApi, person: dict) -> None:
    st.subheader("住客访问凭证", icon=":material/key:")
    with st.expander("登记或更替本次学术接待入住"):
        st.caption("这里登记本系统的接待台账；外部 PMS 的办理结果需前台自行核验。存在未结工单时不能更替。")
        with st.form("register-stay"):
            room_no = st.text_input("房号", key="stay-room")
            alias = st.text_input("住客称呼（无需证件号）")
            departure = st.date_input("计划退房日期", value=date.today() + timedelta(days=1))
            reference = st.text_input("已核验的接待订单或接待函编号")
            replace = st.checkbox("已核对原入住，确认更替并吊销原访问凭证")
            verified = st.checkbox("已通过前台入住流程核验身份与订单")
            if st.form_submit_button("保存本次入住"):
                api.request("POST", f"/api/rooms/{room_no}/stay", json={"guest_alias": alias,
                            "checkout_date": departure.isoformat(), "order_reference": reference,
                            "replace_existing": replace, "identity_checked": verified})
                st.success("本系统接待记录已保存，可以签发该住客的访问凭证。")
    with st.form("issue-pass"):
        room = st.text_input("已核验的在住房号")
        ttl = st.number_input("有效小时", min_value=1, max_value=168, value=24)
        checked = st.checkbox("已通过前台入住记录核验身份")
        if st.form_submit_button("签发本次入住凭证"):
            record = api.request("POST", "/api/guest-access", json={"room_no": room, "ttl_seconds": int(ttl * 3600), "identity_checked": checked})
            st.session_state.issued_pass = record
    record = st.session_state.get("issued_pass")
    if record:
        st.caption("通过酒店受控渠道交给该住客；不要放入地址栏、公共群或截图。")
        if st.checkbox("显示刚签发的凭证", key="show-issued-pass"):
            st.code(record["access_token"])
        st.caption("会话标识：" + record["session_id"])
        if st.button("吊销刚签发的凭证"):
            api.request("POST", f"/api/guest-access/{record['grant_id']}/revoke")
            st.session_state.pop("issued_pass", None)
            st.rerun()
    if person["role"] in {"admin", "manager", "local"}:
        with st.expander("员工操作审计"):
            st.dataframe(api.request("GET", "/api/audit")["events"], hide_index=True)


def main() -> None:
    st.title("学术酒店服务运营中枢", anchor=False)
    st.caption("访学接待 · 学术会议住宿 · 客房服务 · 部门协作")
    st.session_state.setdefault("access_token", "")
    with st.sidebar:
        st.subheader("AegisStay")
        st.caption("前台核验入住后领取访问凭证，员工使用个人岗位凭据。")
        st.text_input("访问凭证", type="password", key="access_token")
        if st.button("退出当前访问"):
            st.session_state.clear()
            st.rerun()
    if not st.session_state.access_token:
        st.info("请在侧栏输入前台签发的入住访问凭证，或员工岗位凭据。")
        return
    api = HotelApi(os.environ["AEGIS_API_URL"], st.session_state.access_token)
    try:
        person = api.request("GET", "/api/me")
        if person["role"] == "guest":
            guest_services(api, person)
        elif person["role"] in {"housekeeping", "engineering"}:
            operations(api, person)
        else:
            op, access = st.tabs(["运营工作台", "住客凭证与审计"])
            with op:
                operations(api, person)
            with access:
                access_management(api, person)
    except ApiError as error:
        st.error(str(error))
