# AegisStay 智能体拟人化、高情商闲聊软引流与体验修复 Spec

> 适用范围：历史专题设计／说明，保留当时描述，不作为当前发布规范或已实现能力证明。当前要求见 [主规格](specification.md)、[验收标准](acceptance_criteria.md)；适用范围见 [历史索引](legacy_specs.md)。

## 1. 业务思考与 Token 成本控制哲学 (Cost vs. Delight Trade-off)

在企业级 AI 产品设计中，“闲聊要不要回答”是一个经典的**体验 vs. 算力成本权衡**：
- **纯拒答（粗暴省 Token）**：如直接回复“我只能回答酒店问题”，零额外 Token 消耗，但体验极度生硬机械，用户感觉“这个管家智商不高”；
- **纯畅聊（严重浪费 Token）**：陪客人大聊宇宙天体、写代码，会沦为通识玩具，产生巨量无效 Token 账单；
- **企业级最优解 —— 高情商软引流 (Soft Pivot with Token Cap)**：
  1. **超短 Token 预算**：限制大模型仅用 **1~2 句话（60 字以内，约 80 Token）** 简明幽默回答常识；
  2. **温和锚定酒店服务**：回答完知识后，自然将落脚点转回客房关怀（如光线、温度、送水）；
  3. **频控熔断（Rate Limiting）**：若客人连续 3 次纯闲聊，管家礼貌提示：“小管家需优先专注为您办理酒店事务”，防止被恶意刷量。

> **面试加分点**：向面试官阐述这套“Token 预算控制 + 业务软转折”策略，能极大展现你的业务成本敏感度与落地架构能力。

---

## 2. 待修复问题与新增特性一览

### 缺陷修复 (Bug Fixes)
1. **称呼重复问题**：修复 `赵女士，赵女士，您好...` 的多重前缀拼接。
2. **催单/追问防重复报修**：当房间已有处于“处理中”的工单时，住客追问 *“师傅还没修好呢，怎么回事？”*、*“还要等多久”*、*“催一下”* 时，识别为催办进度问询，刷新催单 Trace，安抚住客并**绝不生成第 2 张重复工单**。
3. **大模型意图枚举补齐**：在 `ReceptionAgent.understand` 中补齐 `续住申请`、`票券服务` 与 `闲聊`，让云端模型精准识别续住诉求。

### 核心飞跃特性 (Smart Persona & Chit-Chat)
4. **高情商常识闲聊软引流 (Soft-Pivot Chit-Chat)**：
   - 面对天文学、闲聊或通识问题，调用 Qwen 生成：`[幽默/简明通识解答 (30字)] + [五星级酒店客房关怀软引流 (30字)]`。
   - 离线/规则模式自动降级为温和提示。
5. **四大子智能体拟人化发声 (Sub-Agent Persona Voices)**：
   - 大堂经理 `ManagerEscalationAgent`、工程师傅 `EngineeringAgent`、保洁管家 `HousekeepingAgent`、礼宾管家 `ConciergeAgent` 拥有各自鲜明的角色语气与专业态度。

---

## 3. 详细技术实现方案 (`hotel_core.py`)

### 3.1 意图枚举更新 (`ReceptionAgent.understand`)
更新大模型 Prompt：
```python
prompt = (
    "提取酒店住客意图 JSON：intent(酒店咨询/送物/工程报修/客诉紧急/延迟退房/续住申请/票券服务/闲聊/其他)、"
    "sentiment(正常/愤怒)。"
    "注意：询问退房时间/退房规则属于'酒店咨询'；明确要求推迟退房时间才属于'延迟退房'；"
    "想多住几天属于'续住申请'；要早餐券属于'票券服务'；"
    "天文学、闲聊、问诗词天气等无关话题属于'闲聊'；"
    "修好了/送到了等服务完结反馈不得判定为新的工程报修或送物。"
)
```

### 3.2 催单与进度追问状态处理 (`handle_request`)
1. 关键词定义：
   ```python
   pacing_keywords = ("还没好", "还没修好", "怎么回事", "还要多久", "多久能到", "催一下", "有人来吗", "进度如何", "还没送到")
   ```
2. 当住客提出追问且当前房间存在 `status == "处理中"` 的工单时：
   - 匹配该工单，不生成新工单；
   - 记录 Trace：`{"agent": ticket.get("agent", "EngineeringAgent"), "event": "催单加急响应", "detail": f"住客追问进度，已加急督办工单 {ticket['ticket_id']}"}`；
   - 生成安抚答复：`"{guest_name}，您的{ticket['category']}（{ticket['item_detail']}）正在加急处理中，已为您再次催促工作人员尽快上门，请您稍候！"`。

### 3.3 称呼去重与安全拼接 (`_finish` / `handle_request`)
统一前缀格式化函数：
```python
def _format_greeting(name: str | None, reply: str) -> str:
    if not name:
        return reply
    # 若 reply 已经以该称呼开头（如 "赵女士，"），避免重复拼接
    prefix = f"{name}，"
    if reply.startswith(prefix) or reply.startswith(f"{name} "):
        return reply
    return f"{prefix}{reply}"
```

### 3.4 高情商闲聊软引流生成器 (`_generate_soft_pivot_chitchat`)
在 `ReceptionAgent` 或 `HotelOrchestrator` 中：
```python
def generate_chitchat_reply(self, text: str, guest_name: str | None) -> str:
    if not self.model.client:
        return "我可以帮您办理酒店客房服务或解答设施政策，请告诉我您的具体需求。"
    prompt = (
        "你是 AegisStay 五星级智慧酒店的贴身管家。"
        f"住客向你提出了一个闲聊或范围外话题：'{text}'。"
        "请按以下规则回答："
        "1. 用 1 句话（30字以内）简明、准确、有趣地回答住客的问题；"
        "2. 用 1 句话自然地将话题转回酒店客房关怀（例如关心房内舒适度、询问是否需要送物或饮水）；"
        "3. 总字数严格控制在 70 字以内，语气优雅体贴，不要长篇大论。"
    )
    raw = self.model.chat([{"role": "user", "content": prompt}], max_tokens=100)
    return raw.strip() if raw else "我可以帮您办理酒店客房服务或解答设施政策，请告诉我您的具体需求。"
```

---

## 4. 自动化测试用例矩阵 (`tests/test_app.py`)

1. `test_no_duplicate_greeting`: 验证无论何种分支，回复开头绝不出现 `赵女士，赵女士` 的重复称呼。
2. `test_pacing_inquiry_does_not_create_duplicate_ticket`: 
   - 第 1 轮：`1106 房间空调不制冷` -> 创建工程工单（处理中）；
   - 第 2 轮：`师傅还没修好呢，怎么回事？` -> 工单数依然为 1，状态依然为处理中，回复包含催办安抚。
3. `test_stay_extension_intent_extracted`:
   - 输入 `1106 想续住两天` -> 准确生成 `续住申请` 工单，接管 Agent 为 `ConciergeAgent`。
4. `test_soft_pivot_chitchat`:
   - 输入 `银河系在哪片星云？` -> 系统给出常识解答并包含客房关怀，且不创建任何工单。
