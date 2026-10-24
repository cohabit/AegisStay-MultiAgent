# 学术酒店验收标准与证据判定

本规范适用于单酒店、单 API 写入者、SQLite 持久化和受凭证保护的学校学术酒店工作台。要求编号以 [工作计划](work_plan.md) 为准；实施步骤见 [实验方案](experiments.md)，完整测试点名见 [可追溯表](traceability.md) 和 [机器映射](traceability.json)。接口、模型与业务边界见 [主规格](specification.md)。

## 1. 验收对象与执行状态

本文定义验收门槛，不嵌入持续变化的成绩。每次执行前为 `PENDING_RUN`，单次运行结论只认对应 `manifest.json` 和独立验收记录；完成后无需将本规范中的执行前状态改写为永久PASS。REQ-14 的部署门槛见[部署与运维子规格](design/deployment.md)，FIELD-01～06 的现场门槛见[现场验收协议](field_validation.md)，FUT-01～06 的未来门槛见[未来集成规格](future_integrations.md)。

`academic` 是学校接待政策基线。`standard` 是商业会员、固定早餐时间等历史演示夹具，只用于兼容性回归。两种 profile 的结果分别标识，不能用 `standard` 的黑金自动延退、已发放餐券或固定设施信息证明学术住客享有相同权益。

| 证据层 | 当前状态 | 能证明的范围 |
| --- | --- | --- |
| 2026-10-23 已记录回归 | `HISTORICAL_RECORDED`：[原记录](release_verification_2026-10-23.md) 记录完整 127 项、核心 40 项及 Harness 20/20 | 当时的代码与本机夹具；该 Markdown 不是本次新运行的原始日志 |
| 每次统一证据包 | 执行前 `PENDING_RUN`；完成后据对应 `manifest.json` 状态 | 只有该次来源标识、原始日志、结构化结果和断言全部通过后才能在其运行记录判为 `PASS` |
| Docker/Compose、真实 PMS、学校 SSO、酒店现场容量及恢复演练 | `TARGET_NOT_RUN` | 只有方案与目标，没有实跑结论；见实验方案 FIELD 系列 |

REQ-11 的已知送物措辞绕过已修复：学术模式已知餐券表达进入核验账本，保洁直调拒绝，已有送物改券保留原单及库存。Task 1 在 `.artifacts/verification/task1-coupon/` 保存4方法/12个subTest失败的 `red.log`、同4方法通过的 `green.log`、81项零跳过的 `regression.log`。实现状态为 `implemented`，完整证据执行前为 `PENDING_RUN`、完成后以其manifest为准；这些焦点结果不证明所有自然语言、迁移前脏数据、真实电子券发行或学校现场已验收。

`implemented` / `partial` / `planned` 描述代码覆盖状态；`PASS` / `FAIL` / `PENDING_RUN` / `TARGET_NOT_RUN` 描述指定环境、代码来源与运行的证据状态，两者不得互相替代。统一入口清单范围固定为 `scope=offline-code-baseline`，`field_readiness=NOT_VERIFIED`；离线通过不会把现场状态改为已验收。

## 2. 自动证据的硬门槛

在仓库根目录用项目 Python 执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_release.py --output-dir .artifacts/verification/<新目录>
```

`<新目录>` 必须替换为未存在的运行目录。入口的退出码、清单与各结构化报告要同时满足以下门槛；任一项缺失、超时、失败、损坏或出现跳过，整次验证为 `FAIL`。

| 检查 | 硬门槛 | 统计与反蒙混判断 |
| --- | --- | --- |
| 完整 unittest | `tests_run >= 127`，全部通过，`failures = errors = skipped = expected_failures = unexpected_successes = 0` | [原始127个ID](baseline_test_ids.json) 每个均须实际执行并通过；新增测试不能替换原始ID凑数，不得删用例、改发现模式或降低下限；CLI 的 testsRun 对应 JSON 的 tests_run |
| 核心 unittest | `tests_run >= 40`，全部通过，零跳过或预期失败 | 单独执行 `tests.test_app`，原始40个核心ID均须实际通过，不能用完整套件总数替代 |
| Harness 案例集合 | 固定 ID 恰为 H01…H20，20 个唯一 ID，无缺失、无重复；`total = passed = 20`；每行 `status = PASS` | 须检查 JSON 逐行状态与基线集合，不能只读打印摘要或进程退出码 |
| Harness 四项指标 | `intent_accuracy = slot_filling_rate = ticket_precision = ticket_recall = 1.0` | 四个独立字段都须为有限数值 1.0，不能把“100%”字符串或单项指标当全部通过 |
| 黑板与 Trace | 恰三个命名场景、21项状态和 Trace 断言全部为真 | `compound_faq_checkout`、`angry_escalation`、`manual_checkout_approval`；必须保存实际快照、审批前后状态与 Trace，并从快照重算，不能仅信 assertions=true |
| 追溯完整性 | REQ-01…REQ-16 各出现一次，对应 AC-01…AC-16；每项至少一个可加载且在本次完整套件成功执行的真实测试 | 测试字符串存在还不够：模块、类与方法必须加载为测试，不能映射到 `_FailedTest` 或空套件 |
| 来源与新鲜度 | 全部步骤、报告与清单属于同一新 `run_id`；保存解释器、实际依赖版本、平台、UTC 时间、命令、退出码、Git 状态与源文件 SHA-256 | 每个子进程安全回执核对run_id/UTC/设置，manifest记录校验结果；源码起止哈希须一致。Git有未提交改动时commit单独不能标识工作区；日志复制、改摘要和复用旧结果均无效 |

基线数据只有 20 条，四项指标的分母遵循 `harness/eval_runner.py`：意图命中数 / 全部用例数；完整送物槽位数 / 应建单数；预期建单且部门正确的实际建单数 / 实际建单数；同一正确建单数 / 应建单数。每个病例的 PASS 还要求意图、槽位、是否建单和部门同时满足条件。它们是固定规则回归指标，不是云端模型准确率、自然语言总体泛化能力或线上服务质量。

现有 `python -m harness.eval_runner` 只打印报告，出现 FAIL 仍可能退出 0。验收必须读取并校验 `harness.json`；终端显示成功、HTTP 200、出现回复文字或网页可访问均不能单独证明业务成功。尤其任务写入失败可由 `/api/chat` 返回 HTTP 200 和 `run_status=failed`，此时必须判失败；员工写入失败与不可就绪返回 503。

本次统一输出契约：`manifest.json`、`full-tests.log`、`full-tests.json`、`core-tests.log`、`core-tests.json`、`harness.log`、`harness-structured.log`、`harness.json`、`blackboard.log`、`blackboard.json`、`traceability.json`、`validation.log`。这些名称均指新证据目录内的文件，来源映射文件仍是 `spec/traceability.json`。本地原始目录`.artifacts/`默认不入Git；CI当前保留原始证据14天，报告须给出对应运行和commit，不把历史附件当永久证据。不得在公开证据中保存实际访问令牌、真实住客姓名、入住订单、聊天逐字稿或完整环境变量。

## 2.1 分层质量扩展门槛

固定 H01–H20 仍是兼容性硬门槛，但 20 条样本不能单独代表产品质量。产品质量证据必须额外运行 [分层质量验收子规格](quality_evaluation.md) 的 E01–E40：五个业务组各 8 条、扩展 40/40 通过，并保存每组分母、通过率、Wilson 95% 区间、意图/槽位/派单指标和 P95。本扩展集增加覆盖，不替换 H01–H20，也不改变统一发布验证器的 H01–H20 契约。

扩展集的最低机械门槛为：意图准确率 `>=95%`、槽位填充率 `>=95%`、派单精准度 `>=98%`、派单召回率 `>=95%`；`policy_safety_scope` 的未审核票券派单、多物品未澄清副作用和越权写入均必须为 0。只展示总通过数、平均耗时或 UI 截图不能判定扩展门槛通过。

## 2.2 界面视觉与空间仿真门槛

界面范围按[界面视觉规格](ui_visual_quality.md)单独验收。运行 `.\.venv\Scripts\python.exe -B -X utf8 -m unittest -v tests.test_visual_contract tests.test_streamlit`，必须零失败、零错误、零跳过，并满足：三主题正文/辅助文字/主按钮对比度均至少 4.5:1；默认住客端只含一个可见输入框，运营内容通过工作区选择器隔离；首条消息后历史面板固定为 300px（小屏按视口收缩）且在面板内滚动，输入框位于其下方，处理中提示只出现一处；住客首页有经过虚化的校园背景，运营中枢使用紧凑头部而不加载大面积装饰照片；房态矩阵宽桌面 6 列、中等视口 3 列、小屏 2 列并补齐占位格；空间仿真默认显示全楼、选楼层后显示单层，并同时包含房间、电梯、楼梯、服务核心且读取同一 PMS 快照。截图只作人工布局证据，不能替代自动颜色计算、源代码契约和 AppTest。

## 2.3 公开身份与提示注入安全门槛

安全范围按[住客端公开身份与提示注入安全规格](security_prompt_injection.md)验收。当前 40 条安全集合（最低 24 条）中，配置模型/供应商/真实密钥/提示内容泄露必须为 0，探询触发云调用和业务副作用均为 0，探询后正常酒店请求恢复成功率为 100%。另验证异步订单不中断、攻击房号不绑定、request_id 重试幂等以及住客 chat/task API 的模型字段投影。安全测试只用合成数据；未知攻击变体仍须遵守鉴权和业务核验，不能把关键词覆盖率宣称为通用安全保证。

## 3. AC-01 至 AC-16 逐项判定

每项必须同时满足本表业务断言、对应 EXP 的时序和共同证据门槛。表中的测试是最低证据入口，完整映射列在 JSON。对未覆盖分支，使用 `partial` 并明确阻断范围，不能把“至少一条测试通过”解释成全要求已通过。

| 验收 | 要求 / 实验 | 可机械判定的业务结果 | 真实测试入口与状态 |
| --- | --- | --- | --- |
| AC-01 | REQ-01 / EXP-01 | 签发 201；跨会话、跨房及运营接口 403；过期、吊销、stay 变化和换客后的旧凭证 401；吊销中的旧 worker 不得建单，毛巾仍 24 | `tests.test_deployment.DeploymentTests.test_guest_is_bound_to_own_session_room_and_stay`；另含 expiry、turnover、revoked inflight 反例 |
| AC-02 | REQ-02 / EXP-02 | 无凭证 401；工程办理保洁工单 403；工程工单视图中保洁工单数为 0，房态不含 `guest_name` / `stay_id`，私聊及历史 403；健康检查 200 | `tests.test_deployment.DeploymentTests.test_department_cannot_complete_other_department_ticket` |
| AC-03 | REQ-03 / EXP-03 | 同一句含 FAQ 与业务时两条信息均保留；学术会务回复要求核验、延退未审时 PMS 仍 12:00；未知会议与设施时间不编造；纯 FAQ 不建单 | `tests.test_app.MultiAgentFlowTests.test_compound_intent_faq_and_late_checkout_with_room`；`tests.test_deployment.DeploymentTests.test_academic_meeting_and_checkout_need_real_confirmation`。模板未知政策的全域覆盖仍须 FIELD-04 审核 |
| AC-04 | REQ-04 / EXP-04 | 未绑定演示流程先问房号、0 工单；补有效房号后仅 1 工单；未知 9999 不绑定；模型给出的未提及/无效房号不能覆盖输入；鉴权住客显式跨房 403 | `tests.test_app.MultiAgentFlowTests.test_second_turn_completes_pending_request`；`tests.test_app.MultiAgentFlowTests.test_cloud_room_requires_pms_and_mentioned_number` |
| AC-05 | REQ-05 / EXP-05 | Event 确认旧任务运行后再替换，同 task 的 version 为 2/3；旧版本 superseded；释放旧 worker 后仍仅最新 1 单；取消后 0 单、毛巾 24；独立会话 FAQ 在旧 worker 阻塞时完成；竞争 24 条毛巾的两个 20 条请求只预留 `[0,20]`、剩 4 | `tests.test_task_runtime.TaskRuntimeTests.test_replace_running_delivery_commits_only_latest_version`；`tests.test_task_runtime.TaskRuntimeTests.test_concurrent_guests_cannot_oversell_shared_inventory`。这是受控竞态正确性，未证明生产容量 |
| AC-06 | REQ-06 / EXP-06 | 未派送改物复用 ticket_id；口语“水不用了”只取消矿泉水，毛巾保留；多单“不要了”先澄清；配送中改物在确认前仍旧物及库存；重复确认不二次扣库；旧 change_id 与待变更结单 409 | `tests.test_delivery_language.DeliveryLanguageTests.test_screenshot_followup_cancels_only_water_and_preserves_task_id`；`tests.test_deployment.DeploymentTests.test_stale_change_confirmation_and_pending_completion_are_rejected` |
| AC-07 | REQ-07 / EXP-07 | 注入错物、错房、虚构库存或降级安全优先级时 `needs_review`，不派单、不扣库，冲突可见；人工“已核验”不直接生成被拒工单；严重客诉为经理/高/红色 | `tests.test_task_runtime.TaskRuntimeTests.test_conflicting_child_item_is_rejected_without_stock_commit`；`tests.test_api.FastAPISessionTests.test_conflicting_child_is_visible_as_review_without_order` |
| AC-08 | REQ-08 / EXP-08 | 第二写入者抛 `StorageConflict`；注入 task.commit 故障后 `run_status=failed`、无成功承诺、0 工单、库存 24，重开库仍 0 工单；SQL 会话写失败时业务整体不变；员工操作 503、原 delivery_status 保留、`/ready` 503 | `tests.test_deployment.DeploymentTests.test_write_failure_rolls_back_stock_and_does_not_ack_success`；`tests.test_deployment.DeploymentTests.test_atomic_checkpoint_rolls_back_if_session_write_fails` |
| AC-09 | REQ-09 / EXP-09 | 同 session + request_id + 相同载荷返回原 run_id；重启和内存裁剪后仍 1 单、毛巾 22；同 ID 改载荷 409；不得只在进程内字典验证一次 | `tests.test_deployment.DeploymentTests.test_request_retry_survives_restart_without_second_order`；`tests.test_deployment.DeploymentTests.test_pruned_run_keeps_durable_idempotency_record` |
| AC-10 | REQ-10 / EXP-10 | 8 轮对话在线窗口 `<16` 条；重启归档返回 16 条，before 游标分页返回前 8 条；他人历史不可读；业务日已落库后新的环境日期不覆盖；已存 running 任务恢复为 interrupted | `tests.test_deployment.DeploymentTests.test_history_survives_window_compaction_and_can_be_paged`；`tests.test_persistence.PersistenceTests.test_restart_marks_inflight_task_as_interrupted` |
| AC-11 | REQ-11 / EXP-11 | 学术延退和已知餐券表达须核验；审批前12:00、当前申请获批后14:00，费用按接待订单；未核验券status=需前台核验并绑定session/stay，Trace含issue_breakfast_vouchers、0保洁单、券库存40；保洁直调拒绝，毛巾改券保留原单且毛巾22；旧入住申请不能获批 | `tests.test_deployment.DeploymentTests.test_academic_coupon_delivery_wording_requires_review_without_stock_reservation`；`tests.test_deployment.DeploymentTests.test_checkout_approval_is_bound_to_current_occupant_request`；另含直调/改单及旧权益反例，完整7方法见映射。已知表达保护implemented；本次统一证据PENDING_RUN |
| AC-12 | REQ-12 / EXP-12 | 同 expected_date 夜审仅第一次 200、第二次 409；重启保留新日期，旧 worker 不覆盖夜审；已送达回收未 returned=True 不可确认；确认后毛巾可用量仍 22、待检量 +2；借用品记录与通知同步 | `tests.test_deployment.DeploymentTests.test_night_audit_cannot_be_repeated_with_stale_date`；`tests.test_task_runtime.TaskRuntimeTests.test_delivered_item_cannot_be_restocked_without_return_confirmation` |
| AC-13 | REQ-13 / EXP-13 | API 模式无凭证页面不创建业务会话、无 chat_input；住客 1 个 chat_input、无签发按钮或运营标签；员工可签发且页面 session_state 无 agent_runtime；AppTest 异常列表为空；API 层仍独立执行授权 | `tests.test_deployment.DeployedUIBoundaryTests.test_staff_ui_shares_api_without_a_second_writer`；另含无凭证和住客视图测试。真实分离进程现场验收仍 TARGET_NOT_RUN |
| AC-14 | REQ-14 / EXP-14 | 初始化拒绝覆盖既有配置；弱凭据、非法岗位和缺鉴权/持久化的部署配置拒绝启动；备份完整性通过且重开含最新1单、毛巾22；Dockerfile有非root用户、Compose单worker及命名卷；MODEL-CONN-01 要求连续3次 `/v1/models` HTTP 200、选定模型命中、每次≤12秒、成功率100%，失败不得静默显示云端在线 | `tests.test_deployment.DeploymentTests.test_validated_backup_includes_latest_committed_order`；`tests.test_deployment.DeploymentTests.test_secret_generation_never_overwrites_deployment`；`tests.test_model_connection.ModelConnectionTests` 四个连接回归；**partial：当前镜像及Compose只做静态审阅，现场连接/聊天 canary、FIELD-01/03 实跑前不能报告容器或生产模型验收通过** |
| AC-15 | REQ-15 / EXP-15 | H01…H20 完整、20/20 和四项 1.0；预置难例与 Few-Shot 检索测试分别通过；演进报告 `evolution_delta=null`，范围标注无前后对照；不输出模型提升百分比 | `tests.test_app.MultiAgentFlowTests.test_harness_dataset_regression_metrics`；`tests.test_evolution.SelfEvolutionAndMemoryTests.test_evolution_delta_runner_cycle` |
| AC-16 | REQ-16 / EXP-16 | 新目录可复现运行；故意制造零用例、跳过、替换原始ID、退出0但结果失败、缺JSON、Harness缺ID/额外PASS行/附加H21 SKIP/H100 ERROR/重复H01 SKIP/FAIL、坏Trace、假测试映射时均拒绝通过；生成本次日志/JSON/来源清单 | `tests.test_release_verification.ReleaseVerificationTests.test_unittest_exit_zero_cannot_mask_failed_result`；`tests.test_release_verification.ReleaseVerificationTests.test_harness_cli_unknown_status_rows_are_not_ignored`；`tests.test_release_verification.ReleaseVerificationTests.test_traceability_rejects_loadable_but_unexecuted_test`；其余逐方法见 [机器映射](traceability.json)，统一入口集成执行前为PENDING_RUN |

## 4. 结果汇报与拒收规则

可接受的报告包含：本次证据目录、清单 `run_id` 与状态、Git commit 与工作区是否有改动、完整/核心测试实际数量、失败/错误/跳过数、Harness ID 集合及四项实值、黑板三个场景结果、REQ-11/REQ-14 等未关闭问题与现场未执行项目。新增代码后必须重跑并生成新目录，不能把修改前结果拼接到修改后的版本。

以下任一情况直接拒收：

1. 只提供截图、自然语言“已通过”、截断摘要或复制历史 `OK`；缺任一必要原始日志或结构化结果。
2. 完整测试数量少于 127、核心少于 40、任意跳过、把 `expectedFailure` 或 `unexpectedSuccess` 当成功、伪造测试名称。
3. Harness 仅检查返回码或平均指标，隐藏 FAIL 行、删除 Hxx、重复 ID 凑数，或把规则回归称为模型泛化/自进化提升。
4. 只跑顺序正常对话就声称并发安全；必须让旧任务真实进入阻塞区、触发替换/撤销/竞争后再释放，并断言最终库存和工单。
5. 仅重载网页、仅 `/health` 200 或 `ready` 200 就声称数据恢复；必须关闭原 manager/进程，重开同一个测试库，并比对已提交的业务、归档、幂等记录与日期。
6. 只断言回复措辞而不验库存、工单、审批和 Trace；HTTP 200 但 `run_status=failed/needs_review` 被汇报为已办成。
7. 用本地模拟 PMS、个人令牌或静态 Docker 文件冒充外部 PMS、学校 SSO、Compose 实跑或生产容量证据。
8. 已知反例未关闭却标整项 PASS，或把现场目标阈值写成既有测量值。

## 5. 现场目标与上线阻断

FIELD-01…FIELD-06 的参数、采样方法、步骤、原始证据与清理见 [实验方案](experiments.md#现场实验target_not_run)。目标阈值可在执行前由酒店负责人按资源和业务确认；变更必须保留旧值、理由、批准记录与新实验版本，不能在失败后悄悄调低门槛。

| 条件 | 当前证据边界 | 何时阻断 |
| --- | --- | --- |
| 学术权益核验 | REQ-11已知表达及直调绕过已修复，有焦点红绿证据；本次完整证据PENDING_RUN | 本次离线门槛未通过、FIELD-04学校政策未审或出现新权益反例时，academic上线阻断 |
| Compose 与非 root 持久化 | 静态配置及本地库测试；未实跑 | 采用容器交付时，FIELD-01 未过则阻断 |
| 负载与恢复 | 受控竞态及本地正常 close/reopen；无真实断电结论 | 承诺酒店多人接待或恢复 SLA 前，FIELD-02/03 未过则阻断 |
| 学校政策与真实身份/PMS | 初始知识模板、手工入住核验和本地模拟 | 上线前政策审核 FIELD-04 必须通过；宣称/启用 PMS 或 SSO 联动前 FIELD-05 必须通过 |
| 云端模型、线上自进化和语义检索 | 默认离线规则、字符相似度 Few-Shot；无独立泛化/提升数据 | 启用或宣传这些扩展前，FIELD-06 独立评测、数据审批和回退验证必须通过 |

## 附录：历史 standard 八用例

旧 TC-01…TC-08 仅用于解释固定历史夹具，不取代当前 AC，也不赋予学术住客任何商业权益。对应自动化仍保留在 `tests.test_app.MultiAgentFlowTests`；执行环境必须 `AEGIS_SITE_PROFILE=standard`。

| 历史编号 | 输入或操作 | standard 夹具预期 | 当前定位 |
| --- | --- | --- | --- |
| TC-01 | 1128 早餐 FAQ + 黑金两点延退 | 答 07:00–10:00；PMS 14:00 | 复合意图及旧权益兼容 |
| TC-02 | 同上但缺房号 | 答 FAQ 并询问房号 | 多轮 pending 兼容 |
| TC-03 | 先两条毛巾，再 1208 | 第一次 0 单，补槽后 1 个保洁单 | 补槽基线 |
| TC-04 | 1208 空调坏了、很生气 | 工程部 / 高 | 具体报修保留工程路线 |
| TC-05 | 1128 投诉很吵、非常生气 | 大堂经理 / 高 / 红色 | 客诉升级基线 |
| TC-06 | 808 两点退房 | 需前台审核，PMS 12:00 | 人工审批前置状态 |
| TC-07 | 前台批准 808 至 14:00 | 已批准、PMS 14:00、人工审批 Trace | 审批闭环历史基线 |
| TC-08 | 写诗/天气等范围外输入 | 不建单并引导酒店服务范围 | 本地规则 soft-pivot 兼容 |

旧版“FAQ ≥95%”“槽位 ≥98%”“单轮 ≤20 ms”“崩溃率 0%”没有独立分母、分布或目标硬件证据，不作为已测结果。若需要这些承诺，须先定义独立样本、统计方法和环境，再按 FIELD 系列执行。
