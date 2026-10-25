# 口语送物、催送与帮助提示验收记录

对应 [SR-06](service_reliability.md)、[UI-07](ui_visual_quality.md) 和 [实施计划](plans/2026-10-25-delivery-followup-language.md)。本文仅记录本次运行，不能用来证明所有自然语言表达或酒店现场均已通过。

## 本次结论与来源

最终离线运行 `ff849634ac7343bd8d231ef1485a17d3` 为 **PASS**。时间为 2026-10-25 08:10:15–08:12:05 UTC；源码基于本地 `9e4d25c960ed990fd053b422e0ce1a252ae0fe5b` 的工作树，开始/结束 **113 个文件的 SHA-256 完全一致**。原始目录为 `.artifacts/verification/delivery-followup-20261025-final/`，清单 SHA-256 为 `fe5e082829354c6d6407492249795370b25e3a4721537d655e5d24e6b3994535`。

| 检查 | 实际结果 |
| --- | --- |
| 完整 unittest | 397/397；失败、错误、跳过、预期失败均为 0 |
| 原始测试身份 | 127/127 实际执行通过，无缺失或替换 |
| 核心测试 | 43/43；原始 40 个 ID 全保留 |
| 固定 Harness | H01–H20 共 20/20；intent/slot/precision/recall 均 1.0 |
| 黑板 | 3 个场景、21/21 断言通过 |
| 规格映射 | 16 项要求，326/326 映射的真实测试实际通过 |

新增的是 **27 个送物/催送测试方法和 1 个主题方法**；循环子场景不计成独立方法，397 项也不是 397 条真实模型样本。范围保持 `offline-code-baseline`，`field_readiness=NOT_VERIFIED`，REQ-14 现场部分仍为 partial。本记录、README 链接和计划勾选在运行后补充，业务源码和测试未随之改变；最终提交由远端 CI 再验证。

远端代码提交 `315d3888e943251ea35faf73dddb13ba7759abc4` 的 [CI 40176412402](https://github.com/cohabit/AegisStay-MultiAgent/actions/runs/40176412402) 已成功，统一验证、证据归档、Docker 构建及目录/图片打包检查均通过。此回执和计划勾选在该 CI 完成后追加，未改业务、测试或依赖。

## 复现、修复与反例

1. 原句“我还要两瓶水两桶泡面，很饿，快点送”曾把水、泡面、快点送均当未知物品。现在饮用水别名能跨连写数量边界，只报告未核验泡面；整次不建单、不扣库，不能静默只派水。
2. “快点把水送来，我很口渴”关联唯一的当前会话有效水单，记录 `reminder_count`、`last_reminded_at`，必要时将低优先级提至中；保留原 ticket_id、库存和其他物品工单。无有效单、两张同物品单分别询问新增数量或具体单号。
3. 复核复现了旧进度分支吞掉新增数量/未知物品、查错最近物品和否定催单的问题，均补测试关闭：“再送两瓶水，催一下”仍新增两瓶；“矿泉水还没送到，快点送两罐红牛”报告红牛未核验；“水还要多久”定位水单；“水不用催”不改业务记录。
4. 催送不复活已取消/结单记录，不重复预留，缺货仍未确认派送；提醒记录和优先级经 SQLite 关闭/重开恢复，request_id 重放只执行一次，同房不同会话不串改。催送夹带冒烟时保留高优先级工程报修；同轮设施 FAQ 仍合成答复。

RED/GREEN 原始日志均在本地 `.artifacts/`：`delivery-followup-alias.log`（别名修复后剩 11 个失败）、`delivery-followup-review-red.log`（24 方法中 7 个失败子场景）、`delivery-followup-risk-red.log`（27 方法中 1 个工程风险失败）、`delivery-followup-final-focused.log`（192 方法通过）。最初 `delivery-followup-red.log` 还包含一处测试调用名错误，纠正为真实 `complete_ticket` 后才继续业务验证；该测试脚本错误不当成产品缺陷。主题 RED 为 `tooltip-red.log`（1 方法 30 个子场景失败），GREEN 为 `tooltip-green-verified.log`（11 方法通过）。

## 硅基流动有限联调

独立使用现有供应商配置、academic 合成房态和内存隔离状态；不更改密钥或用户正在运行的会话。12 轮均返回 `completed`，12 次 `json_extract` 调用均启用且有返回，观测耗时 **1.375–2.177 秒/调用**，不据此宣称稳定 SLA 或意图准确率。

| 输入/步骤 | 状态与库存核对 |
| --- | --- |
| 送一块浴巾 → 1128 | 先追问房号，补槽后仅 1 单 |
| 浴巾已派出后改两块毛巾 → 员工确认 | 确认前不宣称取消；确认后原单变 2 条毛巾，浴巾库存恢复 24、毛巾 22 |
| 两瓶水两桶泡面，很饿，快点送 | 只报告泡面；仍 1 单，库存无变化 |
| 我还要两瓶水 → 我还要两桶泡面 | 水库存 34，总计 2 单；泡面不新增工单 |
| 快点把水送来 → 水还要多久 | 原水单提醒次数依次 1、2；水库存仍 34，总计仍 2 单 |
| 水不用催 → 混合未送到与两罐红牛 | 提醒、工单和库存均不改变；红牛明确未核验 |
| 再送两瓶水，催一下 → 再催水 | 新增 2 瓶，水库存 32，总计 3 单；两张水单时澄清，不随意选择 |

原始文件 `.artifacts/live-delivery-followup-20261025.json`，SHA-256 `4fefae8244f68b2f2f0998350d8bcc188aed50befb848c59493518e09c085fc0`。它记录调用启用/返回、时长、各轮回复与工单/库存，不写入 API Key。该次联调后的应用修改仅为提示框最大宽度；三份业务模块源码指纹与最终离线运行一致。

## 浏览器与主题证据

在独立 8502 端口、纯规则合成会话核验，未重启用户 PyCharm 的 8501 服务。原生 help portal 实际正文/背景为：

| 主题 | 正文 / 背景 | 对比度 |
| --- | --- | --- |
| 日间 | #102a43 / #ffffff | 14.6416:1 |
| 护眼 | #2d3b32 / #f5f8f0 | 10.9732:1 |
| 夜间 | #e6f0ed / #182630 | 13.2853:1 |

三主题普通 help 实际打开，color/text fill 与背景有配对；错误 help 及 Markdown 子元素另由 cascade 回归覆盖，未冒充逐个浏览器实测。窄屏首次观测 right=408 超出 390；最终含边框宽度上限收为视口减 16，390×844 复测 left=8、right=382、width=374，正文换行未裁剪。页面宽度 390，输入框 bottom=606.69，发送按钮 bottom=664.89，均在首屏内。

按用户要求截图仅在本地：

- `.artifacts/delivery-tooltip-final-night.jpg`，SHA-256 `c868442e7e691997460a89ab88211d86c8d86c0a6e5c5e91f26e5fe54f7fc2b2`；
- `.artifacts/delivery-tooltip-final-mobile.jpg`，SHA-256 `9da231a8bdea1bc0898ffdf83f6bb8bd1156003966efbc129449062683a34a67`；
- `.artifacts/delivery-tooltip-browser.json`、`delivery-mobile-browser-final.json` 保存浏览器实际颜色与几何值。

## 可复现命令与边界

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts/verify_release.py --output-dir .artifacts/verification/<全新目录>
```

测试入口强制规则、独立临时库、合成资料和禁止外呼。有限真实模型联调必须单独进行，不能和隐藏配置的离线验证并行；重新联调前仍需确认目标环境只有合成数据。催送目前是平台记录与优先级变化，未接酒店员工通知推送/PMS 外部回执。目录外泡面、红牛不自动学成可配送商品；语言规则的覆盖仍有边界，不能用这 12 轮替代现场独立质量集。
