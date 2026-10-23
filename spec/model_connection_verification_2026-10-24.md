# 硅基流动连接修复与验收记录

## 结论

本次问题不是 API Key、Base URL 或模型名失效。脱敏探测得到：`https://api.siliconflow.cn/v1/models` 返回 HTTP 200，`Qwen/Qwen2.5-7B-Instruct` 在服务端模型列表中，响应耗时约 621 ms。

实际根因有两个：

1. 当前虚拟环境没有 `openai` Python SDK；原代码捕获 `ImportError` 后把客户端留空，界面仍提示“已成功加载大模型配置”。
2. 本地演示以 `AEGIS_FORCE_RULES=1` 启动，侧栏云端按钮没有清除该进程开关，因此即使输入云端配置也继续使用规则模式。

## 修复内容

- `OpenAICompatibleClient` 增加不含密钥的初始化错误码和 `probe()`，在 `/v1/models` 预检失败时不报告云端在线。
- 云端按钮先清除本进程的 `AEGIS_FORCE_RULES`，再执行预检；失败保留原规则模式并显示可操作原因。
- `openai` SDK 缺失但 `httpx` 存在时，使用同一 OpenAI 兼容协议访问 `/models`、`/chat/completions` 和工具调用；两者都缺失时返回 `openai_sdk_missing`。
- 选定模型不在服务端列表时返回 `model_not_listed`，不切换工作台状态。
- REQ-14/AC-14/EXP-14 新增 `MODEL-CONN-01`：连续 3 次 `/v1/models` 必须 HTTP 200、模型命中、每次≤12秒、成功率100%，证据不得含 API Key；完整推理另需合成 chat canary。

## 可复查证据

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest -v tests.test_model_connection
.\.venv\Scripts\python.exe -B -X utf8 -c "from hotel_core import OpenAICompatibleClient; c=OpenAICompatibleClient(); print(c.probe())"
```

代码回归：4/4 通过，覆盖云端按钮解除离线开关、缺 SDK 不静默成功、缺 SDK 的 HTTP 兼容传输和模型未列出阻断。当前真实配置的无密钥输出摘要为：`transport=httpx`、目标模型命中、`ok=true`、单次 `/models` 探测约 1391 ms；本机显式带 `AEGIS_FORCE_RULES=1` 调用云端 helper 后仍为 `disabled=false`、探测约 1297 ms。另以合成提示“只回复OK”、`max_tokens=1` 做了 1 次本机 chat canary，HTTP 兼容传输返回 2 个字符；该结果只证明当前账号/网络的本机连通性，不替代现场 3/3 门槛。

`pip install -r requirements.txt` 在本机因环境设置 `PIP_NO_INDEX=1` 无法下载 `openai`；因此本次实际连接验证走 `httpx` 兼容传输。该情况不改变部署要求：正式环境仍应固定依赖版本，或明确提供已审核的兼容 HTTP 传输。

## 未完成边界

本记录未执行现场连续 3/3 探测、生产网络代理或真实酒店数据；本机 1 次合成 chat canary 不能替代 FIELD-01/03 和 `MODEL-CONN-01` 的现场门槛，现场状态仍保持 `TARGET_NOT_RUN`/`NOT_VERIFIED`，也不能把一次本机 `/models` 成功当作生产 SLA。
