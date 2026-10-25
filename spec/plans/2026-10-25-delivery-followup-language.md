# 口语送物与催送修复计划

> **For agentic workers:** Use superpowers:executing-plans for the business fix and dispatch the independent theme task with superpowers:dispatching-parallel-agents. Follow TDD and verify before completion.

**Goal:** 修复用户原句中的饮用水别名、时效修饰误识别、已有订单催送及夜间帮助提示框。

**Architecture:** 物品解析继续由确定性规则约束模型提案；催送定位当前入住会话的有效工单，经过既有差量提交与持久化，不创建新工单或重复扣库。Streamlit 原生帮助提示保留，单独处理弹层的背景和文本。

**Tech Stack:** Python / unittest / SQLite / Streamlit 1.66。

**Spec:** [服务可靠性 SR-06](../service_reliability.md)、[界面视觉 UI-07](../ui_visual_quality.md)。

## Global Constraints

- 未核验的泡面仍不是可配送库存；混合未知物品整次不派单、不扣库。
- 不更改入住/岗位授权，不影响取消、改物与在途确认机制。
- 离线测试强制规则和合成数据；真实模型验证单独记录消费次数与返回。
- 截图只保留于本地 `.artifacts/`，不上传仓库；不操作用户 PyCharm 服务或清空原会话。

## Review Focus

1. 连写的数量与物品：两瓶水两桶泡面只报告泡面，不能漏报或部分派水。
2. 快点、很饿、口渴和不用急不是商品；快点送红牛仍须识别未知红牛。
3. 水单与毛巾单并存时催水只关联水单；两张水单须澄清，不随意选最近单。
4. 热水、漏水、水管、洗发水不能归一成矿泉水；已取消/已结单不能重新激活。
5. 原生 help 弹层在护眼、夜间、日间实际计算对比度均不低于 4.5:1。

## Task 1 · 业务解析与催送（主代理）

Files: `agent_protocol.py`、`hotel_core.py`、`tests/test_delivery_followup.py`；必要时运行时集成测试。

- [x] 新测试先复现原句、别名边界、真实未知物品、重复催送、多个同 SKU 订单和重启恢复；记录 RED。
- [x] 最小修复物品边界和时效从句；增加只操作当前有效目标的催送记录，库存与订单数量保持不变，不降低原优先级。
- [x] 焦点 GREEN：192 方法通过；保留原始 127 项测试；映射新增 28 个真实测试 ID，合计 326。

## Task 2 · 帮助提示弹层（独立界面子代理）

Files: `app.py` 样式段、`tests/test_visual_contract.py`、`spec/ui_visual_quality.md`。

- [x] 查 installed Streamlit 主题文档和实际 DOM，增加三主题对比度回归并先观察失败。
- [x] 修复 tooltip 背景、正文和子元素前景，保留原生 help 和已有按钮对比度。
- [x] 返回修改清单、RED/GREEN 与实际选择器；主代理在浏览器悬浮核验并存本地截图。追加窄屏实际溢出反例，390px 复测 right=382。

## Task 3 · 集成与交付（主代理）

- [x] 有限真实模型原句验证：12 轮/12 次调用返回，记录工单和库存差量；独立复核三处 P2 反例后修复，确认关闭。
- [x] 源码稳定后运行统一验证入口：397 完整、43 核心、127 原始 ID、H20、21 黑板断言、326 映射全部通过；113 文件源码哈希稳定。
- [ ] 写公开证据和限制，提交代码/文档并核对远端 CI；截图保持本地。

裁定：沿用已授权的当前工作区与持续实现方式，不另建 checkout 或重复询问通用计划批准；只发布代码/文档，截图保持本地。首次完整运行之后发现手机提示框超出屏幕 18px，最小修复宽度后在新目录完整重跑，最终只以 `delivery-followup-20261025-final` 为交付证据。

原始证据与边界见 [本次验收记录](../delivery_followup_verification_2026-10-25.md)。
