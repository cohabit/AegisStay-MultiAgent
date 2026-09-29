# AegisStay FastAPI 多会话服务与并发隔离 Spec (拓展 2)

## 1. 背景与目标

在真实的酒店商用场景中，住客通过**手机小程序**、**客房智能屏（如小度）**或**大堂无人自助机（Kiosk）**与系统交互：
- **并发与串流痛点**：若所有请求共享单个状态实例，不同住客的房号、对话上下文、未决待办（pending）及工单历史将严重冲突与泄漏（如 1106 房看到 1128 房的报修流水，或 1180 的请求篡改了 1208 的在办状态）。
- **工程化目标**：
  1. 引入工业级异步框架 **FastAPI + Uvicorn**，提供高性能、标准化 RESTful API 接口与 Swagger 交互文档（`/docs`）；
  2. 设计线程安全的 **`SessionManager`**，以 `session_id` 物理隔离各住客的对话上下文与待办状态，同时共享全局 PMS 房态与部门工单池；
  3. 支持大堂自助机（Kiosk）超时自动复位（30秒清空）与主动安全登出；
  4. 编写完整的接口自动化测试用例（`tests/test_api.py`），验证会话隔离与多用户并发安全性。

---

## 2. 系统架构设计

```mermaid
flowchart TD
    ClientA[住客 A (1106房 小程序)] -->|POST /api/chat sess_1106| APIGateway[FastAPI 网关 server.py]
    ClientB[住客 B (1128房 智能屏)] -->|POST /api/chat sess_1128| APIGateway
    ClientKiosk[大堂自助机 Kiosk] -->|POST /api/session/reset| APIGateway

    subgraph ServiceLayer[服务层与会话调度]
        APIGateway --> SessionMgr[SessionManager 会话管理器]
        SessionMgr -->|获取/创建| FlowA[HotelOrchestrator A (会话独立)]
        SessionMgr -->|获取/创建| FlowB[HotelOrchestrator B (会话独立)]
        SessionMgr -->|TTL/主动销毁| Cleanup[会话超时回收]
    end

    subgraph SharedData[企业全局数据中枢]
        FlowA --> SharedPMS[(共享全局 PMS 房态与工单总线)]
        FlowB --> SharedPMS
        SharedPMS --> DeptAgents[Housekeeping / Engineering / Concierge / Manager]
    end
```

### 核心隔离原则：
1. **会话级隔离（Session Scope）**：
   - 包含：`messages`（聊天记录）、`room_no`（当前绑定房号）、`pending`（多轮待补全槽位）、`sentiment`（当前住客情绪）、`guest_profile`（当前住客档案）。
   - 实现：每个 `session_id` 拥有专属的会话状态实例。
2. **酒店级共享（Global Hotel Scope）**：
   - 包含：`pms_rooms`（全局 240 间房态）、`tickets`（全店集中工单流水）、`voucher_ledger`（早餐券核销账本）、知识库。
   - 实现：工单流水与房态在多会话间全局同步，任何会话创建的工单均汇入集中工单池，各部门智能体可统一监控。

---

## 3. 依赖项配置 (`requirements.txt`)

新增 FastAPI 及 ASGI 容器依赖：
```text
fastapi>=0.110.0
uvicorn>=0.30.0
httpx>=0.27.0
```

---

## 4. RESTful API 接口定义

### 4.1 住客多轮对话接口 (`POST /api/chat`)
* **Request Body** (`ChatRequest`):
  ```json
  {
    "session_id": "guest_1106_uuid",
    "message": "1106 卫生间花洒漏水",
    "room_no": "1106" // 可选，若传入则自动关联
  }
  ```
* **Response Body** (`ChatResponse`):
  ```json
  {
    "session_id": "guest_1106_uuid",
    "reply": "赵女士，请立即远离漏水/带电区域，关闭房内电源并等待工程部确认安全。已通知工程人员检查，工作人员将尽快联系您。",
    "active_agent": "EngineeringAgent",
    "room_no": "1106",
    "intent": "工程报修",
    "sentiment": "正常",
    "model_mode": "云模型 + 规则校验 / EngineeringAgent",
    "trace": [
      {"agent": "ReceptionAgent", "event": "关联档案", "detail": "房间 1106；已关联 CRM"},
      {"agent": "EngineeringAgent", "event": "vertical_agent_dispatch", "detail": "HT-6450A9498E → 工程部 / 高"}
    ],
    "progress_items": [
      {"label": "工程报修：处理中", "status": "处理中", "type": "warning"}
    ]
  }
  ```

### 4.2 会话重置与安全销毁 (`POST /api/session/reset`)
* 用于住客主动退出或大堂自助终端 30 秒超时复位。
* **Request Body** (`SessionResetRequest`):
  ```json
  {
    "session_id": "guest_1106_uuid"
  }
  ```
* **Response Body**:
  ```json
  {
    "status": "success",
    "session_id": "guest_1106_uuid",
    "message": "会话已安全重置并销毁历史上下文"
  }
  ```

### 4.3 会话详情查询 (`GET /api/session/{session_id}`)
* 查询指定会话的当前状态与历史消息。
* **Response Body**:
  ```json
  {
    "session_id": "guest_1106_uuid",
    "room_no": "1106",
    "guest_name": "赵女士",
    "member_level": "携程白金会员",
    "active_agent": "EngineeringAgent",
    "messages": [
      {"role": "user", "content": "1106 卫生间花洒漏水"},
      {"role": "assistant", "content": "..."}
    ],
    "tickets": [...]
  }
  ```

### 4.4 酒店运营中枢全景概览 (`GET /api/pms/overview`)
* 供酒店后台运营看板与各部门监控大板调用。
* **Response Body**:
  ```json
  {
    "total_rooms": 240,
    "occupied": 156,
    "clean": 48,
    "pending_tickets": 1,
    "department_agents": {
      "HousekeepingAgent": {"name": "客房保洁智能体", "status": "在线", "active_tasks": 0},
      "EngineeringAgent": {"name": "工程安防智能体", "status": "在线", "active_tasks": 1},
      "ConciergeAgent": {"name": "礼宾接待智能体", "status": "在线", "active_tasks": 0},
      "ManagerEscalationAgent": {"name": "大堂经理危机响应", "status": "待命", "active_tasks": 0}
    }
  }
  ```

### 4.5 集中工单列表与办结 (`GET /api/tickets` & `POST /api/tickets/{ticket_id}/complete`)
* `GET /api/tickets?room_no=1106&status=处理中`：获取满足条件的工单列表；
* `POST /api/tickets/{ticket_id}/complete`：运营人员或部门直接手动办结该工单。

### 4.6 健康检查 (`GET /health`)
* 返回服务运行状态、大模型在线情况及已激活会话总数。

---

## 5. SessionManager 实现规范

1. **结构设计**：
   ```python
   class SessionManager:
       def __init__(self, ttl_seconds: int = 1800):
           self.sessions: dict[str, HotelOrchestrator] = {}
           self.last_active: dict[str, float] = {}
           self.lock = threading.Lock()
           self.shared_state = HotelState() # 全局共享状态（PMS房态/全局工单池）
   ```
2. **生命周期管理**：
   - `get_or_create(session_id: str) -> HotelOrchestrator`: 若不存在则创建独立 flow，并挂载共享的 PMS 房态与工单池；更新最后活跃时间。
   - `reset(session_id: str)`: 销毁对应 flow，释放上下文。
   - `cleanup_expired()`: 清理超过 TTL（默认 30 分钟）的过期会话。

---

## 6. 验收标准与测试用例 (`tests/test_api.py`)

1. **多会话并发无串流测试 (`test_multi_session_isolation`)**：
   - 客户端 A 使用 `session_A` 发送 `"1106 送毛巾"`；
   - 客户端 B 使用 `session_B` 发送 `"1128 房间空调坏了"`；
   - 断言：
     - `session_A` 的当前房间为 `1106`，活跃 Agent 为 `HousekeepingAgent`；
     - `session_B` 的当前房间为 `1128`，活跃 Agent 为 `EngineeringAgent`；
     - 两个 session 互相不包含对方的消息和待办事项。
2. **跨房安全防护验证 (`test_cross_room_api_intercept`)**：
   - 客户端使用 `session_A` 绑定 `1102`；
   - 再次发送 `"帮 1208 房间送水"`；
   - 响应中返回跨房拦截提示，阻止串房操作。
3. **会话重置验证 (`test_session_reset`)**：
   - 调用 `/api/session/reset` 后，再调用 `/api/session/{session_id}`，历史消息被清空。
4. **全局工单流转与概览测试 (`test_pms_overview_and_ticket_flow`)**：
   - 两个不同会话产生的工单均能在 `/api/tickets` 和 `/api/pms/overview` 中汇总可见。
