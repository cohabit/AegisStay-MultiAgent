# 学校学术酒店系统主规格

系统服务单所学校的单酒店，以本地入住台账、受保护 API 与部门回执构成闭环。真实 PMS、学校 SSO、电子券发行和现场容量尚未完成验收。

规范入口：[仓库地图](../AGENTS.md) → 本文 → [业务规格](design/business.md) / [架构规格](design/architecture.md) → [实验方案](experiments.md) / [验收标准](acceptance_criteria.md) / [追溯说明](traceability.md) / [机器映射](traceability.json)。[历史索引](legacy_specs.md) 保留旧专题，[工作计划](work_plan.md) 记录实施边界，[运维手册](../docs/operations.md) 记录部署操作。

## 范围与状态

| 项目 | 当前约定 |
| --- | --- |
| 业务基线 | `academic`；接待订单、会务排期、是否含早和收费由前台核验 |
| 兼容基线 | `standard`；商业会员和模拟配额仅用于旧测试回归 |
| 服务能力 | 多主题 FAQ + 单服务、多轮补槽、送物与变更、工程/客诉、续住申请、延退审核、餐券核验记录 |
| 运行边界 | 单 API 写入者、SQLite 文件库、API 工作台；本地演示不等于受保护部署 |
| 实现状态 | `implemented` / `partial` / `planned`，以追溯表为准 |
| 证据状态 | 指定运行的 `PENDING_RUN` / `PASS` / `FAIL`；现场目标 `TARGET_NOT_RUN` |
| 验收范围 | `scope=offline-code-baseline`、`field_readiness=NOT_VERIFIED` |

知识检索使用关键词、短语和字符序列相似度，没有向量数据库或语义 RAG。多主题 FAQ 可融合，但没有承诺一轮执行任意多个独立写服务。部门 Agent 是受约束处理器；云模型辅助理解和工具建议不拥有审批或提交权限。

academic 的“给我两张早餐券”“给房间发两张早餐券”“送餐券”等已知措辞进入 `需前台核验` 台账，不生成保洁单或扣券库存；保洁直接派券拒绝，已有送物改券保留原单并提示核验。仓库没有真实电子券签发接口，待核验不能解释为发券成功。

## 当前要求

编号稳定为 REQ-01…REQ-16，分别对应 AC-01…AC-16 和 EXP-01…EXP-16。前置、时序、数值、失败断言及完整测试名由详细规格和追溯表展开。

| ID | 行为要求 | 详细位置 / 验收 |
| --- | --- | --- |
| REQ-01 | 凭证绑定房号、入住、会话；过期、吊销、换客和旧在途任务失效 | [身份业务](design/business.md#身份与岗位) / AC-01 |
| REQ-02 | API 岗位与归属过滤；部门仅获必要字段，不能读私聊或办他部门工单 | [身份业务](design/business.md#身份与岗位) / AC-02 |
| REQ-03 | FAQ 与服务复合保留；学校未知政策转前台/会务确认 | [接待业务](design/business.md#接待与补槽) / AC-03 |
| REQ-04 | 多轮补槽、未知档案不臆造、已认证房间不能聊天改绑 | [接待业务](design/business.md#接待与补槽) / AC-04 |
| REQ-05 | 异步补充/取消/替换保持身份和版本；旧 worker 不提交，并发不超卖 | [运行时](design/architecture.md#任务执行与提交) / AC-05 |
| REQ-06 | 送物生命周期、口语目标定位；已派出变更须确认、旧变更拒绝 | [送物业务](design/business.md#送物生命周期) / AC-06 |
| REQ-07 | 部门提案先核验；风险规则不可降低，冲突待人工且无副作用 | [提案边界](design/architecture.md#部门提案边界) / AC-07 |
| REQ-08 | 单写入者、原子检查点、故障回滚且不返回成功承诺 | [存储](design/architecture.md#持久化与恢复) / AC-08 |
| REQ-09 | request_id 同载荷复用 run，重启/裁剪后仍幂等，不同载荷拒绝 | [运行时](design/architecture.md#任务执行与提交) / AC-09 |
| REQ-10 | 黑板/归档分层、分页历史、跨天重启恢复与会话隔离 | [存储](design/architecture.md#持久化与恢复) / AC-10 |
| REQ-11 | 学术延退和餐券人工核验；不自动免费承诺或绕过核验扣券库存 | [待遇业务](design/business.md#学术接待待遇与人工核验) / AC-11 |
| REQ-12 | 部门回执同步、夜审日期保护、已送达回收待检 | [回执业务](design/business.md#部门回执与夜审) / AC-12 |
| REQ-13 | 部署工作台仅调用 API，凭证/岗位决定 UI 和服务端能力 | [部署](design/architecture.md#部署与配置) / AC-13 |
| REQ-14 | 随机配置不覆盖，非 root、单 worker、持久卷，校验备份与恢复边界 | [架构部署边界](design/architecture.md#部署与配置) / [部署与运维子规格](design/deployment.md) / AC-14；现场部分 partial |
| REQ-15 | 固定 Harness/Few-Shot 范围明确，无对照不声称泛化或提升 | [评测](design/architecture.md#评测与证据) / AC-15 |
| REQ-16 | 原始测试 ID、日志、来源、JSON、状态/Trace 可复现，拒绝假通过 | [验收标准](acceptance_criteria.md) / AC-16 |

## 验收与交付

统一入口 `python scripts/verify_release.py --output-dir .artifacts/verification/<新目录>` 在规则模式、合成数据与独立临时库中执行。门槛：原始 [127 个测试 ID](baseline_test_ids.json) 均实际通过，完整不少于 127、核心 `tests.test_app` 不少于 40、零跳过，固定 H01…H20 全过、四指标均 1.0，三组黑板场景及必要状态/Trace 断言全真，16 项要求的测试在本次完整运行真实成功。

新增测试增加总量，不得替换旧测试凑数；文件契约和拒绝通过规则以 [验收标准](acceptance_criteria.md) 为准。本次 Task 1 餐券焦点日志位于已忽略的 `.artifacts/verification/task1-coupon/`，只证明其中方法和环境；完整发布证据由集成人员执行。旧 [2026-10-23 记录](release_verification_2026-10-23.md) 不证明新增代码通过，Docker/Compose 与现场演练仍按 [FIELD 方案](experiments.md) 验证。

本轮已执行的 169 项完整测试、40 项核心、20 条 Harness、21 项黑板断言及 120 个追溯方法的证据身份与边界见 [规格体系验收记录](specification_verification_2026-10-23.md)。

## 剩余范围的权威子规格

主规格中的当前实现、现场目标和未来扩展分别由以下文档展开；子规格完成表示契约、步骤和判定方法完整，不改变当前实现状态：

- [REQ-14 部署与运维](design/deployment.md)：配置、密钥、容器、单写入者、备份、恢复、升级、回滚和上线预检；现场卷权限、重启和恢复仍未验证。
- [FIELD-01～FIELD-06 现场验收](field_validation.md)：学校学术酒店现场执行协议、证据文件、量化门槛和回退流程；全部保持 `TARGET_NOT_RUN`。
- [FUT-01～FUT-06 未来集成](future_integrations.md)：PMS、SSO、电子券、批准知识/RAG、容量恢复和样例治理；全部保持 `planned`，不得当作当前 API。

本轮剩余规格的静态链接、字段和复核边界见[补全规格验收记录](remaining_spec_verification_2026-10-23.md)。

## 后续范围

| 编号 | 尚未交付或尚未现场验证的扩展 |
| --- | --- |
| FUT-01 | 真实 PMS 双向适配、回执与失败补偿 |
| FUT-02 | 学校 SSO、身份来源与组织账号生命周期 |
| FUT-03 | 真实电子券签发/核销/撤回与接待订单对账 |
| FUT-04 | 学校批准知识、语义检索与更新审核 |
| FUT-05 | 现场容量、告警、容灾、值班与恢复演练 |
| FUT-06 | 线上失败样本授权、脱敏、保留及独立前后对照 |

扩展实施前需更新规格、实验和追溯；配置和模拟代码不能当作外部系统接通证据。
