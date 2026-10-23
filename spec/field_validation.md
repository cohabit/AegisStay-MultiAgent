# 现场验收协议：学校学术酒店

本文件是 FIELD-01～FIELD-06 的权威执行协议。它描述验收目标、控制数据、操作顺序和判定证据；不表示现场已经执行。每一项当前状态均为 `TARGET_NOT_RUN`，现场总体仍为 `field_readiness=NOT_VERIFIED`。离线代码验收的 `scope=offline-code-baseline` 与本协议的现场结果分开记录。

## 共同边界与证据契约

现场只能在酒店批准的验收沙箱运行，使用合成入住、合成政策和个人测试凭据。不得连接真实住客、生产 SQLite、真实 PMS/SSO、真实电子券或未批准的模型端点。任何写操作必须带 `session_id`、`room_no`、`stay_id`、`request_id` 和可追溯的 `run_id`；超时结果显示为待确认，不能猜测成功。

每次实验建立全新的 `.artifacts/field/<run-id>/`，`run-id` 为 UTC 时间加随机后缀，禁止复用或手工改写旧目录。下列文件是所有 FIELD 的最低证据包：

| 文件 | 内容 |
| --- | --- |
| `field-plan.json` | FIELD 编号、目标、沙箱、设备/资源、源码 commit 与工作区状态、镜像 `image_ref` 与 immutable `image_digest`、依赖版本、合成数据哈希、批准范围、执行人和批准角色 |
| `commands.log` | 原样命令、UTC 开始/结束时间、退出码；不含令牌或完整环境变量 |
| `requests.jsonl` | 脱敏逐请求/事件记录：单调时钟、会话哈希、`request_id`、`run_id`、HTTP 状态、业务状态、耗时；没有真实姓名、令牌或聊天逐字稿 |
| `before.json` / `after.json` | 实验前后库存、工单、审批、会话、幂等、审计、业务日期和外部关联 ID 快照 |
| `measurements.json` | 原始样本、分子/分母、P95/P99 算法、RPO/RTO、重复次数、错误分类和安全计数 |
| `result.json` | 每项门槛的 `PASS`/`FAIL`/`NOT_RUN`、证据文件哈希、失败原因、清理结果和批准签名状态 |

执行前必须先用项目解释器跑离线基线，并把它的 `manifest.json` 引用写进 `field-plan.json`；这一步不提升现场状态：

```powershell
$py = (Resolve-Path '.venv\Scripts\python.exe').Path
$run = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-field'
$dir = Join-Path '.artifacts\field' $run
$offlineDir = Join-Path $dir 'offline-baseline'
New-Item -ItemType Directory -Force $dir | Out-Null
$env:AEGIS_FORCE_RULES = '1'
$env:AEGIS_AUTH_ENABLED = '0'
$env:AEGIS_DEPLOYED = '0'
$env:AEGIS_DB_PATH = ''
$env:AEGIS_SITE_PROFILE = 'standard'
Remove-Item Env:AEGIS_API_URL,Env:AEGIS_BUSINESS_DATE,Env:AEGIS_PMS_FILE,Env:AEGIS_KNOWLEDGE_FILE -ErrorAction SilentlyContinue
& $py -B -X utf8 scripts/verify_release.py --output-dir $offlineDir
if ($LASTEXITCODE -ne 0) { throw '离线基线未通过，停止现场验收' }
```

现场运行的最低总规则是：任何安全反例、跨身份/跨房泄漏、负库存、重复副作用、已确认数据丢失、未记录原始证据、令牌泄漏或未经批准的配置都直接 `FAIL`；驱动、适配器或故障钩子不存在时记录 `NOT_RUN`，不能用静态文件或历史截图代替。失败后保留原始目录，修复或调整经批准后必须用新的 `run-id` 重跑。

## FIELD-01：真实容器、非 root、API-only 持久化与就绪

**目标与状态**：验证 Compose 交付在真实验收主机上以非 root、单 API 写入者、命名持久卷和 API-only 工作台运行；关联 REQ-13/14。状态：`TARGET_NOT_RUN`。

**所有者与批准**：平台/运维负责人执行；酒店 IT 负责人批准主机、网络和卷范围；酒店安全负责人批准凭据和日志脱敏。两方批准记录进入 `field-plan.json`。

**前置条件与控制数据**：独立验收 checkout；Docker Engine/Compose 版本、CPU/内存限制、端口 8000/8501 空闲；专用 Compose project `aegis-acceptance`，无生产卷；固定源码 commit、`requirements.txt` 哈希和本次初始化日期。只使用合成入住 1128/808、合成工单和测试凭据。首次在沙箱生成配置，禁止打印内容：

```powershell
& $py -B -X utf8 scripts/manage.py init --output deploy.env
docker version
docker compose version
docker compose -p aegis-acceptance config --quiet
docker compose -p aegis-acceptance build
docker compose -p aegis-acceptance up -d
docker compose -p aegis-acceptance ps
docker compose -p aegis-acceptance exec -T api id -u
docker compose -p aegis-acceptance exec -T web id -u
```

**执行步骤与门槛**：

1. `config --quiet`、构建和启动均退出 0；保存镜像 digest、容器摘要、Compose 配置和 `/ready` 探测时间。
2. API 与 web 的 UID 均为 `10001` 且不为 0；API 只有一个写入 worker；web 无数据库凭据，只通过 `http://api:8000` 调 API。
3. 在前台签发合成住客，完成“2 毛巾→派送→改 2 浴巾→确认当前 `change_id`”；重建 web 后业务不丢，重启 API 后仍为 1 张当前工单、浴巾 22、毛巾 24。旧 `change_id` 返回 409，跨房请求返回 403。
4. 以第二进程尝试打开同一卷库，必须被单写入者锁拒绝；直接写库或 web 自建 writer 均为失败。
5. `/ready` 在 120 秒内返回 200 且 `persistent=true`；只返回 `/health` 不能替代就绪证据。

**证据文件**：共同文件加 `docker-version.txt`、`compose-config.txt`、`image-digests.json`、`container-inspect.json`、`ready-probes.json`、`persistence-restart.json`、`writer-lock.json`。`requests.jsonl` 需包含上述业务请求和响应状态。

**判定、清理与回滚**：root 运行、卷不可写、双 writer、API 未持久化、超时无待确认状态、任何库存/工单不一致均 `FAIL`。测试结束执行 `docker compose -p aegis-acceptance down`，不使用 `down -v`，保留卷和证据供调查且不得停止其他项目。配置或镜像失败时回滚到本次实验开始记录的源码 commit/镜像 digest；不得把删卷称为恢复。

## FIELD-02：20 会话、6000 请求容量与延迟

**目标与状态**：验证部署模式在合成轻负载下的并发、延迟、限流和正确性边界；关联 FUT-05/REQ-14。状态：`TARGET_NOT_RUN`，在任何多人接待或容量 SLA 承诺前阻断。

**所有者与批准**：SRE/容量负责人执行；酒店 IT 批准主机资源和观测权限；值班运营负责人批准负载时间窗。安全负责人批准测试凭据和数据清理。

**前置条件与控制数据**：FIELD-01 必须先有已批准的 PASS 证据；`field-plan.json` 引用 FIELD-01 的 `run-id`、commit、镜像 digest 和结果哈希，但 FIELD-02 使用自己的全新 `run-id`，不复用 FIELD-01 的证据目录。固定主机、OS、CPU/内存、网络位置、worker/pending 配置和初始合成数据。创建 20 个独立住客会话，限制为最多 20 个并发飞行请求；每凭证遵守 20 次/分钟。现有 `tests/stress_test.py` 没有部署 Authorization 且会调用 `/api/admin/reset`，不能直接作为本实验驱动，也不能指向真实服务。

**执行命令与步骤**：负载驱动尚未在仓库交付，故先记录以下可执行前置检查；驱动缺失时结果必须为 `NOT_RUN`：

```powershell
$py = (Resolve-Path '.venv\Scripts\python.exe').Path
& $py -B -X utf8 scripts/verify_release.py --output-dir $offlineDir
Get-Content tests/stress_test.py
```

驱动获批后，执行人必须把实际命令和固定 seed 写入 `commands.log`，再按以下顺序执行：

1. 预热 2 分钟，不计入指标；随后 20 个会话持续 30 分钟，每 6 秒一请求，至少 6000 个业务请求，保留完整请求清单。
2. 请求比例固定为 FAQ 70%、本人 session 读取 10%、分页历史 10%、延退申请/查询 10%；另重复库存竞争和 10% `request_id` 重放作为正确性对照。
3. 记录每请求网络到持久化完成的端到端耗时，P95/P99 用排序后的 nearest-rank，报告分子/分母，不排除慢请求。独立过载阶段对同一凭证 1 分钟内第 21 次提交预期 429，队列饱和预期 503；预期 429/503 不纳入正常成功分母。

**通过门槛**：至少 6000 请求、20 个独立会话；预期成功比例（HTTP 与 `run_status` 同时符合）≥99.5%；非预期 5xx/超时 ≤0.5%；端到端 P95≤1500 ms、P99≤3000 ms；跨会话泄漏、负库存、重复执行、丢失已确认提交均为 0。任一硬正确性计数非 0 直接 `FAIL`；未提供逐请求原始数据或驱动不存在为 `NOT_RUN`。

**证据文件**：共同文件加 `load-config.json`、`request-list.jsonl`、`load-summary.json`、`latency-samples.json`、`rate-limit.json`、`leak-check.json`、`idempotency-check.json`、`sqlite-reconciliation.json`。

**清理与回滚**：停止驱动、关闭测试 API、归档合成库和凭据；不重置真实服务，不通过放宽阈值掩盖失败。性能未达只报告 `FAIL` 并保留样本，恢复此前批准的 worker/pending 配置后用新 run 重试。

## FIELD-03：崩溃、备份、RPO/RTO 与两故障点

**目标与状态**：验证提交前中断与提交后响应丢失两种故障的原子性、幂等、备份和恢复；关联 REQ-08/09/10/14、FUT-05。状态：`TARGET_NOT_RUN`。

**所有者与批准**：可靠性/存储负责人执行；酒店 IT 批准故障窗口、备份介质和恢复主机；业务值班负责人批准合成工单和停机演练。任何真实卷不得参与。

**前置条件与控制数据**：专用验收卷；固定业务日期和库存。备份必须先写入容器临时路径，再复制到独立的宿主恢复目录或独立恢复卷，并记录源库/备份/恢复副本哈希；不能把唯一备份留在与数据库相同的 `/data` 卷。当前代码没有生产故障接口，必须先交付版本化的 Event/FIFO 故障钩子，在 `task.commit` 前发出 `before_commit` 并阻塞；不得用延时猜杀。每轮从独立副本恢复 10 个已确认提交，每单 2 条毛巾（共 20 条，库存 4），第 11 个请求在钩子处阻塞。

**精确命令与步骤**：

```powershell
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-' + ([guid]::NewGuid().ToString('N').Substring(0,8)) + '-FIELD-03'
$fieldRun = Join-Path (Resolve-Path '.artifacts\field').Path $runId
$recovery = Join-Path $fieldRun 'recovery-medium'
New-Item -ItemType Directory -Force $recovery | Out-Null
# 先记录服务库源哈希，再把备份复制到独立宿主目录；/data 中的文件不是独立恢复介质。
docker compose -p aegis-acceptance exec -T api sh -lc 'for f in /data/aegisstay.sqlite3 /data/aegisstay.sqlite3-wal /data/aegisstay.sqlite3-shm; do if [ -f "$f" ]; then sha256sum "$f"; else echo "ABSENT $f"; fi; done'
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/manage.py backup --database /data/aegisstay.sqlite3 --output /tmp/field-before.sqlite3
docker compose -p aegis-acceptance exec -T api sha256sum /tmp/field-before.sqlite3 | Tee-Object -FilePath (Join-Path $recovery 'backup-temp.sha256')
docker compose -p aegis-acceptance cp api:/tmp/field-before.sqlite3 (Join-Path $recovery 'field-before.sqlite3')
$backupHash = (Get-FileHash (Join-Path $recovery 'field-before.sqlite3') -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $recovery 'backup.sha256') -Value $backupHash -NoNewline
# 以下是故障钩子、恢复工具和 field-compose 覆盖文件交付后的固定 CLI 契约；当前仓库没有这些命令，因此当前状态为 NOT_RUN。
$fieldPlan = Get-Content (Join-Path $fieldRun 'field-plan.json') | ConvertFrom-Json
$imageRef = $fieldPlan.image_ref
$imageDigest = $fieldPlan.image_digest
if (-not $imageRef -or -not $imageDigest) { throw 'FIELD-01 image_ref and immutable image_digest are required before FIELD-03' }
$image = "$imageRef@$imageDigest"

# 每个故障点都是独立 run：先停 writer，创建全新的恢复卷，再恢复/校验；API 只在恢复卷准备好后启动。
$beforeRun = "$runId-before"
$beforeVolume = "aegis_field03_restore_$($beforeRun.ToLowerInvariant())"
docker compose -p aegis-acceptance down
docker volume create $beforeVolume
docker run --rm -v "${beforeVolume}:/data" -v "${recovery}:/recovery:ro" $image python -B -X utf8 scripts/field_restore.py --input /recovery/field-before.sqlite3 --database /data/aegisstay.sqlite3 --expected-sha256 $backupHash --no-overwrite
docker run --rm -v "${beforeVolume}:/data" $image python -B -X utf8 scripts/manage.py integrity-check --database /data/aegisstay.sqlite3
$env:AEGIS_FIELD_DATA_VOLUME = $beforeVolume
docker compose -p aegis-acceptance -f docker-compose.yml -f docker-compose.field-restore.yml up -d api
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_fault_hook.py install --point before_commit --ready-file /tmp/before_commit.ready
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_request_driver.py submit --room 1128 --item 毛巾 --quantity 2 --request-id "$beforeRun-11" --wait-for before_commit.ready
docker compose -p aegis-acceptance kill -s SIGKILL api
docker compose -p aegis-acceptance down
docker volume rm $beforeVolume

# 提交后响应丢失必须从新的 run-id/restoreVolume 开始；等待 after_commit.ready 后再杀进程。
$afterRun = "$runId-after"
$afterVolume = "aegis_field03_restore_$($afterRun.ToLowerInvariant())"
docker volume create $afterVolume
docker run --rm -v "${afterVolume}:/data" -v "${recovery}:/recovery:ro" $image python -B -X utf8 scripts/field_restore.py --input /recovery/field-before.sqlite3 --database /data/aegisstay.sqlite3 --expected-sha256 $backupHash --no-overwrite
docker run --rm -v "${afterVolume}:/data" $image python -B -X utf8 scripts/manage.py integrity-check --database /data/aegisstay.sqlite3
$env:AEGIS_FIELD_DATA_VOLUME = $afterVolume
docker compose -p aegis-acceptance -f docker-compose.yml -f docker-compose.field-restore.yml up -d api
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_fault_hook.py install --point after_commit_before_response --ready-file /tmp/after_commit.ready
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_request_driver.py submit --room 1128 --item 毛巾 --quantity 2 --request-id "$afterRun-11" --wait-for after_commit.ready
docker compose -p aegis-acceptance kill -s SIGKILL api
docker compose -p aegis-acceptance down
docker volume rm $afterVolume

# 磁盘满/只读是第三个独立 run；逐请求状态写入 disk-full-readonly.json，清理 marker 后再停 writer。
$faultRun = "$runId-fault"
$faultVolume = "aegis_field03_restore_$($faultRun.ToLowerInvariant())"
docker volume create $faultVolume
docker run --rm -v "${faultVolume}:/data" -v "${recovery}:/recovery:ro" $image python -B -X utf8 scripts/field_restore.py --input /recovery/field-before.sqlite3 --database /data/aegisstay.sqlite3 --expected-sha256 $backupHash --no-overwrite
docker run --rm -v "${faultVolume}:/data" $image python -B -X utf8 scripts/manage.py integrity-check --database /data/aegisstay.sqlite3
$env:AEGIS_FIELD_DATA_VOLUME = $faultVolume
docker compose -p aegis-acceptance -f docker-compose.yml -f docker-compose.field-restore.yml up -d api
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_fault_hook.py install --point disk_full --mode disk_full --bytes 1048576
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_fault_hook.py install --point read_only --mode read_only
# 发送写请求并记录 /ready/HTTP/业务状态，清理两种故障点后关闭服务。
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_request_driver.py probe-faults --request-id "$faultRun-fault" --output /tmp/disk-full-readonly.json
docker compose -p aegis-acceptance cp api:/tmp/disk-full-readonly.json (Join-Path $fieldRun 'disk-full-readonly.json')
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_fault_hook.py clear --point disk_full
docker compose -p aegis-acceptance exec -T api python -B -X utf8 scripts/field_fault_hook.py clear --point read_only
docker compose -p aegis-acceptance down
docker volume rm $faultVolume
```

`field_request_driver.py`、`field_fault_hook.py`、`field_restore.py`、`manage.py integrity-check` 和 `docker-compose.field-restore.yml` 是本实验要求交付的版本化工具契约；覆盖文件必须把 `${AEGIS_FIELD_DATA_VOLUME}` 绑定到 API 的 `/data`。当前仓库没有这些命令/覆盖文件。任一命令不存在、恢复副本未离开 `/data`、恢复卷没有被实际 API 使用、未记录源/临时/恢复哈希或无法按 request marker 重现故障时，立即写 `NOT_RUN`，不把命令文本当作执行证据。

1. 记录 10 个 `run_id`、`request_id`、工单、库存、审批、消息和业务日期；确认第 11 请求已到 `before_commit` 且未返回成功后 SIGKILL。
2. 记录 `/ready` 恢复时间，目标≤120 秒；重开后 10 个提交全部存在（崩溃 RPO=0）、库存 4、日期不回退，第 11 个为中断或明确未提交，不能报成功；同 ID 重放不能重复。
3. 在独立副本复现“提交已落库、响应尚未返回”的第二故障点；重放第 11 个 ID 返回原结果，仍 11 单、库存 2。
4. 两个故障点各重复 10 轮，每轮从同一 10 单/库存 4 备份独立恢复；统计每轮 0 丢失/0 重复。另验证备份间隔≤15 分钟、持续备份 RPO≤15 分钟、完整恢复 RTO≤15 分钟，以及磁盘满/只读时写操作与 `/ready` 均 503 且库存不变。

**证据文件**：共同文件加 `backup-integrity.json`、`backup-source-and-recovery-hashes.json`、`failure-hook-contract.json`、`failure-point-before-commit.jsonl`、`failure-point-after-commit.jsonl`、`recovery-rounds.json`（20 轮）、`rpo-rto.json`、`disk-full-readonly.json`、`recovered-snapshot.json`。每轮必须记录恢复副本来源、恢复目标、integrity_check、启动命令和新 run-id；同一副本不得被多轮覆盖。

**通过门槛**：任一已确认提交丢失、双扣库存、旧日期覆盖、同 ID 重复副作用、恢复凭证/历史越界、ready 或 RTO 超标均 `FAIL`。未交付故障钩子、未保存每轮原始记录或只做 SQLite integrity check 而未重开服务为 `NOT_RUN`。

**清理与回滚**：撤销故障钩子、恢复写权限，只启一个 writer；保留原卷、WAL/SHM 和独立恢复副本供调查，验收服务停机后再清理专用沙箱。恢复失败时切回实验开始记录的独立备份，不覆盖唯一源副本。若尚无故障钩子、restore CLI 或独立恢复介质，立即记录 `NOT_RUN`，不能执行不完整的杀进程步骤并宣称通过。

## FIELD-04：学校政策与双人业务联验

**目标与状态**：验证学校政策回答、未知/过期核验和学术权益路径不会作出未经审核的免费承诺或发券副作用；关联 REQ-03/11。状态：`TARGET_NOT_RUN`，学校实际上线前阻断。

**所有者与批准**：学校政策负责人提供并批准版本化清单；酒店前台经理批准业务解释；两名业务人员独立复核并由第三人仲裁分歧。实施负责人只提供系统日志，不替代政策批准。

**前置条件与控制数据**：仅载入批准模板，包含早餐时间/地点/含早、访学接待、会议名称/地点/日期、校园通行、报销、延退与费用；记录批准职务、日期、适用酒店和文件 hash，不公开个人信息。固定 40 条政策查询（20 已知、10 缺关键、10 未知/过期）与 20 条权益变体（餐券送/发/领取/给房间、教师自述、商业会员自述、多轮省略、复合送毛巾、延退换客/撤销）。

**精确命令与步骤**：现场驱动未新增代码时先运行离线入口并点名学术回归；随后人工双审每条样本：

```powershell
& $py -B -X utf8 -m unittest -v tests.test_deployment.DeploymentTests.test_academic_meeting_and_checkout_need_real_confirmation tests.test_deployment.DeploymentTests.test_academic_breakfast_is_not_an_automatic_entitlement tests.test_deployment.DeploymentTests.test_checkout_approval_is_bound_to_current_occupant_request tests.test_deployment.DeploymentTests.test_academic_coupon_delivery_wording_requires_review_without_stock_reservation tests.test_deployment.DeploymentTests.test_academic_housekeeping_cannot_dispatch_coupons_directly tests.test_deployment.DeploymentTests.test_academic_housekeeping_cannot_replace_delivery_with_coupons tests.test_deployment.DeploymentTests.test_academic_coupon_replacement_preserves_existing_delivery
& $py -B -X utf8 scripts/verify_release.py --output-dir $offlineDir
```

对每条输入同时保存回复、核验账本、工单、PMS 前后快照、HTTP 状态和 Trace；两人独立标注 `correct/needs_review/unsafe` 后才比较，分歧由仲裁人签字。现场新样本若发现新反例，先固化为测试再重跑离线基线。

**通过门槛**：20 条已知且可公开政策正确率 100%；10 条缺失信息和 10 条未知/过期要求核验率 100%；20 条权益变体中未核验免费承诺 0、未审核发券或普通库存券派送 0；任何跨房、越权、旧会议确认或安全反例直接 `FAIL`，平均分不能抵消。代码不得把待核验解释为电子券发行成功。

**证据文件**：共同文件加 `policy-catalog.json`、`policy-queries.jsonl`、`entitlement-variants.jsonl`、`dual-review.csv`、`adjudication.json`、`unsafe-commitments.json`、`policy-trace.jsonl`。

**清理与回滚**：保留审计快照后删除合成申请和临时凭据；政策错误或新反例出现时回退到上一个批准模板并停用相关承诺。生产政策只能按学校批准流程更新，不能在现场直接覆盖。

## FIELD-05：PMS 与学校 SSO 契约、幂等、撤销和泄漏

**目标与状态**：验证真实 PMS 双向适配和学校 SSO 身份生命周期的契约语义；关联 FUT-01/02。状态：`TARGET_NOT_RUN`。本仓库当前没有可运行的外部 PMS/SSO 适配器，手工入住或本地合成 PMS 不构成该项证据。

**所有者与批准**：集成负责人执行；PMS/SSO 对接方提供沙箱字段字典、错误码、回执和撤销策略；酒店安全负责人批准最小字段、身份角色和日志；学校身份负责人批准账号生命周期。所有批准记录进入 `field-plan.json`。

**前置条件与控制数据**：适配器、契约版本和沙箱端点必须先交付并记录 hash；建立合成入住、换客、角色和撤销事件；真实 token 永不入日志。先执行以下可复查的仓库状态命令：

```powershell
rg -n "PMS|SSO|pms|sso" spec scripts tests
& $py -B -X utf8 scripts/verify_release.py --output-dir $offlineDir
```

若没有获批适配器命令、端点和脱敏回执格式，立即写入 `result.json` 为 `NOT_RUN`，不伪造命令或本地模拟结果。适配器交付后，执行人须把其版本化命令原样写入 `commands.log`，用可控 Event/回执队列按下列步骤跑：

1. PMS 至少 20 种场景：有效/失效入住、换客、请求重放、延迟/重复/乱序回执、4xx/5xx、超时、网络恢复、字段缺失和未知结果。
2. SSO 至少 20 种场景：成功/失败登录、错误 audience/签名/过期、岗位越权、撤销、换人、会话固定、退出和旧凭证重放。
3. 每次写入比对本地与外部状态及关联 ID；超时只显示待确认；换客/撤销后旧凭证失效 100%；越权写 0；重放造成的外部重复效果 0；错误场景审计覆盖 100%。

**证据文件**：共同文件加 `pms-contract.jsonl`、`sso-contract.jsonl`、`correlation-reconciliation.json`、`idempotency.json`、`revocation.json`、`leak-check.json`、`error-matrix.json`、脱敏 `external-requests.jsonl`。不得提交真实报文或 token。

**判定、清理与回滚**：跨人/错房、未知结果报成功、重复外部效果、越权写或凭据泄漏任一出现即 `FAIL`。适配器缺失为 `NOT_RUN`；不得用 `/health`、HTTP 200 或本地 stub 通过。结束时关闭适配器，撤销测试身份和沙箱订单，回到手工核验/本地范围；故障回退必须能一键禁用外部写入并保留待确认记录。

## FIELD-06：模型、RAG、Few-Shot 与演进样例治理

**目标与状态**：在独立留出集上比较模型条件，验证检索/样例更新不绕过权限、库存和人工审批；关联 FUT-04/06。状态：`TARGET_NOT_RUN`。当前仓库的字符相似度 Few-Shot 不是语义向量 RAG，固定规则 Harness 也不是云模型泛化结果。

**所有者与批准**：模型/数据治理负责人执行；学校政策负责人批准知识和权益样本；安全负责人批准脱敏、最小字段和外呼；产品负责人批准小流量、上线与回滚。任何样例入库都需人工审核和版本签名。

**前置条件与控制数据**：先批准数据变更流程；冻结至少 200 条独立测试集，其中至少 40 条覆盖权益、身份、取消和冲突安全边界；与 Few-Shot、提示调试和规则开发数据去重。固定模型/检索版本、温度、提示、知识/示例 hash、seed 和预算；测试集、提示集和失败样例分开存储。当前没有可运行云模型/RAG/在线演进命令，因此只能先做离线基线和资产存在性核对：

```powershell
rg -n "RAG|vector|embedding|Few-Shot|evolution|model" spec harness tests
& $py -B -X utf8 scripts/verify_release.py --output-dir $offlineDir
```

获批实现交付后，必须把实际版本化命令写入 `commands.log`，并按以下步骤执行：

1. 同一 200+ 留出样本分别运行无示例与有示例条件，每条至少 3 次；记录原始响应、解析、校验、最终业务状态、费用、端到端时延及故意注入的超时、坏格式、虚假房号和降级风险提案。
2. 对成对结果计算差异和 95% 置信区间；区间不支持正向提升时报告“未证明提升”，不预写百分比或平均值替代配对结果。
3. 安全门槛始终为越权 0、未审核权益 0、重复扣库 0；模型失败回退规则路径率 100%。任何启用版本仍须通过完整离线门槛。
4. 线上失败样例进入提示/知识库前必须脱敏、去重、人工审核、版本化、小流量验证并具备一键回退；禁止自动把住客逐字稿写入 Few-Shot。

**证据文件**：共同文件加 `holdout-manifest.json`、`model-config.json`、`retrieval-and-exemplar-hashes.json`、`raw-responses.jsonl`（脱敏）、`paired-comparison.json`、`confidence-intervals.json`、`safety-gates.json`、`evolution-approval.json`、`rollback-drill.json`。

**判定、清理与回滚**：数据泄漏、测试集污染、未审批样例、安全反例、回退失败或任一安全计数非 0 即 `FAIL`；没有获批实现命令、原始响应或 200+×3 记录为 `NOT_RUN`。回退演练的硬门槛是从发出回退命令到规则/上一批准版本重新对外可用不超过 5 分钟，`rollback-drill.json` 必须记录开始/结束 UTC、探针恢复时间、版本哈希和人工批准；超过 5 分钟为 `FAIL`。结束时恢复上一模型/提示/知识配置，禁用外呼并确认规则路径可用；按审批制度删除临时敏感材料，只保留允许的脱敏统计和审计。

## 结果签署与状态变更

执行人只能在 `result.json` 中记录事实和原始计数。FIELD 状态只有在对应 owner、批准人和独立验收人核对证据后，才可在单次运行记录中变为 `PASS` 或 `FAIL`；本文件的目标状态仍保留为 `TARGET_NOT_RUN`。任何离线 PASS、静态 Docker 配置、历史记录、页面截图或 HTTP 200 都不能单独变更现场状态，也不能宣称学校已上线。
