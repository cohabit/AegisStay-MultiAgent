# 2026-10-20 发布验收记录

> 适用范围：历史验收记录，仅证明记录所指版本，不能作为新实现通过的证据。当前要求见 [主规格](specification.md)、[验收标准](acceptance_criteria.md)；适用范围见 [历史索引](legacy_specs.md)。

对象：当前 AegisStay 服务。所有测试与运行截图使用 `AEGIS_FORCE_RULES=1`，未调用付费模型或真实酒店服务。页面截图临时启动了一个只监听本机的 Streamlit 实例。

## 环境与执行命令

Python 3.12、Streamlit 1.66.0、PyArrow 25.0.1，使用项目 `.venv\Scripts\python.exe`。测试临时目录位于已忽略的 `.venv\task-temp`。

```powershell
$env:AEGIS_FORCE_RULES='1'
$taskTestTemp = Join-Path (Get-Location) '.venv\task-temp'
New-Item -ItemType Directory -Path $taskTestTemp -Force | Out-Null
$env:TEMP=$taskTestTemp
$env:TMP=$taskTestTemp
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests/test_app.py
.\.venv\Scripts\python.exe -B -X utf8 -m harness.eval_runner
```

## 98 项单元与集成测试

| 模块 | 用例数 |
| --- | ---: |
| 核心业务 `test_app.py` | 40 |
| 既有难例、夜审与库存模块 `test_evolution.py` | 7 |
| API 与会话归属 `test_api.py` | 9 |
| 任务竞争、库存及部门结果 `test_task_runtime.py` | 29 |
| Streamlit 界面交互 `test_streamlit.py` | 3 |
| 口语取消及关联工单 `test_delivery_language.py` | 8 |
| 持久化恢复 `test_persistence.py` | 2 |
| 合计 | **98** |

完整测试的实际摘要：

```text
----------------------------------------------------------------------
Ran 98 tests in 5.225s

OK
```

测试工具另有 Starlette 关于 httpx 的弃用提示，以及 AppTest 在非服务器环境中的 ScriptRunContext 警告；本次没有失败或跳过界面测试。三项 AppTest 覆盖处理中继续输入、取消按钮和人工核验不绕过规则。

按原验收要求单独运行核心测试，完整摘要：

```text
........................................
----------------------------------------------------------------------
Ran 40 tests in 0.122s

OK
```

竞争测试通过事件或屏障控制旧任务晚返回、同一库存竞争等时序，检查最终状态，不依靠手工操作恰好赶上竞争窗口。

## Harness 20 条完整报告

以下为本次命令行实际输出：

```text
AegisStay Harness Evaluation · ⚡ 离线规则快速回归
======================================================================================
Target: 确定性规则引擎 (离线保底) | Cases: 20 | Passed: 20 | Avg response: 2.31 ms
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

3.31 ms 是这次本机离线规则的平均单例耗时，低于既有规则验收阈值 20 ms。20/20 与各项 100% 只描述该固定数据集，不代表真实 LLM、未见表达或生产端到端表现。页面截图另一次运行是 2.45 ms。

## 业务状态与 Trace 断言

在独立状态对象上直接执行并断言：

1. 1128 的早餐 FAQ 与两点延退同时保留，PMS 更新为 14:00。
2. 愤怒客诉的优先级为“高”、风险为“红色”，派往大堂经理。
3. 808 普通会员在人工审批前维持 12:00，批准后变为 14:00。
4. 建立毛巾和矿泉水两单；“不用了”先追问，“水不用了”只取消矿泉水，仍归属原水单任务 v2，库存恢复，毛巾不变。

实际输出如下，随机 ID 以本次运行为准：

```text
{
  "compound_faq": {
    "reply": "张先生，早餐供应时间为 07:00–10:00，地点在二楼餐厅。 另外，已更正关联 1128 房间。延迟退房已批准，退房时间更新为 14:00，无需额外费用。",
    "checkout_time": "14:00",
    "trace": [
      {
        "agent": "ReceptionAgent",
        "event": "关联档案",
        "detail": "房间 1128；已关联 CRM"
      },
      {
        "agent": "ReceptionAgent",
        "event": "知识检索",
        "detail": "hotel_knowledge.json / 1条命中 (breakfast)"
      },
      {
        "agent": "ReceptionAgent",
        "event": "apply_late_checkout",
        "detail": "14:00 → 已批准；黑金会员且房态充裕，权益内免费延迟退房"
      }
    ]
  },
  "complaint": {
    "priority": "高",
    "risk_level": "红色",
    "assigned_dept": "大堂经理",
    "trace": [
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
        "agent": "ManagerEscalationAgent",
        "event": "vertical_agent_dispatch",
        "detail": "HT-95C3F5DED1 → 大堂经理 / 高"
      }
    ]
  },
  "staff_approval": {
    "before": "12:00",
    "after": "14:00",
    "status": "已批准",
    "trace": [
      {
        "agent": "PMS",
        "event": "人工审批",
        "detail": "房间 808 / 14:00 / 已批准"
      }
    ]
  },
  "water_cancellation": {
    "clarification": "张先生，当前有多项送物服务，请先说明要修改或取消哪一种物品。",
    "reply": "张先生，已取消尚未派送的送物工单，并释放预留库存。",
    "same_task": true,
    "version": 2,
    "tickets": {
      "毛巾": "处理中",
      "矿泉水": "已取消"
    },
    "inventory": {
      "矿泉水": 36,
      "毛巾": 22
    },
    "trace": [
      {
        "agent": "Supervisor",
        "event": "version_checked",
        "detail": "TASK-8402229221d9 / v2"
      },
      {
        "agent": "ReceptionAgent",
        "event": "关联档案",
        "detail": "房间 1128；已关联 CRM"
      },
      {
        "agent": "HousekeepingAgent",
        "event": "delivery_change",
        "detail": "已取消尚未派送的送物工单，并释放预留库存。"
      }
    ]
  }
}
All business-state and Trace assertions passed.
```

对应可重复运行的断言另见 `tests/test_app.py` 的复合意图、客诉、人工审批测试，以及 `tests/test_delivery_language.py::DeliveryLanguageTests.test_screenshot_followup_cancels_only_water_and_preserves_task_id`。

## 浏览器实测与截图

在实际页面依次执行送毛巾、送矿泉水、歧义取消、明确取消。随后确认毛巾开始派送，再申请更换浴巾，运营页出现等待部门确认的变更。Harness 页面实际运行 20 条并显示 20/20。

四张原始浏览器截图及复现步骤见 [截图说明](../docs/images/README.md)，README 使用相对路径引用，可在 GitHub 查看。业务日期为模拟日期；截图没有真实客户信息、密钥或虚构的云端评测结果。

## 验收边界

本版验证的是单进程模拟业务、执行控制和离线规则。没有生产压测、真实酒店 PMS 回执、数据库持久化、重启恢复、独立模型循环、多 Agent token 对照或自动训练。Few-Shot 示例读取和回归不是参数微调；自动采集和审核发布的完整自更新闭环尚未实现。

10 月 16 日和 19 日的记录保留为历史证据；最新数量与行为以本文和 [README](../README.md) 为准。

## 本次生产基线补充

本次新增 SQLite 状态存储、进程重启后的已提交状态恢复、运行中任务的 interrupted 标记、住客／员工 Bearer 角色边界、Dockerfile、Compose 和浅色工作台主题。持久化测试使用临时 SQLite 文件，确认工单、库存、房号和会话任务可以跨进程实例恢复。

SQLite 版本按单酒店单 API worker 运行；多 worker、多副本和真实 PMS 对接仍需要 PostgreSQL/Redis、外部回执、迁移和备份方案。
