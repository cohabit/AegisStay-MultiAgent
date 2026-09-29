# AegisStay 多智能体系统开发者指南 (agents.md)

## 1. 项目核心目标 (Project Goal)

**一句话目标**：
基于**黑板架构（Blackboard Architecture）**构建企业级智慧酒店多智能体协同与工单闭环系统，实现住客端多轮复合需求处理、酒店运营端（PMS）工单流转与人工审批、以及质检底座（Harness）的无模型纯离线自动化回归评测。

---

## 2. 仓库全景结构 (Repository Structure)

```text
E:\Codex\LLM\AegisStay-MultiAgent\
├── agents.md                      # [本项目规范] Agent系统设计概况、仓库全景与核心实现约束
├── README.md                      # 项目快速入门与业务背景说明
├── requirements.txt               # 运行依赖（streamlit, openai 等）
├── app.py                         # 前端统一界面：三端一体（住客管家、运营中枢、Harness 评测台）
├── hotel_core.py                  # 后端核心业务状态机与 Agent 调度引擎（纯 Python，解耦 UI）
├── spec/                          # 规格与验收标准文档目录
│   ├── specification.md           # [核心规格] 架构设计、复合意图分解、PMS判定、工单派发规则与交付物
│   ├── specialized_agents_and_session_spec.md # [拓展规格] 垂直部门智能体集群 (保洁/工程/礼宾/经理) 与 FastAPI 会话隔离
│   ├── acceptance_criteria.md     # [验收标准] 量化指标、用例矩阵与 AI 自动化测试证据标准
│   ├── hotel_simulation_spec.md   # [场景模拟] 10层240间真实酒店房态数据建模与数字化大板规划
│   ├── ui_and_interaction_spec.md # [界面规范] Streamlit生命周期刷新、黑夜模式自适应与大模型侧边栏接驳
│   └── security_and_fraud_spec.md # [安全风控] 防跨房越权冒用、身份核验与 API 防死循环熔断机制
├── data/                          # 酒店领域静态与业务数据
│   └── hotel_knowledge.json       # 酒店知识库（早餐、退房、设施政策、短语同义词索引）
├── harness/                       # 质量工程与回归评测底座
│   ├── eval_dataset.json          # 20+ 组覆盖单轮/多轮/客诉/FAQ/范围外的质检测试集
│   └── eval_runner.py             # 离线自动化评测运行器（输出准确率、召回率、延迟报告）
└── tests/                         # 自动化单元测试集
    └── test_app.py                # 核心状态机流转、多轮补槽、会员退房与工单生成的单元测试
```

---

## 3. 架构设计与实现约束 (Implementation Constraints)

所有参与本项目开发、重构或由 AI (如 Codex / Cursor / Claude) 编写的代码，必须严格遵守以下工程约束：

### 3.1 核心架构模式：黑板模式 (Blackboard Pattern)
- **状态集中管理**：所有住客对话上下文、房号绑定、工单列表、审批请求及 Trace 轨迹统一收敛在 `HotelState` 数据类中。
- **Agent 无状态协同**：Agent（`ReceptionAgent` 与 `DispatchAgent`）不维护独立的会话状态，只对 `HotelState` 进行读取、判定、槽位填补并输出响应。
- **UI 与业务绝对解耦**：`hotel_core.py` 严禁直接或间接 `import streamlit`，必须能在命令行、CLI、Pytest 和云服务中独立运行与测试。

### 3.2 复合意图分解约束 (Compound Intent Processing)
- **多意图兼顾原则**：用户单次输入可能同时包含**只读信息咨询 (FAQ)** 与 **写操作服务申请 (Service Action)**（例如：“你们早餐几点结束？另外我是携程黑金，能帮我两点退房吗？”）。
- **不可互斥吞没**：系统不得仅因命中工单或延退服务，就直接短路丢弃 FAQ 答案。必须支持**复合意图解构**：
  1. 识别并提取 FAQ 意图并生成知识库答复；
  2. 识别并提取服务诉求（如延退/送物/报修），执行槽位提取与业务处理；
  3. 将两者融合为连贯且有逻辑的复合答复。

### 3.3 双轨执行机制：云端 LLM 与本地规则确定性降级 (Dual Mode Execution)
- **无 Key 零成本确定性回归**：在没有配置 `OPENAI_API_KEY` 或云端模型调用超时/失败时，系统必须 100% 自动无缝降级至**本地规则引擎（Rule Fallback）**。
- **自动化测试无云依赖**：`harness/eval_runner.py` 与 `tests/test_app.py` 在默认环境下严禁产生云端 API 消费，保证任何机器拉取代码后均能即时回归且测试结果 100% 幂等。

### 3.4 权限与数据安全约束 (Security & Privilege Boundary)
- **严禁大模型擅自代客决策**：大模型/Agent 绝不允许通过猜测臆造房号、会员级别或越权批准延迟退房。
- **PMS 数据不可伪造**：只有通过 PMS 本地核实为“在住”且等级为“携程黑金会员”，同时房态“充裕”的情况下，方可自动批准延退至 14:00。其余情况（普通会员、房态紧张、超 14:00）必须流转至前台人工审批流。
- **工单派发严格强类型校验**：工单派发必须经由强校验函数（`create_hotel_ticket`），严禁大模型输出非法部门、虚假工单字段或恶意篡改优先级。

### 3.5 风险分级与客诉保护 (Sentiment & Risk Escalation)
- 当检测到住客情绪包含严重负面词（如“投诉”、“差评”、“太差”、“极度生气”等）时：
  1. 情绪状态必须标记为 `愤怒`；
  2. 工单优先级强制升至 `高`，并标记 `红色高危预警`；
  3. 责任部门强制直接路由至 `大堂经理`，杜绝踢皮球。
