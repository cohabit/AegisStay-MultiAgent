# 剩余规格内容实施计划

## 目标

补齐当前主规格中仍是 `partial`、`TARGET_NOT_RUN` 或 `planned` 的内容，使每一项都有独立子规格、责任边界、证据要求和失败处置，同时保持代码状态和现场状态的真实表述。

## 工作包

| 工作包 | 文件所有权 | 结果 |
| --- | --- | --- |
| WP-04 部署与运维 | `spec/design/deployment.md` | REQ-14 的配置、容器、数据、备份、恢复、升级、回滚和上线检查 |
| WP-05 现场验收 | `spec/field_validation.md` | FIELD-01～FIELD-06 的前置、步骤、量化指标、证据、失败和清理 |
| WP-06 未来集成 | `spec/future_integrations.md` | FUT-01～FUT-06 的契约、数据治理、验证门槛和回退 |
| 集成 | `AGENTS.md`、`README.md`、`spec/specification.md`、`spec/experiments.md`、`spec/acceptance_criteria.md`、`spec/traceability.md` | 主入口、链接和状态一致 |

## 约束

- 只补规格和链接，不实现外部系统或修改业务运行时。
- 每个现场/未来项目必须明确当前状态，不得写成 PASS。
- 现场目标沿用 FIELD-01～06 的量化门槛；未来集成需给出可执行的契约测试数量或零容错安全指标。
- 不写真实密钥、住客资料、生产端点或私人面试内容。

## 验收

1. 三个子规格均包含目标、非目标、依赖、数据/权限边界、执行步骤、证据格式、失败条件、回滚/清理和当前状态。
2. 每个 FIELD 和 FUT 编号只出现一个权威定义，主规格和实验方案通过链接引用。
3. 所有本地 Markdown 链接可解析；README 不出现用户禁止的个人材料标签。
4. `REQ-14` 仍为 `partial`，FIELD-01～06 仍为 `TARGET_NOT_RUN`，FUT-01～06 仍为 `planned`。
5. 运行静态文档链接/敏感模式审计和 `git diff --check`；不重新宣称产品代码测试通过，除非代码发生变化并重新运行统一入口。

## 集成后的文件入口

- `specification.md` 通过“剩余范围的权威子规格”统一入口。
- `AGENTS.md`、`README.md`、`experiments.md`、`acceptance_criteria.md`、`traceability.md` 均直接链接到对应子规格。
- 当前代码验收记录不因新增文档改变；REQ-14 继续 `partial`，FIELD-01～06 继续 `TARGET_NOT_RUN`，FUT-01～06 继续 `planned`。
