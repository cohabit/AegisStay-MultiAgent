# 异步中断与主子 Agent 校验：验收记录

日期：2026-10-16。对象：当前工作区的 AegisStay 本地 Demo。所有执行均设置 `AEGIS_FORCE_RULES=1`，没有调用云端模型或真实酒店服务，也没有启动常驻服务。

## 1. 自动化测试

项目虚拟环境：Python 3.12、Streamlit 1.66.0、PyArrow 25.0.1。执行路径为项目的 `.venv\Scripts\python.exe`。

| 测试模块 | 用例数 |
| --- | ---: |
| `tests/test_app.py`：原核心业务回归 | 40 |
| `tests/test_evolution.py`：已有模块回归 | 7 |
| `tests/test_api.py`：API 归属、异步提交、幂等及派送确认 | 9 |
| `tests/test_task_runtime.py`：任务竞争与结果校验 | 29 |
| `tests/test_streamlit.py`：界面交互 | 3 |
| 合计 | **88** |

原核心验收命令：

```powershell
$env:AEGIS_FORCE_RULES='1'
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests/test_app.py
```

实际结果：

```text
........................................
----------------------------------------------------------------------
Ran 40 tests in 0.125s

OK
```

完整回归命令：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*.py'
```

实际结果（包含测试工具的非失败警告）：

```text
E:\ProgramData\anaconda3\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
........................................................2026-10-16 12:08:24.605 WARNING streamlit.runtime.scriptrunner_utils.script_run_context: Thread 'MainThread': missing ScriptRunContext! This warning can be ignored when running in bare mode.
.2026-10-16 12:08:25.903 Thread 'MainThread': missing ScriptRunContext! This warning can be ignored when running in bare mode.
.2026-10-16 12:08:26.667 Thread 'MainThread': missing ScriptRunContext! This warning can be ignored when running in bare mode.
..............................
----------------------------------------------------------------------
Ran 88 tests in 3.554s

OK
```

本次未跳过 Streamlit 测试。三项界面测试分别验证：旧部门任务阻塞时可以输入更换请求、取消按钮阻止待执行送物、运营端核验冲突不会绕过规则创建工单。使用 AppTest 在本地执行，未启动浏览器服务。

新增竞争测试还覆盖连续三次变更、不同会话不互相阻塞、同会话独立 FAQ 排队、会话重置后旧任务不能恢复数据、共享库存防超卖、已派送变更确认、已送达回收确认、库存和风险提案冲突，以及多张工单的任务归属。

## 2. Harness 20 组离线回归

执行命令：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m harness.eval_runner
```

实际结果：

```text
AegisStay Harness Evaluation · ⚡ 离线规则快速回归
======================================================================================
Target: 确定性规则引擎 (离线保底) | Cases: 20 | Passed: 20 | Avg response: 2.99 ms
Intent Accuracy: 100.0%
Slot Filling Rate: 100.0%
Ticket Precision: 100.0% | Ticket Recall: 100.0%
--------------------------------------------------------------------------------------
ID    STATUS  INTENT   ROOM    DEPT     TICKET  MODE                      NAME
H01   PASS    送物       1208    楼层保洁     True    规则降级 / HousekeepingAgent  单轮送毛巾
H02   PASS    送物       1506    楼层保洁     True    规则降级 / HousekeepingAgent  单轮送水
H03   PASS    送物       818     楼层保洁     True    规则降级 / HousekeepingAgent  送吹风机
H04   PASS    送物       1208    楼层保洁     True    规则降级 / HousekeepingAgent  多轮补全送物
H05   PASS    工程报修     1208    工程部      True    规则降级 / EngineeringAgent   空调报修
H06   PASS    工程报修     1506    工程部      True    规则降级 / EngineeringAgent   漏水报修
H07   PASS    工程报修     818     工程部      True    规则降级 / EngineeringAgent   灯具报修
H08   PASS    工程报修     1208    工程部      True    规则降级 / EngineeringAgent   无房号报修补全
H09   PASS    客诉紧急     1208    大堂经理     True    规则降级 / ManagerEscalationAgent 普通投诉
H10   PASS    客诉紧急     1506    大堂经理     True    规则降级 / ManagerEscalationAgent 暴怒客诉
H11   PASS    客诉紧急     818     大堂经理     True    规则降级 / ManagerEscalationAgent 多轮客诉补全
H12   PASS    酒店咨询     —       —        False   规则降级                      早餐 FAQ
H13   PASS    酒店咨询     —       —        False   规则降级                      退房 FAQ
H14   PASS    酒店咨询     —       —        False   规则降级                      Wi-Fi FAQ
H15   PASS    酒店咨询     —       —        False   规则降级                      健身房 FAQ
H16   PASS    酒店咨询     —       —        False   规则降级                      押金 FAQ
H17   PASS    酒店咨询     —       —        False   规则降级                      发票 FAQ
H18   PASS    酒店咨询     —       —        False   规则降级                      停车 FAQ
H19   PASS    其他       —       —        False   规则降级                      范围外闲聊
H20   PASS    其他       —       —        False   规则降级                      范围外天气
```

这里的 100% 只描述这 20 组已知离线规则用例；2.99 ms 为本次本机的平均单例耗时，不代表生产延迟、真实 LLM 泛化准确率或新增并发能力的吞吐量。

## 3. 业务状态与 Trace 断言

在最终源码上，复用以下竞争测试场景执行并读取结果：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_task_runtime.TaskRuntimeTests.test_replace_running_delivery_commits_only_latest_version tests.test_task_runtime.TaskRuntimeTests.test_child_cannot_downgrade_safety_priority -v
```

补充直接执行复合 FAQ 与延退、严重客诉、普通会员人工审批场景。以下为实际状态输出，随机 ID 以本次运行为准：

```json
{
  "late_result": {
    "old_run_status": "superseded",
    "old_worker_finished": true,
    "latest_version": 2,
    "ticket_count": 1,
    "item": "浴巾",
    "quantity": 2,
    "inventory": {
      "毛巾": 24,
      "浴巾": 22
    },
    "assistant_reply_count": 1,
    "trace": [
      {
        "agent": "Supervisor",
        "event": "version_checked",
        "detail": "TASK-2e14c54d1154 / v2"
      },
      {
        "agent": "ReceptionAgent",
        "event": "关联档案",
        "detail": "房间 1128；已关联 CRM"
      },
      {
        "agent": "Supervisor",
        "event": "proposal_validated",
        "detail": "用户需求、部门、房号与风险规则校验通过"
      },
      {
        "agent": "HousekeepingAgent",
        "event": "vertical_agent_dispatch",
        "detail": "HT-22E63FCB8A → 楼层保洁 / 低"
      }
    ]
  },
  "agent_conflict": {
    "status": "needs_review",
    "ticket_count": 0,
    "review_status": "需人工核验",
    "trace": [
      {
        "agent": "Supervisor",
        "event": "version_checked",
        "detail": "TASK-b56ff416a1b1 / v1"
      },
      {
        "agent": "ReceptionAgent",
        "event": "关联档案",
        "detail": "房间 1128；已关联 CRM"
      },
      {
        "agent": "Supervisor",
        "event": "agent_conflict",
        "detail": "部门结果降低了业务规则要求的优先级"
      }
    ]
  },
  "compound_faq": {
    "reply": "张先生，早餐供应时间为 07:00–10:00，地点在二楼餐厅。 另外，已更正关联 1128 房间。延迟退房已批准，退房时间更新为 14:00，无需额外费用。",
    "checkout_time": "14:00"
  },
  "complaint": {
    "priority": "高",
    "risk_level": "红色",
    "assigned_dept": "大堂经理"
  },
  "staff_approval": {
    "before": "12:00",
    "after": "14:00",
    "status": "已批准"
  }
}
```

```text
All business-state and Trace assertions passed.
```

关键断言：旧毛巾任务已经结束且状态为 `superseded`；最终只有两条浴巾的一张工单；毛巾库存仍为 24、浴巾为 22；只有一个成功回复。严重漏水提案被降级时，工单数为 0，并记录 `needs_review` 和 `agent_conflict`。复合请求同时保留早餐答复和延退结果；愤怒客诉为高优先级、红色风险；808 人工审批前后退房时间为 12:00 → 14:00。

## 4. 静态检查和交付范围

`git diff --check` 退出码为 0；仅有 Windows 的 LF/CRLF 提示，无空白错误。新文件另外核对 UTF-8、尾随空格及本地链接目标。

交付包括后台任务运行时、提案契约、业务改单与库存检查、Streamlit 交互、异步提交／查询 API 和中文面试逐字稿。保留原有暂存修改，没有提交 Git、修改简历正文或运行付费模型。本次没有验证分布式部署、数据库恢复、真实 PMS 回执、历史性能数字或自动模型训练。

[实现说明](task_interruption_and_arbitration.md) · [面试逐字稿](../docs/interview.md) · [2026-10-20 最新验收](release_verification_2026-10-20.md)
