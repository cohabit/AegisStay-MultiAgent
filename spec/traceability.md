# 要求、验收、实验与真实测试追溯

以 [主规格](specification.md) 为范围，[业务](design/business.md) / [架构](design/architecture.md) 为实现边界；REQ-14 由[部署与运维子规格](design/deployment.md)展开，[现场验收协议](field_validation.md)定义 FIELD-01～06，[未来集成规格](future_integrations.md)定义 FUT-01～06；[验收标准](acceptance_criteria.md) 定义门槛，[实验方案](experiments.md) 定义夹具、时序、数值、失败和清理。[traceability.json](traceability.json) 是 `scripts/verify_release.py::validate_traceability` 的机器输入。

`implemented` 表示约定范围内的代码和测试存在，`partial` 表示有明确未执行/未交付范围；二者都不代表本次运行PASS。本文定义追溯方案，不嵌入可变成绩；每次执行前为 `PENDING_RUN`，完成后只认对应manifest和独立验收记录。由集成人员在源码稳定后运行统一入口。范围固定 `offline-code-baseline`，`field_readiness=NOT_VERIFIED`，FIELD-01…FIELD-06 均为 `TARGET_NOT_RUN`。

JSON 顶层 `schema_version=1`；`requirements` 恰含REQ-01…REQ-16，每项对应同号AC/EXP，`tests` 仅写真实完整unittest方法，当前合计326个唯一方法；`evidence` 仅写统一目录实际文件名。未来FUT不计入当前16项。机器校验只证明方法可加载且在本次成功的完整套件里实际执行；方法是否支撑业务要求还须检查断言语义。

| 要求 / 验收 / 实验 | 内容 | 实现状态 | 方法数 | 覆盖边界 |
| --- | --- | --- | --- | --- |
| REQ-01 / AC-01 / EXP-01 | 入住凭证与入住/会话绑定 | `implemented` | 5 | 授权、跨房、过期、撤销、换客与在途撤销；不是SSO联调。 |
| REQ-02 / AC-02 / EXP-02 | 岗位授权与必要字段 | `implemented` | 6 | 服务端403和字段过滤；页面隐藏不替代授权。 |
| REQ-03 / AC-03 / EXP-03 | 复合FAQ与学校未知政策 | `implemented` | 80 | standard复合基线与academic核验分开；未知政策模板仍须FIELD-04审核。 |
| REQ-04 / AC-04 / EXP-04 | 补槽与房号边界 | `implemented` | 8 | 演示补槽不授予部署跨房权限；模型提取使用stub。 |
| REQ-05 / AC-05 / EXP-05 | 异步版本、取消与并发 | `implemented` | 23 | Event/Barrier受控竞态和队列保护；不证明生产容量。 |
| REQ-06 / AC-06 / EXP-06 | 送物生命周期与目标定位 | `implemented` | 65 | ticket/task/version身份、库存、歧义和已派出确认；旧change_id拒绝。 |
| REQ-07 / AC-07 / EXP-07 | 提案核验与风险升级 | `implemented` | 16 | 错物/错房/虚构库存/风险降级拒绝；人工核验不补派被拒工单。 |
| REQ-08 / AC-08 / EXP-08 | 单写入、原子检查点与回滚 | `implemented` | 6 | 单库第二writer拒绝、聊天/员工故障回滚；非真实断电实验。 |
| REQ-09 / AC-09 / EXP-09 | 持久幂等与重试 | `implemented` | 5 | 同载荷复用run_id，重启/裁剪仍不重复；改载荷409。 |
| REQ-10 / AC-10 / EXP-10 | 消息归档、日期恢复与隔离 | `implemented` | 28 | 模型窗口与完整归档分层；112条分页身份/顺序/去重、116条重启恢复及两端UI历史页隔离均有断言。 |
| REQ-11 / AC-11 / EXP-11 | 学术待遇人工核验与已知餐券保护 | `implemented` | 9 | 已知餐券表达、保洁直调/改券保护已实现；有4方法红绿和81项焦点回归。不是任意语言或真实发券证明。 |
| REQ-12 / AC-12 / EXP-12 | 部门回执、夜审与回收待检 | `implemented` | 5 | 夜审日期、借用品通知和回收待检；不等价外部财务夜审。 |
| REQ-13 / AC-13 / EXP-13 | API工作台与岗位视图 | `implemented` | 25 | AppTest经HotelApi/TestClient访问唯一临时API；两进程/容器仍须FIELD-01。 |
| REQ-14 / AC-14 / EXP-14 | 配置、备份、部署与硅基流动连接预检 | `partial` | 8 | 本地配置/备份和 MODEL-CONN-01 代码预检有测试；当前镜像、Compose、卷权限、连续现场探测、聊天 canary 与现场恢复未执行。 |
| REQ-15 / AC-15 / EXP-15 | 固定Harness与Few-Shot证据边界 | `implemented` | 5 | 固定20条离线规则、字符相似度Few-Shot；没有模型泛化或提升结论。 |
| REQ-16 / AC-16 / EXP-16 | 可复现证据与反假通过 | `implemented` | 38 | 包含额外Harness行（H21 SKIP、H100 ERROR、重复H01 SKIP）、子进程依赖回执和发布控制文件哈希反例；负向夹具不等价完整发布通过，最终须核验本次全部映射方法实际执行。 |

## 证据解释

每次新目录应有manifest加脚本REQUIRED_FILES：`full-tests.log/json`、`core-tests.log/json`、`harness.log`、`harness-structured.log`、`harness.json`、`blackboard.log/json`、`traceability.json`、`validation.log`。JSON中完整文件名逐条列在每个REQ的evidence；其中运行输出traceability.json是校验结果，不覆盖spec输入。

原始 [127个测试ID](baseline_test_ids.json) 和40个核心ID必须逐个实际通过；完整>=127、核心>=40，零跳过/预期失败。Harness恰H01…H20、20/20、四指标均1.0，JSON和CLI均拒绝额外H21/H100；黑板恰三个场景、21项快照重算状态/Trace断言全真。每步JSON和安全回执核对run_id/UTC；manifest依赖来自经验证的隔离子进程回执，记录streamlit、fastapi、httpx、openai、typing_extensions，跨步骤须一致，未安装写not-installed/version=null。Task 3焦点运行中openai实际为未安装，仅证明离线范围。manifest还含Git dirty、起止源码SHA-256（含Dockerfile、.gitignore、.dockerignore）、命令和退出码。回执位于临时目录，manifest保存校验结果，不能编造独立回执交付文件。

REQ-11焦点证据：已忽略的`.artifacts/verification/task1-coupon/red.log`（4方法/12个subTest失败）、`green.log`（同4方法通过）、`regression.log`（81项、零跳过）。这些日志仅证明当次焦点范围；映射中只允许统一入口文件名，故不把这三条本地路径塞入JSON的evidence。未知券别名、迁移前脏数据和真实发券仍未证明。

本地`.artifacts/`默认不入Git；CI原始证据当前保留14天，引用须附真实运行URL/commit。本次完整运行前不借用历史记录升级为PASS。

## 完整方法点名

以下列表由同一JSON生成；它列出支撑每项的具体断言入口，不能以类名或模块名替代执行身份。

### REQ-01 / AC-01 / EXP-01

授权、跨房、过期、撤销、换客与在途撤销；不是SSO联调。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_guest_is_bound_to_own_session_room_and_stay`
- `tests.test_deployment.DeploymentTests.test_revocation_and_expiry_invalidate_pass`
- `tests.test_deployment.DeploymentTests.test_turnover_revokes_previous_occupant`
- `tests.test_deployment.DeploymentTests.test_revoked_inflight_request_cannot_commit`
- `tests.test_hotel_readiness.ConcurrentGuestApiTests.test_new_stay_has_new_history_and_preferences_old_pass_is_invalid`

### REQ-02 / AC-02 / EXP-02

服务端403和字段过滤；页面隐藏不替代授权。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_missing_auth_and_shared_guest_token_cannot_access`
- `tests.test_deployment.DeploymentTests.test_department_cannot_complete_other_department_ticket`
- `tests.test_deployment.DeploymentTests.test_new_guest_does_not_read_previous_room_orders`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_guest_cannot_read_staff_fulfillment_or_global_inventory`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_staff_endpoints_project_same_details_without_changing_stored_ticket`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_same_room_different_session_cannot_remind_someone_elses_order`

### REQ-03 / AC-03 / EXP-03

standard复合基线与academic核验分开；未知政策模板仍须FIELD-04审核。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`、`core-tests.log`、`core-tests.json`、`blackboard.log`、`blackboard.json`。

- `tests.test_app.MultiAgentFlowTests.test_compound_intent_faq_and_late_checkout_with_room`
- `tests.test_app.MultiAgentFlowTests.test_compound_intent_faq_and_late_checkout_multiturn`
- `tests.test_app.MultiAgentFlowTests.test_complaint_with_explicit_breakfast_faq_answered`
- `tests.test_deployment.DeploymentTests.test_academic_meeting_and_checkout_need_real_confirmation`
- `tests.test_app.MultiAgentFlowTests.test_faq_does_not_create_ticket`
- `tests.test_app.MultiAgentFlowTests.test_multi_topic_faq_and_delivery_compound_turn`
- `tests.test_app.MultiAgentFlowTests.test_emergency_suppresses_facility_faq`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_delivery_speed_negation_is_not_an_order_replacement`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_emotion_alone_cannot_authorize_cloud_invented_service`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_emotion_or_denied_manager_is_not_a_complaint_under_rules_or_model`
- `tests.test_hotel_readiness.HotelReadinessTests.test_service_three_states_and_review_counts_do_not_invent_availability`
- `tests.test_conversation_followup.ConversationFollowupTests.test_human_help_refusal_and_bare_number_are_clarified_without_orders`
- `tests.test_conversation_followup.ConversationFollowupTests.test_footbath_and_gym_are_both_answered_without_fabricated_academic_hours`
- `tests.test_conversation_followup.ConversationFollowupTests.test_multiple_catalog_services_all_receive_a_response`
- `tests.test_conversation_followup.ConversationFollowupTests.test_chitchat_model_cannot_solicit_identity_or_contact_details`
- `tests.test_conversation_followup.ConversationFollowupTests.test_english_chitchat_does_not_match_spa_inside_space`
- `tests.test_conversation_followup.ConversationFollowupTests.test_request_for_staff_does_not_prevent_explicit_cancellation`
- `tests.test_conversation_followup.ConversationFollowupTests.test_staff_and_member_questions_do_not_swallow_real_service`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_missing_local_profile_uses_unverified_academic_facilities`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_explicit_standard_profile_keeps_demo_facility_fixture`
- `tests.test_facility_intents.FacilityIntentTests.test_F01_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F02_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F03_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F04_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F05_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F06_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F07_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F08_closing`
- `tests.test_facility_intents.FacilityIntentTests.test_F09_opening`
- `tests.test_facility_intents.FacilityIntentTests.test_F10_opening`
- `tests.test_facility_intents.FacilityIntentTests.test_F11_opening`
- `tests.test_facility_intents.FacilityIntentTests.test_F12_opening`
- `tests.test_facility_intents.FacilityIntentTests.test_F13_opening`
- `tests.test_facility_intents.FacilityIntentTests.test_F14_opening`
- `tests.test_facility_intents.FacilityIntentTests.test_F15_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F16_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F17_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F18_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F19_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F20_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F21_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F22_fields`
- `tests.test_facility_intents.FacilityIntentTests.test_F23_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F24_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F25_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F26_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F27_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F28_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F29_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F30_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F31_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F32_multi`
- `tests.test_facility_intents.FacilityIntentTests.test_F33_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F34_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F35_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F36_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F37_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F38_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F39_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F40_academic`
- `tests.test_facility_intents.FacilityIntentTests.test_F41_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F42_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F43_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F44_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F45_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F46_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F47_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F48_custom`
- `tests.test_facility_intents.FacilityIntentTests.test_F49_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F50_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F51_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F52_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F53_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F54_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F55_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F56_mixed`
- `tests.test_facility_intents.FacilityIntentTests.test_F57_negative`
- `tests.test_facility_intents.FacilityIntentTests.test_F58_negative`
- `tests.test_facility_intents.FacilityIntentTests.test_F59_negative`
- `tests.test_facility_intents.FacilityIntentTests.test_F60_negative`

### REQ-04 / AC-04 / EXP-04

演示补槽不授予部署跨房权限；模型提取使用stub。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`、`core-tests.log`、`core-tests.json`。

- `tests.test_app.MultiAgentFlowTests.test_reception_asks_for_room_before_dispatch`
- `tests.test_app.MultiAgentFlowTests.test_second_turn_completes_pending_request`
- `tests.test_app.MultiAgentFlowTests.test_room_correction_from_invalid_to_valid`
- `tests.test_app.MultiAgentFlowTests.test_cloud_room_requires_pms_and_mentioned_number`
- `tests.test_deployment.DeploymentTests.test_guest_is_bound_to_own_session_room_and_stay`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_denied_guest_name_switches_to_neutral_address_without_rebinding`
- `tests.test_conversation_followup.ConversationFollowupTests.test_unbound_profile_does_not_invent_identity_or_collect_membership_details`
- `tests.test_conversation_followup.ConversationFollowupTests.test_disputed_name_is_not_asserted_as_identity`

### REQ-05 / AC-05 / EXP-05

Event/Barrier受控竞态和队列保护；不证明生产容量。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_task_runtime.TaskRuntimeTests.test_replace_running_delivery_commits_only_latest_version`
- `tests.test_task_runtime.TaskRuntimeTests.test_cancel_running_delivery_never_creates_ticket_or_reserves_stock`
- `tests.test_task_runtime.TaskRuntimeTests.test_three_rapid_versions_keep_quantity_and_only_publish_latest`
- `tests.test_task_runtime.TaskRuntimeTests.test_other_guest_is_not_blocked_by_slow_department`
- `tests.test_task_runtime.TaskRuntimeTests.test_concurrent_guests_cannot_oversell_shared_inventory`
- `tests.test_task_runtime.TaskRuntimeTests.test_queued_capacity_has_backpressure`
- `tests.test_api.FastAPISessionTests.test_async_submit_accepts_replacement_and_poll_enforces_session`
- `tests.test_task_runtime.TaskRuntimeTests.test_unrelated_faq_queues_without_cancelling_delivery`
- `tests.test_task_runtime.TaskRuntimeTests.test_explicit_delivery_cancel_does_not_discard_active_faq`
- `tests.test_task_runtime.TaskRuntimeTests.test_task_polling_cannot_use_another_session`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_all_cancel_stops_running_and_queued_delivery_before_old_worker_returns`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_explicit_all_cancel_after_faq_releases_every_own_order_once`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_batch_cancel_includes_delivery_identified_by_model_after_queuing`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_batch_cancel_bypasses_blocked_unrelated_business_worker`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_late_delivery_from_unclassified_slot_reply_cannot_commit_after_batch`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_batch_cancel_does_not_touch_another_session_in_same_room`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_item_scoped_batch_status_and_audit_ignore_unrelated_cancellations`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_negated_batch_command_is_not_executed`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_batch_cancel_with_new_or_replacement_request_requires_clarification`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_cross_room_batch_command_cannot_interrupt_current_request`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_cancel_all_checkout_is_not_interpreted_as_delivery_cancellation`
- `tests.test_hotel_readiness.HotelReadinessTests.test_new_regular_item_cancellation_restores_only_reserved_stock`
- `tests.test_hotel_readiness.ConcurrentGuestApiTests.test_eight_guest_clients_share_stock_and_cancel_only_their_own_reservation`

### REQ-06 / AC-06 / EXP-06

ticket/task/version身份、库存、歧义和已派出确认；旧change_id拒绝。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_task_runtime.TaskRuntimeTests.test_completed_compute_can_amend_unshipped_ticket_without_duplicate`
- `tests.test_task_runtime.TaskRuntimeTests.test_dispatched_delivery_waits_for_staff_acknowledgement`
- `tests.test_delivery_language.DeliveryLanguageTests.test_screenshot_followup_cancels_only_water_and_preserves_task_id`
- `tests.test_delivery_language.DeliveryLanguageTests.test_alias_cancel_cannot_supersede_an_unrelated_running_delivery`
- `tests.test_deployment.DeploymentTests.test_stale_change_confirmation_and_pending_completion_are_rejected`
- `tests.test_api.FastAPISessionTests.test_cancelled_delivery_cannot_be_completed`
- `tests.test_task_runtime.TaskRuntimeTests.test_cancel_existing_unshipped_ticket_releases_stock_once`
- `tests.test_task_runtime.TaskRuntimeTests.test_ambiguous_cancel_does_not_cancel_arbitrary_ticket`
- `tests.test_task_runtime.TaskRuntimeTests.test_dispatch_during_amendment_rechecks_actual_delivery_state`
- `tests.test_delivery_language.DeliveryLanguageTests.test_unknown_water_reference_does_not_cancel_existing_drinking_water`
- `tests.test_delivery_language.DeliveryLanguageTests.test_replacing_water_alias_updates_only_its_original_order`
- `tests.test_api.FastAPISessionTests.test_dispatched_change_confirmation_is_idempotent`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_explicit_all_cancel_after_faq_releases_every_own_order_once`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_unknown_and_mixed_catalog_requests_never_create_delivery_ticket`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_batch_cancellation_leaves_dispatched_stock_until_staff_confirmation`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_batch_cancellation_requests_staff_pickup_for_completed_delivery`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_unknown_items_in_additions_conjunctions_and_polite_requests_are_not_dropped`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_catalog_validation_also_applies_to_direct_department_dispatch`
- `tests.test_hotel_readiness.HotelReadinessTests.test_regular_catalog_has_at_least_24_stocked_items_and_safe_metadata`
- `tests.test_hotel_readiness.HotelReadinessTests.test_stock_question_uses_current_inventory_without_model_or_ticket`
- `tests.test_hotel_readiness.HotelReadinessTests.test_catalog_question_does_not_swallow_delivery_or_cancellation`
- `tests.test_hotel_readiness.HotelReadinessTests.test_spoken_delivery_fillers_are_not_unknown_products`
- `tests.test_conversation_followup.ConversationFollowupTests.test_measured_unknown_compounds_never_partially_order_water`
- `tests.test_conversation_followup.ConversationFollowupTests.test_known_sku_in_same_clause_does_not_hide_an_unknown_measured_noun`
- `tests.test_conversation_followup.ConversationFollowupTests.test_all_unknown_nouns_are_reported_instead_of_only_last_one`
- `tests.test_conversation_followup.ConversationFollowupTests.test_valid_delivery_quantities_and_facility_questions_are_not_unknown_nouns`
- `tests.test_conversation_followup.ConversationFollowupTests.test_negated_unknown_and_pure_price_faq_do_not_block_a_valid_towel`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_cancelled_shortage_does_not_request_restock`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_fully_reserved_delivery_remains_ready_when_unreserved_stock_is_zero`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_in_transit_delivered_and_change_pending_are_distinct`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_malformed_legacy_field_types_are_reviewed_instead_of_crashing`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_non_delivery_has_no_inventory_fulfillment`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_old_unknown_or_incomplete_details_are_reviewed_without_guessing`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_ready_line_has_canonical_item_unit_and_all_hand_checked_quantities`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_replenished_stock_is_not_automatically_reserved_or_dispatched`
- `tests.test_ticket_fulfillment.DeliveryFulfillmentProjectionTests.test_shortage_line_shows_seven_requested_six_available_one_missing`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_new_shortage_is_persisted_as_coordination_and_rejected_by_both_actions`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_shortage_replaced_with_available_quantity_becomes_ready`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_valid_dispatch_and_completion_never_deduct_stock_twice`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_named_quantity_change_targets_shortage_among_multiple_active_orders`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_new_item_only_or_duplicate_target_orders_still_require_clarification`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_water_alias_before_change_marker_identifies_its_own_order`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_adjacent_quantities_and_conjunctions_normalize_water`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_ba_water_reminder_is_a_known_noun_not_an_unknown_phrase`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_cancelled_order_cannot_be_revived_by_reminder`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_completed_order_cannot_be_revived_by_reminder`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_fast_delivery_does_not_hide_unknown_products`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_followup_keeps_facility_answer_in_same_turn`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_more_water_with_quantity_remains_a_new_request`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_named_progress_question_does_not_choose_latest_towel_order`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_negated_reminder_does_not_change_order_or_inventory`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_negative_speed_modifier_keeps_new_order_at_normal_priority`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_new_quantity_with_cui_phrase_is_not_swallowed_by_legacy_pacing`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_pacing_and_hunger_are_not_stock_items`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_plumbing_and_toiletry_water_is_not_drinking_water`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_quantityless_cui_targets_only_own_order_even_if_other_room_exists`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_reminder_never_reduces_existing_high_priority`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_reminder_targets_water_preserving_other_tickets_and_stock`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_reminder_without_order_does_not_create_phantom_order`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_repeat_reminder_is_recorded_without_duplicate_reservation`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_shortage_reminder_keeps_unconfirmed_delivery_status`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_two_water_orders_require_clarification`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_unknown_addition_with_existing_order_progress_is_not_dropped`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_user_compound_reports_only_the_real_unknown_item`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_user_sequence_keeps_staff_confirmed_replacement_and_single_water_order`

### REQ-07 / AC-07 / EXP-07

错物/错房/虚构库存/风险降级拒绝；人工核验不补派被拒工单。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`、`core-tests.log`、`core-tests.json`、`blackboard.log`、`blackboard.json`。

- `tests.test_task_runtime.TaskRuntimeTests.test_conflicting_child_item_is_rejected_without_stock_commit`
- `tests.test_task_runtime.TaskRuntimeTests.test_supervisor_cannot_accept_invented_inventory`
- `tests.test_task_runtime.TaskRuntimeTests.test_child_cannot_downgrade_safety_priority`
- `tests.test_api.FastAPISessionTests.test_conflicting_child_is_visible_as_review_without_order`
- `tests.test_streamlit.GuestInteractionTests.test_staff_can_review_conflict_without_bypassing_validation`
- `tests.test_app.MultiAgentFlowTests.test_angry_ticket_gets_red_risk_alert`
- `tests.test_service_reliability.DeliveryReliabilityTests.test_supervisor_rejects_child_that_silently_drops_unknown_item`
- `tests.test_hotel_readiness.HotelReadinessTests.test_broken_bath_is_repair_not_a_service_catalog_question`
- `tests.test_hotel_readiness.HotelReadinessTests.test_smoke_or_electric_leak_is_high_risk_even_if_model_says_chitchat`
- `tests.test_hotel_readiness.HotelReadinessTests.test_delivery_urgency_is_medium_but_explicitly_not_urgent_remains_low`
- `tests.test_hotel_readiness.HotelReadinessTests.test_explicit_denial_of_smoke_does_not_raise_an_ordinary_light_repair`
- `tests.test_conversation_followup.ConversationFollowupTests.test_direct_department_cannot_drop_unknown_measured_addition`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_direct_completion_and_guest_confirmation_do_not_bypass_legacy_review`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_legacy_erroneous_mixed_ticket_cannot_dispatch_or_complete`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_pending_change_cannot_be_dispatched_even_if_stock_is_reserved`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_delivery_followup_cannot_swallow_smoke_emergency`

### REQ-08 / AC-08 / EXP-08

单库第二writer拒绝、聊天/员工故障回滚；非真实断电实验。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_second_writer_is_rejected_instead_of_overwriting`
- `tests.test_deployment.DeploymentTests.test_write_failure_rolls_back_stock_and_does_not_ack_success`
- `tests.test_deployment.DeploymentTests.test_atomic_checkpoint_rolls_back_if_session_write_fails`
- `tests.test_deployment.DeploymentTests.test_staff_storage_failure_is_unavailable_and_rolls_back`
- `tests.test_ticket_fulfillment.TicketFulfillmentIntegrationTests.test_shortage_state_and_derived_details_survive_restart`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_reminder_record_survives_sqlite_restart`

### REQ-09 / AC-09 / EXP-09

同载荷复用run_id，重启/裁剪仍不重复；改载荷409。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_task_runtime.TaskRuntimeTests.test_request_id_retry_does_not_duplicate_delivery`
- `tests.test_deployment.DeploymentTests.test_request_retry_survives_restart_without_second_order`
- `tests.test_deployment.DeploymentTests.test_pruned_run_keeps_durable_idempotency_record`
- `tests.test_api.FastAPISessionTests.test_async_submit_accepts_replacement_and_poll_enforces_session`
- `tests.test_delivery_followup.DeliveryFollowupTests.test_reminder_request_id_replay_is_idempotent`

### REQ-10 / AC-10 / EXP-10

在线窗口与16条归档、8条分页计数、持久日期和interrupted恢复分别检查。当前命名分页测试只断言返回8条，尚未独立核对恰为最早8个消息ID、顺序和无重复；EXP-10保留这些目标判据，不能将行数通过写成身份完整性已证明。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_history_survives_window_compaction_and_can_be_paged`
- `tests.test_deployment.DeploymentTests.test_initial_business_date_is_configurable_and_retained`
- `tests.test_persistence.PersistenceTests.test_business_and_session_state_survive_process_restart`
- `tests.test_persistence.PersistenceTests.test_restart_marks_inflight_task_as_interrupted`
- `tests.test_deployment.DeploymentTests.test_department_cannot_complete_other_department_ticket`
- `tests.test_api.FastAPISessionTests.test_multi_session_isolation`
- `tests.test_deployment.DeploymentTests.test_new_guest_does_not_read_previous_room_orders`
- `tests.test_evolution.SelfEvolutionAndMemoryTests.test_sliding_window_memory_summary`
- `tests.test_service_reliability.TranscriptRetentionTests.test_model_compaction_does_not_delete_guest_transcript`
- `tests.test_service_reliability.TranscriptRetentionTests.test_durable_archive_and_neutral_address_survive_restart`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_local_can_reach_messages_beyond_one_hundred`
- `tests.test_service_reliability.TranscriptRetentionTests.test_memory_archive_pages_are_exclusive_ordered_and_private`
- `tests.test_service_reliability.TranscriptRetentionTests.test_reset_removes_only_the_current_sessions_archive`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_local_main_chat_reads_archive_after_compaction`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_polling_preserves_old_page_until_return_to_latest`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_replacement_local_runtime_cannot_reuse_old_history_page`
- `tests.test_guest_history_ui.DeployedGuestHistoryUITests.test_deployed_main_chat_reads_authorized_archive`
- `tests.test_guest_history_ui.DeployedGuestHistoryUITests.test_deployed_can_reach_messages_beyond_one_hundred`
- `tests.test_guest_history_ui.DeployedGuestHistoryUITests.test_changed_guest_pass_discards_previous_history_page`
- `tests.test_hotel_readiness.HotelReadinessTests.test_catalog_expansion_does_not_invent_stock_in_existing_database`
- `tests.test_guest_history_ui.CumulativeHistoryStateTests.test_initial_and_older_reads_accumulate_sixty_without_dropping_newer_records`
- `tests.test_guest_history_ui.CumulativeHistoryStateTests.test_poll_and_return_latest_deduplicate_without_losing_loaded_beginning`
- `tests.test_guest_history_ui.CumulativeHistoryStateTests.test_poll_fills_the_gap_when_more_than_sixty_new_messages_arrive`
- `tests.test_guest_history_ui.CumulativeHistoryStateTests.test_runtime_api_session_stay_and_grant_changes_reset_cumulative_cache`
- `tests.test_guest_history_ui.LocalGuestHistoryUITests.test_local_return_latest_recreates_viewport_with_unchanged_messages`
- `tests.test_guest_history_ui.DeployedGuestHistoryUITests.test_deployed_polling_and_latest_keep_accumulated_archive`
- `tests.test_guest_history_ui.DeployedGuestHistoryUITests.test_deployed_return_latest_recreates_viewport_with_unchanged_messages`
- `tests.test_guest_history_ui.CumulativeHistoryStateTests.test_latest_keeps_native_scroll_observer_enabled_until_reading_older`

### REQ-11 / AC-11 / EXP-11

已知餐券表达、保洁直调/改券保护已实现；有4方法红绿和81项焦点回归。不是任意语言或真实发券证明。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_academic_meeting_and_checkout_need_real_confirmation`
- `tests.test_deployment.DeploymentTests.test_academic_breakfast_is_not_an_automatic_entitlement`
- `tests.test_deployment.DeploymentTests.test_checkout_approval_is_bound_to_current_occupant_request`
- `tests.test_deployment.DeploymentTests.test_academic_coupon_delivery_wording_requires_review_without_stock_reservation`
- `tests.test_deployment.DeploymentTests.test_academic_housekeeping_cannot_dispatch_coupons_directly`
- `tests.test_deployment.DeploymentTests.test_academic_housekeeping_cannot_replace_delivery_with_coupons`
- `tests.test_deployment.DeploymentTests.test_academic_coupon_replacement_preserves_existing_delivery`
- `tests.test_hotel_readiness.HotelReadinessTests.test_room_access_and_academic_vouchers_queries_require_verification`
- `tests.test_conversation_followup.ConversationFollowupTests.test_member_query_uses_bound_profile_or_academic_policy_without_cloud`

### REQ-12 / AC-12 / EXP-12

夜审日期、借用品通知和回收待检；不等价外部财务夜审。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_night_audit_cannot_be_repeated_with_stale_date`
- `tests.test_task_runtime.TaskRuntimeTests.test_night_audit_cannot_be_overwritten_by_older_snapshot`
- `tests.test_task_runtime.TaskRuntimeTests.test_delivered_item_cannot_be_restocked_without_return_confirmation`
- `tests.test_task_runtime.TaskRuntimeTests.test_staff_confirmation_updates_borrowed_record_and_notifies_guest`
- `tests.test_task_runtime.TaskRuntimeTests.test_rejected_change_keeps_stock_and_clears_waiting_status`

### REQ-13 / AC-13 / EXP-13

AppTest经HotelApi/TestClient访问唯一临时API；两进程/容器仍须FIELD-01。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeployedUIBoundaryTests.test_no_credential_does_not_create_unprotected_business_ui`
- `tests.test_deployment.DeployedUIBoundaryTests.test_guest_ui_only_receives_own_service_view`
- `tests.test_deployment.DeployedUIBoundaryTests.test_staff_ui_shares_api_without_a_second_writer`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_closed_orders_have_no_active_replenishment_request`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_dynamic_ticket_quote_cannot_inject_html`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_each_order_keeps_its_own_item_quantity_and_in_transit_completion`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_engineering_ticket_keeps_ordinary_completion`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_historical_unknown_and_missing_fields_require_verification`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_pending_change_disables_completion_and_preserves_confirmation`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_reserved_water_shows_canonical_quantities_even_when_available_zero`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_revised_order_recomputes_item_and_removes_old_shortage`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_shortage_is_coordination_with_gap_and_no_dispatch`
- `tests.test_operations_ticket_ui.ApiOperationsTicketUITests.test_zero_stock_and_recovered_but_unreserved_have_distinct_states`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_closed_orders_have_no_active_replenishment_request`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_dynamic_ticket_quote_cannot_inject_html`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_each_order_keeps_its_own_item_quantity_and_in_transit_completion`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_engineering_ticket_keeps_ordinary_completion`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_historical_unknown_and_missing_fields_require_verification`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_pending_change_disables_completion_and_preserves_confirmation`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_reserved_water_shows_canonical_quantities_even_when_available_zero`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_revised_order_recomputes_item_and_removes_old_shortage`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_shortage_coordination_is_not_counted_as_dispatch_queue_or_eta`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_shortage_is_coordination_with_gap_and_no_dispatch`
- `tests.test_operations_ticket_ui.LocalOperationsTicketUITests.test_zero_stock_and_recovered_but_unreserved_have_distinct_states`
- `tests.test_visual_contract.VisualContractTests.test_native_help_portal_and_descendants_have_theme_contrast`

### REQ-14 / AC-14 / EXP-14

本地配置/备份和 MODEL-CONN-01 代码预检有测试；当前镜像、Compose、卷权限、连续现场探测、聊天 canary 与现场恢复未执行。ec6331f历史CI构建不能替代当前构建。连接门槛是连续 3 次 `/v1/models` HTTP 200、选定模型命中、每次≤12秒、100%成功率；失败不能静默降级成“云端在线”。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`。

- `tests.test_deployment.DeploymentTests.test_secret_generation_never_overwrites_deployment`
- `tests.test_deployment.DeploymentTests.test_deployed_mode_rejects_missing_credentials`
- `tests.test_deployment.DeploymentTests.test_invalid_staff_mapping_fails_configuration_validation`
- `tests.test_deployment.DeploymentTests.test_validated_backup_includes_latest_committed_order`
- `tests.test_model_connection.ModelConnectionTests.test_explicit_cloud_connection_clears_offline_switch_and_probes_model`
- `tests.test_model_connection.ModelConnectionTests.test_missing_openai_sdk_is_reported_instead_of_silent_fallback`
- `tests.test_model_connection.ModelConnectionTests.test_missing_openai_sdk_uses_http_compatibility_transport`
- `tests.test_model_connection.ModelConnectionTests.test_probe_rejects_a_model_that_is_not_listed`

### REQ-15 / AC-15 / EXP-15

固定20条离线规则、字符相似度Few-Shot；没有模型泛化或提升结论。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`、`core-tests.log`、`core-tests.json`、`harness.log`、`harness-structured.log`、`harness.json`。

- `tests.test_app.MultiAgentFlowTests.test_harness_dataset_regression_metrics`
- `tests.test_app.MultiAgentFlowTests.test_harness_other_accepts_chitchat_alias`
- `tests.test_evolution.SelfEvolutionAndMemoryTests.test_dynamic_exemplar_curator_retrieves_block`
- `tests.test_evolution.SelfEvolutionAndMemoryTests.test_hard_case_miner_loads_default_cases`
- `tests.test_evolution.SelfEvolutionAndMemoryTests.test_evolution_delta_runner_cycle`

### REQ-16 / AC-16 / EXP-16

验证器负向夹具不等价完整发布通过；最终必须由统一入口核验本次实际执行的全部映射方法。

本次证据：`full-tests.log`、`full-tests.json`、`manifest.json`、`core-tests.log`、`core-tests.json`、`harness.log`、`harness-structured.log`、`harness.json`、`blackboard.log`、`blackboard.json`、`traceability.json`、`validation.log`。

- `tests.test_release_verification.ReleaseVerificationTests.test_blackboard_broken_trace_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_blackboard_manual_change_requires_real_before_and_after_states`
- `tests.test_release_verification.ReleaseVerificationTests.test_child_guard_marks_missing_dependency_without_fabricating_version`
- `tests.test_release_verification.ReleaseVerificationTests.test_child_guard_records_actual_isolated_dependency_versions`
- `tests.test_release_verification.ReleaseVerificationTests.test_core_suite_rejects_tests_from_another_module`
- `tests.test_release_verification.ReleaseVerificationTests.test_existing_output_directory_is_rejected`
- `tests.test_release_verification.ReleaseVerificationTests.test_failure_still_writes_manifest_and_returns_nonzero`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_cli_exit_zero_does_not_mask_fail_rows`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_cli_extra_passing_case_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_cli_missing_case_does_not_pass_on_pass_substrings`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_cli_unknown_status_rows_are_not_ignored`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_cloud_mode_is_not_offline_evidence`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_duplicate_case_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_each_metric_is_required_and_exact`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_extra_passing_case_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_failed_row_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_harness_missing_baseline_case_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_json_from_another_run_is_rejected`
- `tests.test_release_verification.ReleaseVerificationTests.test_manifest_uses_child_versions_and_rejects_inconsistent_receipts`
- `tests.test_release_verification.ReleaseVerificationTests.test_missing_json_is_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_real_unittest_failure_preserves_nonzero_and_structured_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_real_unittest_skip_exit_zero_is_rejected`
- `tests.test_release_verification.ReleaseVerificationTests.test_real_unittest_zero_test_report_is_rejected`
- `tests.test_release_verification.ReleaseVerificationTests.test_runner_preserves_raw_logs_and_exit_code_on_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_safe_environment_removes_real_data_and_model_configuration`
- `tests.test_release_verification.ReleaseVerificationTests.test_safety_guards_block_external_network_and_non_temporary_sqlite`
- `tests.test_release_verification.ReleaseVerificationTests.test_same_test_count_cannot_replace_a_committed_baseline_identity`
- `tests.test_release_verification.ReleaseVerificationTests.test_source_capture_hashes_release_control_files_and_detects_drift`
- `tests.test_release_verification.ReleaseVerificationTests.test_stale_json_timestamp_is_rejected`
- `tests.test_release_verification.ReleaseVerificationTests.test_traceability_accepts_partial_without_claiming_field_readiness`
- `tests.test_release_verification.ReleaseVerificationTests.test_traceability_rejects_loadable_but_unexecuted_test`
- `tests.test_release_verification.ReleaseVerificationTests.test_traceability_rejects_unloadable_test`
- `tests.test_release_verification.ReleaseVerificationTests.test_traceability_requires_all_sixteen_requirements`
- `tests.test_release_verification.ReleaseVerificationTests.test_unittest_exit_zero_cannot_mask_failed_result`
- `tests.test_release_verification.ReleaseVerificationTests.test_unittest_expected_failure_is_not_a_full_pass`
- `tests.test_release_verification.ReleaseVerificationTests.test_unittest_forged_count_cannot_replace_executed_identities`
- `tests.test_release_verification.ReleaseVerificationTests.test_unittest_skips_are_failure`
- `tests.test_release_verification.ReleaseVerificationTests.test_unittest_zero_tests_is_failure`

## 后续要求与现场目标

| 后续要求 | 状态 | 目标实验与缺口 |
| --- | --- | --- |
| FUT-01 · 真实PMS双向适配 | `planned` | FIELD-05；外部PMS适配器/回执与补偿须先实现。 |
| FUT-02 · 学校SSO | `planned` | FIELD-05；SSO适配器和身份生命周期须先实现。 |
| FUT-03 · 真实电子券发行/核销/撤回及订单对账 | `planned` | 真实电子券适配及发行/核销/撤回实验尚需另行设计；FIELD-04只审核政策，不证明真实发券。 |
| FUT-04 · 学校批准知识与语义检索审核 | `planned` | FIELD-04审核政策，FIELD-06独立检索/模型评测；当前不是语义RAG。 |
| FUT-05 · 现场容量、告警及恢复演练 | `planned` | FIELD-01/02/03；鉴权负载驱动及隔离进程故障钩子是未完成前置，不得使用会reset且无鉴权的tests/stress_test.py跑现场。 |
| FUT-06 · 线上失败样例治理及独立对照 | `planned` | FIELD-06；授权、脱敏、审核、版本化与独立对照未交付。 |

所有FIELD目标仍为TARGET_NOT_RUN；目标阈值及上线阻断见实验/验收文件，不把本地规则结果推成现场结论。
