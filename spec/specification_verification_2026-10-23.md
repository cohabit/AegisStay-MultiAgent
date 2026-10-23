# 2026-10-23 规格体系与统一证据验收

本轮结果：`PASS`，范围仅为 `offline-code-baseline`；`field_readiness=NOT_VERIFIED`。这份记录对应新的规格、餐券保护及证据工具，不覆盖 [ec6331f 的历史 127 项记录](release_verification_2026-10-23.md)。现场实验仍按 [实验方案](experiments.md) 的 FIELD-01…FIELD-06 执行。

## 交付与分工

- [AGENTS.md](../AGENTS.md)：仓库目标、结构、入口及不可绕过的约束。
- [主规格](specification.md) → [业务](design/business.md) / [架构](design/architecture.md) → [实验](experiments.md) / [验收](acceptance_criteria.md) / [追溯](traceability.md)。16 项当前要求、16 项可复现实验、6 项尚未执行的现场实验及 FUT 扩展均有明确编号。
- [机器映射](traceability.json)：16 项要求对应 120 个唯一真实测试方法；REQ-16 包含全部 38 个证据工具测试。
- `scripts/verify_release.py`、`scripts/verify_blackboard.py`：真实 CLI、原始日志、结构化结果、来源哈希、黑板/Trace 和追溯校验；失败保留证据并返回非零。
- GitHub Actions 使用相同入口，成功或失败均上传原始证据，当前保留 14 天；镜像构建单独验证，不等价于 Compose 现场运行。

按已安装的 Superpowers 工作流分别委派地图与业务、实验与追溯、证据工具三个工作包。各包进行独立规格/质量复核。证据工具复核发现未知状态 CLI 行漏检后，原实现者补 RED/GREEN 并经独立修复复核关闭；映射增量另行静态核对。具体分工见 [工作计划](work_plan.md)。

## 本机真实运行

在仓库根目录执行以下命令，原始进程退出码为 `0`：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_release.py --output-dir .artifacts/verification/spec-map-final-001
```

| 证据身份 | 实际值 |
| --- | --- |
| run_id | `ad7fb5d98cd844d0940320984e1f3db5` |
| 开始 / 结束 UTC | `2026-10-23T06:22:11.382624Z` / `2026-10-23T06:22:51.628576Z` |
| 总耗时 | `40.245912` 秒；不是业务接口时延或容量成绩 |
| 来源 | 基于 `ec6331f2d4630bb1c333558dc08db2493b8f2322` 的未提交工作区，`git_dirty=true` |
| 来源校验 | 71 个来源文件的起止 SHA-256 相同，含 Dockerfile、.dockerignore、.gitignore；每个子进程安全回执均已校验 |
| 清单 SHA-256 | `1334ce8ad3859cdebe013a149be2c6e779c3c048d3f7119b37788edfcff0f3e3` |
| 临时资源清理 | `PASS` |

本记录及文档导航是在上述运行后整理的；运行证据标识的是清单内的工作区字节，不能把旧 commit 单独当成该工作区身份。随后提交 `c520075a2b3b7ec36bb3cbdd31620b066e1e5c03` 已由 [CI 工作流](../.github/workflows/tests.yml) 实际运行并成功，精确的远程结果见下文。

| 实际步骤 | 结果 | 必须核对的内容 |
| --- | --- | --- |
| full-tests | 169/169，退出 0 | 原始 127 个 ID 全部实际执行；失败/错误/跳过/预期失败/意外成功均 0 |
| core-tests | 40/40，退出 0 | 原始 40 个核心 ID 全部执行；上述不利计数均 0 |
| harness-cli | 20/20，退出 0 | 恰为 20 个唯一 H01…H20，全部 PASS |
| harness | 20/20，退出 0 | 四指标 intent_accuracy、slot_filling_rate、ticket_precision、ticket_recall 均为 1.0；规则模式 |
| blackboard | 3 个场景，21/21，退出 0 | 从实际快照重算状态与 Trace 断言，不信任预填 true |
| traceability | 16/16，120/120，退出 0 | 映射的 120 个真实方法均在本次完整套件成功执行；REQ-14 仍为 partial |

完整测试原始页脚为 `Ran 169 tests in 37.364s` / `OK`。日志保留既存 Starlette/httpx 弃用提示、Streamlit 测试上下文提示及负向夹具的诊断，不把它描述为零警告，也不凭全文出现 PASS/FAIL 字样判断。

## 提交后的 CI 证据

公开运行：[Offline acceptance #39917403332](https://github.com/cohabit/AegisStay-MultiAgent/actions/runs/39917403332)。`head_sha` 与提交完全一致，状态 `completed`、结论 `success`。作业 `tests` 的统一验收、证据上传和 `docker build -t aegisstay:test .` 三个关键步骤均成功；证据附件 `verification-c520075a2b3b7ec36bb3cbdd31620b066e1e5c03` 未过期，SHA-256 为 `14204237d3976a6a70fe5627f99d3546ee4b48b4be4f26468a914d3451620671`，保留至 2026-11-06 07:24 UTC。CI 证明的是提交树在 Ubuntu/Python 3.12 的离线验收和镜像构建，仍不改变下文的现场未验证边界。

## 可复查的原始文件

同一已忽略目录 `.artifacts/verification/spec-map-final-001/` 包含 12 个交付文件：

```text
manifest.json
full-tests.log          full-tests.json
core-tests.log          core-tests.json
harness.log             harness-structured.log    harness.json
blackboard.log          blackboard.json
validation.log          traceability.json
```

文件属于同一 run_id；清单包含各命令、退出码、耗时、安全校验、依赖与来源。输出 `traceability.json` 是该次验证结果，不替代 `spec/traceability.json` 输入。原始日志默认不入 Git，新的代码必须在新目录重跑；提交后的 CI 另存该提交的证据包。

同机另保留真实先红后绿日志：餐券保护 `.artifacts/verification/task1-coupon/`；证据工具 `.artifacts/verification/task3-red-*` / `task3-green-*`；未知状态行修复 `.artifacts/verification/task3-fix01-red/` / `task3-fix01-green/`。焦点日志只证明各自范围，不替代上面的完整运行。

## 环境与边界

隔离子进程实际依赖为 Streamlit 1.66.0、FastAPI 0.141.1、httpx 0.28.1、typing_extensions 4.15.0；openai 分发在本机为 `not-installed` / `version=null`。启动守卫保留 PYTHONNOUSERSITE、隐藏本地可选配置、限制临时 SQLite 并阻断外网；没有发送模型请求。本次不证明云模型客户端或模型质量。

学校餐券的已知表达、直调保洁和送物改券保护已有真实反例与回归；不外推任意自然语言、迁移前脏数据或真实电子券发行。当前分页测试证明 8 条计数，精确身份/顺序/去重的独立断言尚未补齐，不能把行数证明说成身份完整性证明。

FIELD 目标、真实酒店启动、学校政策审核、PMS/SSO、现场负载、断电/恢复演练、语义 RAG 和模型前后对照均未由本次运行验证。固定 20 条规则回归的四个 1.0 不是模型泛化准确率或自更新提升率。
