# 2026-10-23 学术酒店版本验收记录

> 适用范围：历史验收记录，仅证明记录所指版本，不能作为新实现通过的证据。当前要求见 [主规格](specification.md)、[验收标准](acceptance_criteria.md)；适用范围见 [历史索引](legacy_specs.md)。

验证环境：Windows，Python 3.12.4，Streamlit 1.66.0，FastAPI 0.141.1。全部业务测试强制离线规则模式，不调用云端模型，不使用真实酒店或住客数据。

本轮完成单酒店 API 工作台、入住访问凭证、员工岗位边界、SQLite 单写入与原子检查点、消息归档、重启幂等、配送变更确认、待检库存和学术接待人工审批。没有将真实学校上线、外部 PMS、统一身份认证或生产容量作为已验证能力。

## 执行命令

```powershell
$env:AEGIS_FORCE_RULES='1'
$env:AEGIS_AUTH_ENABLED='0'
$env:AEGIS_DEPLOYED='0'
$env:AEGIS_DB_PATH=''
$env:AEGIS_SITE_PROFILE='standard'
Remove-Item Env:AEGIS_API_URL -ErrorAction SilentlyContinue
Remove-Item Env:AEGIS_BUSINESS_DATE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests/test_app.py
.\.venv\Scripts\python.exe -B -X utf8 -m harness.eval_runner
```

测试中的学术接待与鉴权用例自行切换到 `academic` 并创建临时数据库；旧固定基线维持 `standard`，两种政策不会混作一组权益判断。完整回归含 29 项新增部署验收，已有用例未减少。Streamlit AppTest 的缺少 ScriptRunContext 提示与 Starlette 客户端弃用提示不影响下面结果。

## 完整回归与核心单测

```text
Ran 127 tests in 34.006s
OK
```

按仓库要求单独执行核心模块的完整摘要：

```text
........................................
----------------------------------------------------------------------
Ran 40 tests in 0.132s

OK
```

## Harness 固定业务回归

```text
AegisStay Harness Evaluation · ⚡ 离线规则快速回归
======================================================================================
Target: 确定性规则引擎 (离线保底) | Cases: 20 | Passed: 20 | Avg response: 2.61 ms
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

上表 100% 仅针对仓库固定 20 条规则用例；2.61 ms 是本次本地规则计算均值，不是模型延迟、接口端到端时间或生产承载能力。

## 黑板与 Trace 断言

独立构造强制规则的 `HotelOrchestrator` 验证以下三组状态：

```python
assert all(word in compound_reply for word in ('07:00', '10:00', '14:00', '已批准'))
assert compound.state.pms_rooms['1128']['checkout_time'] == '14:00'
assert complaint.state.tickets[0]['priority'] == '高'
assert complaint.state.tickets[0]['risk_level'] == '红色'
assert checkout.state.pms_rooms['808']['checkout_time'] == '12:00'
checkout.review_checkout('808', '14:00', True)
assert checkout.state.pms_rooms['808']['checkout_time'] == '14:00'
assert checkout.state.last_trace[0]['event'] == '人工审批'
```

实际运行输出：

```json
{
  "profile": "standard",
  "compound": {
    "reply": "张先生，早餐供应时间为 07:00–10:00，地点在二楼餐厅。 另外，已更正关联 1128 房间。延迟退房已批准，退房时间更新为 14:00，无需额外费用。",
    "checkout": "14:00",
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
        "detail": "HT-521DCA0E95 → 大堂经理 / 高"
      }
    ]
  },
  "manual_review": {
    "before": "12:00",
    "after": "14:00",
    "trace": [
      {
        "agent": "PMS",
        "event": "人工审批",
        "detail": "房间 808 / 14:00 / 已批准"
      }
    ]
  },
  "assertions": "PASS"
}
```

这些早餐与商业会员权益是旧 `standard` 固定数据；学术模式的延退测试验证“会务知识回答 + 待审核 + 前台审批 + 重启恢复”，并确认不会自动承诺免除费用。

## 新增业务边界

| 场景 | 实际验证 |
| --- | --- |
| 住客凭证 | 跨房／跨会话拒绝，过期／吊销／换客后失效，旧住客服务不泄露，新会话也不能取消旧会话工单 |
| 岗位授权 | 工程不能处理客房工单、读取住客私聊或获取住客档案字段 |
| 并发与写入 | 第二个写入进程被拒绝；失效版本不能提交；写库失败回滚库存和工单 |
| 原子性 | 注入会话 SQL 写入失败，业务检查点不部分提交 |
| 网络重试 | 重启及内存裁剪后同 request_id 仍返回原 run_id，不重复扣库存 |
| 变更与审批 | 旧 change_id 拒绝；待确认改单不能结单；延退旧入住申请不能获批 |
| 学术接待权益 | 未核验接待订单不自动发放早餐券；延退批准不自动承诺免费 |
| 日常运营 | 夜审重复日期拒绝；初始日期可配置，重启不自行跨日；已送达回收进入待检 |
| 历史与恢复 | 裁剪窗口外消息仍可分页查询，重启保留；一致性备份包含最新工单 |
| 配置和容量保护 | 弱密钥、非法岗位映射和非法知识条目拒绝；频率超限返回 429 |
| 部署工作台 | 无凭证不显示业务，住客只显示自己的服务，员工通过 HTTP 办理，无第二个数据库写入者 |

## 浏览器验收

在仅绑定本机回环地址的独立 API 与 Streamlit 工作台，使用临时随机凭据、临时 SQLite 和初始客房数据完成：住客申请毛巾、员工开始派送、住客更换浴巾、员工确认撤回、前台审批学术接待延退、住客查看办理通知。当前三个产品截图来自这套鉴权工作台，详见 [截图说明](../docs/images/README.md)。

## 尚未验证的部署条件

本机没有 Docker，未运行容器构建或 Compose。仓库已增加 Linux CI 测试与镜像构建步骤，CI 结果应以 GitHub 实际运行记录为准。真实 PMS 回执、学校 SSO、酒店网络配置、真实政策审核及现场容量与恢复演练仍需酒店环境完成。
