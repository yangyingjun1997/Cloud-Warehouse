# Project Agents

本仓库处于内部调试和真实仓库试运行准备阶段。任何 agent 在修改文件前都必须完整阅读本文件，以及下列权威文件：

1. `README.md`：产品边界、架构和运行入口。
2. `PROJECT_STATUS.md`：唯一有效的当前进度、已知问题和待办。
3. `DEVELOPMENT_PLAN.md`：当前阶段目标、实施顺序和验收标准。
4. `PROGRAM_STANDARDS.md`：代码、数据、安全、测试和部署规则。
5. `CHANGELOG.md`：已实施变更记录。

旧计划、旧进度和旧验收报告不得重新引入根目录；历史结论与上述文件冲突时，以上述文件为准。

## Mandatory workflow

### Before editing

- 执行 `git status --short`，不得覆盖或回退其他人的未提交改动。
- 阅读目标模块、已有测试和最新迁移，优先沿用 `backend/apps/` 的现有边界。
- 核对 `PROJECT_STATUS.md` 中的当前任务和非目标；不要顺手扩展范围。
- 涉及数据删除、全量导入、编码重写、权限或部署时，先确认可恢复路径和审计要求。

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

### Deployment and verification

- 当前调试服务器为 `192.168.123.101`，部署不允许清空或覆盖服务器原数据库。
- 每个完成且通过本地回归的改动集都部署到 NUC；部署脚本必须先做 PostgreSQL 和代码快照。
- 发布包不得包含数据库重建、清库或历史基线强制重导脚本；一次性数据修复工具只能保存在不入库、不打包的受控目录，并经用户逐次确认后运行。
- 部署后检查迁移、静态文件、Django/Gunicorn、Nginx、`/health/`，并执行与改动相关的 PostgreSQL 冒烟。
- 部署失败立即停止后续操作，保留日志并按快照回滚；不得反复覆盖生产目录碰运气。
- 密码、Webhook、Secret、数据库口令不得写入仓库、日志或文档。

### Before finishing

- 对照用户需求逐项核对，不以“测试通过”代替业务验收。
- 更新 `PROJECT_STATUS.md` 的进度、测试结果、服务器版本和剩余任务。
- 向 `CHANGELOG.md` 的 `Unreleased` 追加本次实际变更；不要记录未实施设想。
- 若改变规则、架构或部署方式，同步更新 `PROGRAM_STANDARDS.md`、`README.md` 或操作手册。
- 再次执行 `git status --short`，明确哪些文件属于本次修改，禁止留下临时数据库、构建目录和测试输出。

## Current scope

- 当前交付：响应式 Web/PWA + Django + PostgreSQL + Nginx + systemd。
- 微信小程序不在当前开发清单，不因 Web 改动同步开发小程序。
- 当前优先级：导航收口、V3 导入导出、导入回滚、受控测试资产删除、服务器冒烟。
- 新功能若不属于 `DEVELOPMENT_PLAN.md` 当前阶段，先记录到 `PROJECT_STATUS.md` 的候选项并与用户确认。
