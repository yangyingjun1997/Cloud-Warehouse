# Development Plan

更新时间：2026-11-05

## Goal

在不破坏 NUC 现有 PostgreSQL 数据的前提下，将系统从“功能齐全但入口和数据入口分散”收敛为可试运行版本。

## Non-goals

- 不开发或同步微信小程序。
- 不清空服务器数据库，不重导 11.2 源表。
- 不新增与当前收口无关的大功能。
- 不兼容错误的 637 件资产结论；以程序规则和现场核对为准。

## Milestones

### M0 Project governance

- 删除冲突的旧计划、旧进度和旧验收报告。
- 建立 AGENTS、PROJECT_STATUS、DEVELOPMENT_PLAN、PROGRAM_STANDARDS、CHANGELOG 五个权威文件。
- 清除旧模板、临时数据库、构建输出和重复根目录文件。

Acceptance: 新 agent 能够在五分钟内确定当前目标、数据规则、测试要求、服务器地址和剩余工作。

### M1 Navigation consolidation

- 桌面一级导航收敛为工作台、业务办理、任务中心、库存中心、采购与报表。
- 资产检索只保留顶部全局搜索；通知只保留铃铛。
- 系统设置进入账号菜单。
- 低频工具使用模块内工具入口，不增加侧栏项目。
- 按权限隐藏模块，保持服务端权限不变。

Acceptance: 管理员侧栏最多五项；普通申请人只见工作台和业务办理；既有 URL 仍可直接访问。

### M2 V3 import and export

- 一个工作簿包含 `单件资产` 和 `耗材配件` 两张业务表。
- 资产沿用 V2 严格身份规则；耗材使用明确数量、单位、安全库存和成本字段。
- 同一格式可导出、修改并以更新模式重新导入。
- 预检显示新增、更新、跳过、警告和阻断。
- 页面只提供 V3 模板；旧解析器仅兼容历史文件。

Acceptance: 模板结构测试、资产和耗材新增/更新、重复标识、事务失败和全量模式测试通过。

### M3 Recovery controls

- 导入批次记录每条新建和更新对象的可逆信息。
- 批次撤销仅作用于安全可逆记录，存在后续历史时整体阻断。
- 超级管理员可删除无历史测试资产，必须输入 AST 或主资产编码并填写原因。
- 撤销和删除均生成不可变审计快照。

Acceptance: 正向、非管理员、确认文字错误、有业务历史、重复撤销、事务回滚测试全部通过。

### M4 Deployment and PostgreSQL smoke

Status: completed on 2026-11-06. PostgreSQL 回滚式冒烟已覆盖导入撤销、快速出入库、申请审批、采购到货、盘点修正、维修回库和离线冲突，业务数据计数前后保持不变。

- 本地全量测试通过后部署到 `192.168.123.101`。
- 部署前自动生成数据库和代码快照。
- 保留服务器 `.env`、PostgreSQL 和媒体文件。
- 执行迁移、静态文件、服务重启和 `/health/` 检查。
- 使用专用 QA 数据执行导入预检、快速出入库、申请审批、采购到货、盘点和撤销冒烟；测试数据清理由服务层完成。

Acceptance: 健康检查 200；服务 active；冒烟全通过；服务器版本和时间写入 PROJECT_STATUS。

### M5 Label calibration and manual maintenance

Status: software completed, device blocked. 标签机损坏期间不继续真机尺寸、浓度和批量打印验收。

- 旧台账 V4 转换入口已按手工维护方案移除，历史台账只作为人工核对依据。
- Windows 打印客户端增加 60×40 mm 校准页和完整 2×2 四格测试打印。
- 标签打印统一为“打印预览”入口，提供贴纸机与浏览器两条输出通道。

Acceptance: 打印渲染尺寸及四象限测试通过；全量回归通过后部署 NUC，服务器业务数据计数不变。

### M6 Trial-release hardening

- workflow API 分别使用审批和出入库执行权限，通知 API 仅允许读取与标记已读。
- 移除 Basic Authentication，内网 HTTP 阶段只保留时效令牌和 Session。
- 发布构建默认要求干净 Git 工作区，紧急脏构建必须携带 `dirty` 与内容哈希。
- 部署和代码回滚均把发布版本、Git revision、脏工作区状态及源内容哈希持久化到服务器运行状态。
- 重建包含最新标签布局的 Windows 打印客户端，并记录大小与 SHA-256。
- 将累计改动建立为可回滚 Git 基线，再生成干净发布包部署 NUC。

Acceptance: 拆分角色正反向测试、通知只读测试、全量回归和打印渲染通过；干净发布包 manifest 对应真实提交；NUC 部署前后业务数据计数不变。

### M7 Pre-production security and concurrency hardening

Status: in progress. 当前只面向内网小范围试运行，不把自动化回归包装成第三方安全认证。

- 建立上线前安全与质量评估报告，区分已验证、未验证和公网阻断项。
- 登录成功、失败、限流阻断和退出均写入匿名化安全事件审计。
- 使用 PostgreSQL 隔离测试库验证审批、资产预约、快速出库、耗材扣减和安全计数竞态。
- 建立只读 HTTP 并发基线工具，输出 P50/P95/P99、吞吐、状态码和错误率。
- 已确认并实现特权账号会话策略：普通申请人允许多设备，管理员/审批/出库账号默认最多 2 个有效会话，支持查看和撤销其他设备。
- 账号会话撤销、超限淘汰和过期清理均使用数据库共享状态；PostgreSQL 隔离测试库增加并行登录上限竞态回归。
- 资产台账校正与基础资料编辑增加 `updated_at` 乐观锁；页面打开后记录被其他账号修改时，拒绝旧表单覆盖并要求刷新。
- 钉钉双向交互先完成安全边界设计，不开放自由群聊文本直接修改库存。

Acceptance: 本地全量回归通过；PostgreSQL 并发专项在目标环境通过；旧版本表单不能覆盖已变更的资产或基础资料；NUC 只读基线有可复现结果；报告记录剩余风险且不包含明文凭据。

## Regression checklist

1. `python manage.py check`
2. `python manage.py makemigrations --check --dry-run`
3. 相关 app 专项测试
4. Django 全量测试
5. `node --check` 检查修改的 JavaScript
6. `git diff --check`
7. 桌面与移动页面截图检查
8. NUC PostgreSQL 冒烟和健康检查
9. 更新 PROJECT_STATUS 与 CHANGELOG

## Rollback

- 代码：使用部署脚本生成的部署前代码快照。
- 数据库：使用部署前 `pg_dump --format=custom` 快照。
- 迁移：优先使用向前修复；只有经过验证的可逆迁移才执行数据库回退。
- 前端：保留旧 URL，导航改动只改变入口，不删除路由。
