# 运行截图说明

截图日期：2026-10-20。使用本版 `app.py`，Streamlit 1.66.0，浏览器默认 1280 × 720 视口，应用原生深色主题，`AEGIS_FORCE_RULES=1`。图片直接保存浏览器截图，没有拼接或修改页面内容。

所有房间、住客、库存与工单均来自本地模拟数据，没有真实客户信息或 API Key。页面中的业务日期是模拟日期，不能作为截图采集日期。

| 图片 | 页面与复现过程 |
| --- | --- |
| [operations-overview.jpg](operations-overview.jpg) | 打开运营页，查看 240 间房与各部门总览 |
| [guest-cancellation.jpg](guest-cancellation.jpg) | 依次输入“1128 给我送两条毛巾”“再送两瓶矿泉水”“不用了”“水不用了”；只取消矿泉水 |
| [delivery-change-confirmation.jpg](delivery-change-confirmation.jpg) | 运营页搜索 1128，确认毛巾开始派送；住客输入“不要毛巾了，改成浴巾”；运营页出现待确认变更 |
| [harness-evaluation.jpg](harness-evaluation.jpg) | 选择离线规则评测，点击运行 20 组回归；展示 20/20 与逐条结果 |

Harness 图片中的 2.45 ms 是该次页面运行的平均单例耗时，终端另一次运行的数值可能不同；均不能代表真实模型或生产端到端延迟。取消和改单截图验证的是规则与状态控制，没有调用云端模型。
