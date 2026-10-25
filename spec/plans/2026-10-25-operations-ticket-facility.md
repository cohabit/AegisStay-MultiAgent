# 运营工单清单与设施问项实施计划

> **For agentic workers:** 使用 Superpowers 分域委派，先 RED 后 GREEN；子代理不再派代理，不提交或推送。已有用户授权持续完善并发布，无需再次选择执行方式。

**Goal:** 员工明确知道送什么、送多少及缺货处置，住客获得逐项、准确的设施答复。

**Architecture:** `ticket_details.py` 提供只读库存履约投影，API 和两个工作台共用；运行时守卫执行同一有效性边界。设施问答从批准知识提取问项，不由模型填补缺失事实。

**Tech Stack:** Python 3.12、unittest、Streamlit 1.66、FastAPI、SQLite。

**Spec:** [工单与设施规格](../operations_ticket_and_facility.md)。

## 约束与复核重点

保留原 127 测试 ID、固定 H01…H20；离线测试强制规则、合成数据和临时库。不清除用户原会话或停止 PyCharm8501。所有时间仅按当前 profile 的知识源；所有员工 HTML 转义；未预留和待变更确认不可派送/完成；已预留不二次扣库；历史错误单不补造明细；咨询与业务动作不能互相吞掉。

## 分工与步骤

### 1. 设施问答

所有权：`hotel_core.py` 的知识/FAQ/目录问答与合成部分、新 `facility_answers.py`（如有必要）、`data/hotel_knowledge.json` / `data/academic_hotel_knowledge.json` 的结构化问项（不新增学校事实）、`tests/test_facility_intents.py`。不改 Housekeeping 和结单函数，留待集成。

- [x] 至少 48 条编号表驱动输入先 RED，断言问项值、各主题完整性、无工单/扣库和混合动作保留。
- [x] 按问项读取可信知识，纯关门问题直接返回结束时间，未知值明确待核验；同句足浴/按摩/健身房分别答复。
- [x] 焦点 GREEN、报告真实方法与子用例数量和日志；冻结文件供集成。

### 2. 运营卡片

所有权：`app.py`、`deployed_ui.py`、`tests/test_operations_ticket_ui.py`。读取集成方提供的纯函数；不改业务/配置、不启动用户服务。

接口：`from ticket_details import delivery_fulfillment`；输入工单和当前可用库存，返回 None（非送物）或字典：`state`、`label`、`items`（name/unit/requested_quantity/reserved_quantity/available_quantity/shortage_quantity/needs_restock）、`needs_restock`、`can_dispatch`、`can_complete`、`needs_verification`、`message`。API 工单可自带同名 `fulfillment` 字段；无字段时调用纯函数。不要自行解析住客原文形成执行清单。

- [x] 两端 AppTest 先 RED：水2瓶、吹风机7个/可用6/缺1、库存回升未预留、旧混合错误、取消和正常工程单。
- [x] 用原生卡片/紧凑表格展示明细和库存协调提示，原话折叠或次要显示；禁用无法派送/完成的按钮；转义动态内容。
- [x] 两端焦点 GREEN，保持原有 UI 历史/主题行为；交付日志与屏幕元素断言。

### 3. 库存投影、后端与集成（主代理）

所有权：`ticket_details.py`、`agent_runtime.py`、`server.py`、`tests/test_ticket_fulfillment.py`；设施任务冻结后最小集成 `hotel_core.py` 的新缺货状态、改单重置、结单守卫。

- [x] 纯函数、API、缺货/历史错误单阻断、持久化重启先 RED。
- [x] 实现与卡片完全同源的投影，员工接口附加字段，住客接口不泄露；新缺货状态持久化；原交易约束保持。
- [x] 焦点 GREEN，更新 REQ 映射和规格链接；一次有界独立差量复核。
- [x] 文件稳定后完整发布验证，再用隔离实例检查两断点实际卡片和真实提供商有限合成联调；保留新证据目录。
- [x] 文档记录已知边界与实际证据；发布提交和最终 CI 以交付消息中的 GitHub 链接为准，成功前不宣称通过。

## 执行记录

设施60方法、两端运营UI21方法、履约及后端21方法已实现；最终完整验证369/369、核心43/43、H01…H20、黑板21断言、映射298项全过。真实提供商12轮/7次调用通过。详细来源、先红后绿、旧用例契约调整和截图见 [验收记录](../operations_facility_verification_2026-10-25.md)。独立只读复核未发现当时差量的P1/P2；后续目标选择与颜色修复的边界在验收记录中单列。
