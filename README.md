# AegisStay Multi-Agent

一个可直接演示的智慧酒店多智能体协同系统：住客端、酒店运营端和 Harness 质量底座在同一个 Streamlit 工程中，通过共享 Blackboard 状态完成 Agent 交接。PMS 仿真数据包含 240 间客房，并保留 1128、808、902 三个验收基准房。

服务请求会从 ReceptionAgent 分流到四个垂直部门 Agent：HousekeepingAgent 负责送物库存与 ETA，EngineeringAgent 负责故障险情，ConciergeAgent 负责续住和票券，ManagerEscalationAgent 负责红色客诉和补偿建议。

## 一键运行

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置环境（可选，支持离线规则保底）
```bash
cp .env.example .env
# 编辑 .env 填入你的 API Key（支持 SiliconFlow / DeepSeek / OpenAI / Ollama 等兼容接口）
```

### 3. 启动服务
- **启动 Streamlit 可视化面板（住客端 / 运营端 / 自动化质检底座）**：
  ```bash
  streamlit run app.py
  ```
- **启动 FastAPI 高并发交互网关**：
  ```bash
  uvicorn server:app --host 127.0.0.1 --port 8000
  ```

### 4. 自动化回归与压测
- **端到端单元测试（44 项用例）**：
  ```bash
  python -m unittest discover tests
  ```
- **Harness 20 场景真实大模型 / 规则质检回归**：
  ```bash
  python -m harness.eval_runner --cloud    # 云端真实模型评测
  python -m harness.eval_runner            # 离线极速回归
  ```
- **多会话高并发异步压测（会话隔离与零污染验证）**：
  ```bash
  python tests/stress_test.py              # 本地 200+ QPS 微秒级压测
  python tests/stress_test.py --cloud      # 真实云端并发压测
  ```

## 三个业务视角

1. `📱 住客智能管家端`：只展示住客消息、管家回复和“我的服务进度”，不暴露内部工单号、黑板日志和模型信息。
2. `🖥️ 酒店运营与工单中枢`：展示 1128/808/902 房的 PMS/CRM 档案、实时工单、高危客诉、延迟退房审批和完成结单操作。
3. `🧪 Harness 自动化质检底座`：回放 20 条业务用例，显示进度、意图准确率、槽位召回率、任务完成率、派单精准度和明细表。

## 业务链路

```text
住客消息
  -> ReceptionAgent：拆出 FAQ 信息流 + 服务操作流
  -> FAQ 流：知识库检索并生成政策回答
  -> 操作流：意图 / 情绪 / 房号 / 时间抽取
  -> HotelState Blackboard：共享上下文与待补槽请求
  -> 缺房号：继续追问，不写工单
  -> 服务槽位完整：垂直部门 Agent -> create_hotel_ticket
  -> 垂直部门 Agent 执行库存 / 险情 / 房态 / 补偿规则
  -> 本地校验部门、优先级、幂等复用 -> PMS 工单流水
  -> Staff 端结单或审批，Guest 端只看安全进度
```

### PMS 与高价值流程

- `1128 / 张先生 / 携程黑金会员 / 房态充裕`：输入“1128 能两点退房吗？”会调用 `apply_late_checkout`，自动把 PMS 退房时间更新为 14:00。
- `808 / 李女士 / 普通会员 / 房态紧张`：同样申请会进入“需前台审核”，不会越权自动批准。
- “投诉、差评、赔偿、无法忍受”等明确高危表达生成红色客诉工单并派给大堂经理；“空调坏了，很生气”仍然路由工程部，但优先级升级为高。

## 云模型与降级

模型调用使用 OpenAI 兼容接口；可配置 SiliconFlow、DeepSeek 或 Qwen：

```powershell
$env:OPENAI_API_KEY = "你的 key"
$env:OPENAI_BASE_URL = "https://api.siliconflow.cn/v1"
$env:OPENAI_MODEL = "Qwen/Qwen3-8B"
```

没有 Key、超时或 JSON 不合法时，系统自动使用确定性规则和本地 Tool 校验继续运行。Harness 强制关闭云调用，不产生费用。

## 项目结构

```text
AegisStay-MultiAgent/
├─ app.py                       # Streamlit 住客/运营/质检三端交互界面
├─ server.py                    # FastAPI 多会话隔离高并发交互网关
├─ hotel_core.py                # Blackboard 黑板模式、多 Agent 定义、状态机安全护栏
├─ data/
│  ├─ pms_rooms_240.json        # 240 间客房 PMS 仿真房态与 CRM 档案
│  ├─ hotel_simulation_gen.py   # 房态仿真数据生成器
│  └─ hotel_knowledge.json      # 酒店 FAQ 知识库与语义短语
├─ harness/
│  ├─ eval_dataset.json         # 20 个边界场景回归评测用例集
│  └─ eval_runner.py            # 自动化回归与云端模型真实质检引擎
├─ tests/
│  ├─ stress_test.py            # 异步多会话并发压测与数据防串线检测
│  ├─ test_app.py               # 44 项业务全流程端到端单元测试
│  └─ test_api.py               # FastAPI 网关会话隔离测试
├─ spec/                        # 需求分析与架构设计规范工程文档
├─ requirements.txt             # 项目依赖清单
├─ .env.example                 # 环境变量配置模板
└─ README.md
```

当前是面试演示级本地状态，不会联系真实酒店、支付或真实 PMS；生产化时应加入认证、持久化数据库、审计日志、幂等键和消息队列。
