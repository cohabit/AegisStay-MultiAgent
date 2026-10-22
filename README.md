# AegisStay · 学术酒店服务运营中枢

面向学校学术酒店的访学接待与学术会议住宿，AegisStay 将住客服务请求、部门工单、房态和人工审核放在同一条可追踪业务链上。系统由接待、客房保洁、工程、礼宾和大堂经理等职责模块组成，支持多轮补充、处理中取消或改单、部门确认、库存校验和质量回归。

系统的工作是把一句自然语言需求变成可核验、可派发、可修改、可追踪的部门工单。当前版本面向单酒店小规模部署，提供 Streamlit 工作台、FastAPI 网关、SQLite 持久化、Docker Compose 运行方式和可选的 OpenAI 兼容模型接入。仓库内置 240 间客房初始数据；通过适配层替换为酒店实际 PMS 前，需要完成接口映射、权限审批和现场验收。

项目机制、业务边界与代码对应见 [项目剖析](docs/academic_hotel_analysis.md)，部署、备份和恢复见 [值班手册](docs/operations.md)。

## 产品界面

截图来自当前版本页面，统一使用浅色主题和 1280 × 720 浏览器视口。数据为仓库内置的初始酒店数据，图片展示完整业务上下文，不包含密钥或真实住客信息。

### 运营总览

![酒店运营总览](docs/images/operations-overview.jpg)

### 住客服务处理

![住客服务对话与服务进度](docs/images/guest-cancellation.jpg)

### 配送变更确认

![配送中的工单变更确认](docs/images/delivery-change-confirmation.jpg)

截图复现步骤和采集环境见 [产品界面截图说明](docs/images/README.md)。

## 核心流程

~~~mermaid
flowchart LR
    A[住客消息] --> B[接待理解与补槽]
    B --> C[任务关系判断]
    C --> D[后台任务与版本控制]
    D --> E[部门处理模块]
    E --> F[业务规则与库存校验]
    F --> G{当前版本仍有效？}
    G -->|是| H[写入工单与 PMS]
    G -->|否| I[丢弃过时结果]
    H --> J[运营确认与状态回执]
~~~

- **需求处理**：缺房号先补充；FAQ 与服务申请可以在同一轮保留。
- **任务控制**：每次执行有 run_id、task_id 和版本号；新需求可以取消或更新旧任务。
- **业务安全**：提交前检查房号、部门、物品、数量、风险、库存和当前业务基线。
- **执行阶段**：待派送可修改，配送中等待部门撤回确认，已送达需要确认取回；回收物品进入待检库存。
- **结果仲裁**：主控按最新需求、可信业务记录和规则决定是否提交；冲突进入人工核验。
- **模型边界**：模型可辅助提取意图、房号和情绪；工单写入、权限、库存和状态转换由程序校验。

## 运行方式

### 本地运行

使用 Python 3.12 或更高版本：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:AEGIS_FORCE_RULES='1'
.\.venv\Scripts\python.exe -m streamlit run app.py
~~~

工作台默认访问 http://localhost:8501。需要 API 时：

~~~powershell
$env:AEGIS_FORCE_RULES='1'
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
~~~

### Docker Compose

~~~powershell
.\.venv\Scripts\python.exe scripts/manage.py init
docker compose up -d --build
~~~

初始化生成已被 Git 忽略的 deploy.env，不打印密钥、不覆盖已有部署。个人岗位凭据保存在其中，应通过受控方式分发给相应员工；不要提交或公开展示该文件。

- 工作台：http://localhost:8501
- API 文档：http://localhost:8000/docs
- 就绪检查：http://localhost:8000/ready
- 持久化文件：仅 API 挂载的数据卷 /data/aegisstay.sqlite3

工作台只通过 API 办理业务，没有独立数据库和模型密钥。一个 API worker 是唯一写入者；文件写锁阻止第二个进程覆盖旧状态。端口默认仅绑定本机，酒店网络访问应经过 HTTPS 反向代理。当前机器未安装 Docker，容器构建未在本机实跑；CI 已增加 Linux 测试与镜像构建步骤，结果需以实际运行记录为准。

## 配置与权限

本地离线开发使用 .env.example；部署使用生成的 deploy.env。生产强制启用鉴权、持久化和随机强密钥，缺少配置时启动失败。

| 变量 | 作用 |
| --- | --- |
| AEGIS_DB_PATH | API 的 SQLite 文件路径 |
| AEGIS_API_URL | 工作台连接的固定 API 地址；工作台不打开数据库 |
| AEGIS_DEPLOYED | 部署模式，禁止无鉴权、无持久化运行 |
| AEGIS_STAFF_TOKEN | 初始化管理员凭据，不能分发给住客 |
| AEGIS_STAFF_KEYS_JSON | 每位员工的 subject 与岗位凭据映射 |
| AEGIS_SIGNING_KEY | 住客凭证签名密钥，不能与员工凭据共用 |
| AEGIS_SITE_PROFILE | academic 为学术接待政策；standard 为原固定回归基线 |
| AEGIS_BUSINESS_DATE | 新库初始业务日期；已有数据库按持久化日期恢复，不随重启推进 |
| AEGIS_PMS_FILE / AEGIS_KNOWLEDGE_FILE | 经核验的初始房态与批准的政策文件 |
| AEGIS_ALLOWED_ORIGINS | API 允许的前端来源 |
| AEGIS_FORCE_RULES | 1 使用规则；关闭后才允许模型调用 |
| OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY | 仅 API 服务持有的模型配置 |

住客没有全店共享 Token。前台核验订单和入住身份后签发有有效期的访问凭证，绑定 room_no、stay_id、session_id；吊销、到期、换客后失效。员工按前台、客房、工程、经理和管理员岗位授权，部门岗位不能读取住客聊天档案。资源归属与权限在 API 检查，不能依赖页面隐藏按钮。

学术酒店的延退由前台核对接待订单和排期；系统不会根据自述学者／嘉宾身份自动赠送权益。初始政策模板没有真实学校会场、早餐时间和校园通行规则，须经酒店审核补齐。

## API 入口

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| POST | /api/chat | 住客 | 同步处理一条消息 |
| POST | /api/chat/submit | 住客 | 提交后台任务，返回执行标识 |
| GET | /api/tasks/{run_id} | 住客 | 查询本会话任务 |
| GET | /api/session/{session_id} | 住客 | 查询会话与服务进度 |
| POST | /api/session/reset | 住客 | 清理本会话 |
| GET | /api/pms/overview | 员工 | 查看房态与部门队列 |
| GET | /api/tickets | 员工 | 查询工单 |
| POST | /api/tickets/{ticket_id}/dispatch | 员工 | 确认开始配送 |
| POST | /api/tickets/{ticket_id}/change/confirm | 员工 | 确认或拒绝配送变更 |
| POST | /api/tickets/{ticket_id}/complete | 员工 | 完成工单 |
| POST | /api/pms/night-audit | 员工 | 执行跨日夜审 |
| POST | /api/guest-access | 前台／经理 | 核验后签发本次入住凭证 |
| POST | /api/guest-access/{id}/revoke | 前台／经理 | 吊销访问凭证 |
| POST | /api/rooms/{room_no}/stay | 前台／经理 | 登记接待台账，更替入住并吊销旧凭证 |
| POST | /api/checkout/{room_no}/review | 前台／经理 | 审批延退 |
| GET | /api/session/{session_id}/messages | 本会话住客 | 分页查询历史消息 |
| GET | /api/audit | 经理／管理员 | 查看员工操作审计 |

## 持久化与恢复

设置 AEGIS_DB_PATH 后，SQLite 在同一事务保存共享房态、工单、库存、延退记录、会话、请求幂等记录、消息和审计。只有写入成功才返回业务成功；写库失败会回滚内存修改。服务重启时会恢复已提交业务状态；正在运行的后台任务会标记为 interrupted，需要重新提交，避免恢复一个已经失去外部回执的动作。

会话消息按滑动窗口裁剪，早期消息独立归档，住客持有效凭证可跨天恢复同一会话并分页查看。工作黑板保存房号、待办、任务版本和冲突。数据库保存的是应用状态，不等于完整 CRM 客史；多酒店租户隔离、数据保留策略和删除请求需要根据酒店要求另行配置。定期备份 /data/aegisstay.sqlite3，并限制文件权限。

## 模型与质量控制

没有模型密钥或调用失败时，系统使用本地规则继续提供确定性业务处理。动态 Few-Shot 只改变本次模型输入，不会更新模型参数。当前质量底座包括 20 条固定回归、预置难例和运行时示例检索；它不把评测通过率当成真实模型泛化率，也不把示例更新称为微调。

固定回归与业务边界测试：

~~~powershell
$env:AEGIS_FORCE_RULES='1'
$env:AEGIS_AUTH_ENABLED='0'
$env:AEGIS_DEPLOYED='0'
$env:AEGIS_DB_PATH=''
$env:AEGIS_SITE_PROFILE='standard'
Remove-Item Env:AEGIS_API_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests/test_app.py
.\.venv\Scripts\python.exe -B -X utf8 -m harness.eval_runner
~~~

当前版本新增了入住凭证、岗位隔离、同库多写入防护、写入失败回滚、重启幂等和 API 工作台等验收。完整结果见 [2026-10-23 验收记录](spec/release_verification_2026-10-23.md)。原版记录保留为历史证据。这是离线规则和本地状态验证，不是生产容量或真实 PMS 回执证明。

## 目录

~~~text
app.py                 Streamlit 工作台
deployed_ui.py         按身份和岗位显示的 API 工作台
api_client.py          工作台 HTTP 访问层
server.py              FastAPI 网关与权限入口
hotel_core.py          黑板状态、部门处理和业务规则
agent_runtime.py       后台任务、版本、幂等、SQLite 状态边界
persistence.py         SQLite 状态存储
auth.py                岗位凭据、入住签名凭证与资源归属
data/                  初始房态与酒店知识
harness/               回归数据、难例和示例检索
tests/                 业务、API、并发、界面和持久化测试
spec/                  接口契约、验收记录和后续设计
scripts/manage.py      部署初始化与一致性备份
Dockerfile             非 root 服务镜像
docker-compose.yml     单酒店部署编排
~~~

## 酒店现场对接

1. 接入酒店 PMS、库存和工单系统，使用幂等命令和外部回执替换本地写入。
2. 对接学校统一身份认证，确认个人岗位、数据保留策略与凭据分发。
3. 配置备份、恢复演练和监控；需要多实例部署时再迁移数据库并设计多副本一致性。
4. 建立真实 bad case 审核、独立测试集、发布审批和回滚流程。
5. 在酒店现场用脱敏数据验证取消、撤回、延退、客诉升级和夜审规则。
