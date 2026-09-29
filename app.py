"""AegisStay: guest, staff and quality views over one shared Blackboard."""
from __future__ import annotations

import os
import streamlit as st

from hotel_core import HotelOrchestrator

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
            --as-card-border: #dce8e1;
            --as-text: #102a2a;
            --as-muted: #70817c;
            --as-tab-color: #243834;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --as-card-bg: #1c2128;
                --as-card-border: #30363d;
                --as-text: #e6edf3;
                --as-muted: #8b949e;
                --as-tab-color: #e6edf3;
            }
        }
        [data-testid="stHeader"] { background: transparent; }
        .hero { padding: 1.0rem 0 .5rem; }
        .eyebrow { color:#27866b; letter-spacing:.16em; font-size:.72rem; font-weight:700; }
        .muted { color: var(--as-muted); font-size:.85rem; }
        .room-card {
            background-color: var(--as-card-bg);
            border: 1px solid var(--as-card-border);
            color: var(--as-text);
            border-radius: 12px;
            padding: 10px 12px;
            margin: 4px 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .room-card-occ { border-left: 5px solid #27866b !important; }
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
        /* Fix Dark Mode Tabs invisibility */
        [data-testid="stTabs"] button {
            color: var(--as-tab-color) !important;
            font-weight: 600;
            font-size: 0.95rem;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            color: #27866b !important;
            border-bottom-color: #27866b !important;
        }
        </style>
        """, unsafe_allow_html=True,
    )


def _render_guest(orchestrator: HotelOrchestrator) -> None:
    state = orchestrator.state
    st.markdown('<div class="eyebrow">GUEST EXPERIENCE</div>', unsafe_allow_html=True)
    st.subheader("住客智能管家")
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

    chat = st.container(height=440)
    with chat:
        for message in state.messages:
            with st.chat_message("user" if message["role"] == "user" else "assistant"):
                st.write(message["content"])

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

    prompt = st.chat_input("例如：1128 能两点退房吗？")
    if prompt:
        orchestrator.process(prompt)
        st.rerun()  # Immediate rerun eliminates single-turn chat latency


def _render_staff(orchestrator: HotelOrchestrator) -> None:
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
    st.subheader("酒店运营管理中枢 (PMS 240房数字大板)")

    # KPI Metrics
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    occ_rate = occupied / total_rooms if total_rooms else 0.0
    m1.metric("总客房数 / 入住率", f"{total_rooms} 间", f"{occ_rate:.1%}")
    m2.metric("在住 (OCC)", occupied)
    m3.metric("空净房 (VC)", clean)
    m4.metric("待打扫 (VD)", dirty)
    m5.metric("维修中 (OOO)", repair)
    m6.metric("高危客诉", alerts, delta="需立即处理" if alerts else None, delta_color="inverse")

    st.markdown("#### 🤖 各部门智能体实时监控")
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
        st.markdown("#### 🏢 全酒店数字化房态矩阵 (10层仿真大板)")
        
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
                        orchestrator.review_checkout(request["room_no"], request["requested_time"], True)
                        st.rerun()
                    if y.button("❌ 拒绝延退", key=f"reject-{req_key}"):
                        orchestrator.review_checkout(request["room_no"], request["requested_time"], False)
                        st.rerun()
                elif r_status == "已批准":
                    c1, c2 = st.columns([3.2, 1.2])
                    with c1:
                        st.success(f"✅ [已批准] {request['room_no']} 房延迟至 {request['requested_time']}（退房时间已更新）")
                    with c2:
                        if st.button("↩️ 撤回", key=f"revoke-{req_key}"):
                            orchestrator.review_checkout(request["room_no"], request["requested_time"], None)
                            st.rerun()
                elif r_status == "未批准":
                    c1, c2 = st.columns([3.2, 1.2])
                    with c1:
                        st.error(f"❌ [已拒绝] {request['room_no']} 房延迟至 {request['requested_time']}（{request['reason']}）")
                    with c2:
                        if st.button("↩️ 撤回", key=f"revoke-{req_key}"):
                            orchestrator.review_checkout(request["room_no"], request["requested_time"], None)
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
            if ticket["status"] == "处理中" and st.button("完成结单", key=f"close-{ticket['ticket_id']}"):
                orchestrator.complete_ticket(ticket["ticket_id"])
                st.rerun()

    with st.expander("🔍 查看 Blackboard 与 Agent 交接追踪（运营内部）"):
        st.json({
            "active_agent": state.active_agent,
            "room_no": state.room_no,
            "intent": state.intent,
            "sentiment": state.sentiment,
            "model_mode": state.model_mode,
            "last_trace": state.last_trace,
        })


def _render_harness(orchestrator: HotelOrchestrator) -> None:
    st.markdown('<div class="eyebrow">QUALITY & EVALUATION</div>', unsafe_allow_html=True)
    st.subheader("Harness 自动化质检底座")

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


def main() -> None:
    st.set_page_config(page_title="AegisStay · Multi-Agent", page_icon="🏨", layout="wide", initial_sidebar_state="expanded")
    _style()
    if "orchestrator" not in st.session_state:
        st.session_state.orchestrator = HotelOrchestrator()
    orchestrator: HotelOrchestrator = st.session_state.orchestrator
    has_model = bool(orchestrator.model.client)
    mode_text = f"🟢 云端大模型实时驱动（{orchestrator.model.model}）" if has_model else "🟡 本地确定性规则保底（离线零成本模式）"

    st.markdown(
        f'<div class="hero"><div class="eyebrow">AEGISSTAY · BLACKBOARD ORCHESTRATION</div>'
        f'<h1>智慧酒店多智能体协同系统</h1>'
        f'<div class="muted">住客体验、PMS 240房运营与自动化质量验收，共享同一条可追踪业务链。'
        f'&nbsp;&nbsp;<span style="background: rgba(39, 134, 107, 0.15); color: #27866b; padding: 2px 8px; border-radius: 8px; font-weight: 600; font-size: 0.8rem;">{mode_text}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Sidebar Controls
    st.sidebar.title("AegisStay")
    st.sidebar.caption("企业级演示环境 · 240 间客房真实仿真")
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

    guest, staff, harness = st.tabs(["📱 住客智能管家端", "🖥️ 酒店运营与工单中枢", "🧪 Harness 自动化质检底座"])
    with guest:
        _render_guest(orchestrator)
    with staff:
        _render_staff(orchestrator)
    with harness:
        _render_harness(orchestrator)


if __name__ == "__main__":
    main()
