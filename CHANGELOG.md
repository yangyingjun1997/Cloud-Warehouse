# Changelog

本文件只记录已经实施并验证的变更。计划和候选项写入 `PROJECT_STATUS.md` 与 `DEVELOPMENT_PLAN.md`。

## Unreleased

### 2026-11-23 上传前安全审计与仓库清理

- 审计发现两个 `rehearsal-check-*/rehearsal.sqlite3` 被误提交（各约 2MB，
  82 张空表结构、0 行业务数据），来源是迁移演练验证脚本用
  `tempfile.mkdtemp()` 生成后落在仓库根目录。已 `git rm --cached` 并从工作区删除。
- `.gitignore` 重写：原文件中文注释乱码、规则有缺口。补齐 `*.sqlite3`、
  `*.log`、`rehearsal-check-*/`、`.test-run-temp.*`、`app.env` 等规则。
- 逐项核实「硬编码密码/密钥」告警：199 处为测试夹具口令（`Pw#2026aa` 等），
  2 处为 README 占位符（`YourQaPassword123`），5 处非测试非文档的均为
  运行时动态生成（`openssl rand`、`guid`）或中文占位文本。**无真实凭据入库。**
- 内网 IP `192.168.123.101` 共 32 处，均为文档说明、部署示例和内部工具默认值，
  属 192.168 私有地址段，不构成泄露。
- 已跟踪文件无 `.env`（仅两个 `.env.example`）、无数据库、无备份、无日志，
  总计 7.8 MB，无超过 5MB 的大文件。

### 2026-11-23 离线队列敏感字段不落盘（R10-P2-04）

- 离线队列原先把整张表单原样存进 IndexedDB，含接收人姓名、手机号、
  外部持有人、申请原因与备注。IndexedDB 明文可读，条目保留 7 天，
  共用电脑或设备送修时可被翻出。涉及快速出入库、新建申请、盘点三个页面。
- `offline.js` 新增 `SENSITIVE_FIELD_PATTERNS` 与字段分流：敏感字段不入队，
  只记录 `omittedFields` 名字；含敏感字段的记录强制关闭自动同步
  （缺字段提交必然失败，还可能产生半截单据）。
- 用户提示改为「为避免在本机留存接收人姓名、电话等信息，这部分内容未保存」。
- 新增 9 例测试锁住该约定。

### 2026-11-23 check-all 三处误报修复

服务器实跑 `check-all.sh --fast` 时发现它把环境问题报成代码问题：

- **Python 选错解释器**：脚本只找 `.venv/`，而服务器 release 里是 `venv/`（无点），
  回退到系统 Python 3.10（项目要求 3.11+），导致 `makemigrations` 与 `pip check`
  全部失败并被误报成「有模型改动未生成迁移」「存在依赖冲突」。
  改为依次探测 `.venv/`、`venv/`，并新增版本检查，低于 3.11 明确告警。
- **Bandit 误判阻断**：bandit 只要有任意 finding（含 Low）就返回非零，
  实际 0 High / 2 Medium / 302 Low 被报成「发现阻断项」。
  改用 JSON 输出按严重级别判定，只把 HIGH 当阻断。
- **pip-audit 审计对象错误**：原命令审当前 venv（独立工具环境里审的是工具自身依赖），
  改为审 `requirements-linux.txt`，即服务器实际安装的版本集合。

另新增「前端 JS 语法」检查（`node --check`），防止 JS 错误让离线队列、
事件委托在浏览器里静默失效。

### 2026-11-23 依赖锁一致性校验

- 新增 `backend/scripts/check_requirements_consistency.py`：校验
  `requirements.txt` 的直接依赖被 lock 或 linux 文件覆盖、linux 文件必须
  引用 lock、服务器专用包必须固定版本；`--venv` 模式额外比对已装版本。
- 接入 `check-all.sh`。首次运行即发现 `gunicorn` 在 `requirements.txt` 声明
  但未在任何固定版本中覆盖——服务器必需的包缺固定版本。
- 新增 9 例测试，含两个反向用例证明校验器能真正检出漂移。

### 2026-11-23 迁移回滚演练（R10-P2-13）

- 新增 `rehearsal_migrate_rollback` 管理命令：在独立 PostgreSQL 库上逐个
  「回退一步再重新应用」，找出不可回退的迁移。库名必须以 `_rehearsal` 结尾，
  与生产库同名或前缀不符直接拒绝；演练库由命令自行创建与清理。
- 生产演练（95 个迁移）结论：**不能把 `migrate` 回退当生产回切手段**。
  两类失败：同进程连续反向迁移触发 PostgreSQL `pending trigger events`；
  早期迁移把可空字段改回非空时已有 NULL 数据阻止回退。
  正确流程是「恢复发布前快照 → 切回旧 release → 验证」。
  结论已写入 `PROGRAM_STANDARDS.md`。
- 演练全程未影响生产库：迁移数、资产数、表数在演练前后一致。

### 2026-11-23 资产持有人批量补录

- 线上 636 件资产中 261 件状态为「已借出/已领用/内部借用」，
  但 `current_holder` 与 `external_holder_name` 全为空——导入时只填状态未填持有人。
- 新增 `apps/inventory/holder_import.py` 与 `import_asset_holders` 命令：
  按 `system_asset_no` 匹配补录持有人，只改持有人与部门，
  不碰状态/仓库/库位/金额；姓名匹配要求全库唯一，有歧义拒绝；
  每行独立事务、逐行审计、支持 `--dry-run`。
- 新增 `apps/inventory/borrow_health.py` 识别「借出但无持有人」的孤儿资产，
  接入催办命令输出与工作台提示条。
- 新增 36 例测试。

### 2026-11-23 资产质保到期提醒

- 636 件资产中 29 件带质保日，此前没有任何「即将过保」入口。
- 新增 `/warehouse/warranty/` 页面、`warranty_services.py` 与工作台指标卡。
  窗口可选 30/60/90/180/365 天，默认 90 天（实测最早到期在 321 天后，
  原 60 天窗口永远为空）。排除报废/售出/退供应商/停用资产。
- 新增 15 例测试。

### 2026-11-23 权限组 13 → 4 角色聚合

- 13 个细粒度权限组对 4 个账号的系统是纯负担（一个仓库员要勾 5-8 个组）。
- 保留细粒度组作为「能力标签」，新增 4 个角色组聚合：
  `warehouse_role_viewer`（报表+财务）、`warehouse_role_editor`（录入类 6 项）、
  `warehouse_role_operator`（出入库+台账校正）、`warehouse_role_approver`（审批+账号开通）。
- 谓词函数改为「角色组 或 原能力组」任一命中即通过，权限语义不变，
  老账号零迁移，角色之间不越权。
- `warehouse_staff` 不再分配（线上 0 人），保留常量兼容历史账号。
- 新增 16 例测试，其中包含「持有角色组 == 持有其聚合的全部能力组」的等价性断言。

### 2026-11-23 采购走 ERP（隐藏采购申请入口）

- 用户决策：采购走 ERP，系统只做实物到货后的一次入库。
- 按 B1 方案只改导航与文案，不动模型与服务：主导航「采购与报表」落地页
  改为采购订单列表，隐藏「采购申请」标签页，页面内 4 处申请入口
  改为入库语境文案。保留 URL 兼容旧书签。
- 保留到货登记（`PurchaseReceipt`）作为入库通道——它支持部分到货、
  逐件录 SN/资产编码、幂等键防重放，并生成正规入库单与库存流水。
- 保留 3 个供应商报表（价格历史、对账、到货汇总）。

### 2026-11-23 CSP 去掉 unsafe-inline（nonce 方案）

- 过去 `script-src`/`style-src` 都带 `'unsafe-inline'`，等于拆掉 XSS 防线的一大半。
- `SecurityHeadersMiddleware` 每请求用 `secrets.token_urlsafe` 生成 nonce，
  写进 CSP 头；context processor 把 nonce 传给模板。
- 57 个模板的内联 `<script>`/`<style>` 全部加 `nonce="{{ csp_nonce }}"`。
- 35 处内联事件属性（onclick/onsubmit/onchange）改为声明式 data 属性，
  新增 `ui-actions.js` 用捕获阶段事件委托接管，**破坏性操作的二次确认全部保留**。
- 教训：首次改造只扫描 `templates/*.html`，漏掉 `registration/login_base.html`
  等子目录，导致登录页样式被 CSP 拦成白板。测试已改为递归扫描全部 57 个模板
  并断言数量下限。

### 2026-11-23 通知脱敏与上传文案对齐

- 账号申请通知原先把「联系电话」和「申请说明」原样拼进钉钉消息。
  手机号属个人敏感信息，申请人填写时未预期被转发到第三方 IM。
  改为：站内通知保留完整信息（需登录且有审批权限），钉钉只发概要。
- `asset_lifecycle.html` 写「单个文件最大 20 MB」，实际图片 10MB、附件 100MB；
  `lifecycle_services` 又单独对图片放宽到 20MB。统一为图片 ≤10MB、附件 ≤100MB。

### 2026-11-23 部署脚本修复

- `install.sh` 原先只 rsync `backend/`，release 目录里没有 `deploy/`，
  导致部署后无法用本版本自己的安全检查与回滚脚本复核。已同步 `deploy/`。
- 修复 `cloudflared-selfheal` 告警策略时踩坑：第一次只改服务器上
  `/usr/local/sbin/` 的脚本，下次部署被 `install.sh` 从
  `deploy/ubuntu22/cloudflared-selfheal.sh` 重新安装覆盖，改动等于没做。
  已改为修改仓库源文件并加 8 例测试锁住。

### 2026-11-23 M19 P1-01 主数据删除保护

- 将 `Location.warehouse`、资产和耗材的仓库/库位归属，以及申请、库存流水、盘点扫描和盘点调整中的仓库/库位历史引用统一改为 `PROTECT`，直接 ORM 删除不能再静默删除库位或清空资产归属。
- 改进通用 Django admin 删除流程。在同一事务写入删除前完整字段快照并保留真实对象 ID，受保护删除回滚且不产生孤儿审计，批量删除遇到保护错误时整体回滚。API 原有业务引用提示继续保留。
- 新增主数据删除专项覆盖 API、admin 单删、admin 批量删、直接 ORM、历史引用和删除审计。专项 `11/11`，Django 全量 `807/807` 通过，8 项 PostgreSQL 专项跳过，系统检查、迁移漂移和差异格式检查通过。
- 现场 PostgreSQL 迁移、NUC 快照、部署、健康检查和真实数据回归尚未执行。首次全量测试因 Windows 默认临时目录 ACL 失败，改用工作区受控临时目录后通过。

### 2026-11-23 M19 P2-13 回滚 schema 兼容门禁

- 发布安装在迁移完成后记录 `database_migration_check` 和迁移状态摘要到 `release-info`，迁移状态读取失败或仍有未应用迁移时停止发布。
- 回滚前要求目标 release 具备迁移元数据，并用目标 release 的 Django 代码执行只读 `showmigrations --plan`。存在未应用迁移时拒绝切换，缺少历史元数据时默认拒绝，可由完成人工兼容确认的运维显式使用 `--allow-unknown-schema`。
- 新增部署安全静态测试，部署脚本 `bash -n` 通过。真实 PostgreSQL 数据迁移、代码回切和恢复演练尚未执行。

### 2026-11-23 M19 P2-06 发布安全门禁

- `check-all.sh` 纳入全部 Ubuntu 部署脚本 `bash -n`、Bandit SAST 和 pip-audit 依赖审计。Bandit/pip-audit 缺失时默认失败，只有显式传入 `--allow-missing-security-tools` 才记录平台豁免。
- `security-check.sh` 对 pip-audit 缺失或发现漏洞改为阻断，支持通过 `ALLOW_MISSING_SECURITY_TOOLS=1` 记录人工豁免。测试临时目录改为唯一目录并由退出 trap 清理。
- 部署安全专项 `14/14` 通过，两个脚本 Bash 语法检查通过。当前本机通过显式豁免运行 check-all 时，Django 和依赖检查受 WSL/Python 环境差异影响失败，该结果不作为发布通过证据。

### 2026-11-23 M19 P2-09 SQLite 一次性导入临时文件

- `install.sh --import-sqlite` 改用 `$DATA_DIR/sqlite-import.XXXXXX` 私有临时目录，目录权限 700，导出文件权限 600，不再使用固定 `/tmp/after-sales-warehouse-initial.json`。
- 导出失败、loaddata 失败或候选发布失败时统一清理临时目录，导入成功后立即删除。新增部署安全专项覆盖固定路径、权限、umask 和失败清理。
- 真实首次部署、PostgreSQL 导入和中断恢复仍未执行。

### 2026-11-23 第一轮全景审查

- 新增 `REVIEW_REPORT_1.md`，记录当前提交基线、工作区和 NUC release 三态、模块边界、核心调用链、数据流、部署拓扑、测试证据、P0/P1/P2 问题、六角色评审和可执行改进顺序。
- 本轮只读复核在本机完成 Django 全量 `797/797`、8 项 SQLite 不适用专项跳过、系统检查、迁移漂移、`pip check`、JavaScript 语法和本机 HTTP 冒烟。未连接 NUC，未修改业务代码、生产数据库或生产配置。
- 记录未关闭风险：主数据删除模型层约束、报表全量物化、发布脚本安全门禁、开发配置误用和本机 SQLite 未应用 19 个迁移。Bandit、pip-audit、PostgreSQL、真实浏览器和设备验收本轮未执行。

### 2026-11-23 第二轮删除与容量复核

- 只读确认 `WarehouseAdmin` 和 `LocationAdmin` 继承通用删除逻辑，模型仍使用 `CASCADE/SET_NULL`。API 的删除保护不能覆盖 admin 和直接 ORM 路径，P1 风险继续保留。
- 定位供应商价格、供应商对账、呆滞库存和报表总览的全量物化点，补充 P2-04 容量风险证据。当前没有 PostgreSQL 大数据量实测，不记录性能通过结论。
- 删除和部署专项测试共 `16/16` 通过。未修改业务代码、未连接 NUC、未执行迁移或部署。

### 2026-11-23 第三轮 admin 与索引复核

- 使用临时 Django 测试数据库直接调用注册的 `WarehouseAdmin.delete_model()`，探针 `1/1` 复现了在用仓库删除、库位级联删除、资产仓库/库位置空和删除审计写入。测试文件执行后已删除。
- 只读清点确认 `PurchaseOrderLine`、`PurchaseReceiptLine` 无自定义索引，供应商价格与对账查询存在跨表连接和排序。PostgreSQL 计划与容量压测仍未执行。
- 未修改业务代码、未连接 NUC、未执行迁移或部署。P1-01 由源码推断升级为临时数据库已实证。

### 2026-11-23 第四轮发布与迁移复核

- 只读确认本机 SQLite 有 19 个未应用迁移，迁移目录包含 18 个 `RunPython` 数据迁移，部分逆向为空操作。未执行迁移。
- 确认 `check-all.sh` 未纳入 shell/Bandit/pip-audit 严格门禁，`security-check.sh` 对 pip-audit 只告警；P2-06 证据升级。
- 确认 `install.sh --import-sqlite` 使用固定 `/tmp/after-sales-warehouse-initial.json`，P2-09 证据升级；回滚脚本明确只回滚代码，不回滚数据库，新增 P2-13。
- 未修改业务代码、未连接 NUC、未执行迁移、构建或部署。

### 2026-11-23 第五轮空库迁移演练

- 在隔离临时 SQLite 数据库中执行完整迁移链和 `manage.py check`，迁移成功且系统检查无问题。现有 `runtime-data/db.sqlite3` 未修改。
- 临时探针第一次因 Django 连接仍打开而清理失败，进程退出后已删除数据库和探针文件。该结果只证明空库迁移可执行，不代表 PostgreSQL 真实数据升级或回滚通过。
- 未修改业务代码、未连接 NUC、未执行 PostgreSQL 迁移或部署。

### 2026-11-23 第六轮 PostgreSQL 验收准备

- 本机未发现 PostgreSQL 客户端、容器运行时或 `pg_isready`，PostgreSQL 专项 `8/8` 明确跳过。
- 静态复核 `run-concurrency-tests.sh` 的 PostgreSQL 限制、隔离测试库、临时 `CREATEDB` 权限和退出清理逻辑。
- 在 `REVIEW_REPORT_1.md` 建立 NUC/PostgreSQL 迁移、并发、业务冒烟、恢复、回切和健康检查验收矩阵。未连接 NUC、未执行部署或生产数据库写入。

### 2026-11-23 第七轮鉴权与响应安全复核

- 静态对照页面路由、视图装饰器和 API 权限，未发现新的未鉴权业务页面。健康检查、Service Worker 和账号申请保持公开入口。
- 缓存头、CSP、Permissions Policy、Service Worker、扫码脚本更新和离线清理专项 `10/10` 通过。
- 新增 P2-14：CSP 仍允许 `unsafe-inline`，后续需迁移内联脚本/样式或采用 nonce/hash。未修改业务代码、未连接 NUC、未部署。

### 2026-11-23 第八轮媒体与上传边界复核

- 复核归档附件的 Nginx 拦截、Django 下载权限和上传校验。专项在可写临时目录重跑为 `63/63` 通过。
- P2-10 证据升级：普通 `/media/` 直接映射，资产照片 URL 与归档附件的授权边界不同，是否收紧待业务确认。
- 新增 P2-15：资产生命周期页面仍显示单文件 20 MB，后端和 Nginx 实际限制已为 100 MB。
- 未修改业务代码、未连接 NUC、未访问生产媒体或部署。

### 2026-11-23 第九轮敏感信息与通知复核

- 复核令牌、认证审计、DingTalk outbox 和异常日志，未发现明文密码、Token、Secret 或完整 Webhook 写入路径。
- 新增待业务确认 P2-16：账号申请通知将姓名、完整手机号和申请说明同时写入站内通知与 DingTalk outbox，个人通知失败时可能回退群通知。
- 未修改业务代码、未发送第三方通知、未连接 NUC 或部署。

### 2026-11-23 第十轮问题登记收口

- 汇总并去重当前有效问题，形成 P1-01、P2-04、P2-06、P2-09、P2-10、P2-13、P2-14、P2-15、P2-16 的统一登记和修复顺序。
- 确认当前没有新增可证实 P0。未修改业务代码、未连接 NUC、未部署。后续等待用户确认“开始修复”或恢复现场连接后执行 PostgreSQL/设备验收。

### 2026-11-20 第十二轮复盘：4 个 P1 与 3 个 P2 修复

> 全部改动仅本地代码 + 自动化验证，**未部署、未连接生产库**。
> 代码实现与自动化通过为已完成；现场验收与部署为未完成。

- **P1-01 成本导出越权（已修复）**。`transaction_export()` 表头按财务权限分支，
  数据行却无条件写 `item.cost_amount`，导致无财务权限的报表岗仍能读到每行成本，
  且列数错位（成本被下游当「项目编号」解析）。修复：表头与数据行共用同一
  `show_financials` 开关。原测试只断言表头不含「库存成本价值」且未造数据，
  因此从未覆盖数据行；新增 `TransactionExportCostLeakTests` 3 例，显式断言
  数值不出现、列数与表头一致、财务账号仍能看到成本。已在回退源码上验证
  新用例确实失败（2 例 FAIL）。

- **P1-02 删除主数据致资产静默脱仓（已修复）**。`WarehouseViewSet` /
  `LocationViewSet` 硬删时，`Asset.warehouse/location` 是 `SET_NULL`、
  `Location.warehouse` 是 `CASCADE`，删仓库会让资产凭空失去归属、库位被物理删除，
  且不写任何资产履历（探针实测 204 + warehouse_id/location_id 变 NULL + 库位消失）。
  修复：`AuditedModelViewSet` 新增 `_assert_deletable()` 钩子，两个 ViewSet 覆盖它，
  在仍有资产/耗材/库位引用时返回 400 并提示改用「停用」。
  **模型层 `CASCADE→PROTECT` 迁移尚未做**，见 `REVIEW_REPORT.md` P1-02 修复建议。

- **P1-03 采购到货无幂等键（已修复）**。`receive_purchase_order` 只校验订单状态，
  部分到货时订单仍是 `PARTIAL_RECEIVED`，同参数重放会二次入库（探针实测 12 → 14，
  2 张到货单、2 条流水）。修复：`PurchaseReceipt` 新增 `client_operation_id`
  （迁移 `0022`，`(order, client_operation_id)` 条件唯一约束），服务层在持有订单行锁后
  先查幂等键命中并返回既有回执；到货表单新增隐藏令牌（每次渲染生成 UUID），
  视图中损坏令牌直接报业务错误而不是退化为无键。新增专项 4 例。

- **P1-04 恢复演练脚本失效（已修复）**。`backup.sh` 已改为 `media-snapshots`
  硬链接增量，不再生成 `media.tar.gz`，但 `restore-drill.sh` 仍要求该文件，
  演练在恢复数据库前必然 exit 2——灾难恢复能力实际为零验证。同时 `SHA256SUMS`
  的媒体路径被改写成 `<stamp>/media/...`，与真实快照路径不一致，整份
  `sha256sum --check` 也会失败。修复：改为只校验 `database.dump` 那一行、
  从清单注释定位媒体快照并抽样可读性、恢复失败 `trap` 清理半成品临时库、
  增加资产/审计行数读取校验。新增 `RestoreDrillFormatTests` 6 例静态防回退；
  23 个部署脚本 `bash -n` 全部通过。**脚本未在真实 Linux+PostgreSQL 上实跑**。

- **P2-01 资产履历 API 越权（已修复）**。`assets/{id}/lifecycle/` 与 list/retrieve
  共用 `IsAuthenticated`，任意登录员工可读完整履历（探针实测 200，响应含内部
  维修备注与手机号），与页面侧 `is_warehouse_operator` 判定不一致。修复：
  该 action 单独使用 `IsWarehouseOperator`。新增 3 例（员工 403、匿名拒绝、仓库角色 200）。

- **P2-02 停用仓库/库位仍可写入（已修复）**。服务层只校验「库位属于仓库」，
  `is_active` 只在表单 queryset 过滤。修复：新增 `_assert_active_location()`，
  仅对**新写入**生效（`submit_request`、`quick_process_request`、`receive_purchase_order`），
  `process_request` 执行已审批在途单据时保持宽松，避免管理员一停用库位就卡死在途单。
  新增专项 4 例。

- **P2-03 PROTECT 删除抛 500 且留孤儿审计（已修复）**。`perform_destroy`
  先写审计再 `delete()`，两者无事务；命中 `PROTECT` 时 `ProtectedError` 冒出视图
  （生产 500），审计表留下「已删除」但对象仍在的记录。修复：包 `transaction.atomic`、
  捕获 `ProtectedError` 转 DRF 400、审计移至删除成功之后并写入删除前字段快照。
  新增 `test_master_data_delete.py` 4 例。

- **测试环境说明**：本轮首次全量运行出现 4 个 `PermissionError`，根因是 DSH
  文件沙箱给临时目录继承了 `Everyone:(DENY)(DC)` ACL，`tempfile.mkdtemp()` 建出的目录
  无法写入/清理（`common/tests.py:2253/2284/2362/2393`）。放开沙箱后同一批用例 9/9 通过，
  属环境限制而非代码缺陷；已作为 P2-09 记录，建议改用 `ignore_cleanup_errors=True`。

### 2026-11-19 ~ 2026-11-20 依赖安全、权限收紧与表单语义修正

- **P1-01 依赖版本三套漂移（已修复并部署）**。本机 venv 为 Django 5.2.9 / DRF 3.16.1 / Pillow 12.3.0，
  生产为 5.2.17 / 3.18.0 / 12.3.0，锁文件为 5.2.19 / 3.18.3 / 13.0.0。OSV 实测：本机 121 条已知漏洞、
  生产 65 条、锁文件 0 条。根因是 `install.sh` 离线更新分支直接 `cp -a` 旧 venv 从不校验，
  导致生产 venv 停留在 Python 3.10.12，而 Pillow 13 要求 Python >= 3.11。
  修复：本机与生产均升级到锁文件版本（生产用 python3.12 重建 venv 并原子切换），
  `install.sh` 离线分支增加 venv 兼容性闸门（Python >= 3.11 + 锁文件逐项比对），
  新增 `test_dependency_consistency.py` 6 例。生产验证：版本 100% 一致、公网 200、业务数据完整。

- **P1-02 库管代借时资产被记成外部持有人（已修复并部署）**。实测：库管走快速出入库、
  接收人选系统内账号时，资产 `current_holder` 为空、接收人被存成 `external_holder_name` 文本，
  实际借用人既看不到设备也查不到单据。根因是 `services.py` 按「是否快速出入库」而非
  「接收人是什么」分流，丢掉了 `recipient` 外键。修复：按接收人类型分流（有外键→落外键，
  只有文本→存 external_*，都没有→落申请人），并限定只有借用/领用/出售设持有人。
  连带修复 `my_loans` 允许按 `recipient` 查单据。新增 `test_delegated_holder.py` 8 例。

- **P1-03 单据模板缺少场景约束（已修复并部署）**。出售强制客户单位（前后端双重校验）；
  回仓类（归还/调拨）与处置类（维修/报损/报废/退供应商）隐藏接收人区块。

- **P2-04 宽泛异常收窄（已修复）**。3 处 `except Exception` 改为具体异常类型 + 日志：
  v2/v3 xlsx 嗅探改 `(BadZipFile, InvalidFileException, KeyError, OSError, ValueError)`，
  context_processor 改 `(DatabaseError, OperationalError)`。

- **P2-07 V2 导入不可撤销（已修复并部署）**。V2 不写 `ImportMutation` 快照，
  撤销必然报「没有可逆变更账本」。修复：V2 写入 `reversible=False`、V3 写入 `True`，
  导入页对不可撤销批次显示警示条。新增 `test_import_reversible_flag.py` 8 例。

- **P2-03 views.py 拆包（方案 B）**。`apps/common/views.py`（5119 行）改为
  `apps/common/views/{__init__.py, legacy.py}`，转发层保持 `views.xxx` 全部可用，
  新功能可写独立模块。新增 `test_views_package.py` 7 例。

- **终态校正权限收紧（安全修复）**。复核发现 `is_system_administrator` 把 `warehouse_admin`
  组也算作系统管理员，导致仓库管理员可直接把资产改为已报废/已售出/已退供应商，绕过审批
  （线上历史校正记录 0 条，未被利用）。修复：新增 `TERMINAL_CORRECTION_STATUSES` 与
  `SUPERADMIN_CORRECTION_TARGET_STATUSES`，终态仅 `is_superuser` 可写；
  `ADMIN_CORRECTION_TARGET_STATUSES` 移除三个终态；`can_correct_asset_state` 改为只认
  `warehouse_state_correction`（不再因 warehouse_admin 自动放行）。

- **表单语义修正**。「使用部门/客户单位」统一为「接收人单位」并移入接收人区块；
  出售时隐藏整个用途区块（此前只禁用「日常」单选）；新增「供应商」下拉用于退货给供应商
  （新增 `WorkflowRequest.supplier_ref` 外键，迁移 `0021`），后端校验必填且启用。

- **运维加固（NUC）**。停止 Cloudflare 隧道与自愈 timer（改全内网 IP 访问）；
  启用 ufw 白名单（3 网段 × 4 端口 = 12 条规则，默认 deny incoming）；
  停止 blue-client / release-console / s1-ota-agent 三个无用服务；
  journald 留存从 14 天延到 30 天（并消除主配置与 `conf.d` 的取值冲突）；
  logrotate 改为 `daily + maxsize 200M + rotate 7`。
  事故记录：曾用 `logrotate -f` 强制轮转导致 rsyslog 的 omfile 挂起、14 个日志文件消失，
  已回滚配置、补齐文件并验证写入，业务未受影响。

- **测试基线**：700 → 773 通过（跳过 8 项 SQLite 不适用项）；
  PostgreSQL 并发测试 6/6 在 NUC 独立库验证通过。


### 2026-11-18

- **完成第十轮配置、PWA 与页面权限复核**。缓存/Service Worker、账号评审与接收人字段专项 `33/33` 通过，Ubuntu shell 脚本语法检查通过；确认 `warehouse_setup` 登录保护、结构化日志、PostgreSQL 连接复用和动态缓存版本已在当前工作区实现。新增 **P2-12**：`.env.example` 默认 DEBUG、开发密钥、`ALLOWED_HOSTS=*` 和全 CORS，手工部署直接复制可能形成公网误配置。未修改业务代码、未连接 NUC、未部署。

- **完成第九轮本地容量与 PostgreSQL 专项可运行性复核**。一次性 SQLite E2E 数据库覆盖 8 条登录态只读路由、并发 1/4/8/12 共 160 次请求，全部 HTTP 200、错误率 0；12 并发 P95 366.19ms、P99 441.81ms。PostgreSQL 并发/审计专项 8 项因本机 SQLite 按设计跳过；当前环境无 Playwright，浏览器 E2E/UI 审计未执行。容量临时产物已清理，未修改业务代码、未连接 NUC、未部署。

- **完成第八轮报表、导出、审计归档与容量复核**。本机专项 `152/152` 通过（159.665 秒，SQLite 临时库，system check 无问题）；审计归档、供应商/呆滞库存/交易导出和财务权限回归均执行。发现 **P1-12**：`transaction_export()` 在无财务权限时隐藏成本表头但仍输出 `cost_amount` 数据，临时测试已复现；发现 **P2-11**：供应商价格、报表总览和呆滞库存路径存在无上限 Python 全量物化，需 PostgreSQL 大数据量压测。未修改业务代码、未连接业务数据库、未部署；NUC release 本轮未复核。

- **完成第三轮身份/权限边界与核心 API 复核**。本机 SQLite 临时库专项测试 `30/30` 通过（14.731 秒），覆盖申请/审批/出入库动作权限、成本字段财务权限、照片上传、资产写权限、账号治理和 `/me` 一致性。静态确认申请与资产 API 禁用硬删除，嵌套申请创建使用事务；未发现新增 P0/P1。当前 NUC release 本轮 SSH 无法复核，不能把此前读取的 `20261118-122912-b79551b` 当作本轮现场验收。

- **P2-01 提交随后已部署到 NUC**（由其他并发执行者完成，非本轮操作）：上次成功读取的 release 为 `20261118-122912-b79551b`，source revision 与本地 HEAD 一致，工作树干净。本轮无法重新连接 NUC，未追加 PostgreSQL 或现场设备验收结论。

- **完成第四轮通知/outbox 与附件访问复核**。通知收件人隔离、钉钉 outbox 幂等/回滚/重试/失败恢复和上传校验专项 `8/8` 通过；生命周期附件直链由 Nginx 拦截并经 Django 授权下载。新增待确认 P2 候选：发票/费用凭证附件目前未按财务权限细分，需业务确认后再登记开发计划。

- **完成第五轮部署与恢复路径复核**。发现 P1-09：`backup.sh` 已切换到 `media-snapshots` 硬链接增量格式，但 `restore-drill.sh` 仍要求 `media.tar.gz`，当前恢复演练无法启动；历史恢复演练结论不适用于新格式。另记录 P2-08：`check-all.sh` 未清理 `.test-run-temp`。本轮未修改部署脚本、未连接 NUC、未执行数据库恢复。

- **补充发现 P2-09**：`install.sh --import-sqlite` 将完整 `dumpdata` 输出写入固定 `/tmp` 文件，异常时可能遗留业务数据且未显式限制权限；修复前不建议在共享服务器启用该一次性导入路径。

- **补充发现 P2-10**：Workflow API/离线同步未在服务层校验目标仓库和库位的启用状态，停用对象仍可能被写入新的调拨、归还或维修入库流程；需确认管理员例外后登记修复。

- **完成第六轮核心 workflow 与离线流程回归**。`OfflineOperationTests` 与 `WorkflowApiTests` 共 `35/35` 通过（24.577 秒），覆盖幂等、跨账号、payload 篡改、冲突回滚、拒绝和重复复核；未执行 PostgreSQL 并发、真实浏览器、断网设备和现场硬件验收。

- **完成第七轮采购、盘点、导入和审计专项**。专项 37 项通过、2 项跳过（共 39 项，43.159 秒），跳过项为 PostgreSQL 原始 SQL 审计触发器测试；覆盖盘点差异、采购部分到货、导入撤销和应用层审计不可变性。新增 P1-11：采购到货无幂等键，成功后重试可能重复增加库存。

- **完成第二轮安全门禁复核**。WSL `bash -n` 检查 Ubuntu 脚本 `22/22` 通过；Bandit 1.9.4 生产代码扫描中危/高危 `0`（低危启发式 12 项）；pip-audit 对锁定依赖报告 `No known vulnerabilities found`。扫描器使用临时目录，未修改仓库；PostgreSQL 8 项并发/审计专项仍未执行。新增 P2-06：`check-all.sh` 尚未纳入 shell 语法和安全扫描。

- **P2-01 资产编码前缀规则改动已提交但未部署**。其他并发执行者将修复提交为 `b79551b`：V2 导入与 `apply_asset_identity` 复用 `code_services.py` 的唯一事实来源，并新增 `test_asset_code_prefix_consistency.py` 10 例；专项 `10/10`、全量 `603/603` 通过（8 项 PostgreSQL 跳过）。本轮未执行该提交，NUC 仍运行 `c822551`，不能视为现场验收或已部署。
- **完成第一轮全量只读审查与基础回归**。记录 Git 基线 `b40c190`、本地工作区和 NUC release 三态；补充模块边界、核心调用链、数据流、部署拓扑和六视角评审到 `REVIEW_REPORT.md`。本机 Django 全量通过、跳过 8 项 PostgreSQL 专项；`manage.py check`、迁移漂移、`pip check`、JavaScript 语法和 `git diff --check` 通过。**未执行项**：`pip-audit`（安装被中断，改用 OSV API 完成，见下条）、Bandit SAST、真实浏览器与设备验收。本轮未访问业务数据库、未部署、未修改业务代码。

- **待解决问题推进**。①布局：审计确认实际只有 3 个页面（composite_unit / inventory_conversion / transaction_list）的按钮还在 `page-heading` 内，已改为独立 `page-actions` 行；其余 9 个带筛选栏页面此前已规范。②依赖漏洞：`pip-audit` 安装被中断，改用 **OSV.dev API 逐个查询 16 个锁定依赖，结果 0 个已知漏洞**；生产实际版本 Django 5.2.19 / DRF 3.18.3 / Pillow 13.0.0 均已是当前安全版本。`CHANGELOG` 中 2026-11-09 记录的「pip-audit 发现 104/128 条公告」是**当时旧版本**的结论，已随依赖升级失效，不代表当前状态。③异机备份：新增 `offsite-backup.sh` + 每日 03:30 timer，支持 rsync / 本地挂载盘 / S3；未配置 `offsite.env` 时静默跳过（实测确认），启用只需建该文件。④CI：新增 `check-all.sh` 一键检查（check、迁移漂移、pip check、diff --check、全量测试），`--fast` 可跳过测试。
- **首次完成备份恢复演练**。此前只验证过 `pg_restore --list`（能读目录），从未真恢复。本轮从最近备份恢复到独立临时库 `warehouse_restore_drill`：哈希校验通过、`pg_restore` exit=0、资产 636 / 用户 4 / 审计 1096 / 物品类型 78 / 库位 25 全部到位、真实读到 `AST-00000022` 与 `asset_identity_applied`、外键 193 个、索引 402 个、**审计只增不改的 10 个触发器随备份恢复**；演练库用完即删，生产库未受影响。结论：备份可用。
- 新增 `PROJECT_REVIEW_20261118.md` 项目完整复盘：项目定位、已具备功能、待推进、待解决、远期规划、近期改动、验证记录、下一步建议。
- releases 保留数 5 → 3（省 270 MB）。每个版本约 130 MB（代码 + venv），2 个回滚点足够覆盖常见回退。
- 新增 `media-gc.sh` 与每周日 03:30 的 timer：清理数据库已不引用且超过 7 天宽限期的 media 孤儿文件。默认 dry-run，`--apply` 才真删；只扫白名单目录；删空目录自动清理；单次 ≥20 个时钉钉通知。实测发现真实遗留 `branding/logo.png.bak-20261116`。
- 备份改为硬链接增量（`rsync --link-dest`）+ 数据库与 media 解耦 + 无变化时跳过快照。media 40GB / 保留 30 天从理论 1.2TB 降到约 60GB。修复 itemize 判断：目录 mtime 变化会输出 `.d..t`，原 `grep '^[<>ch]'` 误判为有变化导致每天建空快照，改为只认 `>f`/`cf`/`hf`。NUC 实测硬链接生效（同一文件两份快照 inode 相同）。
- 附件上传上限 20 MB → 100 MB，Nginx `client_max_body_size` 同步，`client_body_timeout` / `proxy_send_timeout` / `proxy_read_timeout` 放宽到 600s。图片仍限 10 MB（Pillow 解码有内存风险）。实测 99MB 通过、101MB 明确拒绝。
- 启用/停用状态用颜色区分：原来「停用」复用 `.tag.neutral` 与「启用」共用同一套绿色；新增 `.tag.is-active`（绿底✓）与 `.tag.is-inactive`（灰底×），应用到 `warehouse_setup.html` 全部 7 处。
- 库位列表补「状态」列，与仓库/项目/工单格式一致（此前库位停用后列表看不出状态）。

### Review

- 2026-11-17：完成复盘后的 P1 修复。申请明细主单字段改为只读，申请单更新携带 `lines` 明确拒绝；V2/V3 全量基准导入在写入前阻断活动资产、预占、未完成维修、耗材持有余额和未归还借用，避免整批导入产生库存破坏。新增专项 `27/27`，Django 全量 `536/536` 通过、跳过 8 项 PostgreSQL 专项；`check`、迁移漂移、`pip check`、`git diff --check` 通过。本轮未连接 NUC、未部署、未修改服务器数据库。

### Planned

- M9 的资产台账物品类型聚合视图、只读测试数据候选报告和账号策略收口已实现并完成本机专项验证；本条不表示服务器数据已清理或第三方登录已实现。

### Added

- 2026-11-17：启动 M8 微信小程序 HTTPS 体验版接入。目标地址为 `https://cloudwarehouse.dpdns.org`，AppID 为用户提供的 `wx02c8193db5952f39`；小程序源码已完成地址、AppID 和合法域名校验切换，尚未上传体验版。
- 2026-11-17：用户已完成微信开发者工具真机预览和体验版上传。手机完整业务链路尚未回归，admin 小程序当前仅提供现场端的工作台、申请与审批、通知，Web 后台管理能力不在本里程碑范围内。

- 2026-11-17：工作区脏候选 release `20261117-113500-1901c85-dirty-bcc703adf026` 已部署到 NUC。部署前 PostgreSQL 快照为 `pre-deploy-20261117_113812`；无待执行迁移，业务、Nginx、PostgreSQL、Cloudflare Tunnel 均 active；补登记 `cloudwarehouse.dpdns.org` 到应用白名单和 Nginx。未清库、未重导、未修改服务器业务数据。

- 2026-11-17：**修复三个 P1 并部署验证**（release `20261117-162436-c05db86`，提交 `c05db86`）。① **导出公式注入**：新增 `common.utils.sanitize_csv_cell/sanitize_csv_row`，对以 `= + - @ TAB CR` 开头的单元格前置单引号，防护 **7 处导出数据行**（一致性检查、资产台账、补货建议、供应商价格、供应商对账、呆滞库存、出入库流水）；② **盘点扫码归一化**：`_resolve_asset` 与 `scan_check_value` 改用 `normalize_scan_value`，全角冒号/字母与扫码枪尾随换行不再导致「看着一样却查不到」；③ **耗材歧义保护**：`_resolve_stock` 改为编码精确命中优先，名称命中多条时抛错而非 `.first()` 静默取错。新增 `apps/inventory/test_p1_hardening.py` 11 例，全量 **551/551** 通过。三项均在生产环境实跑验证：`=cmd|'/c calc'!A1` 被转义为文本、`WMS2：A：AST-1` 归一化为 `WMS2:A:AST-1`、真实资产 `AST-00000254` 带尾随换行仍可解析。
- 2026-11-17：**建立可追溯 Git 基线**。此前积累的 121 项未提交改动（91 修改 + 30 未跟踪）分批落为 10 个本地提交（未 push），HEAD 从 `1901c85` 推进到 `e3307fe`：`chore` 忽略测试临时目录 → `accounts` 账号申请与拼音账号 → `common` 审计演员快照与权限展示 → `workflow` 审批职责分离 → `inventory` 搜索布尔解析与导入加固 → `label-printer` ZPL 中文改位图 → `pwa` iOS 图标与移动端布局 → `test` 回归用例补充 → `deploy` Cloudflare Tunnel 与 Nginx 域名入口 → `docs` 小程序归档与进展同步。提交前后各跑一次全量测试，均 `540/540` 通过，确认提交内容无遗漏、无 CRLF 内容漂移。此后回滚可精确到单个功能边界。
- 2026-11-17：收口复盘。全量 `540/540` 通过（跳过 8 项 PG 专项）、`check`/迁移漂移/`pip check`/`git diff --check` 全通过；页面渲染冒烟 **32 页面 × 7 角色 = 224 组合零 5xx**；服务器四服务 active、迁移无待执行、8 个 timer 正常、PWA 图标与 manifest 全部 200、公网 `/login/` 与 `/health/` 200。清理约 86 MB 无用产物（dist 32 个历史包、2 个 11-06 归档共 71 MB、测试临时目录）。修正文档一致性：`PROJECT_STATUS.md` 的 release 从落后两个版本更新为 `10df922f6818`、测试基线 536→540；`AGENTS.md` 当前优先级更新为 PWA 收口 → 遗留 P1 → 邮件摘要 → 依赖锁。
- 2026-11-17：移动端 UI 与延迟修复并部署（release `20261117-151147-1901c85-dirty-9c91f97b8cef`）。① **模板注释泄漏**：`web_base.html` 用了跨行 `{# #}`，Django 只支持单行注释，导致注释原文渲染到每页顶部；改为 `{% comment %}` 并加测试锁定；② **日期输入框对不齐**：全局 `min-height:38px` 压过局部 `height:32px`，且 Safari 日期控件原生更高；改为固定两行网格（label 16px + 控件 32px）+ `::-webkit-date-and-time-value` 归位，并把 `filter-compact` 样式从各模板提取到 `web_base.html` 全局，9 个带日期筛选页面同时生效；③ **按钮文字折叠**：`.button` 缺 `white-space: nowrap`；④ **布局错位**：`我的申请`/`我的借用` 的操作按钮从 `page-heading` 移出为独立 `page-actions` 行。全量 `540/540` 通过。
- 2026-11-17：**公网延迟从 3.17s 降到 1.07s**。根因是 cloudflared 同时使用外接网卡的 IPv6 出口（`2606:4700::`，RTT 370ms 且抖动到 438ms），而 IPv4 边缘只有 198ms。通过 systemd `RestrictAddressFamilies=AF_INET AF_UNIX` 强制隧道只走 IPv4；用户随后拔掉外接 USB 网卡，路由进一步收敛到单条公司 WiFi，延迟稳定在 1.07–1.5s。移动热点方案最终未采用（运营商封 7844 端口）。
- 2026-11-17：PWA 移动端修复并部署（release `20261117-142820-1901c85-dirty-6b038429c864`）。① **iOS 图标**：新增 `icon-180/192/512.png` 与 `icon-maskable-512.png`，manifest 改为提供 PNG（iOS 不读 SVG），`web_base.html` 与 `registration/login_base.html` 均补 `apple-touch-icon`、`apple-mobile-web-app-capable/status-bar-style/title`；② **扫码刷屏**：`scanner.js::record` 对「已存在」「未识别」不再追加列表行，去重窗口 1500ms→3000ms，提示语改为「请移开换下一个」；③ **移动端横向溢出**：表格 520px 硬宽度改为由 `.table-scroll` 内部消化，补 `html,body{overflow-x:hidden}` 与 `min-width:0` 兜底。新增 `test_pwa_meta.py`（3 例），全量 `539/539` 通过。
- 2026-11-17：确认 iPhone PWA 必须用 **Safari** 添加到主屏幕：iOS 上 Chrome/Firefox 均被强制使用 WebKit，且只有 Safari 支持 `display: standalone` 独立窗口；Chrome iOS 只能加成带地址栏的书签。
- 2026-11-17：**战略收敛：放弃微信小程序，专做 PWA。** `miniprogram/` 33 个文件移入 `docs/archive/miniprogram-paused-20261117/`，新增归档说明记录决策依据（六大需求 Web 端已全实现、双份维护成本高、小程序离线能力不优于 PWA、真机运行时校验未完成）。用户确认仓管员以 iPhone 为主、离线本期不做。`DEVELOPMENT_PLAN.md` 新增 M17「PWA 移动端收口」里程碑，Non-goals 同步更新。
- 2026-11-17：完成四层回归并出报告（写入 `REVIEW_REPORT.md` 顶部新章节）：本机 Django 全量 **536/536** 通过（跳过 8 项 PG 专项）、`check`/迁移漂移/`pip check`/`git diff --check` 全通过；服务器 4 个服务 active、迁移无待执行、8 个 timer 正常；**Web 页面 31×7 角色 = 217 组合零 5xx**；公网 `https://cloudwarehouse.dpdns.org/login/` 200、健康检查正常。
- 2026-11-17：本轮新识别 3 个 P2：iPhone PWA 缺 PNG 图标与 iOS 专用 meta（`manifest.webmanifest` 仅 SVG，iOS 不支持）、`warehouse_setup`/`warehouse_setup_edit` 缺 `@login_required` 导致未登录重定向链绕行且 `next` 丢失、`check --deploy` 的 4 个 HTTPS 警告缺架构说明（Cloudflare 终止 TLS，源站必须保持 HTTP）。无新增 P0/P1。
- 2026-11-17：打通公网入口 `https://cloudwarehouse.dpdns.org`（Cloudflare Named Tunnel，隧道名 `warehouse-nuc`）。服务器新增 systemd 服务 `cloudflared.service`：`User=cloudflared`、token 通过 `--token-file` 读取（不出现在命令行与 ps 输出）、文件权限 `root:cloudflared 640`、开机自启。Cloudflare 侧配置 `Published application` 路由到 `http://localhost:80`；`ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、Nginx `server_name` 均已加入该域名。用户用手机 4G 实测可正常打开登录页；公司内网继续走原内网地址，不受影响。新增 `deploy/ubuntu22/cloudflared.service`。
- 2026-11-17：网络隔离方案探索结论（**未采用**，脚本保留备查）：尝试用 network namespace 把外接 USB 无线网卡隔离给隧道专用，实测 **Linux 内核不允许 WiFi 网卡进入 netns**（`ip link set wlan0 netns xxx` 返回 `RTNETLINK answers: Invalid argument`，因 wireless phy 是全局对象）。改用 iptables `owner --uid-owner` + fwmark 策略路由可成功隔离并让 cloudflared 走出指定网卡，但**移动热点（运营商网络）封 7844、8080 等非标端口**，隧道无法建立，故最终仍走公司网。新增 `deploy/ubuntu22/tunnel-routing.sh`、`deploy/ubuntu22/tunnel-netns.sh` 备查。
- 2026-11-17：修复标签打印客户端 ZPL 通道中文丢失：ZT610 内置字体（`^A0N`）不含中文字形，直接 `^FD` 输出中文会被打印机静默丢弃（表现为中文全没、数字字母仍在）。改为整页位图以 `^GFA` 嵌入、纸张边界仍由 `^PW`/`^LL` 锁定，兼顾中文正确与不多走纸。客户端 `BwtLabelClient.exe` 已重建（29.9 MB），新增回归断言确保中文字形真的渲染出像素。
- 2026-11-17：发布构建脚本排除本地测试/审查临时目录，避免 `.test-tmp`、`.review-tmp` 等可再生输出进入部署包；仅完成本地脚本验证，尚未部署到 NUC。
- 2026-11-16：完成 M11 台账视图状态隔离：类型汇总钻取到单件资产时使用一次性上下文，返回汇总页清除钻取类型条件；切换视图和清空条件保持当前视图。个人资料页增加仅用于尝试唤起本机钉钉客户端的 `dingtalk://` 入口，不自动修改绑定状态。专项 `7/7`、系统检查、迁移漂移检查和差异格式检查通过。
- 2026-11-16：性能优化已部署（release `20261116-181751-1901c85-dirty-bb8da5de8be5`）。三项根因：① Nginx 未开 gzip，基础模板约 100 KB、zxing 库 395 KB 明文传输；② 站点 logo 是 566×530 的 79 KB PNG，却只显示在 34px 位置，且每页都加载；③ 通知未读数每 30 秒轮询一次，访问日志中 2349 条请求里 931 条是它。修复后：登录页 3130→1433 字节、zxing 395 KB→103 KB、logo 79.7 KB→16.7 KB；轮询改为 120 秒且页面在后台时跳过。同时在上传环节压缩 Logo（缩到 320px + 128 色量化，约降 79%），后续上传自动生效。
- 2026-11-16：停用账号登录提示改为明确文案「该账号已停用，无法登录」。只有"用户名+密码都正确但账号已停用"时才显示该提示，密码错误时仍统一提示"用户名或密码不正确"，不泄露账号是否存在。
- 2026-11-16：M15 账号治理已部署：release `20261116-174612-1901c85-dirty-14517796e138`。包含「停用/启用账号」、「工号改选填」、「申请表单精简为姓名+电话」、「中文名自动生成拼音账号」、「建号与提权补审计」、「导入未知模板不再 500」、「搜索深度上限」、「编辑权限按钮无反应修复」。三个新迁移（`accounts/0006`、`0007`、`common/0015`）均为纯新增/放宽约束。全量 `522/522` 通过、跳过 8 个 PostgreSQL 专项。
- 2026-11-16：账号删除的正确姿势改为「停用」。此前任何做过一次操作（哪怕只是改自己中文名）的账号都会因 `OperationAuditLog.actor` 是 PROTECT 而永远无法删除，提示却写"已有受保护的业务记录"，容易误判。现新增停用（立即作废会话、保留全部历史引用、写 `account_disabled` 审计），并把删除失败文案明确指向停用。`OperationAuditLog` 增加 `actor_username`/`actor_display_name` 快照，`actor` 外键**保持 PROTECT**——数据库触发器 `warehouse_immutable_audit` 禁止对审计表做任何 UPDATE/DELETE，改 `SET_NULL` 会在删号时发 UPDATE 被拒（首次部署已实测拦下）。
- 2026-11-16：员工工号改为选填（公司暂无统一工号体系，外包同事可能没有工号），保留唯一约束供将来对接 HR/钉钉；登录页申请开通账号不再要求填写工号和部门（申请人不知道系统内的部门名称，填了也是错的），去重键改为「姓名+电话」，部门由管理员建号时从字典选择。
- 2026-11-16：新增 `apps/accounts/username_services.py`，按中文姓名用 pypinyin 自动生成登录账号（「杨俊杰」→ `yangjunjie`，重名追加 2/3/4…），权限页待开通申请列表直接展示建议账号。
- 2026-11-16：补账号治理审计：`account_created`（建号及所分配权限）与 `account_permissions_updated`（提权/降权，含前后权限集合与 is_admin）此前完全缺失，现已写入。
- 2026-11-16：修复导入未知 Excel 模板返回 500（V3/V2 嗅探均不匹配时 `summary` 未定义），改为提示"无法识别该 Excel 模板"并返回 200；修复布尔搜索解析器可被深嵌套括号打成 500（新增深度上限 16 并捕获 `RecursionError`）；修复权限页「编辑权限」按钮因 `data-target` 与表单 `id` 不一致而点击无反应。
- 2026-11-16：标签打印 ZPL 通道中文丢失修复：ZT610 内置字体（`^A0N`）没有中文字形，直接 `^FD` 输出中文会被打印机静默丢弃。改为整页位图以 `^GFA` 嵌入、纸张边界仍由 `^PW`/`^LL` 锁定，兼顾"中文正常"与"不越界多走纸"；客户端 `BwtLabelClient.exe` 已重建（29.9 MB），新增回归断言确保中文真的渲染出像素。
- 2026-11-16：`label-printer` 账号设为不可删除（打印客户端专用 API 账号，删除会导致所有打印机停止工作且报错不直观）。
- 2026-11-16：M14 已部署到 NUC：release `20261116-163159-1901c85-dirty-5218c11d2586`（source `1901c85`，dirty）。包含两个纯新增迁移（`accounts/0005_accountrequest`、`workflow/0020_workflowrequest_recipient_and_external_flag`），部署后 `showmigrations` 确认均已应用。服务、健康检查、`/login/`、`/login/request-account/` 均正常；资产 636、耗材 31、账号 5、申请 0 与部署前一致，新表 0 行。全量回归 `488/488` 通过、跳过 8 个 PostgreSQL 专项。
- 2026-11-16：人员显示统一为「中文姓名（登录账号）」，覆盖申请、采购、审批、盘点、转换、导入、台账流水、审计日志、资产生命周期等 14 个模板；新增 `person_label` 过滤器与 `person` 标签。`username` 仍是唯一真相且不可变，改名不影响历史单据归属。
- 2026-11-16：权限页重排：11 项权限改为可换行胶囊、勾选即时变色；角色列只显示权限项数量，消除权限名拉长导致的整行变形；表格改为固定列宽。
- 2026-11-16：管理员可为账号重置密码，生成 12 位临时密码（`secrets`，去除易混淆字符），立即作废该账号全部登录会话并写 `account_password_reset` 审计；超级管理员密码仍只能走服务器运维。
- 2026-11-16：登录页新增「申请开通账号」入口与 `AccountRequest` 模型；权限页新增待开通申请列表，可标记已建号或驳回。**不开放自助注册**，提交申请不会创建账号。
- 2026-11-16：审批职责分离：通知范围从"全部仓库操作员"收窄为具备审批权限的账号；指定审批人下拉排除申请人自己；存在其他审批人时禁止自批，系统中只剩一位审批人时放行并写 `request_self_approved` 审计与超管提醒。
- 2026-11-16：出入库申请接收人改为选人：可从系统账号选择并按账号派生姓名，或勾选「接收人不在系统内」手工填写并打 `is_external_recipient` 标记；兼容直接提交姓名的旧调用。
- 2026-11-16：M12 补充已部署：release `20261116-152337-1901c85-dirty-1d0cba9e1619`（source `1901c85`，dirty）。权限页新增「工号与部门」列，管理员可在线修改员工工号与所属部门，含非空、全表唯一校验与 `account_profile_updated` 审计（此前创建账号后无任何修改入口，只能去 `/admin/` 或改库）。单据人员显示统一为「中文姓名（登录账号）」，新增 `person_label` 过滤器；`username` 仍是唯一真相且不可变，改名不影响历史单据归属。全量回归 `471/471` 通过、跳过 8 个 PostgreSQL 专项；部署后服务、健康检查通过，资产/耗材/批次/申请/流水/账号计数与部署前一致。
- 2026-11-16：重新构建标签打印客户端 `tools/label_printer_client/dist/BwtLabelClient.exe`（29.9 MB，2026-11-16 15:22，SHA-256 前缀 `a6732f0eedb69c04`）。原有 EXE 为 2026-11-09 构建，早于 11-16 的源码改动，不包含当前 RAW ZPL 通道；客户端自测 `11 passed`。`dist/` 在 `.gitignore` 中，不进入仓库与发布包。ZT610 60×40 mm 真机走纸仍待验收。
- 2026-11-16：M12 已部署到 NUC：release `20261116-145503-1901c85-dirty-5a814624ed8f`（source `1901c85`，dirty），内容哈希 `5a814624ed8f…`。部署前 PostgreSQL 快照由 `install.sh` 完成，迁移无待执行，Gunicorn/Nginx/PostgreSQL 均 active，Gunicorn `/health/` 返回数据库正常、Nginx `/health/` 与 `/login/` 均 200；服务器上确认 `asset_reset_filter_query` 与 `detail-list` 已生效。部署前后业务计数完全一致（资产 636、耗材 31、批次 2、申请 0、流水 0、账号 5、审计 1055），未清理或覆盖服务器数据。全量回归 `461/461` 通过、跳过 8 个 PostgreSQL 专项。
- 2026-11-16：资产台账搜索框右侧新增「清空」动作，一次清除搜索词与全部筛选条件，保留当前标签页、视图与每页条数；无筛选时按钮为禁用态。移除「更多筛选」面板内重复的清空入口。修正统计卡片点击后静默清空筛选的问题（卡片标签为“当前筛选资产/耗材配件”），现保持当前筛选切换标签页。专项 `7/7` 通过。
- 2026-11-16：个人资料页改为只读信息展示：登录账号、员工工号、所属部门、钉钉绑定状态改用既有 `.detail-list` 组件呈现，不再使用看起来可编辑的只读输入框；移除无法在 PC 端兑现的 `dingtalk://dingtalkclient/` 按钮与大段说明文字，仅保留“留空时显示登录账号”一行提示。新增断言确认伪造 `username`/`employee_no`/`department`/`dingtalk_user_id` 的 POST 不改变任何数据。
- 2026-11-16：钉钉身份登录列入 `DEVELOPMENT_PLAN.md` M13 未来规划，标注为受 HTTPS 回调地址与建号策略阻塞，当前未实现；通讯录只读同步作为可先行步骤记录。
- 2026-11-16：接力复跑全量回归并完成部署。使用绝对可写临时目录（`TEMP`/`TMP`/`TMPDIR` 指向 `.test-run-temp`，未改应用逻辑）完成 Django 全量 `452/452` 通过、跳过 8 个 PostgreSQL 专项；`manage.py check`、`makemigrations --check --dry-run`、`git diff --check` 一并通过。定位到 Windows 默认 `%TEMP%` 对 Python 不可写会让 openpyxl 导出类用例批量报错并表现为 500，属环境故障而非代码缺陷。修复发布包卫生：`build-package.ps1` 排除 `.test-tmp`/`.review-tmp`/`.test-run-temp`/`.review-tmp`；`.gitignore` 增加 `.test-run-temp/`。
- 2026-11-16：候选包 `after-sales-warehouse-20261116-m11-profile-filter-dirty-b8edaa14defb.zip` 已部署到 NUC `192.168.123.101`，当前 release 为 `20261116-m11-profile-filter-dirty-b8edaa14defb`，source revision `1901c85`，内容哈希 `b8edaa14defb…`，`working_tree_dirty=true`。部署前 PostgreSQL 快照写入 `pre-deploy-20261116_141756`；迁移无待执行、163 个静态文件同步、Gunicorn/Nginx/PostgreSQL 均 active、`/health/` 返回数据库正常、Nginx `/login/` 200 与 `/` 302 符合预期。部署前后业务计数完全一致（资产 636、耗材 31、批次 2、申请 0、流水 0、账号 5、审计 1055），未清理或覆盖服务器数据；回滚列表保留 5 个可切换 release。
- 2026-11-16：记录一处易误判的版本陷阱：`/opt/after-sales-warehouse/current-release` 是 2026-11-06 遗留文件，仍写着旧版本号；判断 NUC 实际版本必须读 `current/release-info`。
- 2026-11-16：修正 M11 交互遗漏：库存台账“更多筛选”不再因 URL 条件自动展开，改为仅用户点击后展开；钉钉入口改用 `dingtalk://dingtalkclient/` 链接元素，提高桌面客户端协议唤起兼容性。专项 `7/7`、系统检查、迁移漂移检查和差异格式检查通过。
- 2026-11-16：M11 已部署到 NUC `192.168.123.101`，release 为 `20261116-m11-view-profile-candidate-dirty-ae4d716aabc1`；部署前 PostgreSQL 快照、迁移检查、三项服务状态和 `/health/` 通过，服务器业务数据未清理或覆盖。

- 2026-11-16：完成 M8 本机软件修复：批量审批改用 UUID 并逐项报告失败原因；中文输入法扫码增加 composition 生命周期和 Enter/Tab 兜底；ZT610 增加 RAW ZPL 60×40 mm 主通道；打印客户端 worker 重启改为独立停止事件并等待旧线程退出。Django 全量 `447/447`、打印客户端专项 `11/11` 通过；NUC、DS3678 和 ZT610 真机仍未验收。
- 2026-11-16：完成 M9 本机功能：资产台账按物品类型聚合/单件切换、只读测试数据候选报告、admin 创建账号策略收口。未删除任何测试数据，未开放公开注册或第三方登录。
- 2026-11-16：将累计稳定化工作建立为干净 Git 基线，生成候选包 `after-sales-warehouse-20261116-m8-m9-candidate.zip`；精确 revision 与内容摘要由包内 `release-manifest.json` 固化。包内不含数据库、环境文件、媒体、备份、EXE 或构建产物。
- 2026-11-16：NUC 恢复可达后使用 PostgreSQL 快照和离线依赖复用部署 `20261116-m8-m9-candidate`；迁移无待执行，服务和 `/health/` 正常，业务计数未做清理。
- 2026-11-16：新增 M10 个人资料页：用户可维护中文显示姓名，顶部头像/菜单优先展示姓名；钉钉绑定只读展示并继续由管理员核对维护，不开放手工 userid 自助绑定。专项 `10/10` 通过，无数据库迁移。
- 2026-11-16：M10 候选版本已部署到 NUC，当前 release 为 `20261116-m10-profile-candidate`；服务、健康检查、迁移状态和业务计数核对通过，服务器临时上传文件已清理。

- 完成 Python 3.12 与依赖安全升级：Django 5.2.9→5.2.19、DRF 3.16.1→3.18.3、Pillow 12.3.0→13.0.0、sqlparse 0.5.5→0.6.1、psycopg 3.2.9→3.3.8、Gunicorn 23.0.0→26.2.0；消除 pip-audit 报告的 104/128 条公告（剩余 12 条全部为 pip 构建工具自身，不部署到生产环境）。
- NUC 通过 deadsnakes PPA 安装 Python 3.12.16，与现有 Python 3.10 并存；部署脚本自动检测并安装 Python 3.12，创建独立 venv 避免污染系统环境。
- 验证升级兼容性：Django 全量测试 443/443 通过（跳过 8 项 PostgreSQL 专项），专项测试 135/135 通过，Pillow 13 图片解码/验证/重编码和 qrcode 生成冒烟通过，DRF 3.18 API 契约验证通过，PostgreSQL 并发专项 8/8 通过。
- 部署后业务数据保持一致：资产 637、耗材 31、导入批次 2、申请 0、出入库流水 0，未修改任何业务记录。
- 完成 Excel 导入阶梯压测（100/500/1000/2000 条耗材，事务回滚不落数据）：2000 条解析 0.15s、校验 5.55s、事务导入 6.52s；确认回滚后无残留。
- 完成图片上传压测：JPEG 800×600 平均 8.4ms、PNG 1920×1080 平均 42.7ms、WebP 3840×2160 平均 170.7ms，全部成功。
- 完成 5 分钟真实 HTTP 稳定性压测（Nginx→Gunicorn，admin 登录，9 条登录后业务路由，并发 6）：20880 次 GET 全部成功、错误率 0；吞吐中位 96.1 req/s、P95 中位 85.3ms、P99 中位 98.1ms；Gunicorn RSS 从 431MB 升至 480MB（+11%，正常范围），PostgreSQL 峰值连接 3，无内存/连接泄漏。

- 新增可复用的认证只读容量工具测试和本地一次性容量脚本；支持正常登录、Cookie、分路由统计及 1/4/8/12 并发阶梯，报告不写入凭据。
- PostgreSQL 为操作审计、安全事件、资产域变更、生命周期和状态校正表增加 UPDATE/DELETE 拒绝触发器；审计关联账号改为保护删除。
- 新增钉钉持久化 outbox、幂等键、过期占用恢复、指数退避、失败终态及每分钟 systemd worker；业务请求只入队，不等待外部网络。
- 新增浏览器 E2E 工具，覆盖登录、桌面/移动布局、HID 扫码、快速借出、CSP 和离线记录过期/账号隔离。
- 新增 Linux 独立依赖锁入口和原子 release 发布/回滚契约测试。
- 新增 `REVIEW_REPORT.md`，记录提交 `bbc187c` 的架构、数据流、测试证据、P0/P1/P2 风险、六角色评审和分批修复顺序；报告明确区分本机 SQLite、历史 NUC 结果和本轮未执行的 PostgreSQL/生产验证。
- 资产检索支持布尔语法：`!关键词` 排除、`&` 同时满足、`|` 任一满足、`()` 分组，相邻关键词按“同时满足”处理；不带运算符时保持原有包含搜索。
- 新增管理员“系统设置”页：可修改系统名称、副标题，上传站点 Logo、标签彩色/单色 Logo，并查看发布版本、代码修订、部署时间、内容摘要和页面缓存版本；支持一键请求浏览器清理页面缓存。
- 新增账号登录设备管理：普通申请人可多设备登录，管理员/审批/出库账号默认最多 2 个有效会话；用户可查看脱敏设备信息并撤销单个或全部其他设备。
- 新增 PostgreSQL 特权账号并行登录竞态测试，验证多请求同时登记会话时仍不能突破上限。
- 新增 PostgreSQL 隔离测试库竞态套件，覆盖重复审批、同资产预约、同资产快速出库、耗材并发扣减和多进程登录限流计数；新增自动临时授权/撤销建库权限的服务器执行脚本。
- 新增依赖零第三方库的只读 HTTP 基线工具，输出 P50/P95/P99、吞吐量、状态码和错误率；建立上线前安全与质量评估报告。
- PostgreSQL 回滚式业务冒烟新增采购到货、盘点差异修正、V3 导入整批撤销、离线重复同步和状态冲突链路；扩展场景使用隔离保存点，任何环境均不保留 QA 数据。
- 建立唯一权威的项目状态、开发计划、标准和 agent 工作规则。
- 生成资产 V2 示例模板，包含 SC、DZSB、系统续号、暂未赋码和 AST 更新示例。
- 新增库存 V3 工作簿：`单件资产` 与 `耗材配件` 分表，支持明确新增/更新、统一预检、同批事务写入及完整库存导出。
- 新增 V3 导入对象变更账本与整批撤销：创建、更新和全量基准调整均保存前后快照，发现后续修改或业务历史时阻止整批撤销。
- 新增超级管理员受控删除无业务历史测试资产入口，要求输入 AST/主资产编码和原因，删除前保存完整审计快照。
- 新增 Windows 打印客户端 60×40 mm 校准页、完整 2×2 四格测试打印及渲染单元测试。
- 资产增加可选“客户”关联，可在录入、详情与 API 中查看。
- 新增财务查看权限组 `warehouse_finance`，采购金额在表单、详情、报表、CSV、API 与导出中按权限隐藏。
- 新增管理员专用资产状态调整：可在台账校正中将资产改为已售出、维修中、已报废等状态，保留原因、快照和审计；存在有效预占时阻断。
- 扫码枪监听限定在存在物品录入的业务页面，默认开启并可在“设备状态”面板关闭；状态与记忆按浏览器保存。

### Changed

- 2026-11-10 完成最新工作区覆盖复盘并收口权威文档：明确 Git 基线、本地脏工作区与 NUC release 必须分开记录；登记批量审批 UUID、中文输入法 HID 扫码和 ZEBRA ZT610 多走纸为当前 P1 阻断项；新增 M8 稳定化顺序、自动化/现场验收标准和 agent 工作约束。本条仅记录文档与计划收口，不表示三个缺陷已经修复或部署。
- 修复标签打印客户端 XY 偏移"调了没反应"：真实根因是「保存并启动」在运行中点击直接 return（配置不保存不生效），实际打印用启动时固化的旧配置。修复为运行中点「保存并启动」自动重启生效 + 每次打印前热加载配置偏移。渲染保持"偏移画进位图、位图从 (0,0) 贴 DC"，走纸长度固定（曾改为移动贴图原点导致正 Y 偏移多吐纸，已回退）。新增"预览标签"按钮，按当前配置（含偏移）渲染预览，不耗纸。客户端测试 `9/9` 通过；新 EXE 31,341,619 字节，SHA-256 `26569ebab75dbfca0d6349c890d78ec5135f2d795429b28913a99e1c3978c4ca`。
- 限制布尔资产搜索长度/词元数并校验页面筛选 UUID 和二维码标签 ID，恶意或畸形输入返回空结果/业务提示而不再触发 500；密码修改页移除多余 PUT/PATCH 方法。并校验页面筛选 UUID 和二维码标签 ID，恶意或畸形输入返回空结果/业务提示而不再触发 500；密码修改页移除多余 PUT/PATCH 方法。
- 钉钉出站 URL 只允许官方 HTTPS 主机且禁止 URL 凭据；历史 SHA-1 标识生成明确声明非安全用途并保持旧编号兼容。
- PWA 离线记录限定七天保留；同步成功立即删除，退出时清理当前账号记录并保留账号隔离。
- HTML 响应增加 CSP、同源 Referrer Policy 和最小化 Permissions Policy；当前模板仍因内联脚本保留 `unsafe-inline`。
- 图片上传只接受解码后的 JPEG/PNG/WebP，限制 2500 万像素并统一重编码，拒绝解压炸弹警告和伪装格式。
- Ubuntu 部署改为独立 release + 持久化 `app.env` + 原子 `current` 链接；候选版本先检查和收集静态文件，健康失败恢复旧指针，PostgreSQL 快照失败立即停止。
- 运维巡检跟随环境文件符号链接检查真实权限，并通过当前 release 的 `python -m pip` 检查依赖；PostgreSQL 专项脚本纳入原始 SQL 审计防篡改测试。
- workflow 申请 API 禁用通用 `DELETE`，申请主表和明细改为同一事务创建；异常写入会完整回滚，不留下孤立草稿。
- 耗材单价和库存流水成本按财务权限从 API 响应移除；资产照片上传权限与库存录入权限统一；Nginx 拦截生命周期附件媒体直链。
- 审计模型统一拒绝实例及 QuerySet 更新/删除；安全状态清理不再删除安全审计事件；终态资产不能通过台账校正直接恢复。
- 受控测试资产删除对资产标识和网络接口使用完整字段快照；部署前 PostgreSQL 快照失败时立即终止部署。
- 标签打印版式调整：二维码从占格高 46% 缩小到 30%、顶对齐并左移，底边不再压到资产编码行；1×1 整张 60×40 mm 时字号、Logo 和内边距按 2 倍缩放；客户端优先读取打印机驱动真实 DPI，避免 600 配置配 203 驱动导致整张放大溢出；浏览器打印改为 60×40 mm 四联拼版并隐藏页头、模块标签和面包屑。
- 报表中心的成本指标、库存趋势指标和 CSV 成本列按财务权限显示；供应商价格历史、采购对账及导出仅财务角色可访问；采购订单详情对非财务角色隐藏单价列。
- 基础资料版本保护改用 `_state.adding` 判断新增，修复 UUID 主键导致新建记录被要求版本令牌、无法保存的问题。
- 标签打印发送到贴纸机后改为 PRG 跳转，刷新或后退不再重复创建打印作业；移除指向不存在作业列表的“标签打印队列”入口。
- 快速出入库在线提交先做浏览器校验并禁用提交按钮，避免慢网连点重复入账；健康检查失败转入离线后恢复按钮。
- 修复扫码结果超过 20 条后计数停止更新且列表无上限的问题。
- 登录、退出、手动撤销和特权账号会话超限淘汰统一维护设备会话账本；撤销与超限事件写入不含明文账号/IP/会话标识的安全审计。
- 每日安全状态清理扩展为删除超期已撤销或底层会话已失效的设备元数据，保留仍有效的活跃会话。
- 登录成功与退出现在和登录失败/限流阻断一样写入匿名化安全审计，不保存明文账号或 IP。
- 离线操作编号同时绑定登记账号、客户端时间和原始表单内容；正常重试保持幂等，跨账号复用或相同编号内容不一致时明确拒绝。
- 盘点扫码与提交只锁定盘点明细自身，并覆盖默认关联排序，兼容 PostgreSQL 对可空外连接的行锁限制。
- 部署脚本将 release manifest 中的版本、Git revision、脏工作区状态和源内容哈希持久化到服务器；运行状态可直接查看，代码快照与回滚保留对应发布身份。
- workflow API 的审批/拒绝与出入库执行改用独立组合权限，未授权角色直接返回 403；通知 API 收敛为只读、未读计数和标记已读。
- 移除 DRF Basic Authentication，内网 API 继续使用时效令牌或 Django Session，避免 HTTP 环境传输基础认证口令。
- 发布构建默认拒绝脏 Git 工作区；紧急 `-AllowDirty` 包自动携带 `dirty`、完整 revision 和源内容哈希。
- 低库存与归还提醒文档时间和 systemd timer 对齐为每周一 09:00、每日 09:10。
- 根据最新 60×40 mm 标签布局重新构建 Windows 打印客户端。
- 连续 HID 扫码不再受搜索框焦点影响：扫码枪 Enter/Tab 在捕获阶段统一进入串行扫码队列，避免第二个及后续条码退化为普通候选搜索。
- `scanner.js` 增加静态资源版本标识；PWA 对扫码与离线脚本改为网络优先缓存策略，并升级缓存/浏览器缓存清理版本，避免部署后继续执行旧脚本。
- 移除顶部常驻网络状态；正常联网时不显示冗余文字，仅在连接中断或存在离线记录时显示异常状态。设备面板只展示扫码监听、本页适用性和本会话扫码验证，不展示无法可靠检测的 USB 状态。
- 我的申请筛选区改为紧凑两行布局，不再折叠。
- 扫码枪开关改为按页面是否存在物品输入框自动启用，覆盖新建申请、快速出入库、盘点等录入场景；开关可见性不再受隐藏面板影响。
- 扫码枪开关并入候选物品搜索行，扫描结果直接加入当前申请或出入库清单。
- 标签打印统一为“打印预览”入口，预览页提供“发送到贴纸打印机”和“本机浏览器打印”。
- HTML 页面统一发送 no-store 缓存头，并一次性下发 `Clear-Site-Data: "cache"`，避免返回旧页面和旧按钮状态。
- USB HID 扫码枪支持普通厂家 SN、Enter/Tab 后缀，并避免全局监听抢占业务输入框。
- 快速出入库和新建申请使用串行扫码队列，并在请求发出前立即取走输入值，防止连续扫码时前一次响应覆盖后一个条码。
- 修复资产 API 序列化器只读字段声明缩进错误，系统编号、状态、持有人和四维状态继续只能由受控业务流程修改。
- 导入规则明确为保留源表 SC/DZSB，不再凭 SN、IP、状态、数量或备注推断资产。
- Web/PWA 一级导航收敛为工作台、业务办理、任务中心、库存中心、采购与报表；全局搜索、通知和系统设置分别固定到顶部搜索、铃铛和账号菜单。
- 模块内低频入口改为二级工具标签，并按申请、审批、执行、导入、打印、报表等权限分别显示。
- 导入导出页面只展示 V3 日常入口；旧 V1/V2 解析器和 URL 保留兼容但不再引导用户继续使用旧模板。
- 资产编码规范与实际实现对齐：源表 SC/DZSB 原样保留，无行政编码时可待赋码或按已维护前缀受控续发 SC。
- 打印客户端手册更新为当前调试地址，并移除明文 API Token；令牌改为现场配置且禁止写入文档。
- 标签偏移改为固定 60×40 mm 画布内平移，避免偏移补偿改变位图物理尺寸；打印客户端 EXE 现会包含单色 Logo 资源。

### Removed

- 移除账号与权限页面中的全量清库入口及服务端删除实现；正式应用不再携带清空业务数据能力。
- 移除旧台账 V4 转换器、V1 旧版导入解析器及其页面入口；日常批量导入只保留 V3 工作簿。
- 删除互相矛盾的旧计划、旧进度、旧验收和错误数据基线文档。
- 删除旧 V1/V2 根目录模板、旧导入输出、重复内部部署文档及可再生测试/构建缓存；V3 模板改由系统实时生成。
- 删除会重建 PostgreSQL 并强制重导 11.2 基线的旧部署脚本，避免调试更新包携带与数据保护规则冲突的工具。

### Verification

- 2026-11-09：pip-audit 默认 PyPI/OSV 分别发现 104/128 条公告，集中于 Django、DRF、Pillow 和 sqlparse；Pillow 13 受 NUC Python 3.10 阻塞，未直接改锁文件。Bandit 生产代码高危 0、中危 0；认证 Wapiti DAST 爬取 162 个 URL/表单，确认漏洞 0、500 异常 0。专项 `13/13`、容量工具 `5/5`、Django 全量 `443/443` 通过（SQLite 跳过 8 项 PostgreSQL 专项）。版本 `20261109-security-capacity-r4-dirty-45fbd472a437` 已原子部署，PostgreSQL 专项 `8/8`、回滚式业务冒烟和安全巡检通过；NUC 部署后八路由共 640 次 GET，1/4/8/12 并发错误率 0，12 并发 P95 166.58 ms、P99 191.05 ms。业务计数保持 `637/31/2/0/0/0/0`，临时会话、测试库与文件已清理。
- 2026-11-09：本机专项 `66/66`、Django 全量 `438/438`（SQLite 跳过 8 项 PostgreSQL 专项）和浏览器 E2E 通过；系统检查、迁移漂移、`pip check`、JavaScript/Shell 语法和差异格式通过。
- 2026-11-09：版本 `20261109-audit-hardening-r3-dirty-85834a540857` 已原子部署到 NUC；隔离 PostgreSQL 专项 `8/8`、回滚式全业务冒烟、outbox worker、Nginx/Django 检查及有线/无线健康检查通过。部署前后业务计数保持 `637/31/2/0/0`，测试库销毁且建库权限撤销；安全巡检 0 阻断、1 个未安装 `pip-audit` 提醒。
- 2026-11-09：首轮审查修复专项回归通过；Django 全量测试 `421/421` 通过（本机 SQLite 跳过 6 项 PostgreSQL 专项），系统检查、迁移漂移、部署脚本语法和差异格式检查通过。未连接或部署 NUC，未修改真实数据库。
- 2026-11-09：Django 全量测试 `409/409` 通过（本机 SQLite 跳过 6 项 PostgreSQL 专项）；系统检查、迁移漂移、Python/JavaScript 语法、部署脚本语法、标签渲染 `7/7`、`pip check` 和差异格式检查通过。额外隔离测试复现 3 项待修复风险，不将风险复现误记为功能通过；本轮未连接 NUC、未修改生产数据。
- 2026-11-06：标签打印版式与 DPI 修正完成；客户端渲染测试 `7/7`、服务端标签测试 `11/11` 通过；新 EXE 31,339,701 字节，SHA-256 `3b55901f14a3c8fda90c9e31c50e2f5331159742158b0d893d41fc7771290c12`；校准按钮提前并改名为“一键打印校准页”；真机纸卷对齐仍待现场复核。
- 2026-11-06：资产布尔检索、财务金额收口、管理员系统设置与版本信息完成；修复基础资料新增回归、标签打印重复建作业、扫码 20 条边界和快速提交重复；Django 全量测试 `393/393` 通过（本机 SQLite 跳过 6 项 PostgreSQL 专项），系统检查、迁移漂移和差异格式检查通过。
- 2026-11-06：账号会话专项 `5/5`、Django 全量回归 `369/369` 通过（本机 SQLite 按设计跳过 6 项 PostgreSQL 专项）；系统检查、迁移漂移、依赖一致性与差异格式检查通过。
- 2026-11-06：`d1ed82b` 已部署到 NUC，应用 `accounts.0004`；代码快照 `/opt/after-sales-warehouse/releases/20261106_124543`，数据库快照 `/srv/after-sales-warehouse/backups/pre-deploy-20261106_124543`。PostgreSQL 隔离竞态测试 `6/6` 通过，临时测试库已销毁且应用账号无建库权限；两个内网健康入口均为 200，部署前后业务计数保持 `637/31/2/0/0`。
- 2026-11-06：Django 全量回归 `364/364` 通过（本机 SQLite 跳过 5 项数据库专项）；NUC 隔离 PostgreSQL 测试库竞态专项 `5/5` 通过，测试库销毁、应用账号临时建库权限撤销，业务计数保持 `637/31/2/0/0`。
- 2026-11-06：NUC 安全巡检 0 阻断、1 提醒；并发 8、200 次只读 `/health/` 与 `/login/` 基线错误率 0，P95 204.38 ms、P99 256.16 ms。依赖 CVE/SAST 工具安装因 PyPI 网络超时未完成，已保留为上线前待办。
- 2026-11-06：部署后只读数据一致性检查为错误 0 项、警告 256 项，其中缺少持有人 254 件、重复生产厂家 SN 2 组；未执行自动修复。
- 2026-11-06：NUC 钉钉群机器人实发联通性测试返回成功，低库存汇总 timer 为 enabled/active；个人工作通知缺少企业应用三项参数且暂无账号绑定，保留为配置验收项。
- 2026-11-06：专项测试 `16/16`、Django 全量测试 `358/358`、系统检查和迁移漂移检查通过。NUC PostgreSQL 首轮冒烟复现并定位盘点 `FOR UPDATE` 外连接错误；修复后采购、盘点、导入撤销、离线幂等/冲突及既有业务链路全部通过，回滚后业务计数与执行前完全一致。
- 2026-11-06：部署元数据 JSON 解析、正式包/紧急脏包字段、缺少 manifest 降级标记和回滚元数据保留完成检查。
- 2026-11-06：累计改动建立 Git 基线 `1228c04`，发布手册纳入修正为 `cbe99d5`；干净发布包验证 `working_tree_dirty=false`、revision/内容哈希正确，且不含环境文件、业务数据、EXE 或构建产物。
- 2026-11-06：权限/API 加固专项测试 `27/27`、Django 全量测试 `356/356`、打印渲染测试 `6/6`、系统检查、迁移漂移、JavaScript 语法和差异格式检查通过。Win11 系统临时目录不可写时改用受控 `.worktmp` 运行全量测试，确认首轮批量错误均为环境问题。
- 2026-11-06：发布脚本默认拒绝脏工作区验证通过；显式紧急构建生成 `dirty + 内容哈希` 包。新 `BwtLabelClient.exe` 为 32,395,359 字节，SHA-256 `8d436201ccba1b73aed4c8ac246e6aa90b6d6891ea88530bf19cf1dd5e19f488`。
- 2026-11-05：连续扫码焦点与旧缓存修复完成；连续三个 WMS 编码的聚焦输入框键盘事件模拟通过，扫码专项测试 `10/10`、Django 全量测试 `354/354`、打印客户端测试 `6/6`、系统检查、迁移漂移检查、JavaScript 语法和差异格式检查通过。
- 2026-11-05：连续扫码与缓存修复部署到 NUC；无迁移、无数据导入，部署前后业务计数保持 `637/31/2/0/0`。代码与 PostgreSQL 快照成功，三项服务和两个内网入口健康检查正常。
- 2026-11-05：连续扫码竞态修复及状态栏精简完成；Django 全量测试 351 项、系统检查、迁移漂移检查、JavaScript 语法检查和差异格式检查通过。
- 2026-11-05：连续扫码修复部署到 NUC，本次无数据库迁移；服务与健康检查正常，部署前后保持 637 件资产、31 条耗材配件、2 个导入批次、0 张申请和 0 条出入库记录。
- 2026-11-05：扫码状态语义修正、网络/设备状态拆分及资产 API 权限修复完成；Django 全量测试 350 项、系统检查、迁移漂移检查、JavaScript 语法检查和差异格式检查通过。
- 2026-11-05：最新程序部署到 NUC，仅应用 `inventory.0029` 新增可空客户外键；部署前代码和 PostgreSQL 快照成功，三项服务与健康检查正常，部署前后保持 637 件资产、31 条耗材配件、2 个导入批次、0 张申请和 0 条出入库记录。
- 2026-11-05：顶部“服务健康 + 扫码枪”合一状态、扫码按页面自动启用、我的申请紧凑筛选、标签打印统一预览完成；Django 全量测试 349 项通过，系统检查与迁移漂移检查通过。
- 2026-11-05：管理员状态调整、财务权限、客户字段、admin 写权限与扫码枪收敛完成；Django 全量测试 343 项通过，系统检查与迁移漂移检查通过。
- Django 全量测试 326 项通过。
- 五模块导航专项测试 71 项通过；Django 检查、迁移漂移检查和差异格式检查通过。
- 五模块导航已部署到 NUC；部署前代码/数据库快照、服务状态、健康检查和迁移状态验证通过。
- Windows 构建的部署校验文件固定使用 LF，并从 NUC 包排除打印客户端本机构建产物，部署包由约 65 MB 缩减到约 0.8 MB。
- V3 与 V2 导入专项测试 24 项通过；全量测试 333 项通过。
- V3 已部署并在 NUC PostgreSQL 环境完成模板生成与路由冒烟，未写入业务数据。
- 导入恢复与受控删除专项测试 17 项通过；全量测试 343 项通过。
- 恢复控制迁移已在 NUC 应用，服务、健康、迁移与空账本检查通过，未修改既有业务数据。
- 最终本地全量自动化回归 344 项通过；Django 检查、迁移漂移检查和差异格式检查通过。
- 最新版本已部署到 NUC；部署前代码和 PostgreSQL 快照成功，核心出入库/审批/维修流程在事务回滚模式下通过，前后业务计数一致。
- NUC V3 导出、Django 检查、健康检查、服务状态和 `inventory.0028` 迁移状态通过。
- 最终运维配置已同步，tmpfiles 不再把发布 ZIP 当作临时目录处理。
- 资产编码与导入专项测试 56 项通过。
- 扫码枪浏览器侧行为模拟通过。
- 旧台账 V4 专项测试 6 项、打印渲染测试 4 项、Django 全量测试 350 项全部通过；系统检查、迁移漂移检查和差异格式检查通过。
- 最新代码已部署到 NUC；部署前代码/PostgreSQL 快照成功，服务与健康检查正常，部署前后保持 637 件资产、31 条耗材配件、2 个导入批次。
- NUC 对真实《11.2库存-修改版.xlsx》完成只读转换冒烟：318 条安全资产、3 个耗材分组、832 条逐行问题；未执行导入，临时文件已删除。
