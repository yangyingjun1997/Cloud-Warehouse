# Development Plan

更新时间：2026-11-16

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

Status: automated security and authenticated read-capacity checks completed; Python 3.12 upgrade has a deployment record but awaits NUC reconfirmation; external security and field acceptance remain. 当前只面向内网小范围试运行，不把自动化回归包装成第三方安全认证。

- 建立上线前安全与质量评估报告，区分已验证、未验证和公网阻断项。
- 登录成功、失败、限流阻断和退出均写入匿名化安全事件审计。
- 使用 PostgreSQL 隔离测试库验证审批、资产预约、快速出库、耗材扣减和安全计数竞态。
- 建立只读 HTTP 并发基线工具，输出 P50/P95/P99、吞吐、状态码和错误率。
- 已确认并实现特权账号会话策略：普通申请人允许多设备，管理员/审批/出库账号默认最多 2 个有效会话，支持查看和撤销其他设备。
- 账号会话撤销、超限淘汰和过期清理均使用数据库共享状态；PostgreSQL 隔离测试库增加并行登录上限竞态回归。
- 资产台账校正与基础资料编辑增加 `updated_at` 乐观锁；页面打开后记录被其他账号修改时，拒绝旧表单覆盖并要求刷新。
- workflow API 已禁用申请单硬删除，主单与明细创建使用同一事务；库存成本字段按财务权限从 API 响应移除。
- 生命周期附件直链在 Nginx 模板中拦截；正式清库入口已从应用移除；审计 QuerySet 更新/删除已阻断。
- 终态资产禁止直接恢复；受控测试资产删除保存完整关联快照；已有数据库快照失败时部署立即停止。
- PostgreSQL 审计表增加 UPDATE/DELETE 拒绝触发器；钉钉通知使用持久化 outbox、幂等键和指数退避。
- 部署使用独立 release、持久化环境文件、原子 `current` 切换、健康失败恢复和保留最近版本。
- Playwright E2E 已覆盖登录、桌面/移动布局、扫码快速借出、CSP 和离线记录隔离；NUC PostgreSQL 专项与回滚式业务冒烟已通过。
- pip-audit、生产代码 Bandit、认证 DAST 和登录后八条业务路由的 1/4/8/12 并发基线已完成；异常 UUID/搜索表达式不再导致 500。
- 已有记录显示 Python 3.12 运行时与依赖升级、认证容量和 5 分钟资源采样已完成；2026-11-10 因 NUC 不可达未重新核验 deployed release，恢复连接后必须复核。
- 待完成：HTTPS 公网边界、独立人工渗透、异机完整恢复演练和现场设备验收。
- 钉钉双向交互先完成安全边界设计，不开放自由群聊文本直接修改库存。

Acceptance: 本地全量回归通过；PostgreSQL 并发专项在目标环境通过；旧版本表单不能覆盖已变更的资产或基础资料；NUC 只读基线有可复现结果；报告记录剩余风险且不包含明文凭据。

### M8 Trial stabilization and device closure

Status: software implementation complete, field and NUC verification pending. 2026-11-16 本机已完成批量审批、中文输入法扫码、ZT610 RAW ZPL 和 worker 生命周期修复；本阶段仍冻结无关功能扩展，直到 NUC/设备验收完成。

实施顺序：

1. **版本与依赖基线**：保留当前脏工作区并核对改动归属；把 `requirements-lock.txt` 统一为 UTF-8，在全新 Python 3.12 环境验证 Linux 锁文件。NUC 恢复连接后先只读复核当前 release。
2. **批量审批修复**：按 UUID 解析任务；明确部分成功语义；报告缺失、无权限、已处理和失败任务；增加服务端专项和浏览器交互测试。
3. **中文输入法扫码修复**：共享扫码模块处理 composition 生命周期和 Enter/Tab 兜底；新建申请、快速出入库、盘点使用同一事件协议。
4. **ZT610 打印重构**：新增 RAW ZPL 通道并保留 GDI 兼容通道；ZPL 显式定义 60×40 mm 页面和 2×2 内容；修复 worker 重启竞态、配置边界和错误可见性。
5. **权威文档与发布收口**：专项通过后更新状态和变更记录，建立干净 Git 提交，再构建候选包。禁止从当前未提交工作区直接宣称正式版本。当前文档已记录本机软件验证结果，干净提交和候选包仍待执行。
6. **NUC 与现场验收**：数据库和代码快照后部署，执行 PostgreSQL 冒烟；随后完成 DS3678 中文输入法和 ZT610 连续 10 张验收。

当前结果：

- 批量审批、中文输入法扫码、ZPL 渲染和 worker 生命周期已达到 `software_verified`；本机 Django 全量 `447/447`、打印客户端 `11/11` 通过。
- DS3678、ZT610 真机、NUC PostgreSQL 和部署后的健康检查均为 `not_verified`，不得由自动化结果替代。

验收标准：

- 批量批准、批量拒绝均能处理真实 UUID；拒绝必须有原因；并发重复处理不产生重复日志或错误状态。
- 中文输入法开启时连续扫描三个 WMS/AST/SN 编码，三个结果都进入待处理列表；失败条码保留且不阻断第四次扫描。
- ZT610 使用 60×40 mm 间隙纸连续打印 10 张，每张仅走一张物理标签，不多走约 10 mm，不累计漂移；四个 30×20 mm 区域边界正确，四个二维码均可识别。
- 打印客户端反复“保存并启动”不会产生第二个 worker，不会重复领取或重复打印作业。
- NUC 部署前后业务计数一致；服务、迁移、静态文件、健康检查和相关 PostgreSQL 冒烟通过。
- 未执行的真机、服务器或安全测试必须明确标为未执行，不得标记 completed。

### M9 Inventory presentation and test-data governance

Status: local implementation complete; NUC deployment and any data cleanup remain
unapproved/not executed.

- 在库存中心资产台账内部增加“按物品类型 / 单件资产”显示模式；不新增一级导航，也不删除单件台账。
- 类型聚合页展示总资产、当前在库、有效预占、外借/领用、维修/不可用、终态、最低库存和库存状态；详情页进入该类型下的单件资产。
- 第一版历史指标只使用已有库存流水，展示历史动作数和最近动作时间；不得把流水动作数冒充历史时点库存。历史时点库存留待每日快照设计。
- 增加只读测试数据清理候选报告，按明确测试标识和关联关系输出账号、库位、资产、申请、流水、通知和审计影响；不提供默认删除，不按日期或模糊名称删除。
- 账号继续由 admin 创建和分配权限；公开注册、钉钉/飞书/企业微信登录只记录为候选，不在本阶段开放。

Acceptance:

- 聚合页与单件页共用现有筛选权限，不产生 N+1 查询；点击类型可回到带筛选条件的单件台账。
- 每个聚合状态和安全库存边界有正向及空数据测试；历史指标明确标注为流水统计。
- 测试数据报告默认 dry-run、无写操作，输出可审阅的对象和关联计数，不能包含密码、Token 或 Secret。
- M8 批量审批、中文输入法扫码和现有全量测试仍必须通过。

Implementation result:

- 类型聚合视图、只读候选报告和账号策略已实现；M8 本机回归仍通过。
- 任何测试数据删除仍需单独确认，当前只允许使用候选报告审阅，不执行清理。

### M10 Personal profile and identity display

Status: local implementation complete; deployment verification pending.

- 增加登录用户自己的“个人资料”页面，允许维护中文显示姓名；不开放公开注册。
- 顶部用户菜单和头像优先显示中文姓名，未填写时回退到登录账号。
- 钉钉绑定继续由管理员维护并在个人资料页只读展示状态；在完成钉钉 OAuth/可信回调前，不允许用户手工输入 userid 作为自助绑定。
- 不在本阶段收集身份证号、真实身份证明材料或头像照片；中文显示姓名只用于系统展示和单据操作人识别。

Acceptance:

- 登录用户只能查看和修改自己的显示姓名，不能修改用户名、权限、工号或钉钉 userid。
- 姓名为空时所有页面安全回退到用户名；中文姓名在头像、顶部菜单和个人资料页一致显示。
- 非登录用户被重定向到登录页；管理员维护的钉钉绑定状态在个人页正确展示。
- 增加正向、空姓名、越权字段和 XSS 输入测试；不新增数据库迁移。

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
