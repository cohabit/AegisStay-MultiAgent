# AegisStay 仓库地图与实施约束

学校学术酒店的单酒店服务台：住客提出需求，部门处理器生成提案，编排器与运行时验证后提交，前台和部门人员通过 API 办理审批、派送与回执。当前验收范围为 `offline-code-baseline`，酒店现场状态为 `NOT_VERIFIED`。

## 从哪里读

| 目的 | 入口 |
| --- | --- |
| 当前范围与 REQ-01…REQ-16 | [主规格](spec/specification.md) |
| 角色、政策、业务时序与失败处理 | [详细业务规格](spec/design/business.md) |
| 模块、数据、并发、存储与部署 | [详细架构规格](spec/design/architecture.md) |
| REQ-14 部署、运维、备份与恢复 | [部署与运维子规格](spec/design/deployment.md) |
| 可复现实验与现场目标 | [实验方案](spec/experiments.md) |
| FIELD-01…FIELD-06 现场执行协议 | [现场验收协议](spec/field_validation.md) |
| FUT-01…FUT-06 外部集成与模型治理 | [未来集成规格](spec/future_integrations.md) |
| AC-01…AC-16 与证据硬门槛 | [验收标准](spec/acceptance_criteria.md) |
| 真实测试点名与机器映射 | [追溯说明](spec/traceability.md)、[traceability.json](spec/traceability.json) |
| 分层质量集与验收方法 | [质量验收子规格](spec/quality_evaluation.md) |
| 当前界面视觉、对比度、工作区与空间仿真 | [界面视觉规格](spec/ui_visual_quality.md) |
| 住客公开身份、提示注入与调度/API 边界 | [对话安全规格](spec/security_prompt_injection.md) |
| 完整住客归档、批量取消与物品/称呼边界 | [服务可靠性规格](spec/service_reliability.md) |
| 湖景入口、常规物品、设施核验与风险分级 | [酒店服务完善规格](spec/hotel_readiness.md) |
| 工单执行明细、库存缺口与设施问项 | [运营工单与设施问答规格](spec/operations_ticket_and_facility.md) |
| WiFi 入口、每次入住刷新、数据库与 Redis 方案 | [入住数据与运营说明](docs/stay_data_and_operations.md) |
| 旧专题及旧验收适用范围 | [历史索引](spec/legacy_specs.md) |
| 安装、启动和值班操作 | [README](README.md)、[运维手册](docs/operations.md)、[部署子规格](spec/design/deployment.md) |
| 实施边界与原始测试身份 | [工作计划](spec/work_plan.md)、[127 项基线 ID](spec/baseline_test_ids.json) |

当前规格优先于历史专题。旧商业会员规则属于 `standard` 回归夹具，不能解释为学校待遇；代码实现状态与指定运行的证据状态分别记录。

## 代码地图

| 路径 | 责任与修改入口 |
| --- | --- |
| `hotel_core.py` | `HotelState` 黑板；FAQ、意图、补槽；接待/保洁/工程/礼宾/经理处理器；提案核验与业务 |
| `agent_protocol.py` | 送物口语命令、部门提案约束、`AgentConflict` / `TaskInterrupted` |
| `agent_runtime.py` | `SessionManager`、任务版本、取消/替换、差量提交、共享库存、入住授权与幂等 |
| `persistence.py` | 单写入者锁、SQLite WAL、业务/会话/任务/消息/审计原子检查点 |
| `auth.py` / `server.py` | 凭证、岗位和会话授权、FastAPI 路由、安全字段投影 |
| `app.py` / `deployed_ui.py` / `api_client.py` | Streamlit 本地演示或 API 工作台；部署分支通过 `HotelApi` 访问唯一服务端 |
| `data/academic_hotel_knowledge.json` | 学校政策模板，未知时间/地点/权益要求核验 |
| `hotel_catalog.py` / `data/service_catalog.json` | 24 项库存元数据与设施三态；合成初值不代表酒店实盘，存量库新增 SKU 默认为 0 |
| `ticket_details.py` / `facility_answers.py` | 员工履约明细的只读投影与按设施问项回答；不自动预留、不填补未知事实 |
| `data/hotel_knowledge.json` / `data/pms_rooms_240.json` | standard 知识与合成初始房态；不是外部真实 PMS |
| `harness/eval_runner.py` / `eval_dataset.json` | 固定 H01…H20 规则回归、结构化四指标 |
| `harness/eval_extended.json` / `tests/test_quality_evaluation.py` | 5 组、40 条分层质量集；不替换 H01…H20，报告分组指标与 Wilson 区间 |
| `harness/self_evolution.py` / `hard_cases.json` / `dynamic_exemplars.json` | 难例与 Few-Shot 示例检索；不训练模型参数 |
| `scripts/manage.py` | 随机配置初始化、SQLite 校验备份；恢复见运维手册 |
| `scripts/verify_release.py` / `verify_blackboard.py` | 新目录证据、原始测试 ID、Harness、状态及 Trace 校验 |
| `tests/test_app.py` | standard 核心回归，门槛不少于 40 项 |
| `tests/test_deployment.py` | academic 身份、岗位、待遇核验、存储故障与部署工作台 |
| `tests/test_api.py` / `test_task_runtime.py` | API、异步仲裁、冲突与受控竞态 |
| `tests/test_delivery_language.py` / `test_persistence.py` | 口语送物生命周期、持久化和重启 |
| `tests/test_service_reliability.py` / `test_guest_history_ui.py` | 完整归档与两端分页、批量取消代际、未知物品和情绪/称呼边界 |
| `tests/test_hotel_readiness.py` | 目录事实、口语风险、设施需求记录、8 位住客抢库存及换客空上下文 |
| `tests/test_conversation_followup.py` | 用户实测原句、混合未知物品/量词、档案会员查询、人工澄清与多设施完整答复 |
| `tests/test_delivery_followup.py` | 连写物品与时效修饰、已有订单催送、否定/新增需求、会话隔离、重启与重复预留守卫 |
| `tests/test_ticket_fulfillment.py` / `test_operations_ticket_ui.py` / `test_facility_intents.py` | 可执行物品清单、缺货/旧工单写入守卫、两端运营卡片及编号设施问答 |
| `tests/test_evolution.py` / `test_streamlit.py` / `test_visual_contract.py` | 示例检索、记忆与本地界面、主题对比度回归 |
| `tests/test_release_verification.py` | 证据工具负向测试，拒绝假通过 |
| `tests/stress_test.py` | 开发压测工具；不能对真实酒店执行全量重置或声称生产容量 |
| `Dockerfile` / `docker-compose.yml` | 非 root、单 API worker、独立 Web 和持久卷配置；静态配置不等于实跑通过 |

## 必须保持的约束

- `hotel_core.py` 不导入 Streamlit；部署 UI 不持有第二份可写业务状态。
- academic 延退和餐券先核验，不自动承诺免费、发券或预留券库存；保洁直调和送物改券同样受限。真实电子券发行尚未实现。
- 住客 `room_no`、`stay_id`、`session_id` 和 grant 共同约束写入。模型或住客自称不能授予岗位和跨房权限。
- 住客对话与 API 仅公开服务助手身份；安全探询不外呼模型、不绑定攻击房号、不取消合法在途订单。正则检测不替代鉴权和提案校验。
- FAQ 与服务分别处理再合成；缺槽可追问，已认证房间不能通过聊天切换。
- 部门只提案；房号、部门、优先级、物品、数量、库存和借用变化校验后才提交。冲突进入人工核验。
- 同库只有一个 API 写入者；员工与异步写入均保持原子持久化、回滚和幂等。禁止增加多 worker 掩盖容量问题。
- 离线验证强制规则、合成数据、独立临时库；清除宿主外部 PMS/知识路径、API 地址和日期配置，不读取真实数据或产生模型消费。
- 原始 127 个测试 ID 保留并实际通过；新增测试不能替换旧 ID 凑数。跳过、零用例、失败 JSON、旧 run_id 和坏 Trace 均不能 PASS。
- 原始日志放已忽略的 `.artifacts/`；公开材料不含密钥、真实住客资料或逐字稿。
- 不重置已有未提交成果；按 [工作计划](spec/work_plan.md) 文件所有权协作。

## 验证入口

使用项目 `.venv` Python；隔离环境和完整命令见 [实验方案](spec/experiments.md)。统一入口为 `python scripts/verify_release.py --output-dir .artifacts/verification/<新目录>`。合格证据须同时满足原始 127 项、完整不少于 127、核心不少于 40、零跳过、H01…H20 全过及四指标为 1.0，并核验状态、Trace、来源和 REQ 映射。

不要只凭退出 0、HTTP 200、旧截图或历史 Markdown 下结论。集成人员在文件稳定后执行完整发布验证；一次焦点回归不代表整次发布或现场已验收。
