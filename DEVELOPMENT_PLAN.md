# Development Plan

更新时间：2026-11-17

## Goal

在不破坏 NUC 现有 PostgreSQL 数据的前提下，将系统从“功能齐全但入口和数据入口分散”收敛为可试运行版本。

## Non-goals

- **微信小程序已归档暂停（2026-11-17）**：不再作为交付物，代码移入 `docs/archive/miniprogram-paused-20261117/`。当前唯一客户端是响应式 Web/PWA。恢复前必须重新登记里程碑并评估 PWA 是否已满足现场需求。
- 不清空服务器数据库，不重导 11.2 源表。
- 不新增与当前收口无关的大功能。

## M17 PWA 移动端收口（当前阶段）

Status: in progress. 承接“放弃小程序、专做 PWA”的战略决策。

用户前提：仓管员以 **iPhone 为主**；**离线能力本期不做**。

范围：

- 修复 iPhone PWA 图标与 iOS 专用 meta（`REVIEW_REPORT.md` P2-01）：
  - 生成 180×180 / 192×192 / 512×512 PNG 图标
  - `manifest.webmanifest` 增加 PNG 条目，保留 SVG 作为补充
  - `web_base.html` `<head>` 增加 `apple-touch-icon`、`apple-mobile-web-app-capable`、`apple-mobile-web-app-status-bar-style`、`apple-mobile-web-app-title`
- 移动端扫码入口显性化：评估底部导航常驻扫码按钮的可行性，优先保证快速出入库和盘点页的扫码可达性。
- 快速出入库与盘点页在 760px 以下的可用性验证（按钮尺寸、单列布局、扫码焦点）。

验收：

- iPhone Safari 添加到主屏幕后图标为品牌图、点击无地址栏、状态栏样式正确。
- 快速出入库、盘点、扫码在 iPhone 上可完成核心动作。
- 不影响现有 Web 桌面端与内网访问。

不做：

- 离线队列增强（用户明确本期不做）
- 后台推送（iOS 限制，继续用轮询）
- 小程序恢复
- 不兼容错误的 637 件资产结论；以程序规则和现场核对为准。

## 待规划（已确认需求，尚未排期）

### 项目归档附件

Status: backlog. 用户 2026-11-18 确认需要，暂不排期。

现状与缺口：

- 现有 `AssetAttachment` 只能把文件挂在**单个资产**上（现场照片、维修报告、发票、物流凭证、设备日志、其他资料）。
- `Project` 模型只有编号、名称、客户、备注，**没有任何附件字段**。项目合同、验收单、图纸、方案目前无处存放。
- 设备使用说明书只能逐台挂到资产上，没有按型号共享的文档库。

计划新增（实现前需再确认验收口径）：

- `ProjectAttachment`：项目外键、分类（合同 / 验收单 / 图纸 / 方案 / 其他）、文件、上传人、上传时间。
- 附件下载走受控视图，不暴露 `/media/` 直链（与现有资产附件一致）。
- 型号级共享文档：同一型号的说明书只存一份，挂到 `ItemType` 或 `ProductModelCode`。

### 大文件与长期存储

Status: backlog. 部分已落地（见 M18），长期方案待决策。

- 已落地：单文件上限 20 MB → 100 MB（Nginx `client_max_body_size` 与 Django 校验同步）。
- 待决策：CAD/大图纸是否引入分片上传；是否把 media 迁到独立存储节点。
- 待决策：磁盘扩容方式（原地扩容 vs 增加第二台服务器承担 media/备份）。

## M18 存储与备份加固

Status: done（2026-11-18）。用户决策：备份策略同时采用 A、B、C 三点；上传上限提高到 100 MB。

范围：

- **A（增量）**：media 备份从「每次全量 tar.gz」改为 `rsync --link-dest` 硬链接增量。
  30 份备份只占约 1.5 倍 media 空间，而不是 30 倍。
- **B（解耦）**：数据库每日全量 `pg_dump` 与 media 增量备份分离成两个独立步骤，
  任一失败不影响另一项的产物完整性。
- **C（降频）**：media 增量每日执行但只在有变化时产生新目录；无变化直接跳过，避免空备份堆积。
- 上传上限 20 MB → 100 MB：Nginx `client_max_body_size` 与 `upload_validation.MAX_ATTACHMENT_SIZE` 同步。

验收：备份脚本在 NUC 实测通过，`SHA256SUMS` 与恢复校验均正常；超大文件上传返回明确的 413 提示而不是静默失败。

## M19 Review remediation and release gate hardening

Status: in progress. 用户已于 2026-11-23 明确允许开始修改。P1-01 已完成本地代码实现和 SQLite 自动化验收，现场 PostgreSQL 验收与部署仍未完成。范围来自 `REVIEW_REPORT.md` 第二部分第 19 节，先处理已实证的数据完整性问题，再处理发布安全和低风险体验问题。

### 实施顺序

1. P1-01。将仓库、库位和资产归属删除语义收紧到模型层，覆盖 API、Django admin、服务层和直接删除反向测试。**本地代码和 SQLite 自动化已完成**，真实 PostgreSQL 引用盘点、快照、迁移和现场验收待执行，禁止在 NUC 未快照时执行。
2. P2-13。建立迁移与代码回滚兼容检查，先在隔离数据库验证后再决定是否修改发布脚本。**本地已加入 release 迁移元数据和回滚前只读迁移门禁**，真实 PostgreSQL 回切演练仍待执行。
3. P2-06。把 shell 语法、Bandit、pip-audit 和工具缺失状态纳入发布门禁，保留平台差异说明。**本地脚本已改为缺失工具默认阻断，支持显式豁免参数，服务器 pip-audit 缺失或发现漏洞默认阻断。**
4. P2-09。改造 `--import-sqlite` 临时文件生命周期，覆盖失败、终止和重复执行清理。**本地已改为私有临时目录、600 文件权限和失败清理，真实首次部署仍待演练。**
5. P2-04。基于 PostgreSQL 容量基线决定查询和索引改造，不先凭 SQLite 结果改业务口径。
6. P2-10、P2-14、P2-15、P2-16。分别在业务确认或专项设计后实施媒体授权、CSP、上传文案和通知字段调整。

### P1-01 验收

- API、admin 单条删除、admin 批量删除、服务层和直接 ORM 路径均不能删除仍被业务历史引用的仓库或库位。
- 空仓库和空库位仍可按受控权限删除，删除审计保存快照且不产生孤儿记录。
- Django 全量测试、迁移漂移、专项权限测试和 PostgreSQL 隔离迁移验证通过。
- 不触碰 NUC 生产数据库，部署前必须有 PostgreSQL 快照。

### 本阶段规则

- 本阶段不恢复微信小程序，不扩展无关业务功能。
- 代码实现、自动化通过、现场验收和部署分别记录。
- P2-10 和 P2-16 在业务口径确认前不改变外部可见行为。

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

### M8 post-review P1 remediation

Status: local implementation and automated verification complete; candidate packaging, NUC deployment and field acceptance pending.

- 申请明细 API 将所属申请单设为服务端绑定字段；主单更新携带 `lines` 时明确拒绝，避免未实现的嵌套更新造成 500 或静默不一致。
- V2/V3 全量基准导入在任何业务写入前检查缺失对象的活动状态、库存预占、维修记录、个人耗材持有余额和未归还借用；发现阻断关系时整批回滚且批次保持待复核。

Automated acceptance: 专项 `27/27` 通过；Django 全量 `536/536` 通过，跳过 8 项 PostgreSQL 专项；`check`、迁移漂移、`pip check`、`git diff --check` 通过。

Field acceptance: 未执行。部署前仍需建立可追溯候选 release、做 PostgreSQL 快照，并在 NUC 完成导入保护和申请 API 冒烟；不得清空或覆盖服务器数据库。

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

Status: local implementation in progress; focused verification passed, latest full regression interrupted, deployment pending.

- 增加登录用户自己的“个人资料”页面，允许维护中文显示姓名；不开放公开注册。
- 顶部用户菜单和头像优先显示中文姓名，未填写时回退到登录账号。
- 钉钉绑定继续由管理员维护并在个人资料页只读展示状态；在完成钉钉 OAuth/可信回调前，不允许用户手工输入 userid 作为自助绑定。
- 不在本阶段收集身份证号、真实身份证明材料或头像照片；中文显示姓名只用于系统展示和单据操作人识别。

Acceptance:

- 登录用户只能查看和修改自己的显示姓名，不能修改用户名、权限、工号或钉钉 userid。
- 姓名为空时所有页面安全回退到用户名；中文姓名在头像、顶部菜单和个人资料页一致显示。
- 非登录用户被重定向到登录页；管理员维护的钉钉绑定状态在个人页正确展示。
- 增加正向、空姓名、越权字段和 XSS 输入测试；不新增数据库迁移。

### M11 Inventory view state and local DingTalk launcher

Status: implemented, automated regression passed, deployed to NUC; field browser/device acceptance pending.

### M14 Account governance, approval duty separation and person display

Status: implemented, automated regression passed, deployed to NUC; field acceptance pending.

- 人员显示统一为「中文姓名（登录账号）」，覆盖申请、采购、审批、盘点、转换、导入、台账流水、审计日志、资产生命周期等全部单据。`username` 仍是唯一真相且不可变，改名不影响历史归属；无中文姓名只显示账号，账号已删除显示"已删除账号"。
- 权限页重排：11 项权限改为可换行胶囊并在勾选时即时变色；角色列只显示权限项数量，不再拼接全部权限名，消除"左侧权限名拉长导致整行变形"。
- 管理员可为账号重置密码：生成 12 位临时密码（`secrets`，去除 0/O/1/l/I），立即作废该账号全部登录会话，写入 `account_password_reset` 审计；超级管理员密码仍只能走服务器运维。
- 登录页新增「申请开通账号」：只收集姓名/工号/部门/电话/说明，由管理员在权限页待办列表中建号或驳回。**不开放自助注册**，提交申请不会创建任何账号。
- 审批候选修正：指定审批人下拉只列真正具备审批权限的账号，并排除申请人自己；通知范围从"全部仓库操作员"收窄为"具备审批权限的账号"，消除录入/报表/财务账号的待办噪音。
- 职责分离：存在其他可用审批人时禁止审批自己的申请；系统中只剩一位审批人时放行，但写 `request_self_approved` 审计并向超管发提醒。
- 接收人改为选人：默认从系统账号中选择并按账号派生姓名；勾选"接收人不在系统内"后切换为手工填写，并在单据上打 `is_external_recipient` 标记。兼容直接提交姓名的旧调用。

Acceptance:

- 所有单据上的人员显示为「姓名（账号）」；改名后历史单据仍指向同一账号。
- 权限页在 11 项权限全选时行高稳定、列宽不溢出。
- 重置密码后旧密码失效、旧会话被作废；超级管理员不可在此重置。
- 登录页提交申请不产生账号；重复工号不重复建待办；管理员可驳回。
- 有其他审批人时自批被拒绝并给出明确原因；唯一审批人自批会产生审计与超管提醒。
- 接收人可选系统账号或标记外部人员；两者都不填时提交被拒绝。
- 相关专项、`manage.py check`、迁移漂移检查、全量 Django 测试和 `git diff --check` 通过。

### M8 WeChat mini program HTTPS experience release

Status: in progress. 本里程碑只交付移动现场操作体验版，不改变服务器业务数据，不新增小程序专属后端业务规则。

- 将小程序 API 基地址切换为 `https://cloudwarehouse.dpdns.org`，使用用户提供的真实 AppID `wx02c8193db5952f39`。
- 开启开发者工具的合法域名校验；保留开发工具“预览/体验版”所需的登录、资产查询/扫码、详情、照片上传、借用/领用/归还申请、审批执行和通知能力。
- 保持耗材、多资产申请、采购、盘点、维修、调拨、报表和基础资料维护在 Web 端，不在本里程碑临时复制。
- 先完成后端全量回归、域名 HTTPS 健康检查、接口静态契约核对，再构建小程序体验版；不直接宣称正式发布。

Acceptance:

- 本机后端 `check`、迁移漂移检查、依赖检查、JavaScript 语法检查和 Django 全量测试通过；未通过时不得上传小程序。
- 手机 4G 访问 `https://cloudwarehouse.dpdns.org/health/` 返回数据库正常；开发者工具开启 URL 校验后能完成登录请求。
- 体验版真机回归覆盖：登录、资产搜索、相机扫码、详情、照片上传、借用/领用/归还申请、审批/执行、通知查看；每项记录实际结果。
- 小程序配置不含 `127.0.0.1`、`localhost`、`touristappid` 或明文密钥；服务器部署仅更新代码，保留 PostgreSQL 数据。
- 未实现或未通过的功能必须在 `PROJECT_STATUS.md` 标明，不得用静态检查替代真机验收。

Current result: 用户已完成微信开发者工具真机预览和体验版上传；手机端完整业务链路回归由后续实际使用者执行，当前保持待验收状态。admin 移动端功能暂维持现场端范围，不在本里程碑扩展为完整后台。

Implementation result:

- 新增 `apps/common/templatetags/person.py`（`person_label` 过滤器 + `person` 标签）；14 个模板改用该标签。
- 新增 `apps/accounts/models.py::AccountRequest` 与迁移 `0005_accountrequest`；`apps/common/auth_views.py` 增加 `AccountRequestForm` 与 `account_request_create`；权限页展示待办并支持标记已建号/驳回。
- 新增 `apps/workflow/models.py::WorkflowRequest.recipient` 与 `is_external_recipient`，迁移 `0020_workflowrequest_recipient_and_external_flag`。
- `apps/workflow/services.py`：新增 `_approver_users()` 与 `_person_display()`；`approve_request` 增加职责分离判断与自批审计。
- `apps/common/utils.py`：新增 `generate_temporary_password()`。
- 新增测试：`test_account_request.py`（11 例）、`workflow/test_approval_separation.py`（6 例）、`PersonLabelTests`（5 例已在 `test_account_admin_edit.py`）。
- 全量 `488/488` 通过，跳过 8 个 PostgreSQL 专项；两个迁移均为纯新增（可空字段 + 新表），无破坏性操作。

- 资产台账的“按物品类型汇总”和“按单件资产”保留为两个独立显示视图。类型汇总页的筛选状态属于汇总页；从某类型钻取到单件页时，类型条件只作用于单件页，返回汇总页时不携带该钻取条件。
- 不改变现有筛选业务口径，不新增数据库字段；通过显式的钻取上下文参数生成返回链接，避免把单件页的 `item_type` 污染到类型汇总页。
- 个人资料页增加“尝试打开钉钉客户端”入口。该入口只负责通过 `dingtalk://dingtalkclient/` 协议唤起本机客户端并给出未安装提示，不把客户端已打开或已登录推断为系统身份绑定。
- 钉钉 userid、中文实名和账号绑定仍由现有管理员流程维护；可信 OAuth/回调绑定不在本次实现范围。

Acceptance:

- 在类型汇总页筛选后进入任意类型的单件资产页，返回类型汇总时保留汇总筛选但清除钻取产生的类型条件；直接在汇总页选择物品类型筛选时，条件仍按原逻辑生效。
- 单件资产页和类型汇总页的切换链接不会丢失用户明确设置的搜索、状态、仓库、库位等汇总条件。
- 个人资料页点击打开钉钉时调用本机协议；未安装或协议不可用时显示清晰提示；页面不显示“已绑定”或修改绑定成功等误导性结果。
- 相关 Django 页面测试、模板静态检查、`manage.py check`、全量 Django 测试和 `git diff --check` 通过。

Implementation result:

- 已增加显式 `from_type=1` 钻取上下文；从类型汇总进入单件资产后，返回类型汇总会移除钻取产生的 `item_type`，但保留用户明确设置的搜索、状态、仓库和库位条件。
- 类型汇总与单件资产的切换链接和“清空条件”链接保持当前视图，不再因清空或返回意外切换到另一种视图。
- 资产台账的“更多筛选”不再根据 URL 条件自动展开，只有用户主动点击摘要才展开；快捷跳转和带筛选结果的返回页均保持收起。
- 个人资料页已增加通过 `dingtalk://dingtalkclient/` 尝试唤起本机钉钉的入口；没有新增绑定接口、数据库迁移或自动绑定判断。
- 本机相关专项 `7/7` 通过，系统检查、迁移漂移检查和差异格式检查通过。
- 2026-11-16 接力补齐：使用绝对可写临时目录完成 Django 全量 `452/452` 通过（跳过 8 个 PostgreSQL 专项），`manage.py check`、`makemigrations --check --dry-run`（`No changes detected`）、`git diff --check` 一并通过。
- 2026-11-16 已部署：release `20261116-m11-profile-filter-dirty-b8edaa14defb`（source `1901c85`，dirty）。部署前 PostgreSQL 快照为 `pre-deploy-20261116_141756`，迁移无待执行，Gunicorn/Nginx/PostgreSQL 均 active，`/health/` 正常，部署前后业务计数完全一致（资产 636、耗材 31、批次 2、申请 0、流水 0、账号 5、审计 1055），回滚列表保留 5 个可切换 release。
- 仍需现场验收：真实浏览器中的类型汇总钻取/返回/清空条件与“更多筛选”手动展开；安装钉钉的 Windows 机器上的协议唤起。自动化测试只覆盖服务端渲染，不能替代这两项。

### M12 Account profile read-only clarity and warehouse filter reset

Status: implemented locally; automated regression passed; deployment pending.

- 权限页补齐账号档案维护：管理员可在「已有账号」行内直接修改员工工号与所属部门（此前创建后无入口，只能去 `/admin/` 或改库）。工号做非空与全表唯一校验；变更写入 `OperationAuditLog`（action `account_profile_updated`，含变更前后快照）。
- 单据人员显示统一为「中文姓名（登录账号）」，例如「张三（zhangsan）」。`username` 仍是唯一真相且不可变；中文姓名仅用于展示，改名不会让历史单据"换人"，括号内账号保证可追溯。无中文姓名时只显示账号，账号已删除时显示"已删除账号"。

- 资产台账搜索框右侧新增「清空」：一次性清除搜索词与全部筛选条件（四个状态维度、仓库、库位、物品类型、排序），保留当前标签页、当前视图和每页条数。无筛选条件时按钮为禁用态，避免布局跳动。
- 移除「更多筛选」面板内重复的“清空条件”入口，清空动作只保留一个位置。
- 修正统计卡片静默丢弃筛选的问题：卡片标签为“当前筛选资产/耗材配件”，点击后应保持当前筛选切换标签页，此前会清空全部条件。
- 个人资料页改为信息展示：登录账号、员工工号、所属部门、钉钉绑定状态以只读列表呈现，不再使用看起来可编辑的只读输入框；页面仅保留中文姓名为可编辑项，并去掉冗长说明段落。

Acceptance:

- 任意筛选组合下点「清空」回到当前视图的初始列表，`tab`、`view`、`page_size` 不变；无筛选时按钮禁用。
- 清除条件后不跳转到另一视图；耗材标签页下清空不携带资产视图参数。
- 统计卡片点击后筛选条件不丢失。
- 个人资料页不出现可提交的账号/工号/部门/钉钉字段；伪造这些字段的 POST 不改变任何数据。
- 相关专项、`manage.py check`、迁移漂移检查、全量 Django 测试和 `git diff --check` 通过。

Implementation result:

- `warehouse_management.html`：搜索框右侧新增清空按钮（禁用态复用 `.button-secondary.is-disabled`）；面板内重复清空入口移除；统计卡片改用 `asset_filter_query`/`stock_filter_query` 保留筛选。
- `views.py`：新增 `asset_reset_filter_query` 与 `has_active_filters`；判断是否“有筛选”时排除 `page_size`/`sort`/`dir`，否则每页条数会让按钮永远可点。
- `account_profile.html`：只读项改用既有 `.detail-list` 组件展示；移除无法在 PC 端兑现的 `dingtalk://` 按钮与大段说明，仅保留“留空时显示登录账号”一行提示。
- `permission_management.html`：新增「工号与部门」列，行内可编辑员工工号与所属部门；账号列同时显示中文姓名。
- `apps/common/templatetags/person.py`：新增 `person_label` 过滤器与 `person` 标签，统一「中文姓名（登录账号）」格式；`asset_lookup.html`、`purchase_draft_detail.html`、`purchase_draft_list.html`、`asset_lifecycle.html` 已改用该标签。
- 新增 `test_account_admin_edit.py`（7 例：改工号/部门、重复工号拒绝、保留原工号、空工号拒绝、页面渲染输入框）与 `PersonLabelTests`（5 例）。
- 全量 `471/471` 通过，跳过 8 个 PostgreSQL 专项。
- 新增 `WarehouseResetFilterTests` 7 例与个人资料 2 例只读断言；专项 16/16 通过。

### M13 DingTalk identity login (future, blocked)

Status: planned only; not implemented. Blocked on prerequisites below.

- 目标：账号由钉钉身份直接带入实名、工号、部门，减少管理员手工维护。
- 前置条件（全部满足才可启动）：① 钉钉开放平台企业内应用并配置可信回调；② 一个钉钉服务器可访问的 HTTPS 地址；③ 自动建号的准入策略（限定企业通讯录，禁止公开注册）；④ 权限仍由管理员授予，钉钉只解决身份不解决授权。
- 建议分三步独立验收：通讯录只读同步（回填工号/部门/姓名，不需要回调地址）→ 钉钉扫码登录 → 自动建号与权限初始分配。
- 当前访问入口为 `http://192.168.23.41`，不满足回调条件；`PROJECT_STATUS.md` 已将“正式域名/HTTPS”和“可信钉钉 OAuth”同时列为暂停项。

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
