# 住客首屏、夜间控件与公开身份实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首屏不被工具栏遮挡，湖景与对话布局适配视口，夜间库存控件可读，并让公开身份探询不绕过业务权限。

**Architecture:** 沿用 Streamlit 主题变量与真实容器；安全策略在业务入口和异步调度入口共同执行，API 使用面向住客的字段投影。模型只参与受约束提取和提案，权限和提交留在程序端。

**Tech Stack:** Python、Streamlit 1.66、FastAPI、unittest、浏览器 DOM 与截图复核。

**Spec:** [视觉规格](../ui_visual_quality.md)、[安全规格](../security_prompt_injection.md)。

## Global Constraints

- 保留全部原始业务测试；离线测试使用合成数据，不读取真实密钥、不调用云模型。
- 住客输入不能建立岗位、切换入住授权或提交未校验的工具结果。
- 正文与可操作控件文字对比度 ≥4.5:1；输入框保持首屏可见。
- 安全探询可以新增对话/审计记录，不得改变业务状态或取消合法在途订单。

## Review Focus

- 顶栏固定高度与 CSS padding 同时作用，真实 DOM 确认 banner/brand 不重叠顶栏。
- 夜间折叠面板的 summary、箭头与 radio 标签必须检查实际计算色，而非只搜源码。
- 带“取消毛巾”或其他房号的注入不能在调度阶段使合法任务失效。
- API 不能继续通过 model_name 字段把已隐藏的模型信息交给住客。
- 安全护栏之后正常送物、FAQ、取消与补槽仍可使用。

### Task 1: 视觉布局

**Files:** `app.py`、`tests/test_visual_contract.py`、`spec/ui_visual_quality.md`。

- [x] 写出湖景/主题控件契约测试并观察失败。
- [x] 修正顶栏安全间距与短视口样式；夜间展开控件采用配对前景/背景。
- [x] 浏览器实测空状态、活动态、夜间库存；保存 DOM 位置/颜色和截图。

### Task 2: 公开身份与业务安全

**Files:** `hotel_core.py`、`agent_runtime.py`、`server.py`、`tests/test_security.py`、`tests/test_deployment.py`。

**Interfaces:** `_security_boundary_kind(text: str) -> str | None` 为核心和调度层共用；住客 API 的 `model_name` 返回固定业务身份，员工仍可读取配置模型。

- [x] 写身份/泄露/注入副作用测试，观察失败并加入业务入口护栏。
- [x] 增加异步订单不中断和住客 API 字段反例，观察失败后修正调度与投影。
- [x] 扩展 40 条安全输入的业务快照断言，增加正常请求恢复测试。

### Task 3: 发布验证

**Files:** `spec/security_prompt_injection.md`、`spec/acceptance_criteria.md`、`AGENTS.md`、独立 `.artifacts/verification/`。

- [x] 核对规格与实现范围，公开身份不是自研底模声明；关键词检测不是完备注入检测。
- [x] 运行统一验证入口、扩展质量集与视觉回归，保存原始日志/结构化结果。
发布同步：沿用用户已授权的 master 更新；最终提交和 CI 结果以仓库运行记录为准，不由源文件内的勾选代替。

验收结果与审阅反例关闭证据见 [本次记录](../guest_ui_security_verification_2026-10-25.md)。
