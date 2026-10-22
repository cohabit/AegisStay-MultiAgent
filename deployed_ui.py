"""Role-based web views. No runtime, model keys, or SQLite connections here."""
from __future__ import annotations

import os
import uuid
from functools import wraps
from datetime import date, timedelta

import streamlit as st

from api_client import ApiError, HotelApi


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
    st.subheader("住客服务", icon=":material/room_service:")
    st.caption(f"房间 {person['room_no']} · 已核验本次入住 · 服务请求由前台与相应部门办理")
    chat, progress = st.columns([2.2, 1])
    with chat:
        with st.container(height=380, border=True):
            if not detail["messages"]:
                st.write("您好，您可以咨询会务安排、申请送物或报修。")
            for message in detail["messages"]:
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
        with st.expander("查看历史记录"):
            history = api.request("GET", f"/api/session/{sid}/messages", params={"limit": 100})
            for message in history["messages"]:
                st.caption("住客" if message["role"] == "user" else "酒店")
                st.write(message["content"])
            st.caption("显示最近 100 条；接口支持分页查询更早记录。")


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
            with st.container(border=True):
                st.markdown(f"**{ticket['room_no']} 房 · {ticket['category']}**")
                st.write(ticket["item_detail"])
                st.caption(f"{ticket['assigned_dept']} · {ticket['status']} · {ticket.get('delivery_status', '')}")
                change = ticket.get("change_request", {})
                if change.get("status") == "待部门确认":
                    action_text = "取消原服务" if change["action"] == "cancel" else f"更换为 {change.get('quantity', 1)} 件{change.get('item', '')}"
                    st.warning(f"住客申请{action_text}，请核对实际派送情况。")
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
                    if ticket.get("delivery_status") == "待派送" and ticket["status"] == "处理中":
                        if a.button("开始派送", key="dispatch-" + tid):
                            api.request("POST", f"/api/tickets/{tid}/dispatch")
                            st.rerun(scope="fragment")
                    if ticket["status"] == "处理中" and b.button("确认完成", key="complete-" + tid):
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
