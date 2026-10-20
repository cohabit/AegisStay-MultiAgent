"""AegisStay: guest, staff and quality views over one shared Blackboard."""
from __future__ import annotations

import os
import streamlit as st

from hotel_core import HotelOrchestrator
from agent_runtime import RuntimeBusy, SessionManager

GUEST_SESSION = "streamlit-guest"

AGENT_BADGES = {
    "ReceptionAgent": ("🧭", "接待分流台"),
    "HousekeepingAgent": ("🧹", "客房保洁管家"),
    "EngineeringAgent": ("🔧", "工程安防工程师"),
    "ConciergeAgent": ("🛎️", "礼宾接待管家"),
    "ManagerEscalationAgent": ("👔", "大堂经理危机响应"),
}


def _style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --as-card-bg: #ffffff;
            --as-card-border: #d9e2ec;
            --as-text: #102a43;
            --as-muted: #627d98;
        }
        .hero { padding: .6rem 0 .35rem; }
        .eyebrow { color:#0f766e; letter-spacing:.14em; font-size:.7rem; font-weight:800; }
        .muted { color: var(--as-muted); font-size:.85rem; }
        .room-card {
            background-color: var(--as-card-bg);
            border: 1px solid var(--as-card-border);
            color: var(--as-text);
            border-radius: 12px;
            padding: 10px 12px;
            margin: 4px 0;
            box-shadow: 0 2px 7px rgba(16,42,67,0.06);
        }
        .room-card-occ { border-left: 5px solid #0f766e !important; }
        .room-card-clean { border-left: 5px solid #6b7280 !important; }
        .room-card-dirty { border-left: 5px solid #f59e0b !important; }
        .room-card-repair { border-left: 5px solid #ef4444 !important; }
        .ticket-card {
            background-color: var(--as-card-bg);
            border: 1px solid var(--as-card-border);
            color: var(--as-text);
            border-radius: 12px;
            padding: 12px;
            margin: 8px 0;
        }
        .danger {
            border-left: 5px solid #d94a4a !important;
            background-color: rgba(217, 74, 74, 0.12) !important;
        }
        .agent-badge, .agent-monitor-card {
            background-color: var(--as-card-bg);
            border: 1px solid var(--as-card-border);
            color: var(--as-text);
            border-radius: 12px;
            padding: 10px 12px;
        }
        .agent-badge { display: inline-block; margin: 4px 0 12px; font-weight: 700; }
        .agent-monitor-card { min-height: 76px; }
        .section-label { color:#486581; font-size:.78rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
        </style>
        """, unsafe_allow_html=True,
    )


def _runtime_for_ui(orchestrator: HotelOrchestrator) -> SessionManager:
    runtime = st.session_state.get("agent_runtime")
    if runtime is None or runtime.sessions.get(GUEST_SESSION) is not orchestrator:
        if runtime is not None:
            runtime.close()
        runtime = SessionManager()
        runtime.adopt(GUEST_SESSION, orchestrator)
        st.session_state.agent_runtime = runtime
    return runtime


def _staff_action(orchestrator: HotelOrchestrator, method: str, *args):
    runtime = _runtime_for_ui(orchestrator)
    with runtime.lock:
        result = getattr(orchestrator, method)(*args)
        runtime.persist(GUEST_SESSION)
        return result


@st.fragment(run_every=0.5)
def _render_guest(orchestrator: HotelOrchestrator) -> None:
    runtime = _runtime_for_ui(orchestrator)
    force_rules = orchestrator.force_rules
    orchestrator = runtime.snapshot(GUEST_SESSION)
    state = orchestrator.state
    st.markdown('<div class="eyebrow">GUEST SERVICES</div>', unsafe_allow_html=True)
    st.subheader("住客服务中心")
    has_model = bool(orchestrator.model.client)
    mode_tag = f"🟢 大脑：云端模型 ({orchestrator.model.model})" if has_model else "🟡 大脑：本地规则引擎 (离线保底)"
    if state.guest_profile:
        profile = state.guest_profile
        st.caption(f"已关联 {profile['guest_name']} · 房间 {profile['room_no']} · {profile['member_level']} | {mode_tag}")
    else:
        st.caption(f"告诉我房间号和需求，我会为你安排酒店服务。 | {mode_tag}")
    current_agent = state.active_agent.split(" · ", 1)[0]
    icon, label = AGENT_BADGES.get(current_agent, ("🧭", current_agent))
    st.markdown(f"<div class='agent-badge'>{icon} 当前对接：{label} · <code>{current_agent}</code></div>", unsafe_allow_html=True)

    chat = st.container(height=320)
    with chat:
        for message in state.messages:
            with st.chat_message("user" if message["role"] == "user" else "assistant"):
                st.write(message["content"])

    active_tasks = [task for task in state.tasks.values() if task["status"] in {"running", "queued"}]
    if active_tasks:
        st.info("正在处理您的请求，您可以继续输入补充、修改或取消。")
        if st.button("取消当前请求", key="cancel-current-task"):
            try:
                runtime.submit(GUEST_SESSION, "不用了", force_rules=force_rules)
                st.rerun()
            except RuntimeBusy as error:
                st.warning(str(error))

    st.markdown("#### 我的服务进度")
    progress_items = orchestrator.guest_progress_items()
    if not progress_items:
        st.info("暂无进行中的服务请求", icon="🛎️")
    else:
        for item in progress_items:
            if item["type"] == "success":
                st.success(item["label"], icon="✅")
            elif item["type"] == "error":
                st.error(item["label"], icon="❌")
            elif item["type"] == "warning":
                st.warning(item["label"], icon="⏳")
            else:
                st.info(item["label"], icon="🛎️")

    with st.expander("查看会话与工作状态", expanded=False):
        m_c1, m_c2, m_c3, m_c4 = st.columns(4)
        with m_c1:
            st.markdown("**1️⃣ 短期会话 (L1)**")
            st.caption("Sliding Window 轮次")
            st.write(f"- 消息数: `{len(state.messages)}` 条")
            st.write(f"- 滚动摘要: `{state.rolling_summary or '活跃窗口内'}`")
        with m_c2:
            st.markdown("**2️⃣ 工作黑板 (L2)**")
            st.caption("Blackboard 跨 Agent 共享")
            st.write(f"- 绑定房号: `{state.room_no or '待补全'}`")
            st.write(f"- 待办挂起: `{state.pending.get('intent') if state.pending else '无'}`")
            st.write(f"- 主控 Agent: `{state.active_agent}`")
        with m_c3:
            st.markdown("**3️⃣ 模拟住客档案**")
            st.caption("本地 PMS 档案与权益核准")
            prof = state.guest_profile or {}
            st.write(f"- 住客: `{prof.get('guest_name', '未绑定')}`")
            st.write(f"- 会员: `{prof.get('member_level', '普通')}`")
            st.write(f"- 房态: `{prof.get('room_status', '待查')}`")
        with m_c4:
            st.markdown("**4️⃣ 公共总线 (L4)**")
            st.caption("全店 PMS & 物资总线")
            st.write(f"- 系统日期: `{getattr(state, 'current_date', '2026-10-01')}`")
            st.write(f"- 全局工单: `{len(state.tickets)}` 张")
            st.write(f"- 借用物资: `{len(state.borrowed_items)}` 件")

    prompt = st.chat_input("例如：1128 送两条毛巾；也可以继续输入修改", key="guest-prompt")
    if prompt:
        try:
            runtime.submit(GUEST_SESSION, prompt, force_rules=force_rules)
            st.rerun()
        except (RuntimeBusy, ValueError) as error:
            st.warning(str(error))


@st.fragment(run_every=1.0)
def _render_staff(orchestrator: HotelOrchestrator) -> None:
    canonical = orchestrator
    runtime = _runtime_for_ui(canonical)
    orchestrator = runtime.snapshot(GUEST_SESSION)
    state = orchestrator.state
    tickets = state.tickets
    pms = state.pms_rooms
    total_rooms = len(pms)
    occupied = sum(r.get("room_status") == "在住" for r in pms.values())
    clean = sum(r.get("room_status") == "空净房" for r in pms.values())
    dirty = sum(r.get("room_status") == "待清洁" for r in pms.values())
    repair = sum(r.get("room_status") == "维修中" for r in pms.values())
    pending_tickets = sum(ticket["status"] == "处理中" for ticket in tickets)
    alerts = sum(ticket.get("risk_level") == "红色" and ticket["status"] == "处理中" for ticket in tickets)

    st.markdown('<div class="eyebrow">OPERATIONS CONTROL CENTER</div>', unsafe_allow_html=True)
    st.subheader("酒店运营管理中枢")

    cur_date = getattr(state, "current_date", "2026-10-01")
    date_col1, date_col2 = st.columns([3, 1.2])
    with date_col1:
        summary_txt = f"已压缩生成滚动摘要" if state.rolling_summary else "无需压缩"
        st.markdown(f"**📅 业务系统当前日期**：`{cur_date}` &nbsp;&nbsp;|&nbsp;&nbsp; **PMS 共享总线**：🟢 联机 &nbsp;&nbsp;|&nbsp;&nbsp; **分层记忆**：`{summary_txt}`")
    with date_col2:
        if st.button("🌙 执行夜审跨日流转 (Night Audit)", help="将系统推进至次日，自动流转到期未退客房并重置会员权益"):
            audit_result = runtime.night_audit()
            st.success(f"已完成夜审！日期流转至 {audit_result['date']}，处理 {audit_result['actions_count']} 条到期记录")
            st.rerun()

    # KPI Metrics
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    occ_rate = occupied / total_rooms if total_rooms else 0.0
    m1.metric("总客房数 / 入住率", f"{total_rooms} 间", f"{occ_rate:.1%}")
    m2.metric("在住 (OCC)", occupied)
    m3.metric("空净房 (VC)", clean)
    m4.metric("待打扫 (VD)", dirty)
    m5.metric("维修中 (OOO)", repair)
    m6.metric("高危客诉", alerts, delta="需立即处理" if alerts else None, delta_color="inverse")

    st.markdown("#### 各部门服务队列")
    monitor_cols = st.columns(4)
    for col, monitor in zip(monitor_cols, orchestrator.agent_monitoring()):
        status_icon = "🟢" if monitor["status"] == "处理中" else "⚪"
        with col:
            st.markdown(
                f"<div class='agent-monitor-card'><b>{monitor['icon']} {monitor['label']}</b><br>"
                f"<small>{status_icon} {monitor['status']} · 待处理 {monitor['active_tasks']} 单</small><br>"
                f"<small class='muted'><code>{monitor['name']}</code></small></div>",
                unsafe_allow_html=True,
            )

    left, right = st.columns([1.35, 1.0])

    with left:
        st.markdown("#### 全酒店房态矩阵")
        
        # Filter controls
        f_col1, f_col2, f_col3 = st.columns([1.2, 1.2, 1.6])
        with f_col1:
            floor_opts = ["全部楼层", "8F", "9F", "11F VIP"] + [f"{f}F" for f in range(1, 11) if f not in (8, 9)]
            sel_floor = st.selectbox("楼层筛选", floor_opts, index=0)
        with f_col2:
            status_opts = ["全部状态", "在住", "空净房", "待清洁", "维修中"]
            sel_status = st.selectbox("房态筛选", status_opts, index=0)
        with f_col3:
            kw = st.text_input("快速搜索房号/住客", placeholder="如 1128 或 张先生")

        # Filter logic
        filtered_rooms = list(pms.values())
        if sel_floor != "全部楼层":
            floor_num = sel_floor.replace("F VIP", "").replace("F", "")
            filtered_rooms = [r for r in filtered_rooms if str(r.get("floor", "")) == floor_num]
        if sel_status != "全部状态":
            filtered_rooms = [r for r in filtered_rooms if r.get("room_status") == sel_status]
        if kw.strip():
            target = kw.strip()
            filtered_rooms = [r for r in filtered_rooms if target in r["room_no"] or target in r.get("guest_name", "")]

        st.caption(f"共筛选出 {len(filtered_rooms)} 间客房（点击可查看卡片）")

        # Render the complete filtered inventory as a responsive room matrix.
        # Floor and status filters keep the 240-card board easy to scan.
        grid_cols = st.columns(6)
        status_class_map = {
            "在住": "room-card room-card-occ",
            "空净房": "room-card room-card-clean",
            "待清洁": "room-card room-card-dirty",
            "维修中": "room-card room-card-repair",
        }
        for idx, room in enumerate(filtered_rooms):
            col = grid_cols[idx % 6]
            cls = status_class_map.get(room.get("room_status", "在住"), "room-card")
            with col:
                st.markdown(
                    f"<div class='{cls}'><b>{room['room_no']} · {room.get('room_type', '客房')}</b><br>"
                    f"<small>{room.get('guest_name', '—')} · {room.get('member_level', '—')}</small><br>"
                    f"<small class='muted'>状态: {room.get('room_status')} | 退房: {room.get('checkout_time', '12:00')}</small></div>",
                    unsafe_allow_html=True,
                )

    with right:
        # Late checkout approval workflow
        if state.checkout_requests:
            st.markdown("#### ⏳ 延迟退房审批流 (人机协同)")
            for request in state.checkout_requests:
                r_status = request["status"]
                req_key = f"{request['room_no']}-{request['requested_time']}"
                if r_status == "需前台审核":
                    st.warning(f"⚠️ [待审核] {request['room_no']} 房申请延迟至 {request['requested_time']}：{request['reason']}")
                    x, y = st.columns(2)
                    if x.button("✅ 批准延退", key=f"approve-{req_key}"):
                        _staff_action(canonical, "review_checkout", request["room_no"], request["requested_time"], True)
                        st.rerun()
                    if y.button("❌ 拒绝延退", key=f"reject-{req_key}"):
                        _staff_action(canonical, "review_checkout", request["room_no"], request["requested_time"], False)
                        st.rerun()
                elif r_status == "已批准":
                    c1, c2 = st.columns([3.2, 1.2])
                    with c1:
                        st.success(f"✅ [已批准] {request['room_no']} 房延迟至 {request['requested_time']}（退房时间已更新）")
                    with c2:
                        if st.button("↩️ 撤回", key=f"revoke-{req_key}"):
                            _staff_action(canonical, "review_checkout", request["room_no"], request["requested_time"], None)
                            st.rerun()
                elif r_status == "未批准":
                    c1, c2 = st.columns([3.2, 1.2])
                    with c1:
                        st.error(f"❌ [已拒绝] {request['room_no']} 房延迟至 {request['requested_time']}（{request['reason']}）")
                    with c2:
                        if st.button("↩️ 撤回", key=f"revoke-{req_key}"):
                            _staff_action(canonical, "review_checkout", request["room_no"], request["requested_time"], None)
                            st.rerun()

        # Work orders list
        st.markdown(f"#### 🛠️ 实时工单流水 ({pending_tickets} 单处理中)")
        if not tickets:
            st.info("暂无工单，等待住客请求。")
        for ticket in orchestrator.sorted_tickets():
            danger = ticket.get("risk_level") == "红色"
            card_class = "ticket-card danger" if danger else "ticket-card"
            st.markdown(
                f"<div class='{card_class}'><b>{'🚨 ' if danger else ''}{ticket['category']} · {ticket['room_no']} 房</b><br>"
                f"{ticket['item_detail']}<br><span class='muted'>{ticket['assigned_dept']} · 优先级 {ticket['priority']} · {ticket['status']} · {ticket['ticket_id']}</span><br>"
                f"<small class='muted'>接管 Agent：{ticket.get('agent', 'DispatchAgent')}</small></div>",
                unsafe_allow_html=True,
            )
            if ticket.get("category") == "送物":
                st.caption(f"派送进度：{ticket.get('delivery_status', '待派送')}")
                change = ticket.get("change_request", {})
                if change.get("status") == "待部门确认":
                    st.warning(f"住客申请{ '取消' if change['action'] == 'cancel' else '更换为 ' + str(change.get('quantity', 1)) + ' 件' + str(change.get('item', '')) }，请核实实际派送情况。")
                    delivered = ticket.get("delivery_status") == "已送达" or ticket["status"] == "已结单"
                    returned = st.checkbox("已确认取回原物品", key=f"returned-{ticket['ticket_id']}") if delivered else False
                    approve, reject = st.columns(2)
                    if approve.button("确认撤回并处理变更", key=f"confirm-change-{ticket['ticket_id']}"):
                        try:
                            runtime.confirm_delivery_change(ticket["ticket_id"], approved=True, returned=returned)
                            st.rerun()
                        except ValueError as error:
                            st.warning(str(error))
                    if reject.button("确认无法撤回", key=f"reject-change-{ticket['ticket_id']}"):
                        runtime.confirm_delivery_change(ticket["ticket_id"], approved=False)
                        st.rerun()
                elif ticket["status"] == "处理中" and ticket.get("delivery_status") == "待派送":
                    if st.button("确认开始派送", key=f"dispatch-{ticket['ticket_id']}"):
                        try:
                            runtime.dispatch_delivery(ticket["ticket_id"])
                            st.rerun()
                        except ValueError as error:
                            st.warning(str(error))
            stock_missing = ticket.get("inventory_item") in orchestrator.housekeeping.inventory and ticket.get("reserved_quantity", 0) != ticket.get("quantity", 1)
            if ticket["status"] == "处理中" and st.button("完成结单", key=f"close-{ticket['ticket_id']}", disabled=stock_missing):
                try:
                    _staff_action(canonical, "complete_ticket", ticket["ticket_id"])
                    st.rerun()
                except ValueError as error:
                    st.warning(str(error))

        pending_conflicts = [c for c in state.agent_conflicts if c["status"] == "需人工核验"]
        if pending_conflicts:
            st.markdown("#### 需核验的部门结果")
        for conflict in pending_conflicts:
            st.warning(f"{conflict.get('room_no') or '未绑定'} 房 · {conflict['intent']}：{conflict['reason']}。此次操作暂未派单。")
            if st.button("标记已核验（不自动派单）", key=f"resolve-{conflict['conflict_id']}"):
                _staff_action(canonical, "resolve_agent_conflict", conflict["conflict_id"])
                st.rerun()

        # Live Inventory & Queue ETA Monitor
        active_deliveries = sum(t.get("category") == "送物" and t.get("status") == "处理中" for t in tickets)
        current_eta = 5 if active_deliveries <= 1 else min(10, 8 + active_deliveries - 2)
        st.markdown("#### 📦 物料库存与实时派单队列 (Atomic Inventory & ETA)")
        inv_c1, inv_c2 = st.columns(2)
        inv_c1.metric("送物队列排队深度", f"{active_deliveries} 单待送")
        inv_c2.metric("当前动态推导 ETA", f"{current_eta} 分钟", delta="根据拥堵自适应推导")
        with st.expander("查看保洁部物资实时库存 (支持原子扣减防超卖)"):
            st.write(orchestrator.housekeeping.inventory)
            if state.borrowed_items:
                st.markdown("**借出在房借还物资：**")
                st.dataframe(state.borrowed_items, hide_index=True)

    with st.expander("🔍 查看 Blackboard 与 Agent 交接追踪（运营内部）"):
        st.json({
            "active_agent": state.active_agent,
            "room_no": state.room_no,
            "intent": state.intent,
            "sentiment": state.sentiment,
            "model_mode": state.model_mode,
            "last_trace": state.last_trace,
            "tasks": list(state.tasks.values()),
            "agent_conflicts": state.agent_conflicts,
        })


def _render_harness(orchestrator: HotelOrchestrator) -> None:
    st.markdown('<div class="eyebrow">QUALITY & EVALUATION</div>', unsafe_allow_html=True)
    st.subheader("质量验收与回归")

    has_model = bool(orchestrator.model.client)
    col_opt, col_status = st.columns([1.5, 1.0])
    with col_opt:
        eval_engine = st.radio(
            "选择评测质检引擎",
            ["⚡ 离线规则快速回归 (毫秒级 · 0成本 · CI/CD基线)", "🤖 云端大模型真实质检 (实时调用当前配置 API · 检验模型泛化与 Tool Calling)"],
            index=1 if has_model else 0,
            horizontal=False,
        )
    use_cloud = "云端大模型" in eval_engine
    with col_status:
        if use_cloud:
            if has_model:
                st.success(f"🟢 **云端评测就绪**\n\n目标模型：`{orchestrator.model.model}`\n\n每例真实调用云端 API，统计大模型端到端准确率。", icon="🤖")
            else:
                st.warning("⚠️ **未连接云端大模型**\n\n请先在左侧侧边栏填入 API Key 并保存，或选择离线规则回归模式。", icon="🔑")
        else:
            st.info("🛡️ **离线零成本评测**\n\n使用本地确定性规则引擎与本地知识库，毫秒级快速回归，无需消耗 Token。", icon="⚡")

    if "harness_report" not in st.session_state:
        st.session_state.harness_report = None

    btn_disabled = use_cloud and not has_model
    btn_text = f"▶ 运行全量 20 组回归测试 ({'云端大模型' if use_cloud else '离线规则'})"
    if st.button(btn_text, type="primary", disabled=btn_disabled):
        from harness.eval_runner import run_evaluation
        bar = st.progress(0, text="准备运行 Harness 质检…")

        def progress(done: int, total: int, row: dict) -> None:
            bar.progress(done / total, text=f"正在评测 {done}/{total} · {row['id']} · {row['name']} → {row['status']} ({row['response_ms']} ms)")

        st.session_state.harness_report = run_evaluation(progress_callback=progress, use_cloud_model=use_cloud)
        bar.empty()
        st.rerun()

    report = st.session_state.harness_report
    if not report:
        st.info("选择质检模式后，点击上方按钮开始验收。")
        return

    st.markdown(
        f"<div style='display:flex; justify-content:space-between; align-items:center; margin: 10px 0;'>"
        f"<span><b>评测模式</b>：<code>{report.get('eval_mode', '')}</code> · <b>评测对象</b>：<code>{report.get('model_name', '')}</code></span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    a, b, c, d = st.columns(4)
    a.metric("通过用例", f"{report['passed']}/{report['total']}")
    b.metric("意图准确率", f"{report['intent_accuracy']:.1%}")
    c.metric("槽位召回率", f"{report['slot_filling_rate']:.1%}")
    d.metric("任务完成率", f"{report['ticket_recall']:.1%}")
    st.caption(f"派单精准度 {report['ticket_precision']:.1%} · 平均单例耗时 {report['average_response_ms']:.2f} ms")
    st.dataframe(report["rows"], width="stretch", hide_index=True)


def _render_evolution(orchestrator: HotelOrchestrator) -> None:
    st.markdown('<div class="eyebrow">EXAMPLES & REGRESSION</div>', unsafe_allow_html=True)
    st.subheader("难例回归与 Few-Shot 示例库")
    st.caption("运行已有基线与难例，查看示例库。当前未接通线上错误自动收集、自动诊断或严格的效果前后对照。")

    if "evolution_report" not in st.session_state:
        st.session_state.evolution_report = None

    col_btn, col_info = st.columns([1.2, 2])
    with col_btn:
        if st.button("运行基线与难例回归", type="primary"):
            from harness.self_evolution import EvolutionDeltaRunner
            with st.spinner("正在读取难例并执行离线回归…"):
                runner = EvolutionDeltaRunner()
                st.session_state.evolution_report = runner.run_evolution_cycle()
            st.success("基线与难例评测完成！")
            st.rerun()
    with col_info:
        st.info("Few-shot 在调用时提供参考示例，不修改模型参数。当前从已有 JSON 示例库按字符相似度选择最多两条；线上错误收集、审核与发布仍需实现。")

    evo = st.session_state.evolution_report
    if not evo:
        from harness.self_evolution import HardCaseMiner, DynamicExemplarCurator
        miner = HardCaseMiner()
        curator = DynamicExemplarCurator()
        cases = miner.load_cases()
        exemplars = curator.load_exemplars()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("基准用例", "20 组（待运行）")
        m2.metric("当前已收录难例", f"{len(cases)} 组")
        m3.metric("提炼 Few-Shot 样本", f"{len(exemplars)} 条")
        m4.metric("示例读取方式", "运行时按需读取")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("基线用例回归", evo.get("baseline_pass_rate", "100.0%"))
        m2.metric("难例通过率", f"{evo.get('hard_cases_passed')}/{evo.get('hard_cases_total')} ({evo.get('hard_cases_accuracy')})")
        m3.metric("提炼 Few-Shot 样本", f"{evo.get('curated_exemplars_count')} 条")
        m4.metric("效果前后对照", "尚未实测")

        st.markdown("#### 🔬 难例库诊断与根因反思清单 (Diagnostic Reflection)")
        st.dataframe(evo.get("diagnostics", []), width="stretch", hide_index=True)

    with st.expander("📚 查看当前已提炼并动态热注入的 Few-Shot 黄金样例库"):
        import json
        from harness.self_evolution import DynamicExemplarCurator
        exemplars = DynamicExemplarCurator().load_exemplars()
        for idx, ex in enumerate(exemplars, 1):
            st.markdown(f"**【黄金示例 {idx}】**")
            st.code(f"住客表达: {ex.get('user')}\n思考逻辑: {ex.get('assistant_thought')}\n输出JSON: {json.dumps(ex.get('json_output', {}), ensure_ascii=False)}")


def main() -> None:
    st.set_page_config(page_title="AegisStay · Hotel Operations", page_icon=":material/hotel:", layout="wide", initial_sidebar_state="collapsed")
    _style()
    if "orchestrator" not in st.session_state:
        st.session_state.orchestrator = HotelOrchestrator()
    orchestrator: HotelOrchestrator = st.session_state.orchestrator
    has_model = bool(orchestrator.model.client)
    mode_text = f"🟢 云端大模型实时驱动（{orchestrator.model.model}）" if has_model else "🟡 本地确定性规则保底（离线零成本模式）"

    st.markdown(
        f'<div class="hero"><div class="eyebrow">AEGISSTAY · HOTEL OPERATIONS</div>'
        f'<h1>酒店服务运营中枢</h1>'
        f'<div class="muted">住客服务、工单流转与房态管理，共享一条可追踪的业务链。'
        f'&nbsp;&nbsp;<span style="background: #e6fffa; color: #0f766e; padding: 3px 9px; border-radius: 999px; font-weight: 700; font-size: 0.78rem;">{mode_text}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Sidebar Controls
    st.sidebar.title("AegisStay")
    st.sidebar.caption("单酒店运营工作台 · 240 间客房")
    if has_model:
        st.sidebar.success(f"🟢 **云端大模型在线**\n\n`{orchestrator.model.model}`", icon="⚡")
    else:
        st.sidebar.info("🟡 **离线规则引擎保底**\n\n`确定性快速模式`", icon="🛡️")

    with st.sidebar.expander("⚙️ 大模型连接配置 (支持云端 / 规则)", expanded=not has_model):
        provider = st.selectbox(
            "选择服务商",
            ["离线规则引擎 (零成本 · 当前)", "SiliconFlow (硅基流动)", "DeepSeek 官方", "OpenAI 官方", "本地 Ollama / 自定义"],
        )
        if provider == "离线规则引擎 (零成本 · 当前)":
            st.info("当前由本地确定性规则引擎与本地知识库驱动，0 成本、毫秒级响应。")
            if st.button("恢复纯规则模式"):
                os.environ.pop("OPENAI_API_KEY", None)
                os.environ.pop("SILICONFLOW_API_KEY", None)
                st.session_state.orchestrator = HotelOrchestrator(force_rules=True)
                st.rerun()
        else:
            default_base = {
                "SiliconFlow (硅基流动)": "https://api.siliconflow.cn/v1",
                "DeepSeek 官方": "https://api.deepseek.com/v1",
                "OpenAI 官方": "https://api.openai.com/v1",
                "本地 Ollama / 自定义": "http://localhost:11434/v1",
            }.get(provider, "")
            default_model = {
                "SiliconFlow (硅基流动)": "Qwen/Qwen2.5-7B-Instruct",
                "DeepSeek 官方": "deepseek-chat",
                "OpenAI 官方": "gpt-4o-mini",
                "本地 Ollama / 自定义": "qwen2.5:7b",
            }.get(provider, "")

            key_input = st.text_input("API Key", type="password", value=os.getenv("OPENAI_API_KEY") or os.getenv("SILICONFLOW_API_KEY") or "")
            base_input = st.text_input("Base URL", value=os.getenv("OPENAI_BASE_URL") or default_base)
            model_input = st.text_input("模型名称", value=os.getenv("OPENAI_MODEL") or default_model)

            if st.button("保存并连接云端大模型", type="primary"):
                if key_input.strip():
                    os.environ["OPENAI_API_KEY"] = key_input.strip()
                    os.environ["OPENAI_BASE_URL"] = base_input.strip()
                    os.environ["OPENAI_MODEL"] = model_input.strip()
                    st.session_state.orchestrator = HotelOrchestrator()
                    st.success("已成功加载大模型配置！")
                    st.rerun()
                else:
                    st.warning("请填入有效的 API Key")

    if st.sidebar.button("🔄 清空会话与刷新系统"):
        st.session_state.orchestrator = HotelOrchestrator()
        st.session_state.harness_report = None
        st.rerun()

    with st.sidebar.expander("系统能力与运行状态", expanded=False):
        st.markdown("""
- 🛡️ **接待理解与业务校验**
  可选模型辅助理解，业务写入由程序规则核验
- 🧠 **会话与工作黑板**
  滑动消息窗口 + 工作状态 + 持久化 PMS
- ⚡ **后台任务与版本校验**
  支持处理中继续输入、取消、改单及旧结果失效
- ⚖️ **部门结果校验**
  按最新需求、库存与安全规则核验，冲突转人工确认
- 🧬 **Harness 与 Few-Shot 示例库**
  离线回归 + 预置难例 + 运行时示例检索
- 🌙 **PMS 跨日夜审 (Night Audit)**
  跨日到期房态流转 + 次日预订保洁冲突预检
""")

    guest, staff, harness, evolution = st.tabs([
        "住客服务",
        "运营中枢",
        "质量验收",
        "难例回归",
    ])
    with guest:
        _render_guest(orchestrator)
    with staff:
        _render_staff(orchestrator)
    with harness:
        _render_harness(orchestrator)
    with evolution:
        _render_evolution(orchestrator)


if __name__ == "__main__":
    main()
