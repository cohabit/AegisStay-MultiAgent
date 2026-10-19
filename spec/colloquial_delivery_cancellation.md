# “水不用了”取消失败：原因与修复记录

日期：2026-10-19。基于用户截图中的两张送物工单和后续澄清复现。

## 当前到底有没有用大模型

项目有可选的模型接入，位于 `ReceptionAgent.understand`，用于送物、报修、咨询等意图以及房号、情绪的提取。该调用主要使用本轮文本和提示，未接入当前工单列表来解析取消对象。

此前测试命令设置了 `AEGIS_FORCE_RULES=1`，会禁用所有实际模型调用。取消／更换动作、具体物品和目标工单的定位目前由 `agent_protocol.py`、业务核心和任务运行时共同处理。因此不能把本次规则通过率当成 LLM 语义理解能力，也不能承诺仅开启模型就能解决指代问题。

## 原因与本次修改

旧实现只在部分送水语句中认识“水”，目标工单匹配仍依赖“矿泉水”的完整名称。“不用了”在多工单场景中正确要求澄清；随后“水不用了”没有被转换成明确的矿泉水对象，所以又一次要求澄清。

本次统一了物品别名和否定表达解析，保留规范化后的物品引用供运行时和工单定位共同使用。支持“水不用了”“水不需要了”“那瓶水不用了”“饮用水不用了”等简单表达，同时保留原始用户消息。否定温度要求“矿泉水不要太凉”不会被当成取消；热水、漏水和水管等未识别对象不会直接套用到矿泉水工单。

当另一项送毛巾任务仍在执行时，“水不用了”也只关联矿泉水任务，不会误中断毛巾任务。动作提交仍经过版本和库存校验。

这是一项确定性规则修复，没有新增 LLM 上下文工单选择器，也未调用收费模型。通用指代、多物品复合要求和更复杂的语义，仍需扩展解析与评测；未来接入模型时，应让它根据本轮文本及相关工单候选输出结构化的动作和目标，由程序继续核验。

## 回归结果

设置 `AEGIS_FORCE_RULES=1`，在项目虚拟环境执行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_app.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe -m harness.eval_runner
```

原核心结果：

```text
........................................
----------------------------------------------------------------------
Ran 40 tests in 0.125s

OK
```

完整回归结果：

```text
Ran 96 tests in 9.999s

OK
```

新增 `tests/test_delivery_language.py` 共 8 项测试，其中多个用例使用子用例覆盖不同表述；原 88 项保留。完整回归包含原来的 3 项 Streamlit AppTest，均未跳过。测试工具仅出现兼容性弃用提示与 AppTest 的上下文提示，没有测试失败。

Harness 实际输出：

```text
AegisStay Harness Evaluation · ⚡ 离线规则快速回归
======================================================================================
Target: 确定性规则引擎 (离线保底) | Cases: 20 | Passed: 20 | Avg response: 2.19 ms
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

上述 100% 仅指已知 20 组离线规则用例，不代表云端模型泛化准确率。

## 截图场景与业务状态证明

依次执行“1128 送两条毛巾”“送两瓶矿泉水”“不用了”“水不用了”，并断言：毛巾单保留、矿泉水单取消、任务归属不变、库存正确返还。另回归复合 FAQ、愤怒客诉和普通会员人工审批的状态变化。

实际输出：

```json
{
  "input": "水不用了",
  "reply": "张先生，已取消尚未派送的送物工单，并释放预留库存。",
  "before": {
    "毛巾": "处理中",
    "矿泉水": "处理中"
  },
  "after": {
    "毛巾": "处理中",
    "矿泉水": "已取消"
  },
  "inventory": {
    "毛巾": 22,
    "矿泉水": 36
  },
  "same_water_task": true,
  "version": 2,
  "llm_disabled": true,
  "trace": [
    {
      "agent": "Supervisor",
      "event": "version_checked",
      "detail": "TASK-e750c3f5ed2a / v2"
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
  ],
  "compound_faq": {
    "faq_preserved": true,
    "checkout_time": "14:00"
  },
  "complaint": {
    "priority": "高",
    "risk_level": "红色"
  },
  "staff_approval": {
    "before": "12:00",
    "after": "14:00"
  }
}
```

```text
All cancellation, inventory and Trace assertions passed.
```

修改文件：`agent_protocol.py`、`hotel_core.py`、`agent_runtime.py`；新增回归为 `tests/test_delivery_language.py`。没有修改简历、模型配置、API Key 或启动常驻服务。

页面复测时，建议停止并重新启动服务，再清空会话，以免保留旧的内存对象。仍可使用离线开关验证这个修复，无需开启云端调用。手动步骤见 [测试范例](manual_testing_examples.md)。
