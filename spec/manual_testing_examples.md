# 测试范例：取消、改单、异步中断与主子结果冲突

建议先在页面手动测试前四个例子，再运行自动化测试。每个例子都先点击侧边栏的“🔄 清空会话与刷新系统”，确保库存和工单从初始状态开始。

## 1. 启动页面

在 PowerShell 中执行：

```powershell
Set-Location 'E:\Codex\LLM\AegisStay-MultiAgent'
$env:AEGIS_FORCE_RULES='1'
.\.venv\Scripts\python.exe -m streamlit run app.py
```

打开终端显示的 Local URL，通常是 `http://localhost:8501`。这里强制使用离线规则，无需配置模型 Key。暂时不要启动 FastAPI，Streamlit 就能完成以下页面测试。

`AEGIS_FORCE_RULES=1` 会禁用模型调用，即使配置了 Key 也仍走规则。项目接待层有可选的 LLM 意图分类，但当前取消／改单的动作、物品指代和工单定位主要由规则处理，未把当前工单列表交给模型解析。开启模型本身不等于补齐了这些能力。

如需自行测试云端分类：先停止当前服务，在同一终端执行 `Remove-Item Env:AEGIS_FORCE_RULES -ErrorAction SilentlyContinue`，重新启动，再在侧边栏配置并连接模型。本次回归没有调用云端模型。

## 2. 范例一：把毛巾改成浴巾

在“📱 住客智能管家端”依次输入：

```text
1128 给我送两块毛巾
```

等服务单创建后，再输入：

```text
我不要毛巾了，我要浴巾
```

切换到“🖥️ 酒店运营与工单中枢”，检查：

- 工单仍然只有一张，工单编号不变。
- 物品改为浴巾，数量仍为 2，而不是变成 1。
- 不要先点击“确认开始派送”或“完成结单”，这一步验证尚未派送时的直接改单。
- 展开保洁库存：毛巾为 24，浴巾为 22。

这个例子验证：修改原工单、继承数量、释放旧库存和预留新库存。

## 3. 范例二：取消尚未派送的工单

重置后依次输入：

```text
1128 送两条毛巾
不用了
```

预期：回复取消成功，原工单显示“已取消”，毛巾库存恢复为 24。再次输入“不用了”，库存仍为 24，不会重复返还到 26。取消记录会保留，不是从列表直接删除。

## 4. 范例三：已经派送，再要求更换

重置后按以下顺序操作：

1. 住客端发送：`1128 送两条毛巾`。
2. 在运营端找到这张工单，点击“确认开始派送”。
3. 回住客端发送：`不要毛巾了，改成浴巾`。

此时预期：

- 回复正在等待保洁确认，明确表示尚未确认取消成功。
- 原工单仍是毛巾，状态为配送中，并出现待部门确认的变更。
- 库存暂时仍为毛巾 22、浴巾 24。

再到运营端点击“确认撤回并处理变更”。预期：

- 同一张工单改为 2 件浴巾，回到待派送阶段。
- 毛巾库存恢复为 24，浴巾变为 22。
- 住客端出现“保洁确认”通知。

另开一轮可以改点“确认无法撤回”：原毛巾工单和库存保持，住客收到无法撤回的通知。如果你先点击“完成结单”，再要求更换，则还需确认已取回原物品；这是本地模拟中的回收确认，不是真实酒店仓储回执。

## 5. 范例四：多张工单，不能随便取消

重置后依次输入，每条等处理完成再发下一条：

```text
1128 送两条毛巾
再送两瓶矿泉水
不用了
```

预期：询问要取消哪一种物品，两张单都保持处理中。然后输入：

```text
取消毛巾
```

预期：只取消毛巾，矿泉水仍在处理中；毛巾库存为 24，矿泉水为 34。列表仍保留两张记录，其中一张已取消。

2026-10-19 补充了口语指代的回归。另起一轮重置后，依次发送：

```text
1128 送两条毛巾
再送两瓶矿泉水
不用了
水不用了
```

第三句应要求澄清，第四句应只取消矿泉水，保留毛巾。此时毛巾库存 22、矿泉水库存 36。也覆盖了“水不需要了”“那瓶水不用了”等表达；“热水不用了”“水管不用了”不会据此取消矿泉水。复杂或未识别的指代仍需澄清。

单独检查这组口语表达：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_delivery_language -v
```

这些是本地规则回归，不是云端模型语义理解效果的证明。详见 [问题原因与修复记录](colloquial_delivery_cancellation.md)。

## 6. 怎么验证“任务还没处理完时又来一条消息”？

离线规则很快，手动输入第二条时，第一条往往已经处理完。这样仍能验证改单，但不能据此证明旧任务晚返回时也安全。

以下自动化测试会用线程事件控制时序：先让旧任务停在部门处理阶段，再发送新请求，最后释放旧任务，并检查它没有再派单或扣库存。无需手速，也不依赖真实模型的响应速度。

先进入项目目录，并保持离线开关：

```powershell
Set-Location 'E:\Codex\LLM\AegisStay-MultiAgent'
$env:AEGIS_FORCE_RULES='1'
.\.venv\Scripts\python.exe -m unittest tests.test_task_runtime -v
```

当前应看到 `Ran 29 tests` 和 `OK`。2026-10-19 已实际重新执行：29 项全部通过，测试摘要为 `Ran 29 tests in 0.371s / OK`。其中：

| 测试名中的关键词 | 实际检查什么 |
| --- | --- |
| `replace_running_delivery` | 毛巾旧任务仍在运行时改要浴巾；最终只留一张两条浴巾工单 |
| `cancel_running_delivery` | 旧任务尚未建单时取消；最终无工单、无库存扣减 |
| `three_rapid_versions` | 连续更换两次，只有最后版本生效，数量不丢失 |
| `other_guest_is_not_blocked` | 一个住客处理缓慢时，另一住客仍能得到回复 |
| `concurrent_guests_cannot_oversell` | 两个住客同时申请库存，不能超卖 |
| `conflicting_child_item` | 模拟子 Agent 把浴巾返回为毛巾，主控拒绝提交 |
| `child_cannot_downgrade_safety_priority` | 模拟子 Agent 把严重漏水降为低优先级，主控拦截并记录待核验 |

主子结果冲突采用受控的错误提案模拟；普通聊天不一定能稳定制造这种内部错误。

只测最关键的异步更换，也可以单独运行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_task_runtime.TaskRuntimeTests.test_replace_running_delivery_commits_only_latest_version -v
```

## 7. 自动检查页面交互和全量回归

不需要另开网页服务，也能用 AppTest 检查输入框、取消按钮和冲突核验交互：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_streamlit -v
```

当前应为 3 项通过。如果显示跳过，确认使用项目 `.venv`，其中 Streamlit 需要 >= 1.57。

全量回归和原 Harness：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe -m harness.eval_runner
```

2026-10-16 的验收记录分别为 88 项测试通过、20/20 组离线 Harness 通过。当前测试结果以你实际运行的输出为准；旧记录见 [验收文件](task_interruption_verification.md)。

## 8. 可选：用 FastAPI 测试任务接口

在另一个 PowerShell 终端启动：

```powershell
Set-Location 'E:\Codex\LLM\AegisStay-MultiAgent'
$env:AEGIS_FORCE_RULES='1'
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/docs`，在 `POST /api/chat/submit` 中执行：

```json
{
  "session_id": "test-1128",
  "request_id": "delivery-1",
  "message": "1128 给我送两条毛巾",
  "force_rules": true
}
```

预期 HTTP 202，返回 `run_id`、`task_id` 和 `version`。接着将 `request_id` 改为 `delivery-2`，`message` 改为“我不要毛巾了，我要浴巾”，再次提交。用第二次的 `run_id` 和同一个 `session_id` 调用 `GET /api/tasks/{run_id}` 查看结果，再用 `GET /api/session/test-1128` 查看最新工单。

预期最终是一张 2 件浴巾工单。第一条如果已经结束，其 run 保持 completed；只有确实被新消息中断的执行才会显示 superseded。completed 表示本轮处理完成，不表示物品已送达。

Streamlit 与 FastAPI 在不同进程中运行，模拟数据互不共享。API 测试的结果请在 API 查询，不要去 Streamlit 页面寻找对应工单。
