# 规格与证据体系工作计划

> **For agentic workers:** 使用 `superpowers:subagent-driven-development` 逐任务接续本计划；若子代理因实际服务额度无法执行，则记录阻断与接续方式，不将其报告为完成。

**Goal:** 建立可导航、可实施、可追溯且必须提供真实测试证据的学术酒店规格体系。

**Architecture:** 根 AGENTS.md 是地图，主规格链接业务／架构／实验／验收子文档。统一证据入口执行真实测试并检查结构化结果、状态、Trace、来源和原始测试身份。

**Tech Stack:** Python 3.12、unittest、JSON、Markdown、GitHub Actions；运行时既有 FastAPI／Streamlit／SQLite。

**Spec:** 本计划中 REQ-01…REQ-16 直接对应用户的地图、详细规格、实验、量化验收与测试证据要求；发布判据以 [验收标准](acceptance_criteria.md) 为准。

## Global Constraints

- 当前工作区已有用户授权任务的未提交成果；不得重置或丢弃，不移动到另一个空工作区重新开始。
- 业务背景为学校学术酒店；`academic` 的待遇必须人工核验；`standard` 的旧商业会员规则仅作回归。
- 原始 127 个测试 ID 必须实际执行并通过；完整数量不少于 127、核心不少于 40、跳过数为 0、Harness 固定 H01…H20 全过且四指标均为 1.0。
- 统一验收范围为 `offline-code-baseline`，现场状态为 `NOT_VERIFIED`；不能将本地代码测试说成酒店现场上线。
- 不写真实密钥、住客资料、逐字稿；原始日志放已忽略的 `.artifacts/`。
- 子代理只写分配文件，不自行派生代理，不提交或推送；集成与版本操作由主代理统一处理。

## Review Focus

1. 学术餐券的“给我”“给房间发”“送”等表达均不得绕过核验或预留库存。
2. 宿主环境／本地配置不能让离线测试接触真实数据，也不能把 academic 用例强行变成 standard。
3. 退出 0 但跳过、零用例、失败报告或缺文件时必须拒绝通过。
4. 原有测试被替换、Harness 案例缺失、坏 Trace 或旧 run_id 不能蒙混通过。
5. 当前规格与历史专题、实现状态与实测状态分别标识；链接与真实测试名称必须可核对。

状态：三个工作包、本机统一离线验收、提交后 GitHub CI 和 Docker 构建均完成并成功；最终整体子代理复核因服务容量限制未取得独立报告，已在执行台账中披露。范围：学校学术酒店单酒店部署基线；不将未联调的外部 PMS、学校 SSO 或生产容量写成已验收能力。实测来源与原始文件见 [本轮验收记录](specification_verification_2026-10-23.md)。

## 委派与文件边界

用户要求通过 Superpowers 委派。开始时插件尚未安装，先进行了原生子代理委派；随后用户安装了 Superpowers，主代理读取其任务委派、计划、台账与完成前验证技能，并实际运行自带的 `sdd-workspace` / `task-brief` 脚本接续本计划。初始三个子代理因账户额度中断，部分成果保留；完成状态必须以接续报告与真实验收为准。详细台账位于被 Git 忽略的 `.superpowers/sdd/work_plan/`。

| 工作包 | 独立子代理职责 | 文件所有权 | 交付与复核 |
| --- | --- | --- | --- |
| WP-01 · `recover_business_map` | 仓库地图、主规格、业务与架构；修复已复现的餐券路由绕过 | `AGENTS.md`、`spec/specification.md`、`spec/design/business.md`、`spec/design/architecture.md`；最小修复 `hotel_core.py`、`tests/test_deployment.py` | 4 方法 RED/GREEN、81 项焦点回归通过；`review_business_map` 独立批准 |
| WP-02 · `recover_experiments_traceability`；增量接续 `finish_mapping_integration` | 实验方案、验收矩阵、可追溯映射 | `spec/experiments.md`、`spec/acceptance_criteria.md`、`spec/traceability.json`、`spec/traceability.md` | 16 项映射，最终 120 个真实方法；统一完整运行中 120/120 实际通过 |
| WP-03 · `recover_evidence_gate` | 自动化证据与防假通过 | `scripts/verify_release.py`、`scripts/verify_blackboard.py`、`tests/test_release_verification.py` | 38 项焦点自测通过；独立复核发现并关闭未知状态行漏检，修复复核批准 |
| 集成 | 主代理协调与复核 | 本计划、README 索引、历史文档索引、CI、忽略配置、最终证据 | 核对跨文档一致性，运行真实验收，再提交公开材料 |

所有子代理共享工作区，只修改各自所有权文件，不重置他人改动，不提交、不推送。默认不改变业务行为；WP-01 在核对时实际复现了 `academic` 下“给房间发早餐券”走普通送物、未经核验预留餐券的缺口，主代理已将其最小修复及先红后绿回归纳入授权范围。其余规格与代码差异先记录现状和待办，不编造已实现状态。

## 统一要求编号

| 编号 | 要求 | 验收编号 |
| --- | --- | --- |
| REQ-01 | 入住凭证绑定房号、入住、会话，过期／吊销／换客后失效 | AC-01 |
| REQ-02 | API 岗位授权与最小字段，住客和部门的读写归属 | AC-02 |
| REQ-03 | FAQ 与服务复合保留，学校未知政策明确转人工 | AC-03 |
| REQ-04 | 多轮补槽与绑定房间限制 | AC-04 |
| REQ-05 | 异步补充／取消／替换，版本失效与受控并发 | AC-05 |
| REQ-06 | 送物生命周期、口语取消、配送后确认与过时变更拒绝 | AC-06 |
| REQ-07 | 部门提案核验、风险规则与冲突转人工 | AC-07 |
| REQ-08 | SQLite 单写入、原子检查点、失败回滚且不返回成功 | AC-08 |
| REQ-09 | request_id 幂等，重启与内存裁剪后不重复执行 | AC-09 |
| REQ-10 | 黑板与消息归档分层，跨天恢复、分页历史与会话隔离 | AC-10 |
| REQ-11 | 学术接待延退与早餐券须核验，不自动承诺免费权益 | AC-11 |
| REQ-12 | 部门回执、夜审日期保护、已送达回收待检 | AC-12 |
| REQ-13 | 部署工作台只调用 API，凭证和岗位决定页面与接口能力 | AC-13 |
| REQ-14 | 随机配置、非 root 镜像、持久化、备份与恢复边界 | AC-14 |
| REQ-15 | 固定 Harness 与 Few-Shot 范围，不伪造泛化或提升数据 | AC-15 |
| REQ-16 | 可复现测试证据、状态与 Trace 断言、防跳过／失败冒充通过 | AC-16 |

外部真实 PMS、学校 SSO、现场容量、语义 RAG、线上失败样本审批和数据治理等扩展使用 `FUT-xx`，不得挤入当前已实现清单。

## 可机器读取的映射契约

`spec/traceability.json`：顶层 `schema_version: 1`、`requirements` 数组、可选 `future_requirements` 数组。当前要求每项包含 `id`、`status`（implemented / partial / planned）、`acceptance_id`、`tests`（真实 unittest 完整点名）、`experiment_ids`、`evidence`（输出文件名）。必须完整覆盖 REQ-01 至 REQ-16，对应 AC-01 至 AC-16。未来要求不冒充已实现要求。

自动证据入口：`python scripts/verify_release.py --output-dir .artifacts/verification/<新目录>`。输出新建目录，不复用旧结果；默认规则模式、独立临时库、不发送模型请求。至少保存 `manifest.json`、`full-tests.log`、`core-tests.log`、`harness.log`、`harness.json`、`blackboard.log`、`blackboard.json`。允许额外证据，但文档与脚本必须一致。

基线：[ec6331f 原始 127 个测试方法](baseline_test_ids.json)、40 项核心测试、固定 20 条 Harness；新增证据测试会增加完整测试数。门槛为完整套件不少于 127 项且全通过，并逐个保留基线测试的真实执行与通过记录；核心不少于 40 项且全通过、跳过数为 0、Harness 恰为 H01…H20 这 20 个唯一案例且全部通过、四项指标均为 1.0、必要黑板断言为真。不得删除旧用例后用占位测试凑数量，不得把固定用例通过率描述为真实模型准确率。未来扩充 Harness 必须版本化审阅基线，不能在本轮验收里悄悄改变分母。

注意：现有 `python -m harness.eval_runner` 即使出现 FAIL 仍可能返回 0，自动证据入口必须检查结构化报告，不能只相信 CLI 退出码。性能、真实部署和外部接口实验必须注明统计口径、环境与执行状态。

## 完成条件

1. 地图、主规格、子规格、验收矩阵和追溯 JSON 互相可达，历史规格明确标识适用范围。
2. 子代理分别回报文件清单、发现的问题、测试命令和真实结果；主代理交叉复核。
3. 执行统一证据入口，保留原始日志与清单；失败必须解释并修复或明确未通过。
4. 公开仓库不包含密钥、真实住客资料、逐字稿或私有面试材料。
5. 记录最终 Git 提交与 CI 验收结果，禁止用过期日志证明新实现。

## 实施裁定记录

| 裁定 | 依据 | 若判断有误的代价 |
| --- | --- | --- |
| 沿用用户指定的当前目录，不中途另建空工作区 | 已有共享未提交成果，用户要求在此目录创建；不丢失已有工作 | 需回退可逆的本地提交或重新隔离整理 |
| 按已有授权连续执行分工，不再索要通用计划确认 | 用户明确要求不同子代理完成；不是重新选择执行方式 | 需要撤回相应可逆文件修改，不能把此裁定扩展为新的外部发布授权 |
| 服务限额或容量故障后携带报告接续，避免重复启动已完成任务 | 原代理留下部分成果；使用明确模型路由恢复，若可用路径均失败则内联完成可做部分并披露独立复核限制 | 多一次调用与人工接续成本；不允许据此伪造代理完成或测试结果 |
| 本轮 Harness 分母固定为 H01…H20，拒绝额外案例 | 当前验收已明确恰 20 条；数量与指标必须可对照 | 日后扩充数据集须另审版本化基线，增加一次规格与证据更新 |

## Task 1: 接续业务修复与仓库地图

文件所有权：WP-01 列出的 AGENTS.md、spec/specification.md、spec/design/business.md、spec/design/architecture.md、hotel_core.py、tests/test_deployment.py。读取当前部分成果，完成缺项，不重做已经正确的部分。

- [x] 核对 `academic` 下“给房间发两张早餐券”走普通送物的既有复现；新增测试先失败，保留 RED 原始日志。
- [x] 最小修复覆盖入口路由与直接部门处理守卫：未核验餐券不得自动建单扣库存；正常餐券请求进入前台核验；不扩大到真实电子券发行。
- [x] 保存 GREEN 焦点测试，确认 standard 核心回归保持；将真实测试全名交给追溯任务。
- [x] 重写地图与主规格，创建详细业务／架构子文档，链接其余规格；使用 REQ-01…REQ-16，不编造实现或性能。
- [x] 报告写入本计划 SDD 工作区 `task-1-report.md`，包含文件、RED/GREEN 命令和结果、已知边界，不提交推送；独立任务复核已批准，完整集成验收待执行。

## Task 2: 接续实验与验收追溯

文件所有权：spec/experiments.md、spec/acceptance_criteria.md、spec/traceability.json、spec/traceability.md。已有实验与验收草稿，先检查再补全。

- [x] 为 REQ-01…REQ-16、AC-01…AC-16、EXP-01…EXP-16 明确环境、夹具、动作、时序、数值、状态／Trace 断言、原始证据、失败判定和清理。
- [x] 创建完整 JSON 和 Markdown 映射：初版 113 个真实方法隔离加载通过；新增 7 个工具反例后最终 120 个真实 ID，REQ-16 映射全部 38 项工具测试；实际执行仍须最终完整证据验证，不能用加载或 AST 存在替代。
- [x] 读取 Task 1 的修复与真实测试证据，关闭 REQ-11 已知表达及直调绕过的实现缺口；不把“未运行”写成 PASS。
- [x] 现场／外部系统实验为 FIELD-01…FIELD-06、TARGET_NOT_RUN，参数与上线阻断条件可对照；REQ-14 保留现场部分边界。
- [x] 与证据脚本的文件／字段契约核对，记录 CI 原始证据保留 14 天和本地日志默认不入 Git。
- [x] 报告写入本计划 SDD 工作区 `task-2-report.md`；独立任务复核批准，不提交推送。

## Task 3: 接续统一证据工具

文件所有权：scripts/verify_release.py、scripts/verify_blackboard.py、tests/test_release_verification.py。已有脚本和负向测试，不能将中断时的旧失败日志当成完成。

- [x] 阅读既有代码与 `.artifacts/verification/wp03-self-*`，处理已知自测失败，保持只影响测试子进程的安全隔离。
- [x] 清单记录实际隔离子进程依赖版本（streamlit、fastapi、httpx、openai、typing_extensions），缺失如实写 null，不导入业务或输出密钥。
- [x] 实现完整／核心真实 CLI、原始测试身份、Harness 恰 20 行、黑板 3 场景／21 断言和 16 项追溯的验证门槛；实际完整执行待集成。
- [x] 拒绝复用、错误 run_id、缺失 JSON、零测试、FAIL、跳过、坏 Trace、替换原始 ID 与假映射；失败留证并返回非零。
- [x] 38 项焦点测试通过，保留原 31 项；补充未知状态行先红后绿，独立修复复核批准；完整入口仍由主代理运行。
- [x] 报告及修复记录写入本计划 SDD 工作区 `task-3-report.md`，不提交推送。
