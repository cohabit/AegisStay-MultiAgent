# 可复现实验与现场验收方案

本文件回答“如何执行、看什么原始数据、什么情况判失败、如何恢复”。编号 EXP-01…EXP-16 与 [工作计划](work_plan.md) 的 REQ-01…REQ-16、[验收标准](acceptance_criteria.md) 的 AC-01…AC-16 一一对应；测试的完整点名以 [traceability.json](traceability.json) 为机器输入。[主规格](specification.md) 定义业务范围，[追溯说明](traceability.md) 解释当前覆盖和缺口。[分层质量验收子规格](quality_evaluation.md)补充 EXP-15-Q 的 40 条扩展样本。REQ-14 细节见[部署与运维子规格](design/deployment.md)，FIELD-01～06 的现场执行协议见[现场验收协议](field_validation.md)，FUT-01～06 的外部契约见[未来集成规格](future_integrations.md)。

## 执行状态与结果口径

本文定义方案，不嵌入持续变化的成绩。每次执行前为 `PENDING_RUN`；完成后的单次结论只认其新目录 `manifest.json` 和对应独立验收记录，下表不代替运行记录。

| 层级 | 状态 | 已有内容与限制 |
| --- | --- | --- |
| 历史本机回归 | `HISTORICAL_RECORDED` | [2026-10-23 记录](release_verification_2026-10-23.md)：127 项完整、40 项核心、20 条固定 Harness；不是本次代码的原始新运行证据 |
| EXP-01…EXP-16 统一证据 | 执行前 `PENDING_RUN`；完成后据对应 manifest | 方案与真实测试映射就绪后，集成人员执行统一入口；新增测试使完整数量增加，最低门槛仍为 127；只有新目录清单判定可作为该次结论 |
| FIELD-01…FIELD-06 | `TARGET_NOT_RUN` | 尚未执行的目标；不得以静态配置、本机模拟或历史截图替代 |

统一入口只验 `offline-code-baseline`。即使离线 PASS，`field_readiness` 仍为 `NOT_VERIFIED`；现场门槛另行验收。规则回归的 20/20 不支持“模型准确率 100%”“学习后提升 X%”或酒店生产容量等结论。

## 共同环境、夹具、命令与证据

### ENV-R：完全离线的本机回归

从仓库根目录执行。使用项目 `.venv` 的解释器，按 `requirements.txt` 安装依赖（当前 Streamlit `>=1.66,<2`；仓库未承诺锁文件）。历史环境是 Windows、Python 3.12.4、Streamlit 1.66.0、FastAPI 0.141.1；本次实际解释器及至少 streamlit、fastapi、httpx、openai 的分发版本由 `manifest.json` 记录。现有 `GuestInteractionTests` 在 Streamlit 低于 1.57 时会跳过；1.57 是该旧测试的跳过条件，不是当前项目依赖下限。部署 AppTest 在方法内直接导入，不用同一版本跳过装饰器；导入失败应算错误。任何 skip 或导入错误都使本次验收失败。不要把其他机器的历史耗时当成本机响应目标。

```powershell
$py = (Resolve-Path '.venv\Scripts\python.exe').Path
$env:AEGIS_FORCE_RULES = '1'
$env:AEGIS_AUTH_ENABLED = '0'
$env:AEGIS_DEPLOYED = '0'
$env:AEGIS_DB_PATH = ''
$env:AEGIS_SITE_PROFILE = 'standard'
Remove-Item Env:AEGIS_API_URL -ErrorAction SilentlyContinue
Remove-Item Env:AEGIS_BUSINESS_DATE -ErrorAction SilentlyContinue
Remove-Item Env:AEGIS_PMS_FILE -ErrorAction SilentlyContinue
Remove-Item Env:AEGIS_KNOWLEDGE_FILE -ErrorAction SilentlyContinue
$proofDir = Join-Path '.artifacts\verification' ('release-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
& $py -B -X utf8 scripts/verify_release.py --output-dir $proofDir
if ($LASTEXITCODE -ne 0) { throw '本次离线验收失败；保留证据目录排查' }
```

入口须再次规范化子进程环境，不能依赖调用者记住设置。子进程启动守卫在导入业务前隐藏本地 `.env` / `config.json`、阻断外网和临时目录外 SQLite；父进程校验守卫回执的 run_id、生成 UTC、状态与安全设置，并在 manifest 步骤中记录校验结果。回执在临时目录内，清理后不属于交付文件；来源 SHA-256 由 manifest 单独保存，不能把环境回执当源码哈希。强制规则且不加 `--cloud`；不读取真实数据库，不引用学校/酒店业务文件，不输出 `.env` 或全部环境变量。使用仓库合成 PMS 与知识模板；`tests.test_deployment` 和 `tests.test_persistence` 在 `TemporaryDirectory` 中创建独立文件库。测试凭据仅由夹具提供，不能复用部署令牌。

### ENV-A：鉴权与学术接待夹具

ENV-R 是外层发现环境；`DeploymentTests.setUp` 在用例内部临时切换 `AEGIS_AUTH_ENABLED=1`、`AEGIS_SITE_PROFILE=academic`、`AEGIS_DEPLOYED=0`，配置测试管理员、签名与工程岗位凭据，用独立 SQLite 替换 `server.session_manager` 并创建 `TestClient`。`DeployedUIBoundaryTests` 还将 `HotelApi` 的 HTTP 请求路由到该 TestClient。不要在终端全局改成 academic 再跑整个旧核心基线，否则会混淆两套政策。

共同初始状态为合成在住房 1128 / 808 / 1106、退房 12:00、毛巾 24、浴巾 24、矿泉水 36、雨伞 10、吹风机 6；各例重建 manager，不共享测试顺序。输入、数量、room、session、request_id 和 profile 固定；随机 ticket/run ID 比较身份一致性而不比较写死的随机值。时间条件用 patch 或 `expected_date` 控制，不靠真实等待过期。

### 证据目录和运行规则

每次使用新目录 `.artifacts/verification/<run>/`，其中 `<run>` 是本次唯一目录名。至少保存下表文件，禁止复用旧文件或人工重写 PASS：

| 文件 | 原始证据内容 |
| --- | --- |
| `manifest.json` | run_id、开始/结束 UTC、实际解释器/平台、命令/退出码/耗时、步骤状态、Git commit/dirty/改动清单、源文件 SHA-256、安全开关；scope 为 offline-code-baseline、field_readiness 为 NOT_VERIFIED |
| `full-tests.log` / `full-tests.json` | 完整 unittest 原始 stdout/stderr、真实收集的测试 ID、执行计数和失败/错误/跳过计数；阈值 >=127 且全部成功、零跳过；[原始 127 个 ID](baseline_test_ids.json) 必须逐个保留并执行，新增 ID 不能替换旧 ID |
| `core-tests.log` / `core-tests.json` | 核心 `tests.test_app` 原始输出与结构化计数；阈值 >=40 且全部成功、零跳过 |
| `harness.log` / `harness-structured.log` / `harness.json` | CLI 和结构化运行的原始日志；兼容基线固定 H01…H20，页面或 EXP-15-Q 另可生成 20+40 条分层报告、分组指标、Wilson 区间和 P95；统一发布硬门槛仍校验 H01…H20 恰20个唯一案例 |
| `blackboard.log` / `blackboard.json` | 三个场景的实际快照、审批前后状态、Trace 与 21 项重算布尔断言；场景固定输入在 verify_blackboard.py，不能仅写 assertions=PASS |
| `traceability.json` / `validation.log` | 从 spec 映射得到的本次校验结果、各条真实测试是否在完整套件中成功执行、失败原因；这个输出不是 spec 输入映射的替身 |

单项诊断可在 ENV-R 下用各 EXP 给出的 `unittest -v` 点名命令。诊断日志可另存 `.artifacts/experiments/<run>/EXP-xx.log`，但最终仍需统一入口重跑；下文“证据”默认指统一目录，不承诺脚本会额外生成逐 EXP 文件。

本地 `.artifacts` 默认被 Git 忽略，需自行按项目记录制度保留。CI 在实际提交上运行同一入口并上传证据，当前工作流保留 14 天；报告须给出真实 CI URL 和 commit，不能声称历史附件永久可下载。未提交工作区结果应同时给出 dirty 状态和文件哈希。

共同失败/清理规则：任何断言失败、超时、线程未到达阻塞标记、库存为负、过期版本提交、跨身份泄漏、缺日志/JSON、跳过或源文件改变均判失败。Event 一律在 `finally` / `addCleanup(release.set)` 释放，等待 worker 完成，关闭 TestClient/SessionManager 后再让临时目录清理；失败证据目录保留。禁止删除真实数据库、执行全店重置或用 `sleep` 猜竞态时机。

## EXP-01：入住凭证全生命周期（REQ-01 / AC-01）

**环境、前置与控制变量**：ENV-A。为 1128 和 808 分别签发凭证，固定 TTL=86400；用同一旧 token 分别测试跨会话、跨房、过期、吊销和换客，每个子场景重建夹具。时钟只 patch `auth.time.time` 到 `expires_at+1`。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_guest_is_bound_to_own_session_room_and_stay tests.test_deployment.DeploymentTests.test_revocation_and_expiry_invalidate_pass tests.test_deployment.DeploymentTests.test_turnover_revokes_previous_occupant tests.test_deployment.DeploymentTests.test_revoked_inflight_request_cannot_commit
```

1. 前台 `POST /api/guest-access`，断言 201，取得 session/stay/grant 绑定；1128 凭证访问 808 会话、运营接口或请求“808 送水”均为 403。正常“送两条毛巾”为 200，仅生成房间 1128 的 1 单。
2. 修改当前 stay_id、推进补丁时钟、吊销 grant、登记新住客后，旧凭证 `GET /api/me` 均应 401；换客返回新 stay_id，不能等于旧值。
3. patch `HousekeepingAgent.dispatch`：先 `started.set()`，等 `release.wait(5)`。异步提交得 202；主线程 `started.wait(2)` 成功后吊销，再释放；wait 最多 3 秒，结果须 `superseded`，工单 0、毛巾 24。未确认 worker 已进入阻塞区就吊销，不能作为此竞态证据。

**证据、失败与恢复**：`full-tests.log/json` 中以上成功 ID 和计数；`manifest.json` 来源。签发失败、任一错误 HTTP 码、旧 worker 创建工单或扣库存直接失败。释放 Event、关闭临时库；不调整真实凭证。

## EXP-02：API 岗位授权与字段最小化（REQ-02 / AC-02）

**环境、前置与控制变量**：ENV-A；管理员先为 1128 创建 2 条毛巾的保洁工单。请求 URL、ticket_id、session 固定，仅切换为工程 token 或无 token，避免混入不同业务状态。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_missing_auth_and_shared_guest_token_cannot_access tests.test_deployment.DeploymentTests.test_department_cannot_complete_other_department_ticket tests.test_deployment.DeploymentTests.test_new_guest_does_not_read_previous_room_orders
```

1. 无凭证 `/api/tickets`、旧共享凭证 `/api/me` 为 401；无鉴权健康路由 `/health` 为 200，仅证明存活。
2. 工程 `POST /api/tickets/{tid}/complete` 为 403；其 `/api/operations/state` 为 200，但 tickets 为 `[]`，pms_rooms[1128] 不含 `guest_name` 和 `stay_id`；读住客会话及 messages 为 403。
3. 同一房间新签发会话的 tickets/messages/progress_items 全为空；新会话“毛巾不用了”不能定位旧会话 target_ticket_id，旧单仍处理中、毛巾 22。

**证据、失败与恢复**：`full-tests.log/json`。必须检查字段缺失和业务未变，不能只验证页面按钮隐藏。任何越权成功、旧服务泄漏或误取消均失败；关闭 TestClient/临时 manager，恢复环境 patch。

## EXP-03：复合 FAQ 保留与未知学校政策（REQ-03 / AC-03）

**环境、前置与控制变量**：旧复合基线 ENV-R/standard；学术会务 ENV-A/academic；每次新 flow，固定输入。两个 profile 的断言单列，不能把历史早餐时间作为学校已审核政策。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_app.MultiAgentFlowTests.test_compound_intent_faq_and_late_checkout_with_room tests.test_app.MultiAgentFlowTests.test_compound_intent_faq_and_late_checkout_multiturn tests.test_app.MultiAgentFlowTests.test_complaint_with_explicit_breakfast_faq_answered tests.test_deployment.DeploymentTests.test_academic_meeting_and_checkout_need_real_confirmation
```

1. standard 输入“1128 你们早餐几点结束？另外我是携程黑金，能帮我两点退房吗？”；同时含 07:00–10:00、二楼餐厅与批准，PMS 14:00。缺房号时 FAQ 仍答、询问房号、room_no=None；补 1128 后才办理。
2. standard 客诉 + 早餐 FAQ：经理/红色保留，仍回答显式早餐问题；具体工程险情不应被设施广告答复覆盖。
3. academic 输入“会议签到在哪里？下午两点能退房吗？”；reply 同时含“会务”和“审核”，审批前 checkout_time=12:00。模板 `data/academic_hotel_knowledge.json` 对未经确认的会议名称、时间、设施与含早要求前台核验；现场政策矩阵待 FIELD-04，当前固定测试不代表所有未知政策措辞均已穷尽。

**证据、失败与恢复**：`full-tests.log/json`；standard 复合原始快照与关联/知识检索/延退 Trace 由 `blackboard.json/log` 保存。FAQ 被服务分支吞掉、未审即更新学术退房或捏造学校时间/地点均失败；关闭 flow 的 manager（若有），恢复 profile。

## EXP-04：多轮补槽与房号边界（REQ-04 / AC-04）

**环境、前置与控制变量**：ENV-R 新 HotelOrchestrator；安全对照用 ENV-A。演示流允许明确纠正未授权绑定的房号；已签发住客凭证的房间不能据此切换。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_app.MultiAgentFlowTests.test_reception_asks_for_room_before_dispatch tests.test_app.MultiAgentFlowTests.test_second_turn_completes_pending_request tests.test_app.MultiAgentFlowTests.test_room_correction_from_invalid_to_valid tests.test_app.MultiAgentFlowTests.test_cloud_room_requires_pms_and_mentioned_number tests.test_deployment.DeploymentTests.test_guest_is_bound_to_own_session_room_and_stay
```

1. 先“我想要一条毛巾”：工单 0、active_agent 为补全房间号；再“1208”：仅 1 单、部门保洁，last_trace 最后一项 agent=HousekeepingAgent。
2. 复合请求缺房号，再给 9999：明确查不到，不得建单；更正 1128 才绑定，符合该演示夹具的权益流程。
3. 将模型提取方法 stub 为房号 9999，输入真实提及 1106：最终 state.room_no 和 ticket.room_no 必须 1106；模型 stub 不发网络请求。输入明确先说 1102 再纠正 1106，应取最后有效房号。
4. 对鉴权 1128 住客提交含 808 的消息：HTTP 403，绑定不变。不能用本地演示“切换到…”功能证明部署可跨房。

**证据、失败与恢复**：`full-tests.log/json`、`core-tests.log/json`。缺槽即建单、无效/未提及房号被采用、跨房绑定变化均失败；丢弃纯内存 flow，关闭 ENV-A 临时资源。

## EXP-05：异步版本、取消、独立会话和共享库存（REQ-05 / AC-05）

**环境、前置与控制变量**：ENV-R，`TaskRuntimeTests` 每例 SessionManager(max_workers=4)。毛巾/浴巾/水为 24/24/36；通过 Event/Barrier 明确事件先后，不以请求间隔推断并发。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_task_runtime.TaskRuntimeTests.test_replace_running_delivery_commits_only_latest_version tests.test_task_runtime.TaskRuntimeTests.test_cancel_running_delivery_never_creates_ticket_or_reserves_stock tests.test_task_runtime.TaskRuntimeTests.test_three_rapid_versions_keep_quantity_and_only_publish_latest tests.test_task_runtime.TaskRuntimeTests.test_other_guest_is_not_blocked_by_slow_department tests.test_task_runtime.TaskRuntimeTests.test_concurrent_guests_cannot_oversell_shared_inventory tests.test_task_runtime.TaskRuntimeTests.test_queued_capacity_has_backpressure tests.test_api.FastAPISessionTests.test_async_submit_accepts_replacement_and_poll_enforces_session
```

1. 阻塞毛巾 dispatch，`started.wait(2)` 后才提交“改成浴巾”。新 run 与旧 run 的 task_id 相同、version=2、old.status=superseded；新 run 在 2 秒内 completed，只有浴巾 2 条的 1 单，库存24/22。再 release 旧 worker，等待 worker_finished 后仍只有该单、仅 1 条 assistant 回复。
2. 阻塞后取消：释放前后均不得预留或建单；最终工单0、毛巾24。三版本对照逐次等毛巾及浴巾 started，最后改矿泉水 version=3，最后仅矿泉水2、水34、毛巾/浴巾24。
3. 保持 slow 会话 blocked，quick 会话 FAQ 在2秒内返回07:00且 old.result.done=False；这证明会话不被该 worker 串行拖住，不提供普遍时延 SLA。
4. `Barrier(2)` 令两个住客先各基于24条库存提出20条预留，再释放竞争提交；两结果completed、两工单 reserved_quantity 排序 `[0,20]`、库存4、恰1条“库存不足”。不能只检查库存非负。
5. max_workers=1/max_pending=1 下第一任务 blocked，第二独立请求抛 RuntimeBusy；API 映射繁忙为503。异步 API 提交202、旧轮询running无result、另一session查run404、相同request重试同run、改载荷409。

**证据、失败与恢复**：`full-tests.log/json` 中各竞态点名。任一 started 超时、旧版本回写、双预留40、无界接收或另一会话串行等待均失败。finally释放所有Event/Barrier异常，等worker结束再close；保留失败日志。大规模性能另见FIELD-02。

## EXP-06：送物状态与定向变更（REQ-06 / AC-06）

**环境、前置与控制变量**：ENV-R 内存生命周期用例，过时确认用 ENV-A。初始毛巾24/浴巾24/水36；每子场景重置，固定数量2；保持 ticket_id、task_id、change_id 的前后身份关系。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_task_runtime.TaskRuntimeTests.test_completed_compute_can_amend_unshipped_ticket_without_duplicate tests.test_task_runtime.TaskRuntimeTests.test_dispatched_delivery_waits_for_staff_acknowledgement tests.test_delivery_language.DeliveryLanguageTests.test_screenshot_followup_cancels_only_water_and_preserves_task_id tests.test_delivery_language.DeliveryLanguageTests.test_alias_cancel_cannot_supersede_an_unrelated_running_delivery tests.test_deployment.DeploymentTests.test_stale_change_confirmation_and_pending_completion_are_rejected tests.test_api.FastAPISessionTests.test_cancelled_delivery_cannot_be_completed
```

1. 未派送2条毛巾→浴巾：仍1单且ticket_id不变，毛巾24/浴巾22。两次取消只能释放一次，最终毛巾24。
2. 毛巾和水各有一单时，“不用了”进入awaiting_input而不任意取消；随后“水不用了”只取消矿泉水，task_id沿用水单/version2，毛巾仍处理中/22，水36。“热水不用了”等不明水指代不得取消矿泉水，并有delivery_target_required Trace。
3. 毛巾配送中时请求更换：确认前仍毛巾、change_request=待部门确认，不能声称取消成功；部门确认后才变浴巾24/22；重复确认不二次扣库。另一毛巾worker blocked 时取消水不能设置毛巾cancelled。
4. ENV-A 派送→改浴巾→保存change_id→又改水；待确认结单和旧change_id确认均409。取消工单再complete也409，库存不再次变化。

**证据、失败与恢复**：`full-tests.log/json`；task/version/ticket身份和库存断言缺一不可。跨单改写、抢先承诺成功、重复释放、旧变更生效即失败；释放阻塞worker、close临时资源。

## EXP-07：部门提案核验与风险升级（REQ-07 / AC-07）

**环境、前置与控制变量**：ENV-R，每次仅patch一种部门输出错误，其余合法字段由真实部门函数生成。错物、虚构库存999、错房808和priority/risk降级分别独立，不让多个错误掩盖具体防线。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_task_runtime.TaskRuntimeTests.test_conflicting_child_item_is_rejected_without_stock_commit tests.test_task_runtime.TaskRuntimeTests.test_supervisor_cannot_accept_invented_inventory tests.test_task_runtime.TaskRuntimeTests.test_child_cannot_downgrade_safety_priority tests.test_api.FastAPISessionTests.test_conflicting_child_is_visible_as_review_without_order tests.test_streamlit.GuestInteractionTests.test_staff_can_review_conflict_without_bypassing_validation tests.test_app.MultiAgentFlowTests.test_angry_ticket_gets_red_risk_alert
```

1. 用户要2浴巾，patch提案inventory_item=毛巾：outcome=needs_review、回复暂未派单、0工单、浴巾24、agent_conflicts恰1。
2. 毛巾提案inventory_remaining=999：needs_review、毛巾24；严重漏水提案改成低/普通：needs_review、0工单。
3. API工程提案错房808但请求1128：HTTP200只是任务响应，run_status=needs_review，session tickets=[]、冲突status=需人工核验。
4. AppTest“核验”冲突后仅改status=已核验，工单仍0；正常严重客诉对照必须经理/高/红色。黑板场景同时保存proposal_validated和vertical_agent_dispatch等Trace证据。

**证据、失败与恢复**：`full-tests.log/json`、`blackboard.log/json`。任一被拒提案留存工单/扣库存，或核验按钮绕过校验派单即失败；patch退出恢复函数，close manager和UI资源。

## EXP-08：单写入、原子检查点和故障回滚（REQ-08 / AC-08）

**环境、前置与控制变量**：ENV-A，独立临时SQLite，WAL/synchronous=FULL；只对本例连接做故障注入。所有失败前快照包含工单、库存、业务日期；不对真实库创建trigger。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_second_writer_is_rejected_instead_of_overwriting tests.test_deployment.DeploymentTests.test_write_failure_rolls_back_stock_and_does_not_ack_success tests.test_deployment.DeploymentTests.test_atomic_checkpoint_rolls_back_if_session_write_fails tests.test_deployment.DeploymentTests.test_staff_storage_failure_is_unavailable_and_rolls_back
```

1. manager持有库writer.lock时，用同一路径再开SessionManager，必须抛StorageConflict。该测试在同进程使用两个store实例；真实两个独立进程还需FIELD-01/03。
2. patch checkpoint仅在event.action=task.commit抛sqlite3.OperationalError；提交2毛巾后run_status=failed、回复无“已为您创建”、工单0、毛巾24。关闭旧manager并重开同库后仍0单；HTTP200不能当办成。
3. 在临时连接创建BEFORE INSERT aegis_sessions的ABORT trigger，checkpoint同时尝试业务日期变化；必须抛IntegrityError，store.load_business()与故障前完全相同；drop trigger后正常persist可成功。
4. 创建正常毛巾单后，让员工dispatch的checkpoint失败；HTTP503、delivery_status仍待派送、`/ready`503。`/health`仍可能200但degraded，不能因此恢复写入。

**证据、失败与恢复**：`full-tests.log/json`；故障前后及重开状态由测试断言验证。部分业务提交、库存丢失、错误成功回复、第二写入者成功均失败。退出patch、删除本例trigger、close全部store；未通过不得只重开网页消除告警。断电/磁盘硬件故障尚未验收。

## EXP-09：跨重启与裁剪的请求幂等（REQ-09 / AC-09）

**环境、前置与控制变量**：ENV-A；同凭证、session、message和request_id固定。只改变重启/内存裁剪状态及冲突载荷；持久化库保持同一路径。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_task_runtime.TaskRuntimeTests.test_request_id_retry_does_not_duplicate_delivery tests.test_deployment.DeploymentTests.test_request_retry_survives_restart_without_second_order tests.test_deployment.DeploymentTests.test_pruned_run_keeps_durable_idempotency_record
```

1. 同request_id连续submit，相同对象/同run_id，完成后1单、毛巾22；改成浴巾复用ID必须ValueError，API为409。
2. 发送“送两条毛巾”携带retry-same并保存run_id；close原manager、重新构造同库manager，完全相同请求返回原run_id、1单、毛巾22；不同message同ID为409。
3. 另一个子例在manager.lock内删掉该run的 `_runs` 与 `_requests` 内存项，数据库不删；相同请求仍找回原run_id、1单。此为故意注入内存裁剪，不能以仍命中字典的重试替代。

**证据、失败与恢复**：`full-tests.log/json`；缺run_id等值或库存断言即证据不足。重试建第二单、毛巾20、冲突ID被接受即失败。close旧writer后才重开，临时库随用例清理。

## EXP-10：黑板窗口、消息归档与日期恢复（REQ-10 / AC-10）

**环境、前置与控制变量**：ENV-A及临时SQLite；用8轮固定会务问题产生16条消息，日期独立例固定2026-10-23。分页固定before游标和limit8，不依赖随机顺序。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_history_survives_window_compaction_and_can_be_paged tests.test_deployment.DeploymentTests.test_initial_business_date_is_configurable_and_retained tests.test_persistence.PersistenceTests.test_business_and_session_state_survive_process_restart tests.test_persistence.PersistenceTests.test_restart_marks_inflight_task_as_interrupted tests.test_deployment.DeploymentTests.test_department_cannot_complete_other_department_ticket
```

1. 连续8轮会务问答后在线state.messages长度<16；close/reopen，自己的messages接口200且返回16条；before=第9条sequence、limit=8取得前8条。归档和在线窗口分别断言，摘要存在不等于完整历史保存。
2. 首次库配置日期2026-10-23，close后把环境设2026-10-24；新manager.current_date仍为23日，日期不会随系统时间擅自推进。夜审后的跨日恢复另见EXP-12。
3. 正常已提交毛巾2单量：重开后room1128、工单1/处理中、库存22、任务submitted；单独在夹具持久化running任务，重开后应interrupted，不把它报告已完成。该running例是状态恢复模拟，未等价真实杀进程。
4. 工程和其他住客的历史访问被403拒绝；旧入住新会话不读旧消息由EXP-02对照。

**证据、失败与恢复**：`full-tests.log/json`。分页缺失/重复、只恢复摘要、旧日期覆盖、他人历史泄漏或running假报completed即失败。close两代manager释放writer，删除的只限TemporaryDirectory。

## EXP-11：学术接待权益与当前入住审批（REQ-11 / AC-11）

**环境、前置与控制变量**：ENV-A，学术住客，无真实学校接待订单；初始退房12:00。比较同一请求审批前后、换客前后expected_request_id，不能以自述“教师/黑金”授予费用权益。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_academic_meeting_and_checkout_need_real_confirmation tests.test_deployment.DeploymentTests.test_academic_breakfast_is_not_an_automatic_entitlement tests.test_deployment.DeploymentTests.test_checkout_approval_is_bound_to_current_occupant_request tests.test_deployment.DeploymentTests.test_academic_coupon_delivery_wording_requires_review_without_stock_reservation tests.test_deployment.DeploymentTests.test_academic_housekeeping_cannot_dispatch_coupons_directly tests.test_deployment.DeploymentTests.test_academic_housekeeping_cannot_replace_delivery_with_coupons tests.test_deployment.DeploymentTests.test_academic_coupon_replacement_preserves_existing_delivery
```

1. 会务+两点延退：未审核时checkout12:00、待审核；前台带当前expected_request_id审批200后才14:00，通知含“费用按接待订单”，重开仍14:00，audit中action=checkout.review、actor=hotel-admin。
2. “给我两张早餐券”：HTTP200、回复前台核验，ledger最新status=需前台核验、owner_session_id/stay_id等于当前凭证，不含“已发放”。这是已覆盖的票券意图分支。
3. 旧住客申请→换客→新申请：不带expected_request_id为400；旧ID为409；新ID为200，旧申请仍需前台审核、新申请已批准，不能按同一房号错批旧单。
4. **已修复的已知送物措辞绕过**：同一学术凭证依次提交“给房间发两张早餐券”“给1128发两张早餐券”“送两张早餐券到房间”“给我送来两张早饭券”“送两张餐券”“发两张体验券”。每次新增恰1条核验账本、quantity=2、session/stay匹配、Trace含issue_breakfast_vouchers；保洁工单始终0，券库存始终40。直接保洁dispatch上述券别名应抛AgentConflict且无副作用。
5. 毛巾2条已预留后请求“改成两张早餐券”：API与直接revise两种入口均提示前台核验，原工单全部字段不变、毛巾22、券40，不产生改券成功或待确认的券派送。这里保留原单的行为不声称已替用户签发真实电子券。

**证据、失败与恢复**：执行前统一证据为PENDING_RUN，完成后以该次manifest为准；须包含以上新增方法。Task 1焦点证据在已忽略的`.artifacts/verification/task1-coupon/`：`red.log`为4方法、12个subTest失败（不是12个独立方法）；同4方法的`green.log`通过；`regression.log`为81项、零跳过。已知表达及直调保护的实现状态为implemented，不能外推任意自然语言、迁移前脏数据或真实电子券发行。任何未审核自动获权益、错误ID获批、把standard餐券发放当学术通过均失败，阻断academic上线；临时资源close，真实政策仍待FIELD-04。

## EXP-12：部门回执、夜审与待检库存（REQ-12 / AC-12）

**环境、前置与控制变量**：ENV-R生命周期、ENV-A夜审；固定expected_date，隔离库存与quarantine。夜审竞态先确认旧dispatch在Event阻塞区，再推进日期。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_night_audit_cannot_be_repeated_with_stale_date tests.test_task_runtime.TaskRuntimeTests.test_night_audit_cannot_be_overwritten_by_older_snapshot tests.test_task_runtime.TaskRuntimeTests.test_delivered_item_cannot_be_restocked_without_return_confirmation tests.test_task_runtime.TaskRuntimeTests.test_staff_confirmation_updates_borrowed_record_and_notifies_guest tests.test_task_runtime.TaskRuntimeTests.test_rejected_change_keeps_stock_and_clears_waiting_status
```

1. 同一expected_date首次夜审200、重复409；保存新date，close/reopen仍该date。旧毛巾worker先blocked→夜审→release→完成，state.current_date等于夜审date、audit_logs恰1，新会话也同日。
2. 已送达并已结单的2条毛巾→请求更换；未returned确认抛ValueError且可用毛巾22；returned=True确认后可用毛巾仍22、quarantine[毛巾]=2，不能把已到客房的物资直接补回干净库存。
3. 雨伞配送中改吹风机，经部门批准后borrowed_items改为吹风机、雨伞10、吹风机5，住客收到保洁确认消息；拒绝改单时change_request=已拒绝、task=submitted、原毛巾22/浴巾24并说明无法撤回。

**证据、失败与恢复**：`full-tests.log/json`。重复跨日、旧快照盖夜审、无回收确认补库、quarantine缺失或借用品/通知不同步即失败。release所有Event、关闭临时资源；生产实物清点仍需现场流程。

## EXP-13：API 工作台与前后端边界（REQ-13 / AC-13）

**环境、前置与控制变量**：ENV-A，AppTest，AEGIS_API_URL=http://hotel-api；httpx被路由到独立临时manager的TestClient，无外部网络。每次页面只换访问凭据，不给web第二份DB路径。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeployedUIBoundaryTests.test_no_credential_does_not_create_unprotected_business_ui tests.test_deployment.DeployedUIBoundaryTests.test_guest_ui_only_receives_own_service_view tests.test_deployment.DeployedUIBoundaryTests.test_staff_ui_shares_api_without_a_second_writer
```

1. 空token运行app：exception=[]、chat_input数量0、manager.sessions={}。
2. 1128已核验凭证：exception=[]、chat_input数量1，无“签发”按钮、无运营工作台标签。
3. 管理员：exception=[]、可见签发按钮，页面session_state不含agent_runtime；其业务请求经HotelApi/TestClient到API manager。接口越权仍用EXP-02独立验证，不能仅凭按钮数量判断安全。

**证据、失败与恢复**：`full-tests.log/json`。页面异常、无token建会话、住客见运营、web自行开writer均失败。关闭AppTest对应资源/TestClient/manager。此环境是headless路由模拟；真实两进程、浏览器网络及容器验收是FIELD-01，不把截图当部署验收。

## EXP-14：配置、备份与容器静态约束（REQ-14 / AC-14）

**环境、前置与控制变量**：ENV-A，所有配置和备份文件放临时目录；使用仓库manage.initialize/backup，不打印随机值。模型连接只使用批准的测试 Key、`https://api.siliconflow.cn/v1` 和合成模型配置，日志不得记录 Key。当前源码的Dockerfile/Compose只静态审阅；ec6331f的历史CI镜像构建不证明当前改动已构建，新构建尚未执行。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_secret_generation_never_overwrites_deployment tests.test_deployment.DeploymentTests.test_deployed_mode_rejects_missing_credentials tests.test_deployment.DeploymentTests.test_invalid_staff_mapping_fails_configuration_validation tests.test_deployment.DeploymentTests.test_validated_backup_includes_latest_committed_order tests.test_model_connection.ModelConnectionTests.test_explicit_cloud_connection_clears_offline_switch_and_probes_model tests.test_model_connection.ModelConnectionTests.test_missing_openai_sdk_is_reported_instead_of_silent_fallback tests.test_model_connection.ModelConnectionTests.test_missing_openai_sdk_uses_http_compatibility_transport tests.test_model_connection.ModelConnectionTests.test_probe_rejects_a_model_that_is_not_listed
```

1. initialize写新deploy.env，再次同路径抛FileExistsError且原字节不变；不把生成文件写进公共日志。
2. deployed=1时替换弱管理员令牌，应拒绝；非法JSON/短token/非法岗位映射均拒绝。另静态检查auth.validate_configuration中鉴权、DB路径、签名与员工密钥最小32字符且不同的约束，未执行分支不能声称均有独立覆盖。
3. 已提交2毛巾后SQLite online backup到新路径，backup内部integrity_check=ok；用备份路径开新manager，工单1、库存22。此证明一致性备份恢复本机夹具，未测断电恢复或学校备份介质。
4. Dockerfile静态断言USER aegis、UID10001、/data归属；Compose API workers=1、命名卷/data、ready健康检查、web经API、端口仅绑定127.0.0.1。静态存在不等于镜像构建、权限挂载和重启已成功，REQ-14保持partial。
5. 连接测试先确认显式云端配置会清除本进程 `AEGIS_FORCE_RULES`，再调用只读 `/v1/models` 探测；缺少 `openai` SDK 时若 `httpx` 可用则使用兼容传输，两者均缺失时必须给出 `openai_sdk_missing`，模型未列出必须阻断。
6. 真实/现场 MODEL-CONN-01 需独立执行连续 3 次预检：每次 HTTP 200、选定模型命中、≤12 秒，成功率 100%，保存脱敏 `model-probe.json`；若要开放完整云端推理，另做 1 次合成 `chat/completions` canary（`max_tokens≤8`），失败时保持规则模式。

**证据、失败与恢复**：`full-tests.log/json`、`manifest.json`和脱敏 `model-probe.json`。覆盖配置、备份丢最新单、共享双writer、模型连接失败、静默降级或静态缺非root约束均失败。连接失败时保留原规则模式和错误码，不重试提交业务；关闭备份manager后清理临时文件；容器交付必须另外完成FIELD-01/03，禁止通过删卷“恢复”。

## EXP-15：固定 Harness 与 Few-Shot 的证据边界（REQ-15 / AC-15）

**环境、前置与控制变量**：ENV-R/standard、force_rules=True；`harness/eval_dataset.json`、hard_cases.json和dynamic_exemplars.json用仓库固定版本并记录哈希。不改case期望值、不用测试集挑选阈值、不发模型请求。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_app.MultiAgentFlowTests.test_harness_dataset_regression_metrics tests.test_app.MultiAgentFlowTests.test_harness_other_accepts_chitchat_alias tests.test_evolution.SelfEvolutionAndMemoryTests.test_dynamic_exemplar_curator_retrieves_block tests.test_evolution.SelfEvolutionAndMemoryTests.test_hard_case_miner_loads_default_cases tests.test_evolution.SelfEvolutionAndMemoryTests.test_evolution_delta_runner_cycle
& $py -B -X utf8 -m harness.eval_runner
```

1. 统一入口从run_evaluation(use_cloud_model=False)保存JSON；逐行校验H01…H20恰20个唯一ID、全部PASS，total=passed=20，四项指标全部严格1.0。打印CLI仅作人读诊断；即使FAIL它也可能退出0。
2. “其他”允许规则别名“闲聊”，不允许把酒店咨询误判闲聊；Dataset和alias两类断言不能合并隐藏错误。
3. Few-Shot测试验证字符相似度筛选后提示块含黄金参考示例和1106；难例至少4个且类别包含口误纠正/复合分流；固定难例回归成功。`EvolutionDeltaRunner`当前回报evolution_delta=None、evaluation_scope说明无前后对照；代码没有训练、在线自动审批或向量检索闭环。
4. 审核报告措辞：所有100%均附固定20条/离线规则；average_response_ms仅本次串行本地规则均值，不作端到端P95、云模型时延或产能验收。

**证据、失败与恢复**：`harness.log/json`、`full-tests.log/json`、输入文件哈希。缺/重复ID、FAIL行、某项不是1.0、改基线凑通过、虚构提升均失败；不更新难例/示例库，如执行中意外变更应判源数据漂移并重跑。

### EXP-15-Q：分层质量扩展集（REQ-15 / AC-15）

固定 H01–H20 仍按上面的兼容性契约执行；产品质量另运行 `harness/eval_extended.json` 的 E01–E40。扩展集分为 `delivery_and_replacement`、`repair_and_complaint`、`faq_and_compound`、`slot_checkout_identity`、`policy_safety_scope` 五组，每组 8 条。使用 `python -m unittest -v tests.test_quality_evaluation` 和 `python -m harness.eval_runner --extended`，报告总数、每组通过率、Wilson 95% 区间、四项业务指标、平均耗时和 P95。扩展集必须 40/40，通过率指标至少达到 95%/95%/98%/95%，安全组的未审核权益和多物品副作用为 0；不满足时记录 `QUALITY_FAIL`，不能用 H01–H20 的 20/20 抵消。

**证据、失败与恢复**：保存扩展输入文件哈希和结构化 JSON；缺分组、低于 40 条、某组低于 8 条、只运行兼容基线却声称分层通过或把平均耗时解释成生产 SLA，均判失败或未运行。现场自然语言泛化、云模型对照和 RAG/Few-Shot 提升仍按 FIELD-06 另行验证。

## EXP-16：统一证据入口的反例试验（REQ-16 / AC-16）

**环境、前置与控制变量**：ENV-R；新证据测试用临时目录和伪造报告注入验证器，只改变一个失败条件，保留其他合法字段；不污染正式证据目录。真实测试名以当前`tests/test_release_verification.py`和traceability映射为准。

```powershell
& $py -B -X utf8 -m unittest -v tests.test_release_verification
& $py -B -X utf8 scripts/verify_release.py --output-dir $proofDir
```

1. 给验证器“进程exit0但unittest有失败”、零测试、skip、缺JSON、报告run_id不一致等反例，必须得到非通过判定；不能仅用assert函数返回非空字符串代表全部门槛被验证。
2. Harness伪造FAIL行、缺Hxx、重复ID、额外H21/H100 PASS行、额外H21 SKIP或H100 ERROR行、已有H01的附加SKIP行、错误四项数值；黑板缺场景/坏Trace/错误前后状态；追溯假方法、可import但未实际执行的测试；均须拒绝。
3. 已存在输出目录必须拒绝，不能覆盖旧run；真实新目录运行完整/核心/Harness/blackboard/追溯步骤，逐步保存stdout/stderr和退出码，再由独立结构化校验决定manifest状态。
4. 检查blackboard恰三个场景、21项状态/Trace断言：standard复合FAQ+延退含07:00、10:00、14:00与批准，PMS14:00及知识/业务Trace；愤怒客诉经理/高/红色及提案核验Trace；808人工审批前12:00/后14:00、前台通知及人工审批Trace、request_id前后一致。每个状态断言必须有实际快照支撑，验证器须从快照重算。

**证据、失败与恢复**：统一输出契约所有文件；`full-tests.json`须证明映射测试实际成功执行。反例能被误报PASS、缺原始文件、源文件在采集后变化均失败。临时注入文件随测试清理，正式失败目录保留，修复后另开新目录。执行前为PENDING_RUN，完成后只引用该次manifest，不复用历史记录。

## 现场实验（TARGET_NOT_RUN）

以下均是明确的后续目标，不是当前成绩。REQ-11已知表达缺口已修复并有焦点证据；执行现场前仍须本次完整离线门槛通过及学校政策审核。执行环境限定酒店批准的验收沙箱、合成入住和个人测试凭据，禁止让下列写操作触及真实客人或真实PMS。每次建`.artifacts/field/<run>/`保存`field-plan.json`（目标/设备/资源/版本/数据哈希/批准范围）、`commands.log`、脱敏逐请求`requests.jsonl`、`before.json`、`after.json`、`measurements.json`、`result.json`；从运行开始记录UTC和monotonic耗时，失败和清理过程同样保留。字段是现场证据要求，当前统一脚本不生成这些文件。

### FIELD-01：真实容器构建、非 root 与分离工作台

**关联**：REQ-13/14，当前TARGET_NOT_RUN；采用容器交付时阻断项。

**前置/控制**：独立验收checkout、Docker Engine/Compose及版本记录；端口8000/8501未占用、使用专用project `aegis-acceptance`，无生产卷；固定镜像源commit/requirements与资源限制。在该沙箱首次运行`python scripts/manage.py init --output deploy.env`，生成文件留本地私密保存，不能打印内容。初始化日期为本次测试日。

```powershell
docker version
docker compose version
docker compose -p aegis-acceptance config --quiet
docker compose -p aegis-acceptance build
docker compose -p aegis-acceptance up -d
docker compose -p aegis-acceptance ps
docker compose -p aegis-acceptance exec -T api id -u
docker compose -p aegis-acceptance exec -T web id -u
```

**步骤与目标**：构建exit0；两个UID都为10001且非0；API单worker，/ready在120秒内200/persistent=true；web无DB凭据且通过http://api:8000办理。经前台签发合成住客，完成2毛巾→派送→改2浴巾→当前change_id确认，重建仅web后业务不丢，重启api后仍1张当前工单/浴巾22/毛巾24；旧change_id409，跨房403。直接测试第二进程打开同卷库须拒绝写入。记录镜像digest、容器配置摘要、网络请求和库存前后值，不能把页面加载算闭环通过。

**失败/清理**：root运行、卷不可写、API未持久化、双writer或业务变化即FAIL。用`docker compose -p aegis-acceptance down`停止本测试项目，保留卷和证据用于调查；不使用`down -v`，不停止其他项目。日志脱敏后才公开。

### FIELD-02：部署模式容量与延迟

**关联**：FUT-05，当前TARGET_NOT_RUN；在承诺多人同时接待或任何容量SLA前阻断。

**前置/控制**：FIELD-01通过；固定验收硬件、OS、CPU/内存限制、worker/pending配置、网络位置和数据量；只能用合成客人。需先增加鉴权负载驱动，读取私有测试凭据文件，遵守每凭证20次/分钟限制，逐请求记录session哈希/request_id/run_id/HTTP码/run_status/时延，结束读API和SQLite核对幂等。仓库现有`tests/stress_test.py`无部署Authorization并会尝试`/api/admin/reset`，仅可用于独立无鉴权演示环境，不能直接作为本现场驱动，也不能指向真实酒店服务。

**步骤/目标**：2分钟预热不计分；20个独立住客会话、最大20个同时飞行请求、持续30分钟，每会话每6秒一请求，共至少6000个业务请求，顺序固定seed并保留请求清单。负载比例：70%已知FAQ、10%自己的session读取、10%分页历史、10%延退申请/查询；不把该轻写负载宣称成高送物吞吐。另重复EXP-05的库存竞争与10% request_id重放作为正确性对照。

正常阶段目标：成功HTTP与run_status均符合预期的比例>=99.5%，未预期5xx/超时比例<=0.5%，端到端P95<=1500ms、P99<=3000ms；P95采用排序后的nearest-rank，计时含网络和持久化，报告分子/分母，不能排除慢请求。正确性硬门槛为跨会话泄漏0、负库存0、重复执行0、丢失已确认提交0。独立过载阶段每凭证1分钟内第21次提交应429，队列饱和应503，明确预计429/503不混入正常成功率。驱动未实现或未存原始逐请求数据时结果为NOT_RUN。

**失败/清理**：任一硬正确性错误即FAIL；性能目标不达则不作SLA承诺，保留全部样本。停止负载、关闭测试API、归档合成库；不重置真实服务，不通过增大阈值掩盖失败。

### FIELD-03：进程中断、备份和恢复演练

**关联**：REQ-08/09/10/14、FUT-05，当前TARGET_NOT_RUN；对外承诺恢复SLA前阻断。

**前置/控制**：仅专用验收卷，所有时间/库存基线固定；先备份且验证可独立打开。须先加隔离进程故障钩子，在`task.commit`前用Event/FIFO发出`before_commit`标记并阻塞；当前代码没有生产故障接口。准备10个各2条毛巾的已确认提交（共20条，库存4），另1个请求在标记处阻塞。不得仅靠延时猜杀进程时机。

```powershell
docker compose -p aegis-acceptance exec -T api python scripts/manage.py backup --database /data/aegisstay.sqlite3 --output /data/field-before.sqlite3
docker compose -p aegis-acceptance kill -s SIGKILL api
docker compose -p aegis-acceptance up -d api
```

**步骤/目标**：先记录10个成功run_id、request_id、工单、库存、审批、消息和业务日快照；确认第11请求到达提交前标记且未收到成功，再SIGKILL；记录ready恢复耗时，目标<=120秒。重开后10个已确认提交全部存在（本流程RPO=0），库存4、日期不退回、完整归档不丢；第11任务应中断或可解释的未提交状态，不能假报成功。同ID重放不得重复既有单。另在独立副本用提交已落库、响应尚未返回的第二故障标记：第11单应存在、库存2，重放第11个request_id返回原结果，仍11单/库存2。两种标记各重复10轮，每轮从同一10单/库存4的沙箱备份独立恢复，不累计耗尽库存；各轮0丢失/0重复。

另将经integrity_check=ok的备份复制到独立恢复路径，停止所有writer后从副本起单实例，校对备份时点状态；测试备份目标不允许覆盖。持续备份策略目标间隔<=15分钟、恢复RTO<=15分钟；该备份RPO<=15分钟与上面的已提交崩溃RPO=0分别报告。模拟磁盘满/只读时员工写操作503、ready503且库存不变，解除故障后需重新检查一致性，不能只刷新网页。

**失败/清理**：任何已确认提交丢失、双扣库存、旧日期覆盖、恢复超过目标、恢复凭证/历史越界均FAIL。撤销故障钩子，恢复写权限；保留原卷和恢复副本，确保只启一个writer，验收服务停机后再清理专用沙箱。

### FIELD-04：学校政策与业务人员联验

**关联**：REQ-03/11，当前TARGET_NOT_RUN；任何学校实际上线前阻断。

**前置/控制**：由学校/酒店确认一份版本化政策清单：早餐时间地点与含早、访学接待、会议名称/地点/日期、校园通行、报销、延退及费用。只载入获批模板，不取真实住客材料作为测试集；记录批准人职务、日期、文件hash和适用酒店，不公开个人信息。

**步骤/目标**：建立至少40条政策查询（20已知、10缺关键信息、10未知/过期），另至少20条权益变体（餐券送/发/领取/给房间、教师自述、商业会员自述、多轮省略、与送毛巾复合、延退换客/撤销）。人工双人独立标注，分歧先仲裁；对同一输入同时记录reply、ledger、工单、PMS前后、HTTP和Trace。已知且可公开内容正确率100%，未知/过期政策要求核验率100%，未核验免费权益承诺0，未审核发券/普通库存券派送0；任何安全反例阻断，不用平均分抵消。代码回归命令为EXP-03/11和统一入口，现场新样本必须固化成新测试后再跑。

**失败/清理**：政策错误、未核验权益、过期会议仍被确认均FAIL；回退已审核模板并停用相关承诺。合成测试申请清理前先保留审计快照；生产政策只能走酒店批准的更新流程。

### FIELD-05：外部 PMS 与学校 SSO 契约验证

**关联**：FUT-01/02，当前TARGET_NOT_RUN；启用/宣传真实接口联动前阻断，当前手工入住核验和本地PMS不等价。

**前置/控制**：先实现适配器及契约测试；取得沙箱接入范围、字段字典、回执/错误码、身份角色映射和撤销策略，不把真实token写日志。当前仓库无可运行外部PMS/SSO适配命令，因此不得声称已联调。

**步骤/目标**：PMS至少覆盖20种契约场景：有效/失效入住、换客、请求重放、延迟/重复/乱序回执、4xx/5xx/超时、网络恢复、字段缺失等；SSO至少覆盖20种身份场景：登录、错误受众/签名/过期、岗位越权、撤销、换人、会话固定与退出。用可控Event/回执队列规定乱序，不靠手工点击速度。所有业务写须经本地和外部双侧状态/关联ID核对，超时显示待确认，不能超时后声称成功；撤销或换客后旧凭证失效100%，越权写0，重放导致重复外部效果0，失败场景审计覆盖100%。记录两侧脱敏报文、HTTP码、事件序号和前后状态。

**失败/清理**：任一跨人、错房、重复效果、未知结果报成功即FAIL。关闭适配器回到手工核验/本地可用范围；撤销测试身份和沙箱订单，保留脱敏报文及审计，不触碰生产订单。

### FIELD-06：云端模型、语义检索与样例治理

**关联**：FUT-04/06，当前TARGET_NOT_RUN；启用或宣传模型提升/语义RAG/线上学习前阻断。

**前置/控制**：先设计经批准的脱敏数据与样例变更流程；冻结独立测试集（至少200条，含至少40条权益/身份/取消/冲突安全边界），与Few-Shot、提示调试和规则开发样本去重；模型版本、温度、提示、知识/示例哈希、seed与预算固定。当前字符相似度Few-Shot不能描述成语义向量检索或模型训练。

**步骤/目标**：相同独立样本上分别跑无示例与有示例条件，每条至少3次，记录原始模型响应、解析与校验/最终业务状态/费用/端到端时延，故意注入模型超时、格式坏、虚假房号和降级风险提案。前后比较采用配对结果和95%置信区间；提升区间未支持正向改进则报告“未证明提升”，不预写百分比。所有安全边界仍须0越权/0未审权益/0重复扣库，模型失败回退率100%；启用的版本另须通过全部离线门槛。在线失败样例至少经脱敏、去重、人工审核、版本化、小流量验证和一键回退，再进入提示库，不能自动把住客逐字稿写入Few-Shot。

**失败/清理**：数据泄漏、测试集污染、安全反例、无审批样例入库即FAIL。恢复上一提示/知识/模型配置，禁用外呼并确认规则路径可用；按审批制度删除临时敏感材料，只保留允许的脱敏统计和审计。

