# 剩余规格内容补全验收

本记录验证的是规格文档的完整性和可导航性，不把现场目标或未来集成写成已实现功能。当前代码验收仍以[规格体系验收记录](specification_verification_2026-10-23.md)和提交后的 CI 为准；现场状态继续为 `field_readiness=NOT_VERIFIED`。

## 本轮交付

- [REQ-14 部署与运维子规格](design/deployment.md)：配置、密钥、Docker/Compose、单 API 写入者、SQLite、备份、恢复、升级、回滚、监控和上线预检。
- [FIELD-01～FIELD-06 现场验收协议](field_validation.md)：owner/approval、沙箱、证据文件、量化门槛、失败、清理和回滚；全部 `TARGET_NOT_RUN`。
- [FUT-01～FUT-06 未来集成规格](future_integrations.md)：PMS、SSO、电子券、批准知识/语义 RAG、容量恢复、样例治理；全部 `planned`。
- [补全设计](../docs/superpowers/specs/2026-10-23-remaining-spec-completion-design.md)与[实施计划](../docs/superpowers/plans/2026-10-23-remaining-spec-content.md)：方案选择、文件边界、约束和验收契约。

主规格、AGENTS、README、实验、验收和追溯入口均已链接到上述子规格。历史专题仍按 [legacy_specs.md](legacy_specs.md) 标识，不被重新解释为当前能力。

## 静态验收证据

使用项目 `.venv` Python 执行了两个只读检查：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 .venv/audit_remaining_specs.py
.\.venv\Scripts\python.exe -B -X utf8 .venv/audit_spec_delivery.py
git diff --check
```

最近一次结果保存在被 Git 忽略的 `.artifacts/verification/remaining-spec-audit-20261023T144729Z.json`：

| 检查 | 结果 |
| --- | --- |
| REQ-14 子规格必备契约 | 14/14 条文本断言通过 |
| FIELD 子规格 | FIELD-01～06 全部存在，22/22 条通用字段断言通过 |
| FUT 子规格 | FUT-01～06 全部存在，12/12 条通用字段断言通过 |
| 本地 Markdown 链接 | 239/239 通过 |
| 文档/文本敏感模式扫描 | 74 个文件，无命中 |
| `git diff --check` | 通过；仅保留 Windows 的 LF/CRLF 提示 |
| 产品/现场测试 | 本轮未运行；`runtime_tests_run=false` |

## 复核和边界

WP-04 部署子规格的独立复核为 Approved，两个措辞问题已修正。WP-06 未来集成子规格无 Critical/Important 问题；FUT-03 已补 FIELD-05 依赖，FUT-06 已补 10 秒推理/15 秒请求预算和 5 分钟回退门槛。

WP-05 现场协议经过五轮修复复核：独立恢复介质、独立 run-id、故障钩子/restore/请求驱动契约、动态恢复卷、镜像 immutable digest、每轮唯一 request_id、临时/恢复哈希和故障 JSON 复制均已写入协议。达到 Superpowers 五轮上限后，主代理按台账裁定补了最后两个文档细节；没有把该文档静态复核当作 FIELD 通过。当前仓库没有现场故障钩子、restore CLI、容量驱动、PMS/SSO 适配器或云 RAG 命令，因此相关 FIELD/FUT 仍必须记录 `TARGET_NOT_RUN`/`planned`。

本轮没有修改产品代码、测试逻辑或部署配置；若只提交规格文档，既有离线功能证据仍适用，但现场、外部系统、语义 RAG、模型质量和生产容量不会因此改变状态。
