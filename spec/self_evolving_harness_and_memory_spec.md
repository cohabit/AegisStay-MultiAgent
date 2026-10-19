# AegisStay 2.0: 自进化评测底座、分层记忆与企业级 PMS 演进技术规范

> **范围说明（2026-10-20）**：本文保留演进设计目标，不作为当前能力验收。当前只有预置难例与诊断、字符相似度 Few-Shot 检索和回归执行，未接通自动采集、自动提炼、独立对照与自动发布；会话摘要为截断文本且未注入模型，无持久化长期记忆。下文“已完成”及效果表述如无对应实测记录，不能作为本版证据。实际边界以 [README](../README.md) 和 [本版验收](release_verification_2026-10-20.md) 为准。

## 1. 目标与背景

当前 AegisStay 1.0 已经完成了「双轨状态机护栏 + 多会话黑板隔离 + 20 场景离线/云端回归评测」。
在迈向工业级生产落地的进程中，需要解决以下深度进阶需求：

1. **评测底座从“静态只读打分器”向“自进化闭环（Self-Evolving Loop）”跨越**：
   - 能够自动挖掘低置信度、多轮口误或边缘反转的**难例库（Hard Cases Bank）**；
   - 具备 **Critic 智能反思诊断器**，分析失败或歧义的根本原因；
   - 提炼出高质量**动态 Few-Shot 样本**，在主接待理解层实现无需重新发版的“热进化注入（Dynamic Few-Shot Injection）”，使系统越用越聪明。
2. **PMS 房态多日期分区与夜审演进（Date-Partitioned PMS & Night Audit）**：
   - 模拟酒店真实的跨日期生命周期，支持在住态、预订态、待清洁态与超时离店态；
   - 提供「夜审（Night Audit）」跨日批处理，实现会员每日免费权益重置与到期订单流转；
   - 引入次日预订冲突检测，延退时预检次日新客入住排期，杜绝房态撞车。
3. **物料库原子锁与实时排队 ETA 仪表盘（Atomic Inventory & Dynamic ETA 2.0）**：
   - 细化消耗品与借还品（如充电器、雨伞），支持并发原子扣减与超卖拦截；
   - 在前端界面直观呈现服务排队深度与 ETA 动态跳变曲线。
4. **长会话滑动窗口与摘要记忆压缩（Sliding Window & Rolling Summary）**：
   - 针对长轮次住客会话，超出设定窗口时自动沉淀核心背景摘要，控制 Token 成本并防止注意力漂移。

---

## 2. 核心架构设计

### 2.1 Harness 自进化闭环架构 (Self-Evolution Architecture)

```
[ 住客交互 / 极端测试用例 ]
          │
          ▼
   [ 边缘/难例探测器 (Hard Case Miner) ] ──> 沉淀至 hard_cases.json
          │
          ▼
   [ 根因反思诊断器 (Critic Evaluator) ] ──> 诊断语义模糊点、意图冲突点
          │
          ▼
   [ 动态样本萃取器 (Few-Shot Curator) ] ──> 生成规范化的黄金示例
          │
          ▼
   [ 动态少样本库 (dynamic_exemplars.json) ]
          │
          ▼
   [ ReceptionAgent 动态热注入 ] ──> 注入主模型理解 Prompt
          │
          ▼
   [ 演进前后效果对比器 (Evolution Delta Runner) ] ──> 输出可量化的进化战报
```

### 2.2 PMS 多日期夜审引擎 (Night Audit Engine)

- **基准日期**：支持系统仿真当前日期 `current_date`（如 2026-10-01）。
- **房态表结构升级**：
  ```python
  pms_record = {
      "room_no": "1128",
      "guest_name": "张先生",
      "member_level": "携程黑金会员",
      "checkin_date": "2026-10-01",
      "checkout_date": "2026-10-02",
      "checkout_time": "12:00",
      "room_status": "在住", # 在住 | 待离店 | 待清洁 | 空房可用
      "daily_benefits_used": {"breakfast_voucher": 0},
      "next_booking": {"date": "2026-10-02", "guest_name": "王女士", "expected_checkin": "15:00"}
  }
  ```
- **夜审动作（Night Audit Run）**：
  1. 当前日期向前推进 1 天（`D -> D+1`）；
  2. 检查所有在住房间：若超过退房日期未退房，标记为“超时待结”；
  3. 重置会员每日免费权益计数器（`daily_benefits_used` 清零）；
  4. 将次日预订房客激活为“今日待入住”。

### 2.3 原子物料库与借用物资管理 (Atomic Inventory)

- 消耗品（毛巾、矿泉水、拖鞋）：扣减不可逆。
- 借还品（快充充电器、雨伞）：工单绑定房间借用登记，退房时联动 PMS 提示未归还物资。
- 线程安全控制：在 Python 层引入读写安全互斥锁，模拟 Redis 原子操作。

---

## 3. 验收标准与交付产物

1. `harness/self_evolution.py`：实现完整的难例捕获、反思诊断、Few-Shot 提炼与演进对比测试。
2. `hotel_core.py` 升级：
   - 接入 `SelfEvolutionCurator` 动态样例注入；
   - 增强 `PMSNightAuditEngine` 支持跨日夜审与次日预订预检；
   - 增强 `AtomicInventory` 支持借用类与消耗类物料管理。
3. `server.py` 增加夜审与自进化管理 API：
   - `POST /api/pms/night-audit`：一键触发夜审；
   - `POST /api/harness/evolve`：一键触发自进化闭环并返回对比数据。
4. `app.py` 界面升级：
   - 新增【🧬 Harness 自进化与质量演进中枢】工作台；
   - 在运营中枢增加【🌙 模拟过夜夜审】与【📦 实时物料与排队 ETA 监控】看板。
5. 单元测试全覆盖：确保新增能力及原有 44 项测试全部 PASS。
