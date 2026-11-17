# Project Agents

本仓库处于内部调试和真实仓库试运行准备阶段。任何 agent 在修改文件前都必须完整阅读本文件，以及下列权威文件：

1. `README.md`：产品边界、架构和运行入口。
2. `PROJECT_STATUS.md`：唯一有效的当前进度、已知问题和待办。
3. `DEVELOPMENT_PLAN.md`：当前阶段目标、实施顺序和验收标准。
4. `PROGRAM_STANDARDS.md`：代码、数据、安全、测试和部署规则。
5. `CHANGELOG.md`：已实施变更记录。

旧计划、旧进度和旧验收报告不得重新引入根目录；历史结论与上述文件冲突时，以上述文件为准。

## Handoff protocol

任何接力 agent 必须先读取本文件和五个权威文件，再开始搜索或编辑。进入任务后的第一条状态记录必须明确以下三种版本，不得混称：

1. Git 已提交基线：`git rev-parse HEAD` 的 revision。
2. 本地未提交工作区：`git status --short` 列出的文件及其是否属于本轮任务。
3. NUC 实际运行 release：读取服务器 `/opt/after-sales-warehouse/current/release-info`；无法连接时写“未复核”。

接力 agent 必须从 `PROJECT_STATUS.md` 的“Rapid handoff”开始，沿 `DEVELOPMENT_PLAN.md` 当前阶段继续，不得依据聊天记录或旧报告自行恢复已废弃方案。完成一个任务后，必须同时更新状态、计划（若范围或验收改变）和变更记录，并写明代码实现、自动化通过、现场验收和部署是否分别完成。

当前调试数据库和代码保护规则：

- NUC 地址固定为 `192.168.123.101`，数据库是 PostgreSQL；任何部署不得清库、重建、重导 11.2 或覆盖 `/srv/after-sales-warehouse/app.env`。
- 本机默认使用 `runtime-data/db.sqlite3`；不要直接操作 NUC 数据库，也不要把本机测试数据当成服务器数据。
- 当前最新本地工作区包含未提交改动；除非用户明确要求提交，不得替他人提交、回退或覆盖这些文件。
- 新功能必须先登记开发计划；P1 阻断项未关闭前不得顺手加入无关功能。
- 发布前必须先做 PostgreSQL 快照；部署失败停止并保留日志，禁止反复覆盖目录碰运气。
- 不得把密码、Webhook、Secret、数据库口令或完整环境文件写入代码、日志、报告、候选包或聊天。

## Mandatory workflow

### Source-of-truth and progress discipline

- 每次开始工作时必须区分三个状态：Git 已提交基线、本地未提交最新工作区、NUC 实际运行 release。三者不得混写成“当前版本”。
- `PROJECT_STATUS.md` 中的历史部署记录只证明当时结果；本轮无法连接 NUC 时必须写“未复核”，不得沿用旧健康结论。
- 新功能必须先进入 `DEVELOPMENT_PLAN.md`，写明范围、风险、自动化验收和现场验收，再开始实现。其他 agent 临时加入但未入计划的功能一律视为“待审查”，不能直接部署。
- “代码已写”“自动化通过”“现场验收通过”“已部署”是四个独立状态，不得相互替代。
- 当前稳定化阶段优先修复 `PROJECT_STATUS.md` 的 P1 阻断项；阻断项未关闭前不得叠加无关功能。

### Before editing

- 执行 `git status --short`，不得覆盖或回退其他人的未提交改动。
- 阅读目标模块、已有测试和最新迁移，优先沿用 `backend/apps/` 的现有边界。
- 核对 `PROJECT_STATUS.md` 中的当前任务和非目标；不要顺手扩展范围。
- 涉及数据删除、全量导入、编码重写、权限或部署时，先确认可恢复路径和审计要求。
- 扫码、打印、摄像头、离线和 PWA 改动必须先读取对应页面脚本及现场验收记录，不能只凭单元测试推断设备行为。

### During editing

- 保持改动小、可追踪，产品文案使用中文，代码标识符使用英文。
- `common` 放共享能力；`inventory` 放库存与资产；`workflow` 放申请、审批和出入库；`accounts` 放身份；`notifications` 放用户通知。
- 对外 API 保持兼容并可版本化；业务对象使用 UUID，展示流水号另行生成。
- 每个状态变更必须经过服务层、事务、权限检查和审计记录，禁止在模板或视图中直接拼凑状态。
- 不允许仅根据 SN、IP、备注、数量或状态把源表行推断为单件资产。
- SC、DZSB 原样保留；AST 是系统永久身份，不随仓库、状态或归属变化。
- 有业务历史的资产禁止硬删除；测试资产删除也必须通过受控服务和完整快照审计。

### Testing

- 共享数据使用 `apps.common.testing.fixtures.WarehouseTestFixture` 的 `make_*` 工厂。
- 新测试优先放在对应 app；单文件超过约 600 行时拆分 `tests/` 包。
- 每个状态变更至少包含一个正向用例和一个权限或约束反向用例。
- 涉及审计必须验证 `OperationAuditLog` 和 `AssetLifecycleEvent` 的只增不改约束。
- 提交前至少执行相关专项测试、`python manage.py check`、`git diff --check`。
- 当前阶段每个完整改动集还必须执行 Django 全量测试；不能运行时在 `PROJECT_STATUS.md` 记录原因和风险。
- HID 扫码改动必须覆盖英文输入法、中文输入法组合事件、Enter/Tab 后缀、输入框有/无焦点、连续三次扫码和失败后继续扫描。
- ZEBRA ZT610 打印改动必须区分“位图渲染正确”和“真实走纸正确”。单元测试只能证明前者；后者必须使用 60×40 mm 间隙纸完成真机验收。
- 审批等批量动作必须覆盖真实 UUID、无权限、部分任务已处理、重复 ID、拒绝原因和并发重复提交；不得只测试页面渲染。

### Deployment and verification

- 当前调试服务器为 `192.168.123.101`，部署不允许清空或覆盖服务器原数据库。
- 每个完成且通过本地回归的改动集都部署到 NUC；部署脚本必须先做 PostgreSQL 和代码快照。
- 发布包不得包含数据库重建、清库或历史基线强制重导脚本；一次性数据修复工具只能保存在不入库、不打包的受控目录，并经用户逐次确认后运行。
- 部署后检查迁移、静态文件、Django/Gunicorn、Nginx、`/health/`，并执行与改动相关的 PostgreSQL 冒烟。
- 部署失败立即停止后续操作，保留日志并按快照回滚；不得反复覆盖生产目录碰运气。
- 密码、Webhook、Secret、数据库口令不得写入仓库、日志或文档。
- NUC 不可达时不得声称完成部署或服务器回归；只允许记录本地代码状态和待部署版本。

### Before finishing

- 对照用户需求逐项核对，不以“测试通过”代替业务验收。
- 更新 `PROJECT_STATUS.md` 的进度、测试结果、服务器版本和剩余任务。
- 向 `CHANGELOG.md` 的 `Unreleased` 追加本次实际变更；不要记录未实施设想。
- 若改变规则、架构或部署方式，同步更新 `PROGRAM_STANDARDS.md`、`README.md` 或操作手册。
- 再次执行 `git status --short`，明确哪些文件属于本次修改，禁止留下临时数据库、构建目录和测试输出。

## Current scope

- 当前交付：响应式 Web/PWA + Django + PostgreSQL + Nginx + systemd。
- 微信小程序不在当前开发清单，不因 Web 改动同步开发小程序。
- 当前优先级：① 遗留 P2（`warehouse_setup` 缺 `@login_required`、`/me` 权限谓词不一致、无 `LOGGING`、`CONN_MAX_AGE=0`、SW 版本号硬编码、前缀白名单不一致、7 个页面同类布局）；② 邮件摘要（免费 SMTP 方案，待用户确认服务商）；③ 依赖锁统一；④ 现场验收（iPhone Safari 主屏幕、DS3678、ZT610 60×40 mm 走纸）与 PostgreSQL 并发专项。
- 新功能若不属于 `DEVELOPMENT_PLAN.md` 当前阶段，先记录到 `PROJECT_STATUS.md` 的候选项并与用户确认。
