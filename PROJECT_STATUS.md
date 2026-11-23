# Project Status

更新时间：2026-11-23

### 2026-11-23 M19 P1-01 修复进展

- **三态基线**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本轮未提交文件包含既有审查记录以及本轮 P1-01 修改的 `backend/apps/common/admin.py`、`backend/apps/inventory/models.py`、`backend/apps/inventory/views.py`、`backend/apps/inventory/test_master_data_delete.py`、`backend/apps/inventory/migrations/0032_protect_master_data_references.py`、`backend/apps/workflow/models.py`、`backend/apps/workflow/migrations/0023_protect_master_data_references.py`；NUC SSH 仍认证失败，实际运行 release **未复核**。
- **代码实现**：已将仓库、库位、资产/耗材归属、申请目标仓库/库位、库存流水仓库和盘点观察/调整历史的删除策略收紧为 `PROTECT`。通用 admin 删除在同一事务写入删除前快照并保留真实对象 ID，受保护删除回滚且不写孤儿审计，批量删除失败整体回滚。API 原有引用检查继续保留并与模型层兜底配合。
- **自动化通过**：专项 `apps.inventory.test_master_data_delete` **11/11**；Django 全量 **807/807 通过，8 项 PostgreSQL 专项跳过**，使用工作区受控临时目录 `runtime-data/review-tmp`；`manage.py check`、`makemigrations --check --dry-run` 和 `git diff --check` 通过。首次全量运行因 Windows 默认临时目录 ACL 失败，切换受控目录后重跑通过。
- **现场验收和部署**：未执行。未连接 NUC、未执行 PostgreSQL 数据迁移、未修改生产数据库或配置。真实 PostgreSQL 外键迁移、既有引用盘点、部署快照、健康检查和回滚验证仍待后续授权与现场连接。
- **问题状态**：P1-01 的代码与 SQLite 自动化验收完成，现场/部署状态仍未完成。P2-13、P2-06、P2-09、P2-04 及待业务确认项按 M19 原顺序保留。

### 2026-11-23 M19 P2-13 回滚兼容门禁

- **代码实现**：安装脚本在迁移后记录迁移检查标记和状态摘要；回滚脚本在备份和切换前检查目标 release 元数据，并执行目标代码的只读 `showmigrations --plan`。缺少元数据或存在未应用迁移时默认停止，人工确认后可显式使用 `--allow-unknown-schema`。
- **自动化通过**：部署安全专项 **13/13**，`bash -n deploy/ubuntu22/install.sh` 和 `bash -n deploy/ubuntu22/rollback.sh` 通过，`git diff --check` 通过。
- **现场验收和部署**：未执行。真实 PostgreSQL 迁移、回切兼容性、数据库恢复和 NUC 部署仍未验证。

### 2026-11-23 M19 P2-06 发布安全门禁

- **代码实现**：`check-all.sh` 已纳入部署目录 Bash 语法、Bandit 和 pip-audit，工具缺失默认失败，仅显式参数允许平台豁免。`security-check.sh` 对 pip-audit 缺失或漏洞改为阻断，测试临时目录使用唯一目录并退出清理。
- **自动化通过**：部署安全专项 **14/14**，`bash -n` 已覆盖 install、rollback、check-all、security-check 等脚本，`git diff --check` 通过。
- **环境记录**：在 Windows 调用 Bash 运行 `check-all.sh --fast --allow-missing-security-tools` 时，系统 Python/路径与本机 Django 虚拟环境不一致，`manage.py check`、迁移漂移和 pip check 失败。该命令结果记为环境失败，未作为发布通过证据。
- **现场验收和部署**：未执行。NUC 安全巡检、Bandit、pip-audit 和发布门禁现场结果仍待连接服务器后复核。

### 2026-11-23 M19 P2-09 SQLite 一次性导入临时文件

- **代码实现**：`--import-sqlite` 使用数据目录下的私有临时目录，目录 700、导出文件 600，失败路径由发布 trap 清理，成功导入后立即删除，不再写固定 `/tmp` 文件。
- **自动化通过**：部署安全专项新增导入文件生命周期检查，当前专项 **15/15**，Bash 语法和差异检查通过。
- **现场验收和部署**：未执行。真实首次部署、PostgreSQL 导入、进程中断和恢复演练仍待验证。

本文档是项目当前状态的唯一事实来源。旧计划、旧验收报告和聊天记录不得覆盖这里的结论。

### 2026-11-23 第一轮全景审查：代码地图与本机回归

- **版本三态**：本轮最终读取的 Git 已提交基线为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本轮新增审查记录为 `REVIEW_REPORT_1.md`，状态和变更记录也有本轮审查摘要；NUC SSH 返回 `Permission denied (publickey,password)`，实际运行 release **未复核**。
- **本机回归**：Python 3.12.10；Django 全量 **797/797 通过，8 项跳过**；`manage.py check`、迁移漂移、`pip check`、`git diff --check` 和 JavaScript 语法检查通过。本机 HTTP `/health/`、`/login/`、`/sw.js` 返回 200。
- **环境风险**：开发服务器提示本机 SQLite 有 19 个未应用迁移，未执行迁移。`check --deploy` 报告 6 项本地开发配置警告。Bandit、pip-audit 和 WSL shell 语法检查因当前环境不可用未执行。
- **本轮审查结论**：未发现新增可证实 P0。优先待处理为主数据删除的模型层 `CASCADE/SET_NULL` 仍可绕过 ViewSet 守卫、报表和供应商路径全量物化、`check-all.sh` 未纳入 shell 与安全门禁、开发环境示例误用于生产的配置风险。详细调用链、证据等级、六角色意见和下一步见 `REVIEW_REPORT_1.md`。
- **状态独立性**：代码实现、自动化通过、现场验收、部署仍分别记录。本轮只完成代码审查和本机自动化，未完成 NUC 部署、PostgreSQL 专项、真实浏览器、iPhone、DS3678、ZT610 或恢复演练。

### 2026-11-23 第二轮复核：删除跨入口与报表容量

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本轮未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`，均属于审查记录；NUC SSH 认证失败，实际运行 release **未复核**。
- **专项测试**：`apps.inventory.test_master_data_delete` 与 `apps.common.test_deployment_security` 共 **16/16 通过**。测试数据库为临时 SQLite，已销毁。
- **新增证据**：`WarehouseAdmin`、`LocationAdmin` 未覆盖删除方法，继承通用 admin 直接删除行为；模型元数据确认 `Location.warehouse=CASCADE`、`Asset.warehouse/location=SET_NULL`。API 守卫不能覆盖 admin 或直接 ORM 路径，P1-02 风险升级为跨入口未关闭。
- **容量复核**：供应商价格、供应商对账和呆滞库存存在全量 Python 物化，呆滞库存分页发生在物化之后；采购明细没有供应商/日期组合索引。当前仅有代码证据，未执行 PostgreSQL 大数据量压测，继续列为 P2-04。
- **代码实现 / 自动化通过 / 现场验收 / 部署**：本轮没有修改业务代码，专项自动化通过，现场验收和部署未执行。

### 2026-11-23 第三轮复核：admin 删除实证与查询索引

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **P1 实证**：临时 Django 测试数据库执行 admin `WarehouseAdmin.delete_model()` 探针 **1/1 通过**。在用仓库被删除，库位因 `CASCADE` 被删除，资产仓库和库位因 `SET_NULL` 被清空，并生成 `warehouse_admin_deleted` 审计记录。临时测试文件已删除。
- **容量证据**：`PurchaseOrderLine` 和 `PurchaseReceiptLine` 没有自定义索引；供应商价格和对账 SQL 包含跨表连接、订单状态过滤和排序，视图仍做全量 Python 物化。尚未执行 PostgreSQL `EXPLAIN ANALYZE` 或压力测试。
- **状态独立性**：本轮没有业务代码实现，探针自动化通过，现场验收和部署未执行。P1-01 升级为已实证跨入口数据完整性缺陷，P2-04 保持待 PostgreSQL 容量验证。

### 2026-11-23 第四轮复核：发布门禁、迁移链与回滚边界

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **迁移状态**：当前本机 SQLite `showmigrations --plan` 显示 **19 个未应用迁移**。迁移目录共 87 个文件，静态发现 18 个 `RunPython` 数据迁移，部分逆向为空操作。未执行迁移。
- **发布门禁**：`check-all.sh` 未执行 shell 语法、Bandit、pip-audit，也不清理 `.test-run-temp`；`security-check.sh` 对缺少或发现 pip-audit 问题只告警。P2-06 证据升级。
- **一次性导入**：`install.sh --import-sqlite` 仍将完整 dump 写入固定 `/tmp/after-sales-warehouse-initial.json`，失败清理和权限隔离不足，P2-09 证据升级。
- **回滚边界**：`rollback.sh` 只切换代码 release，不回滚 PostgreSQL。迁移后的旧代码兼容性没有自动检查，新增 P2-13。
- **代码实现 / 自动化通过 / 现场验收 / 部署**：本轮没有修改业务代码，完成只读静态复核，Linux/PostgreSQL/NUC/设备验收和部署均未执行。

### 2026-11-23 第五轮复核：空库迁移链演练

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **隔离演练**：新建临时 SQLite 数据库执行完整 `migrate --noinput` 和 `manage.py check`，迁移链应用成功，system check 无问题。临时数据库和探针文件已删除。
- **清理记录**：探针第一次因 Django 连接尚未关闭而清理失败，进程退出后完成手动清理。该环境性事件不影响迁移成功结论。
- **边界**：空 SQLite 通过不能替代真实 PostgreSQL 数据升级、锁等待、失败恢复和代码回切验证，P2-13 保持未关闭。

### 2026-11-23 第六轮复核：PostgreSQL 隔离验收准备

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **PostgreSQL 能力**：本机没有 `psql`、`pg_dump`、`pg_isready`、Docker 或 Podman。PostgreSQL 专项执行结果为 **8/8 skipped**，原因是当前连接为 SQLite。
- **脚本边界**：`run-concurrency-tests.sh` 已静态确认只允许 PostgreSQL、使用 `test_<DB_NAME>`、临时授予并恢复 `CREATEDB`、退出清理测试库。未在现场执行。
- **验收矩阵**：已在 `REVIEW_REPORT_1.md` 建立 release-info、快照、迁移、并发、业务冒烟、恢复、回切和服务健康的执行顺序与通过标准。
- **状态独立性**：本轮没有业务代码实现，测试明确跳过，现场验收和部署未执行。

### 2026-11-23 第七轮复核：页面鉴权与响应安全边界

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **鉴权矩阵**：页面业务入口均有 `login_required` 或 `role_required`，公开入口限定为登录、账号申请、健康检查和 Service Worker。资产 API 普通登录用户使用最小公共序列化器，生命周期接口要求仓库业务权限。
- **响应安全**：缓存、CSP、Permissions Policy、Service Worker 和离线清理专项 **10/10 通过**。退出登录仅 POST 执行状态变化。
- **新增 P2-14**：CSP 仍使用 `script-src/style-src 'unsafe-inline'`，页面存在大量内联脚本和样式，需后续按页面迁移到 nonce/hash 或静态资源。
- **状态独立性**：本轮未修改业务代码，自动化专项通过，真实浏览器、现场验收和部署未执行。

### 2026-11-23 第八轮复核：媒体、附件和上传边界

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **附件边界**：归档附件由 Nginx 拦截直链并经 Django 仓库业务权限下载，图片和通用附件校验逻辑已覆盖。
- **资产照片风险**：普通 `/media/` 仍由 Nginx 直接映射，`AssetPublicSerializer` 对已登录用户返回 `photo_url`。P2-10 升级为资产照片访问边界待业务确认。
- **上传专项**：第一次运行受 Windows 临时目录 ACL 影响，改用 `runtime-data/review-tmp` 后同一专项 **63/63 通过**。后端和 Nginx 上限为 100 MB，但页面仍写 20 MB。
- **新增 P2-15**：`asset_lifecycle.html` 上传提示与实际 100 MB 限制不一致。
- **状态独立性**：本轮没有业务代码实现，专项重跑通过，生产媒体、NUC、浏览器和部署验收未执行。

### 2026-11-23 第九轮复核：敏感信息与第三方通知

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **敏感字段**：令牌只保存哈希，安全审计使用摘要，DingTalk access token 不写日志。本轮文档敏感模式扫描未发现密码、Token、Secret 或完整 Webhook。
- **待确认 P2-16**：账号申请通知正文包含完整姓名、手机号和申请说明，站内通知与 DingTalk outbox 共用正文，个人通知失败时可能回退到群通知。需确认群内是否允许完整手机号外发。
- **状态独立性**：本轮未修改业务代码、未发送第三方通知、未连接 NUC 或部署。

### 2026-11-23 第十轮复核：问题去重与修复优先级

- **版本三态**：Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`；本地未提交仍为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md`；NUC SSH 认证失败，实际运行 release **未复核**。
- **当前有效问题**：P1-01 已完成本地代码和 SQLite 自动化修复，现场 PostgreSQL/部署仍待验证；P2-04 容量、P2-06 发布门禁、P2-09 导入临时文件、P2-10 媒体边界、P2-13 回滚兼容、P2-14 CSP、P2-15 上传文案、P2-16 通知外发仍按 `REVIEW_REPORT_1.md` 第 19 节登记。
- **优先级**：先完成 P1-01 的 PostgreSQL/部署验收，再处理 P2-13/P2-06/P2-09，随后 P2-04，最后处理政策确认和体验加固项。
- **总体状态**：本轮已完成 P1-01 业务代码实现和本机自动化，当前没有新增可证实 P0。PostgreSQL、NUC、浏览器、设备和部署仍未验证。

### 2026-11-20 第十二轮复盘：4 个 P1 + 3 个 P2 已修（代码/自动化完成，未部署）

- **版本三态**：Git 已提交基线 `a2fd9a7`；本地未提交工作区包含本轮修复与文档
  （`CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`，以及 9 个代码/测试/模板/迁移文件）；
  NUC 实际运行 release **未复核**（本轮按约束未连接生产环境）。

- **测试**：全量 **797 通过 / 8 跳过**（本轮新增 21 例；其余为并发改动新增用例），`manage.py check` 无问题，
  `makemigrations --check` 无漂移，23 个部署脚本 `bash -n` 全部通过。
  PostgreSQL 并发专项与真实设备/浏览器**仍未执行**。

- **本轮已修的 4 个 P1（全部有探针复现证据）**：
  1. **P1-01 成本导出越权** —— `transaction_export` 数据行无条件写 `cost_amount`，
     与表头权限分支不一致；已改为共用 `show_financials`。原测试只测表头不测数据，
     新增 3 例反向断言（数值不出现 + 列数一致 + 财务仍可见）。
  2. **P1-02 删除主数据致资产静默脱仓** —— 删仓库时 `Asset.warehouse/location` 被
     `SET_NULL`、库位被 `CASCADE` 物理删除且无履历。已在 ViewSet 层加引用检查返回 400；
     **模型层 `CASCADE→PROTECT` 迁移仍未做**。
  3. **P1-03 采购到货重放** —— 新增 `client_operation_id` 幂等键（迁移 `0022` +
     条件唯一约束），重放返回既有回执；表单每次渲染生成 UUID。新增 4 例。
  4. **P1-04 恢复演练脚本失效** —— 脚本仍要求已废弃的 `media.tar.gz`，演练必然失败；
     已按 `media-snapshots` 新格式重写并加 6 例静态防回退。**未在真实 Linux+PG 上实跑**。

- **本轮已修的 3 个 P2**：P2-01 履历 API 越权（改 `IsWarehouseOperator`）、
  P2-02 停用仓库/库位仍可写入（新增 `_assert_active_location`，只拦新写入不卡在途单）、
  P2-03 `ProtectedError` 抛 500 且留孤儿审计（改事务化 + 转 400 + 写删除前快照）。

- **当前有效问题登记**：当前编号以 2026-11-23 第十轮统一问题表为准。历史轮次曾将主数据删除模型层风险记为 P1-02，当前统一登记为 P1-01。
  1. **P1-01** 主数据删除跨 admin/API/ORM 边界问题已完成本地模型与 admin 修复和 11 项专项回归，真实 PostgreSQL 迁移与部署验收待完成。
  2. **P2-04** 报表和供应商路径全量 Python 物化，尚无 PostgreSQL 容量基线。
  3. **P2-06** 发布安全门禁未严格阻断 shell、Bandit、pip-audit 和工具缺失。
  4. **P2-09** SQLite 一次性导入使用固定 `/tmp` 文件，失败清理和权限隔离不足。
  5. **P2-10** 资产照片与归档附件的媒体授权边界不一致，待业务确认。
  6. **P2-13** 已加入迁移元数据和回滚前只读迁移门禁，真实 PostgreSQL 回切与恢复演练仍待验证。
  7. **P2-14** CSP 仍允许 `unsafe-inline`。
  8. **P2-15** 上传页面提示 20 MB，实际限制为 100 MB。
  9. **P2-16** 账号申请通知可能把手机号和申请说明外发到 DingTalk 群，待业务确认。

- **未执行（不得视为通过）**：PostgreSQL 并发 8 项、真实浏览器 E2E、DS3678 扫码、
  ZT610 60×40 mm 走纸、NUC 部署与 `/health/` 冒烟、恢复演练实跑。
  代码实现 / 自动化通过 / 现场验收 / 部署 是四个独立状态，本轮只有前两项完成。

### 2026-11-20 依赖安全、权限收紧与运维加固（上一轮）

- **版本三态**：Git 已提交基线 `970a368`（本轮修复已提交）；工作区干净；
  NUC 实际运行 release `20261120-154736-970a368`（`working_tree_dirty=false`，已核实）。

- **测试**：全量 **773/773 通过**（跳过 8 项，均为 SQLite 不适用的 PostgreSQL 专项）；
  PostgreSQL 并发测试 **6/6 通过**（在 NUC 临时建独立库执行，测试后撤销权限并删库，
  生产库全程未动）；`manage.py check` 无问题；`makemigrations --check` 无漂移；`pip check` 无冲突。

- **已修复并部署**：P1-01 依赖漏洞（本机 121 → 0，生产 65 → 0）、P1-02 代借持有人归属、
  P1-03 单据场景约束、P2-04 宽泛异常收窄、P2-07 V2 导入不可撤销提示、P2-03 views 拆包（方案 B）、
  终态校正权限收紧（仅 superuser）、表单语义修正（接收人单位/出售隐藏用途/供应商下拉）。

- **运维状态（NUC）**：Cloudflare 隧道与自愈 timer 已停止并禁用（当前全内网访问）；
  ufw 已启用，12 条白名单规则（192.168.20.0/22、192.168.24.0/23、192.168.123.0/24 × 22/80/4000/17001）；
  blue-client / release-console / s1-ota-agent 已停止并禁用；journald 留存 30 天；logrotate 已加固。
  NUC 现以 `192.168.123.101`（网线调试口）为主要连接地址。

- **代码质量复盘结论（未发现新增缺陷）**：workflow/services.py 的事务边界、状态机（7 处）、
  预占释放（4 处场景）、加锁顺序均正确；inventory 12 个 + workflow 3 个唯一约束覆盖充分；
  性能实测无 N+1（库存 19 次查询/27ms、工作台 21 次/13ms，首屏 631ms 全为模板首次编译）；
  通知 outbox 有指数退避 + 锁超时 + 异常隔离；离线提交有三层幂等保护；登录限流实测有效。

- **待处理（需用户决定或排期）**：
  1. 4 个安全更新待装（`python3-jwt` 等）且系统提示需重启（内核/glibc 已更新未生效），需停机窗口。
  2. SSH `PasswordAuthentication yes` 为当前最大风险点，改密钥登录需先配好密钥避免锁死。
  3. `PRIVILEGED_MAX_ACTIVE_SESSIONS` 默认 2（非 3），按会话数而非设备/IP 计；
     多设备办公场景可能偏紧，是否调整待确认。
  4. 导入表用「持有人单位」、表单用「接收人单位」，同一字段两套叫法，是否统一待确认。
  5. 历史单据为实时渲染：改表单后样式会变、数据不变；13 个外键实时关联（主数据改名会反映），
     仅 4 个字段有文本快照。若需冻结历史样式需另加机制。


更新时间：2026-11-18

本文件是项目当前状态的唯一事实来源。旧计划、旧验收报告和聊天记录不得覆盖这里的结论。

### 2026-11-18 第十轮配置、PWA 与页面权限复核

- 版本三态：Git 基线已由并发提交推进为 `3612d5da1714d439399e869c673e994ca0213887`（接收人字段可见性修复）；本轮文档为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`，另有并发产生、非本轮的 `backend/apps/common/test_recipient_field_visibility.py`、`backend/templates/quick_inventory_process.html`、`backend/templates/request_create.html`、`backend/templates/web_base.html` 未提交改动；NUC SSH 认证失败，实际 release 记为“未复核”。
- 缓存头、PWA Service Worker、账号评审/移动页面及并发产生的接收人字段回归 **33/33 通过**；Ubuntu 发布脚本 `bash -n` 全部通过。
- 当前工作区已验证 `warehouse_setup` 两入口包含 `@login_required`、settings 已有 `LOGGING`、PostgreSQL 已配置连接复用/健康检查、Service Worker 版本按静态资源 mtime 生成。
- 新增 **P2-12**：`backend/.env.example` 默认 `DEBUG=1`、开发密钥、`ALLOWED_HOSTS=*`、全 CORS，手工部署直接复制可能形成公网安全误配置；正式 `install.sh` 路径会生成更安全的 app.env，但示例和 README 仍需 fail-closed 约束。
- `check --deploy` 在 `USE_HTTPS=0` 的 Cloudflare 边缘 TLS/源站 HTTP 架构下出现 4 项预期 HTTPS 警告；需保留架构说明，不能把警告误当作生产故障或擅自打开源站重定向。
- 本轮未修改业务代码、未访问业务数据库、未部署；详细证据见 `REVIEW_REPORT.md` 第十轮章节。

### 2026-11-18 第九轮本地容量与 PostgreSQL 专项复核

- 版本三态：Git 基线仍为 `b79551bb55bea3c1251363002c755f2bc82095b4`；本轮文档为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`，另有并发产生的三个非本轮未提交文件；NUC SSH 认证失败，实际 release 记为“未复核”。
- 使用一次性 SQLite E2E 数据库执行 `run_local_capacity.ps1 -RequestsPerStep 40 -ConcurrencyRamp 1,4,8,12`：8 条登录态只读路由共 160 次请求全部 200，错误率 0；12 并发 P95 366.19ms、P99 441.81ms。该结果仅代表最小数据集本机基线，不能解除 P2-11。
- PostgreSQL 并发/审计专项 **8 项全部跳过**（当前数据库为 SQLite），未执行 NUC PostgreSQL 验证；Playwright 运行时缺失，本轮未执行浏览器 E2E/UI 审计。
- 本轮未修改业务代码、未访问业务数据库、未部署；容量临时数据库、报告和日志已清理。详见 `REVIEW_REPORT.md` 第九轮章节。

### 2026-11-18 第八轮报表、导出与容量复核

- 版本三态：Git 已提交基线 `b79551bb55bea3c1251363002c755f2bc82095b4`；本轮审查文档为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`，另有并发产生、非本轮的 `backend/templates/quick_inventory_process.html`、`backend/templates/request_create.html`、`backend/apps/common/test_recipient_field_visibility.py` 未提交改动；本轮 SSH 认证失败，NUC 实际运行 release 记为“未复核”。
- 报表、导出、审计归档和 workflow 相关专项在 `TEMP/TMP=runtime-data` 下执行 **152/152 通过，159.665 秒**，测试库已销毁，system check 无问题。系统临时目录不可写时曾出现 4 项临时文件错误，改用可写目录后完整专项通过。
- 新增 **P1-12**：`transaction_export()` 无财务权限时虽隐藏成本表头，但仍把 `cost_amount` 写入每行；临时测试已复现非财务账号读取成本值。修复前不得向非财务角色发放该 CSV。
- 新增 **P2-11**：供应商价格历史、报表总览、呆滞库存页面/导出存在无上限 Python 全量物化，且呆滞库存分页发生在物化之后；大数据量性能风险尚未压测。
- 本轮未修改业务代码、未访问业务数据库、未部署；临时测试文件和目录已清理。详细证据、路径、修复和验证方法见 `REVIEW_REPORT.md` 第八轮章节。

### 2026-11-18 第三轮权限与核心 API 复核

- 版本三态：Git 已提交基线 `b79551bb55bea3c1251363002c755f2bc82095b4`；本地未提交仅为 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md` 审查文档；本轮 SSH 无法重新连接 NUC，当前 release 记为“未复核”。此前本会话成功读取的最后 release 为 `20261118-122912-b79551b`，source 与本地 HEAD 一致，但不替代本轮现场复核。
- 权限/API 专项：30/30 通过（14.731 秒）。覆盖 workflow 审批/拒绝/执行、批量审批、inventory 成本字段和照片上传、资产写权限、账号治理、`/me` 谓词一致性及移动端权限相关测试；测试库已销毁。
- 静态复核：申请和资产 API 禁用硬删除；核心动作按审批/执行/录入权限分离；资产、耗材、出入库流水成本字段按财务权限过滤；嵌套申请创建使用事务。未发现新增 P0/P1。
- 未完成：PostgreSQL 8 项并发/审计专项、真实浏览器 E2E、DS3678/ZT610 设备验收；P2-06 安全门禁未纳入 `check-all.sh` 仍待修复。详细证据见 `REVIEW_REPORT.md` 第三轮章节。

### 2026-11-18 第四轮通知与附件访问复核

- 通知只读 API 按收件人隔离；钉钉 outbox 的幂等、回滚、重试、失败和 stale claim 专项 `8/8` 通过。
- 生命周期附件已由 Nginx 拦截直链并经 Django 授权下载；文件名路径经过白名单化。
- 新增待确认 P2 候选：附件下载目前只检查仓库业务权限，`invoice`/费用凭证类别未与财务权限关联。现有测试只覆盖仓库操作员成功、普通员工拒绝；需业务确认后再决定是否纳入开发计划。

### 2026-11-18 第五轮部署与恢复路径复核

- 新增 **P1-09**：`backup.sh` 已使用 `media-snapshots/<timestamp>/` 硬链接增量格式，但 `restore-drill.sh` 仍检查不存在的 `media.tar.gz`，当前恢复演练会在数据库恢复前失败。历史恢复演练结论不能覆盖格式切换后的脚本状态。
- 新增 **P2-08**：`check-all.sh` 创建 `.test-run-temp` 后没有退出清理，重复运行可能积累测试产物；本轮已手动清理本地产物。
- 新增 **P2-09**：`install.sh --import-sqlite` 将完整业务 dump 写到固定 `/tmp/after-sales-warehouse-initial.json`，没有显式 600 权限和失败清理；需在任何一次性导入前修复并验证。
- 新增 **P2-10**：Workflow API 和离线同步只校验目标库位与仓库的归属关系，未在服务层阻断停用仓库/库位；需补充 API、离线和 Web 反向用例。
- 新增 **P1-11**：采购到货登记没有幂等键；响应丢失后重试可能重复生成到货单、入库单和库存流水。现有测试未覆盖成功后重放。
- 本轮未连接 NUC、未执行数据库恢复、未修改部署脚本；P1-09 应优先于现场恢复验收处理。

### 2026-11-18 第六轮 workflow 与离线流程回归

- `OfflineOperationTests` + `WorkflowApiTests`：**35/35 通过，24.577 秒**；覆盖离线幂等、跨账号、payload 篡改、冲突回滚、拒绝和重复复核。
- 静态确认 workflow 服务使用事务和稳定锁顺序；SQLite 不能替代 PostgreSQL 并发验证。
- 停用仓库/库位写入仍是 P2-10，真实浏览器、断网恢复、并发重复提交和设备现场验收均未执行。

### 2026-11-18 第七轮采购、盘点、导入与审计专项

- 专项 37 项通过、2 项跳过（共 39 项），43.159 秒；跳过项为 PostgreSQL 原始 SQL 审计触发器测试。
- 覆盖盘点、采购部分到货、导入撤销和应用层审计不可变性；采购到货重放幂等仍是 P1-11，真实 PostgreSQL 和浏览器重试未验证。

### 2026-11-18 第一轮全量审查与基础回归

- 本轮只读审查未访问业务数据库、未提交业务表单、未部署、未修改服务器或生产配置；NUC 仅读取 `/opt/after-sales-warehouse/current/release-info`。
- 版本三态：Git 已提交基线当前为 `b79551bb55bea3c1251363002c755f2bc82095b4`（P2-01 修复提交，由其他并发执行者产生）；本地当前仅有三个审查文档未提交；NUC 实际运行 `20261118-110008-c822551`（source `c82255183906…`，`working_tree_dirty=false`）。本地 HEAD 与 NUC release 不一致，不能混称为“当前版本”。
- 本机基础回归：Python 3.12.10、`pip check`、`manage.py check`、迁移漂移和 JavaScript 语法通过；Django 全量 **593/593 通过，8 项 PostgreSQL 专项跳过**，测试数据库已销毁。
- 本轮未执行：Linux Bash 脚本语法（Windows 无 Bash/WSL）、Bandit、pip-audit、目标 PostgreSQL 并发、真实浏览器/DS3678/ZT610 设备验收。Python `compileall` 因现有 `__pycache__` ACL 拒绝，记录为环境性阻塞。
- 新增/确认待办：P2-01 已在本地提交 `b79551b` 并通过自动化，但尚未部署/现场验收；P2-02 超大模块文件维护成本；P2-03 Linux 发布与安全工具门禁未纳入一键脚本；P2-04 PostgreSQL/浏览器/硬件闭环未验证；P2-05 本地 HEAD 与 NUC release 追溯不一致。详细证据、调用链和六视角评审见 `REVIEW_REPORT.md` 顶部 2026-11-18 章节。

### 2026-11-18 第二轮安全门禁复核

- WSL 对 `deploy/ubuntu22/*.sh` 完成 `bash -n`：22/22 通过。
- Bandit 1.9.4 扫描生产 Python（排除测试/迁移）：中危/高危 0；低危启发式 12 项，均为字段名、运算符、空 token、开发占位值等误报候选。
- pip-audit 针对 `backend/requirements-lock.txt` 使用 `--no-deps --disable-pip`：`No known vulnerabilities found`。扫描器安装在 WSL 临时目录，未改仓库和锁文件。
- PostgreSQL 8 项并发/审计专项仍未执行；本机无 PostgreSQL 客户端，且本轮不连接 NUC 业务库。
- 新增 P2-06：`deploy/ubuntu22/check-all.sh` 未纳入 shell 语法、Bandit、pip-audit，当前安全门禁依靠人工补跑。详见 `REVIEW_REPORT.md` 第二轮章节。
- 本轮中途发现其他工作区新增 P2-01 修复改动，随后由其他并发执行者提交为 `b79551b`：`code_services.py`、`v2_import_services.py` 和 `test_asset_code_prefix_consistency.py`；专项 `10/10`、全量 `603/603` 通过（8 项 PostgreSQL 跳过）。该提交未部署，不能作为 NUC 验收结论。

### 2026-11-17 当前工作区复盘与 P1 修复

- 本轮未连接 NUC、未部署、未修改任何服务器数据库；文档中的 NUC release 和公网健康记录均未在本轮复核。
- 本机使用仓库 `.venv` 和可写临时目录完成回归：Django `551/551` 通过、跳过 8 项 PostgreSQL 专项；`manage.py check`、迁移漂移、`pip check` 和 `git diff --check` 通过。
- 当前工作区仍为脏工作区（70 个文件级别变更），不能直接视为候选发布版本。
- 本轮已修复 P1-01/P1-02：申请明细主单字段改为只读，未实现的申请单嵌套明细更新明确返回校验错误；V2/V3 全量基准导入在写入前阻断仍有活动状态、预占、维修、耗材持有余额或未归还借用的缺失对象。
- P1-03（工作区、Git 基线与 NUC release 的候选版本追溯）仍未完成；本轮未构建候选包、未部署。详细证据、测试记录、六视角评审和下一步顺序见 `REVIEW_REPORT.md` 顶部的 2026-11-17 章节。

### 2026-11-17 P1 修复验证

- 专项测试：`27/27` 通过，覆盖伪造嵌套 `request` 不得跨申请单写入、草稿携带 `lines` 的 PUT/PATCH 明确拒绝、资产基准缺失状态/预占/维修阻断、耗材基准缺失且有个人借用余额/未归还借用阻断，以及失败时不产生部分写入。
- 自动化全量：`551/551` 通过，跳过 8 项 PostgreSQL 专项；未执行 NUC PostgreSQL 并发、真实浏览器/设备、ZT610 真机走纸和公网链路复核。
- 代码实现、自动化通过、现场验收、部署仍分别记录；本轮只有前两项完成。

## Current phase

- 阶段：内部调试，准备仓库小范围试运行。
- 交付：**响应式 Web/PWA（唯一客户端）**。微信小程序已于 2026-11-17 归档暂停，代码在 `docs/archive/miniprogram-paused-20261117/`。
- 本机：Win11 开发与自动化测试。
- NUC：Ubuntu 22.04，`192.168.123.101`；公网入口 `https://cloudwarehouse.dpdns.org`（Cloudflare Tunnel）。

### 2026-11-17 战略决策：放弃小程序，专做 PWA

用户确认仓管员以 **iPhone 为主**、**离线本期不做**。依据：

1. 六大需求（快速出入库、扫码连续处理、待审批与待执行、低库存异常提醒、盘点扫码、维修状态更新）在 Web/PWA 端**全部已实现并现场验证**。
2. 双份客户端维护成本高：业务规则快速演进期（自批禁止、接收人选人、人员显示）已多次出现两端不同步。
3. 小程序离线能力并不优于 PWA；PWA 已有 IndexedDB 离线队列。
4. 小程序真机运行时校验未完成。

**已识别的 PWA 移动端缺陷**：iPhone 缺 PNG 图标与 iOS 专用 meta（P2-01，见 `REVIEW_REPORT.md`）。

### 2026-11-17 iPhone 使用方式（已确认）

- **必须用 Safari「添加到主屏幕」**。iOS 上 Chrome/Firefox 都被强制使用 WebKit，且只有 Safari 支持 `display: standalone` 独立窗口；Chrome iOS 只能加成带地址栏的书签，不是真正的 PWA。
- 当前 release `20261117-151147-1901c85-dirty-9c91f97b8cef` 已补 PNG 图标与 iOS meta，公网 `https://cloudwarehouse.dpdns.org` 实测 200。

### 2026-11-17 网络与延迟基线（已实测）

- **出口**：cloudflared 走公司 WiFi（`wlp89s0` → `192.168.20.1`）。外接 USB 网卡已拔除，移动热点方案**最终未采用**（运营商封 7844 端口，隧道建不起来）。
- **强制 IPv4**：`cloudflared.service` 使用 `RestrictAddressFamilies=AF_INET AF_UNIX`。此前隧道同时走外接网卡的 IPv6 出口（RTT 370ms、抖动到 438ms），而 IPv4 边缘仅 198ms。
- **延迟实测**：内网 `/login/` **11ms**；公网 `/login/` 从 **3.17s → 1.07–1.5s**。
- **1.5s 是 Cloudflare 免费版走美国节点（lax/sjc）的物理下限**。用户已明确**拒绝 frp 与公网 IP 方案**，接受当前延迟。
- 数据库：NUC 使用 PostgreSQL；部署更新必须保留现有数据。
- 版本口径：Git 基线、本地未提交工作区和 NUC release 分开记录；不得将三者混称为“当前版本”。

## Rapid handoff

这是接力开发 agent 的第一阅读区。当前系统是内部调试中的小型售后仓储 Web/PWA，核心闭环为“资产/耗材资料 → 申请 → 审批 → 预占 → 出入库/归还 → 生命周期与审计”；同时支持快速出入库、维修、盘点、采购、V3 Excel 导入导出、报表、站内通知和钉钉群/个人通知队列。

### 公网入口（2026-11-17 已打通并验收）

- 域名：`https://cloudwarehouse.dpdns.org`（DigitalPlat 免费二级域名，到期 2027-11-16，可免费续期）
- 接入方式：**Cloudflare Named Tunnel**（隧道名 `warehouse-nuc`，Tunnel ID `6947d625-1521-4d94-bc82-8df00c8fc0f6`）
- 服务器端：systemd 服务 `cloudflared.service`，`User=cloudflared`，token 存于 `/etc/cloudflared/tunnel-token`（`root:cloudflared 640`），开机自启已启用
- 出口：**走公司 WiFi**（不是移动热点）。实测公司网 7844 和 443 均通，而移动热点封 7844
- 路由：Cloudflare 面板 `Published application` → `cloudwarehouse.dpdns.org` → `http://localhost:80`
- 验收：手机 4G 流量可正常打开登录页（用户实测确认）；`/login/` 200、`/health/` 返回数据库正常
- **公司内网仍走原内网地址访问**，不需要绕公网

> 关键坑（已踩过）：
> 1. `Route added successfully` 但 DNS **不会**自动生成 CNAME；必须手工加，且子域 zone 的「名称」要填 `@`（填 `cloudwarehouse` 会变成 `cloudwarehouse.cloudwarehouse.dpdns.org`）
> 2. `tunnel run` **不接受** `--edge-ip-version` 和 `--loglevel`，传入会打印帮助后以 0 退出，表现为服务无限重启
> 3. `--no-autoupdate` 是全局 flag，必须在 `tunnel` 之前
> 4. token 文件若为 `600 root:root`，`cloudflared` 用户读不到，服务起不来
> 5. 用 token 启动的隧道使用**远程托管配置**，本地 `/etc/cloudflared/config.yml` 会被忽略（已改名为 `.unused-remote-config`）
> 6. 服务器自身解析该域名会失败（公司内网 DNS 不解析），必须用 `--resolve` 绕过才能本地自测

### Three version states

- Git 已提交基线：`b79551bb55bea3c1251363002c755f2bc82095b4`，短 revision `b79551b`，包含资产编码前缀规则收敛修复与 10 项专项测试。
- 本地未提交工作区：`CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md` 三个审查文档有未提交改动；业务代码无未提交改动。
- NUC 实际运行 release：本轮 SSH 复核**未复核**；此前本会话成功读取 `20261118-122912-b79551b`，source revision 与本地 HEAD 一致，`working_tree_dirty=false`。部署地址仍为 `192.168.123.101`；本轮未读取数据库、未修改服务器数据。
- 资产编码修复的本机专项和全量自动化已通过；PostgreSQL、浏览器和设备现场验收不因自动化结果而视为完成。

### 2026-11-18 备份恢复演练（旧媒体 tar 格式，历史结果）

此前备份只验证过 `pg_restore --list`（能读目录），**从未验证过真恢复**。本轮完成端到端演练：

| 步骤 | 结果 |
| --- | --- |
| 备份文件哈希校验 | `database.dump: 成功` |
| `pg_restore` 到临时库 | `exit=0`，无错误 |
| 恢复后数据 | 资产 636、用户 4、审计 1096、物品类型 78、库位 25 |
| 真实读取（非只看表存在） | `AST-00000022` / `admin` / `asset_identity_applied` |
| 外键约束 | 193 个 |
| 索引 | 402 个 |
| **审计只增不改触发器** | **10 个随备份一起恢复** |
| 演练库清理 | 已删除，生产库未受影响 |

**历史结论：当时使用的数据库 dump 可恢复，旧媒体 tar 格式的演练通过。** 之后 `backup.sh` 已切换到 `media-snapshots` 硬链接增量格式；当前 `restore-drill.sh` 仍要求 `media.tar.gz`，因此该历史结果不能作为当前恢复链验收。详见本文件顶部 P1-09。

### 项目完整复盘

见 **`PROJECT_REVIEW_20261118.md`**：项目定位、已具备功能、待推进功能、待解决问题、远期规划、近期改动全清单。该文档面向交接与决策，权威进度仍以本文件为准。

### 2026-11-18 待解决问题推进结果

| 原问题 | 结论 |
| --- | --- |
| 7 个页面「按钮在 page-heading + 筛选栏」布局 | **审计后实际只有 3 个**（composite_unit / inventory_conversion / transaction_list），已全部改为独立 `page-actions` 行。其余 9 个带筛选栏的页面此前已规范。 |
| 依赖有公开漏洞 | **已不存在**。用 OSV API 逐个查询 16 个锁定依赖，**0 个已知漏洞**；生产实际版本 Django 5.2.19 / DRF 3.18.3 / Pillow 13.0.0 均已是当前安全版本。`REVIEW_REPORT.md` 中该条记录已过时。 |
| 异机备份缺失 | **机制已建好**。新增 `offsite-backup.sh` + 每日 03:30 timer，支持 rsync / 本地挂载盘 / S3 三种模式。**未配置 `/srv/after-sales-warehouse/offsite.env` 时静默跳过**（实测确认），不阻断主备份。启用只需按脚本头部注释创建该文件，不需要改代码。 |
| 无 CI | **已补一键检查**。新增 `deploy/ubuntu22/check-all.sh`，覆盖 `manage.py check`、迁移漂移、`pip check`、`git diff --check`、工作区状态与全量测试；`--fast` 可跳过测试。WSL 实测通过。 |
| Cloudflare WAF / 速率限制 | **仍需在 Cloudflare 面板操作**，属用户侧配置，代码层无法代劳。 |

**当前 10 个定时任务**（新增异地备份）：钉钉 outbox（每分钟）、健康检查（5 分钟）、预占过期（每小时）、备份（02:16）、一致性检查（03:04）、**异地备份（03:30）**、安全清理（03:45）、借用提醒（09:10）、media 清理（周日 03:43）、低库存周报（周一 09:03）。

> 版本读取注意：权威文件是 `/opt/after-sales-warehouse/current/release-info`（随 release 目录走）。同一目录下的 `/opt/after-sales-warehouse/current-release` 是 2026-11-06 遗留的陈旧文件（仍写着 `release_version=20261106-182713`、`source_revision=bbc187c`），读取它会得到错误版本，不要用它判断当前 release。

### Previous release (已部署，本轮被替换)

- `20261116-174612-1901c85-dirty-14517796e138`，source revision `1901c85`，部署时间 `2026-11-16T17:46:35+08:00`。已由本轮 `...-dirty-bb8da5de8be5` 替换，仍在 `--list` 回滚列表中。

### 2026-11-17 handoff review

- 本轮只完成复盘，没有部署当前工作区，也没有修改 NUC 数据库。此前候选包上传动作在 SSH 密码提示阶段中断，远程 `install.sh` 未开始；当前工作区不能据此声称已部署。
- 此前用绝对可写临时目录执行的本机全量测试为 `452/452`、`OK`、跳过 8 项；该结果对应当时的较小改动集。其他 agent 随后又加入账号、迁移、打印 ZPL 位图、Cloudflare 等改动，因此不得把 `452/452` 作为当前工作区证据；必须重新专项和全量回归。
- 当前工作区新增的 `deploy/ubuntu22/cloudflared-*`、`tunnel-*` 文件只属于待审查配置，尚未证明已安装到 NUC。`install.sh` 当前不会自动安装或启用 cloudflared；域名 `cloudwarehouse.dpdns.org` 只在工作区配置中出现，公网 DNS、Tunnel token、服务状态、`ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` 均未在本轮复核。
- 当前候选包构建脚本已排除 `backend/.test-tmp`、`.review-tmp`、`.test-run-temp` 和根目录 `.review-tmp`；新包仅为 dirty 候选，不是正式 release。

### 2026-11-17 mini program and domain review

- 微信开发者工具登记的 `request合法域名`、`uploadFile合法域名` 和 `downloadFile合法域名` 均为 `https://cloudwarehouse.dpdns.org`，这只证明开发者工具配置完成，不等于小程序代码已经使用该地址，也不等于公网接口从当前网络可达。
- 当前 `miniprogram/` 没有未提交代码变更，Git 最近一次提交仍为 2026-10-08 初始版本；`miniprogram/miniprogram/utils/config.js` 仍将 API 基地址写为 `http://127.0.0.1:8000`，`project.config.json` 仍使用 `touristappid` 且 `urlCheck=false`。因此当前小程序不是可上传的最新正式版本，只能视为早期开发工具客户端。
- 小程序现有功能范围为：Token 登录、资产搜索/扫码、资产详情与照片上传、单件资产借用/领用/归还申请、申请审批/驳回/出入库处理、通知查看。耗材、多资产申请、采购、盘点、维修、调拨、报表和管理配置仍仅在 Web/PWA 中提供；这属于当前客户端边界，不得宣传为完整系统。
- 小程序代码的 API 路径与当前后端路由在静态层面基本匹配，JavaScript/JSON 语法检查通过；但当前后端工作区已有 43 个文件级别未提交改动，尚未重新执行全量 Django 回归和小程序运行时联调，不能据此宣布“程序没问题”。
- 本轮从当前 Win11 DNS 查询该域名得到 `192.0.2.1`，HTTPS `/health/` 请求超时；与历史文档中“手机 4G 已验收”的记录不一致。该结果可能受 DNS/网络路径影响，必须用手机 4G 和微信开发者工具分别复核真实 HTTPS 响应，不能只看合法域名列表。
- 微信小程序重新进入候选开发范围前，必须先登记一个独立里程碑：替换 API 地址、确认真实 AppID、打开合法域名校验、完成登录/扫码/申请/审批/上传的真机回归，再决定上传体验版。当前不上传。

### 2026-11-17 M8 execution handoff

- 用户确认使用域名 `https://cloudwarehouse.dpdns.org`，并提供真实 AppID `wx02c8193db5952f39`。
- 手机已实测可访问 `https://cloudwarehouse.dpdns.org/health/`；这是域名入口可达证据，不等于小程序真机业务链路已验收。
- 当前工作区后端以受控临时目录完成全量 Django 回归：**`551/551` 通过**、`8` 项按设计跳过；`manage.py check`、迁移漂移检查、`pip check`、`git diff --check` 均通过（2026-11-17 本轮实测）。
- 首次全量测试在 Windows 默认 TEMP 不可用时出现 `42` 个环境性错误，切换到仓库内受控临时目录后全部通过；不得将首次失败误写为业务失败。
- 小程序已于 2026-11-17 归档暂停；此前已完成真实 AppID 与 HTTPS 域名配置、开发者工具预览和体验版上传，但这些记录只证明当时状态。
- 手机端完整业务链路：公网 `https://cloudwarehouse.dpdns.org/login/` 已实测 200，用户手机 4G 可打开登录页；**iPhone Safari 添加到主屏幕的真实体验仍未验收**（见 P2-01）。
- 本轮后端工作区已部署到 NUC：部署前 PostgreSQL 快照为 `/srv/after-sales-warehouse/backups/pre-deploy-20261117_113812`；无待执行迁移；四项服务（业务、Nginx、PostgreSQL、Cloudflare Tunnel）均为 active；Nginx 通过 `Host: cloudwarehouse.dpdns.org` 的本机健康检查返回 200。
- 部署后补登记 `cloudwarehouse.dpdns.org` 到服务器 `ALLOWED_HOSTS`、CSRF 来源和 Nginx `server_name`；域名接入配置未写入数据库。
- 小程序当前仍只覆盖移动现场资产流程：登录、资产查询/扫码、详情、照片上传、借用/领用/归还申请、审批执行、通知；耗材、多资产申请、采购、盘点、维修、调拨、报表和基础资料维护继续由 Web 端提供。
- admin 在小程序中当前看到的工作台、申请与审批、通知属于设计边界，不代表 admin 权限失效。小程序暂未提供 Web 端的资产维护、快速出入库、耗材、采购、盘点、维修、报表、导入导出和系统设置入口。
- 后续若要增强 admin 移动端，应先单独规划“仓库管理员移动工作台”里程碑，按权限提供快速出入库、审批、库存异常和盘点等高频动作；不直接把 Web 后台全部搬到小程序。

### 性能基线（2026-11-16 实测）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 登录页传输 | 3130 B | 1433 B（gzip） |
| zxing 库 | 395 KB | 103.7 KB（gzip） |
| 站点 logo | 79.7 KB / 566×530 | 16.7 KB / 320×300 |
| 通知轮询间隔 | 30 s（日志 2349 条中 931 条是它） | 120 s，且后台标签不轮询 |

服务端本身不慢：`/login/` 15 ms、`/health/` 12 ms、静态资源 0.3 ms。慢的是**每页要下载的资源体积**。

Nginx 已开 gzip（`deploy/ubuntu22/nginx-after-sales-warehouse.conf`）；Logo 在上传时自动缩到 320px + 128 色量化。原 logo 备份在 `/srv/after-sales-warehouse/media/branding/logo.png.bak-20261116`。

### Latest deployed delta (M16 performance and login message)

本轮改动已随 `20261116-181751-1901c85-dirty-bb8da5de8be5` 部署：

- **Nginx 开启 gzip**：`gzip on` + `gzip_types` 覆盖 HTML/CSS/JS/JSON/SVG。登录页 3130→1433 字节，zxing 库 395 KB→103.7 KB。部署后需 `nginx -t && systemctl reload nginx`。
- **Logo 上传自动压缩**：`settings_forms._shrink_logo()` 把图缩到最长边 320px，RGB 图量化到 128 色；实测 79.7 KB→16.7 KB（降 79%）。服务器上现存 logo 已按同法替换，原图备份为 `logo.png.bak-20261116`。
- **通知轮询降频**：未读数轮询从 30 秒改为 120 秒，且 `document.hidden` 时跳过、切回前台立即补一次。此前访问日志 2349 条请求中 931 条是这一个接口。
- **停用账号登录提示**：改为「该账号已停用，无法登录。如需恢复使用请联系系统管理员。」仅在"用户名+密码都对、账号被停用"时显示；密码错误仍统一提示，不泄露账号是否存在。
- 模板 `login.html` 改为渲染表单实际错误而非硬编码文案；`RateLimitedAuthenticationForm.error_messages` 统一了 `invalid_login`/`inactive` 文案，保持枚举防护。
- 新增测试 `test_disabled_login.py`（4 例）、`test_logo_shrink.py`（3 例）。全量 **`551/551`** 通过，跳过 8 个 PostgreSQL 专项。

### M15 (账号治理，上一轮)

本轮 M15 改动已随 `20261116-174612-1901c85-dirty-14517796e138` 部署到 NUC：

- **账号可停用**：权限页新增「停用/启用」按钮。停用会立即作废该账号全部登录会话、写 `account_disabled` 审计，账号与全部历史引用保留。这是人员离职/调岗的正解，硬删除只适用于确实无任何记录的账号。
- **删除失败文案修正**：原提示"已有受保护的业务记录"误导——实际常常只是一条改名字的审计记录。现改为明确指向「停用」。
- **审计操作人快照**：`OperationAuditLog` 新增 `actor_username` / `actor_display_name`，由 `record_operation` 自动写入，用于展示与追溯。**`actor` 外键保持 PROTECT**：数据库触发器 `warehouse_immutable_audit` 禁止对审计表做任何 UPDATE/DELETE，改成 `SET_NULL` 会在删号时发 UPDATE 而被拒绝（首次部署已实测拦下）。
- **工号改为选填**：`UserProfile.employee_no` 改为可空（公司暂无统一工号体系，外包同事可能没有），仍保留唯一约束用于将来对接 HR/钉钉；权限页输入框标注"选填"并去除必填星号。
- **申请表单精简**：`AccountRequest` 移除工号与部门填写（申请人并不清楚系统内配置了哪些部门，填了也是错的；部门由管理员建号时从字典选）。去重键改为「姓名 + 电话」。
- **中文名自动生成账号**：新增 `apps/accounts/username_services.py`，用 pypinyin 把「杨俊杰」转成 `yangjunjie`，重名追加 2/3/4……；权限页待办列表直接展示建议账号，管理员无需自己想英文名。
- **账号治理补审计**：`account_created`（建号并记录权限）与 `account_permissions_updated`（提权/降权，含前后权限集合与 is_admin）现在都会留痕，此前完全没有。
- **修复导入非法模板 500**：V3/V2 嗅探都不匹配时原本 `summary` 未定义 → 500；现在明确提示"无法识别该 Excel 模板"并返回 200。
- **修复搜索解析器可打挂**：`inventory/search.py` 增加 `MAX_BOOLEAN_SEARCH_DEPTH=16`，并捕获 `RecursionError`；`'(' * 1200` 这类输入不再让 `/assets/lookup/`、`/warehouse/`、`/api/inventory/assets/search/` 返回 500。
- **修复「编辑权限」按钮无反应**：`data-target` 与表单 `id` 前缀不一致，点击取不到元素；已修正并加回归测试断言两者必须匹配。
- 新增迁移：`accounts/0006_employee_no_optional`、`accounts/0007_account_request_person_key`、`common/0015_audit_actor_snapshot`。
- 新增测试：`test_account_lifecycle.py`（18 例）、`test_permission_markup.py`（3 例）、`test_label_printer_protection.py`（3 例）、`test_import_template_guard.py`（2 例）、`inventory/test_search_limits.py`（8 例）。全量 `522/522` 通过，跳过 8 个 PostgreSQL 专项。
- 已部署候选包：`dist/after-sales-warehouse-20261116-174612-1901c85-dirty-14517796e138.zip`（SHA-256 `0cfb6805b41f4f7a94f8bc418749c82e2feb744bab0202572de925c0bd80ab87`，远端上传后复核一致）。部署后 `showmigrations` 确认 `accounts/0005`–`0007` 与 `common/0015` 均已应用；审计表触发器仍为 1 个，新字段 `actor_username`/`actor_display_name` 已存在。
- 旧包 `...5218c11d2586`、`...1d0cba9e1619`、`...5a814624ed8f`、`...b8edaa14defb`、`...ae4d716aabc1` 仅用于追溯，禁止直接部署。

### 上一轮 (M14)

M14 已随 `20261116-163159-1901c85-dirty-5218c11d2586` 部署：

- **人员显示**：新增 `apps/common/templatetags/person.py`（`person_label` 过滤器 + `person` 标签），14 个模板统一为「中文姓名（登录账号）」——申请创建、采购申请详情/列表、采购订单详情/列表、审批、盘点详情/列表、管理方式转换、Excel 导入、台账流水、审计日志、资产生命周期、资产检索、快速出入库。
- **权限页重排**：`permission_management.html` 11 项权限改为可换行胶囊（`.permission-chip`，勾选即时变色）；角色列只显示权限项数量（如"11 项权限"），不再拼接全部权限名；表格 `table-layout: fixed` 并固定五列宽度。
- **重置密码**：权限页每行新增「重置密码」，生成 12 位临时密码（`secrets`，去除 0/O/1/l/I），立即作废该账号全部会话，写 `account_password_reset` 审计；超级管理员不可在此重置。新增 `apps/common/utils.py::generate_temporary_password()`。
- **申请开通账号**：登录页新增「没有账号？申请开通」入口 → `/login/request-account/`；新增 `AccountRequest` 模型（迁移 `accounts/0005`）与 `AccountRequestForm`；权限页新增「待开通申请」待办区，可标记已建号或驳回。不开放自助注册，提交申请不创建任何账号。
- **审批池与职责分离**：`services.py` 新增 `_approver_users()`，通知范围从"全部仓库操作员"收窄为"具备审批权限的账号"；指定审批人下拉排除申请人自己；存在其他审批人时禁止自批，唯一审批人时放行并写 `request_self_approved` 审计 + 超管提醒。
- **接收人选人**：`WorkflowRequest` 新增 `recipient` 外键与 `is_external_recipient` 标记（迁移 `workflow/0020`）；申请页改为下拉选人 + 「接收人不在系统内」勾选切换手工填写；兼容直接提交姓名的旧调用。
- 新增测试：`test_account_request.py`（11 例）、`workflow/test_approval_separation.py`（6 例）；全量 `488/488` 通过，跳过 8 个 PostgreSQL 专项。
- 已部署候选包：`dist/after-sales-warehouse-20261116-163159-1901c85-dirty-5218c11d2586.zip`（SHA-256 `b812e3aa187a575dfc8e524c69fd427419f16ba3476faf0c1c2616ef75d58d66`，远端上传后复核一致）。两个迁移均为纯新增（可空字段 + 新表），部署后在服务器 `showmigrations` 确认 `0005_accountrequest` 与 `0020_workflowrequest_recipient_and_external_flag` 均已应用。
- 旧包 `...1d0cba9e1619`、`...5a814624ed8f`、`...b8edaa14defb`、`...ae4d716aabc1`、`...-dirty-dirty-291aa0ba2ff0` 仅用于追溯，禁止直接部署。

### Test evidence boundary

- M15 最终全量回归（2026-11-16）：`Found 522 test(s)` / `Ran 522 tests in 373.012s` / `OK (skipped=8)`。跳过项为按平台设计的 PostgreSQL 专项。
- M15 专项：`test_account_lifecycle` `18/18`；`test_permission_markup` `3/3`；`test_label_printer_protection` `3/3`；`test_import_template_guard` `2/2`；`inventory.test_search_limits` `8/8`；`test_account_request` + `test_account_admin_edit` 合计 `21/21`；`manage.py check` 无问题；`makemigrations --check --dry-run` 输出 `No changes detected`；`git diff --check` 退出码 0。
- M15 部署后核对（2026-11-16 17:46，`...-dirty-14517796e138`）：服务 active，`/health/` 正常，`/login/` 200，`/permissions/` 302；`showmigrations` 确认 `accounts/0005`–`0007`、`common/0015` 已应用；审计表触发器仍有 1 个且生效；业务计数资产 636、账号 4 未变。
- **一次被正确拦下的部署**：首次尝试把 `actor` 改为 `SET_NULL` 并回填历史快照，部署在迁移阶段被 PostgreSQL 触发器 `warehouse_immutable_audit` 拒绝（`audit records are append-only`），`current` 指针未改变、旧版本继续服务。随后改为「外键保持 PROTECT + 只加快照字段 + 用停用代替删除」，重新部署成功。这说明数据库层的防篡改是真生效的。
- M14 最终全量回归（2026-11-16）：`Found 488 test(s)` / `Ran 488 tests in 326.656s` / `OK (skipped=8)`。跳过项为按平台设计的 PostgreSQL 专项。
- M14 专项：`test_account_request` `11/11`；`workflow.test_approval_separation` `6/6`；`DashboardAndAdminTests` `80/80`；`manage.py check` 无问题；`makemigrations --check --dry-run` 输出 `No changes detected`；`git diff --check` 退出码 0。
- M14 部署后核对（2026-11-16 16:32，`...-dirty-5218c11d2586`）：`install.sh` 完成部署前 PostgreSQL 快照与迁移；Gunicorn/Nginx/PostgreSQL 三者 `active`；Gunicorn `/health/` 返回 `{"status":"ok","database":"ok"}`，`/login/` 200、`/login/request-account/` 200；服务器 `showmigrations` 确认 `accounts/0005_accountrequest` 与 `workflow/0020_workflowrequest_recipient_and_external_flag` 均已应用。
- M14 部署前后业务计数：资产 636、耗材配件 31、账号 5、申请 0 完全一致，新增表 `accounts_accountrequest` 为 0 行。未清理、未覆盖、未重导服务器数据。
- M12 最终全量回归（2026-11-16）：`Found 471 test(s)` / `Ran 471 tests in 286.811s` / `OK (skipped=8)`。跳过项为按平台设计的 PostgreSQL 专项。
- M12 专项：`test_account_admin_edit` + `test_account_profile` 合计 `15/15`；`test_inventory_type_summary` `11/11`；`manage.py check` 无问题；`makemigrations --check --dry-run` 输出 `No changes detected`；`git diff --check` 退出码 0。
- M12 部署后核对（2026-11-16 15:24，`...-dirty-1d0cba9e1619`）：`install.sh` 完成部署前 PostgreSQL 快照与静态文件同步，迁移无待执行；Gunicorn/Nginx/PostgreSQL 三者 `active`；Gunicorn `/health/` 返回 `{"status":"ok","database":"ok"}`，Nginx `/health/` 200、`/login/` 200；服务器上 `person_label` 命中 2 处、`permission_management.html` 的 `employee_no` 输入框命中 2 处、`purchase_draft_detail.html` 的 `load person` 命中 1 处，确认新代码已生效。
- M12 部署前后业务计数：资产 636、耗材配件 31、导入批次 2、申请 0、库存流水 0、账号 5 完全一致。审计记录由 1055 变为 1056，经查为 15:02:37 的 `suppliers_updated`（“修改供应商资料 压测供应商（STRESS-SUP）”），发生在本轮部署（15:24）之前，属服务器上的压测操作，不是本次部署写入。未清理、未覆盖、未重导服务器数据。
- 打印客户端自测 `11 passed, 3 subtests passed`；EXE 已重建。首次构建因 `set_exe_build_timestamp` 重试耗尽失败（疑似杀毒软件临时占用），重试后成功——构建脚本失败时应重试一次再判定。
- 上一版 M11（2026-11-16 14:18，`...-dirty-b8edaa14defb`）：全量 `452/452` 通过、跳过 8；部署前快照 `pre-deploy-20261116_141756`；迁移无待执行；服务与健康检查通过；计数一致。
- 本机临时目录告警：Windows 默认 `%TEMP%` 对 Python 不可写，会令 openpyxl 导出类用例批量报错并表现为 500；必须显式指定可写临时目录，否则会把环境故障误判为代码缺陷。
- DS3678 中文输入法已现场确认正常；ZEBRA ZT610 60×40 mm 真机走纸按用户要求暂缓，未验收。

### Runtime and data facts

- 本机启动：项目根目录运行 `start_dev.bat`；它使用 `.venv`、`runtime-data/db.sqlite3` 和 `0.0.0.0:8000`。手工命令应从 `backend` 使用 `..\.venv\Scripts\python.exe`。
- NUC 启动：Gunicorn 监听 `127.0.0.1:8000`，Nginx 对外提供 HTTP 入口，systemd 管理应用、备份、健康、一致性、通知和清理任务。
- NUC PostgreSQL 只读计数（最近部署核对）：资产 636、耗材配件 31、导入批次 2、申请 0、库存流水 0。该计数只证明部署前后未变化，不代表源表业务正确性。
- 资产规则：`AST-*` 是系统永久身份；`SC`/`DZSB` 是业务主资产编码，源表值原样保留；二维码正文为 `WMS2:A:{AST}`；终态资产不可直接校正恢复。
- 账号规则：登录账号、工号、部门和权限由 admin 管理；个人资料只允许维护显示姓名；普通用户不能手工绑定钉钉 userid；公开注册和第三方 OAuth 尚未开放。

### Current feature map

- `accounts`：登录限流、组合权限、部门/工号档案、特权会话查看与撤销、个人显示姓名；账号由 admin 创建。
- `inventory`：资产与耗材配件台账、SC/DZSB/AST 身份、二维码、状态和可用性、预占、生命周期、维修、交接、报废审批、附件、盘点、采购、V3 导入导出、标签打印作业。
- `workflow`：申请单、审批节点、批量审批、申请预占、快速出入库、借用/领用/归还/调拨/维修/报损/出售/退供应商/报废、归还延期、离线待复核队列。
- `notifications`：站内通知、钉钉群机器人、钉钉个人通知 outbox、低库存/归还提醒和一致性告警。
- `common`：工作台、全局搜索、报表、审计日志、数据一致性、系统设置、健康检查、PWA service worker、发布状态。
- 已保留但当前暂停：微信小程序、正式域名/HTTPS、公网入口、可信钉钉 OAuth、相机扫码增强、异机备份恢复、耗材批次效期 FIFO、仓间在途调拨和 RMA 深化。

### Next actions in order

1. ~~M11 全量回归、构建候选包、部署到 NUC~~ 已完成：`452/452` 通过，`...-dirty-b8edaa14defb` 已上线验证。
2. ~~M12 台账清空按钮与个人资料只读展示~~ 已完成：全量 `461/461` 通过，`...-dirty-5a814624ed8f` 已上线验证（快照、迁移、服务、健康、计数、服务器模板命中）。
3. **当前第一优先**：在真实浏览器验证 M12 交互——搜索框右侧「清空」在多条件组合下的行为、无筛选时禁用态、统计卡片保持筛选；个人资料页只读信息展示的观感。自动化只覆盖服务端渲染，不能替代浏览器验收。
4. 继续 P1 现场项：DS3678 连续三次扫码/失败后继续（中文输入法已确认正常）；ZT610 60×40 mm 连续 10 张走纸按用户要求暂缓。
5. 服务器恢复后执行隔离 PostgreSQL 完整业务回滚冒烟；不得在业务库留下测试记录。
6. M13 钉钉身份登录：已写入 `DEVELOPMENT_PLAN.md` 作为未来规划，受 HTTPS 回调地址与建号准入策略阻塞；启动前需先确认 NUC 能否获得钉钉可回调的 HTTPS 入口。可先行步骤是“通讯录只读同步”（不需要回调地址）。
7. 部署通道备忘：`robot@192.168.123.101` 用口令登录（公钥未授权）；候选包是 ZIP，远端用 `unzip` 解压（不是 `tar -xzf`）；打包脚本要求干净工作区，dirty 工作区必须显式加 `-AllowDirty`；应保留包内 `release-manifest.json` 原样，不要用 `tools-private/_deploy.py` 重写它（该脚本会把 `working_tree_dirty` 写成 `false` 并伪造 revision）。

### Do not do

- 不要清空、覆盖、重导服务器数据库，不要修改服务器网口优先级，不要关闭 eth 或 Wi-Fi。
- 不要把钉钉客户端“能打开”实现成身份绑定，不要让用户手工输入 userid 自助冒充绑定。
- 不要用 `item_type`、SN、状态、数量或备注猜测资产身份；不要硬删除有业务历史的资产。
- 不要在未读权威文档、未写计划、未做测试和未记录状态的情况下扩展新功能。

## 2026-11-16 current turn

- 本轮完成 M8 收口并连接 NUC 部署候选版本；未清理服务器业务数据，部署前已生成 PostgreSQL 快照。
- 已确认新增需求纳入 M9：资产台账内的物品类型聚合视图、只读测试数据候选报告、账号策略说明。
- 账号策略暂定为 admin 创建和分配组合权限；公开注册及钉钉/飞书/企业微信登录不进入本阶段实现。
- 聚合视图的历史指标第一版仅为库存流水统计，不代表某一历史日期的库存快照；每日快照属于后续候选。
- 本轮本机已完成 M8 的软件修复和回归：批量审批 UUID、中文输入法组合扫码、ZT610 RAW ZPL 生成及打印 worker 生命周期。
- 本轮软件证据：Django 全量测试 `447/447` 通过（SQLite 按设计跳过 8 项 PostgreSQL 专项），打印客户端专项 `11/11` 通过，`manage.py check`、迁移漂移检查、Python/JavaScript 语法检查和差异格式检查通过。
- NUC 基础部署验证已通过：当前 release、迁移状态、服务状态、`/health/` 和只读业务计数已核对；完整 PostgreSQL 业务冒烟、DS3678 中文输入法真机、ZT610 60×40 mm 连续 10 张走纸均仍未验证。
- 候选包已构建并做内容检查：357 个文件，含 release manifest；未包含 `.env`、数据库、Excel、媒体、备份、EXE 或构建目录。候选包 SHA-256 以 `dist/*.sha256` 为准，重建后可能变化。
- 2026-11-16 NUC 已恢复可达并完成候选包部署：当前 release 为 `20261116-m10-profile-candidate`，部署脚本使用 PostgreSQL 快照和离线依赖复用；迁移无待执行，Gunicorn/Nginx/PostgreSQL active，`/health/` 返回 200。部署前后未执行业务数据清理，当前只读计数为资产 636、耗材 31、导入批次 2、申请 0、库存流水 0。
- M10 个人资料页已完成本机 `10/10` 专项和全量 `450/450` 回归，并随 `20261116-m10-profile-candidate` 部署到 NUC；未登录访问 `/account/profile/` 返回登录跳转，服务端数据库未新增迁移。
- 本轮 M11 本地实现：资产台账类型汇总与单件资产使用独立的钻取返回上下文；从类型汇总进入单件资产后返回会清除钻取产生的类型条件，筛选面板清空仍停留在当前视图；个人资料页增加本机钉钉协议唤起入口，但不把客户端打开推断为身份绑定。相关专项 `7/7`、`manage.py check`、迁移漂移检查和 `git diff --check` 通过。
- M11 已部署到 NUC `192.168.123.101`：release 为 `20261116-m11-view-profile-candidate-dirty-ae4d716aabc1`，source revision 为 `1901c85`，工作区标记为 dirty；部署前 PostgreSQL 快照成功，迁移无待执行，Gunicorn/Nginx/PostgreSQL active，`/health/` 返回数据库正常。上传包和临时解压目录已清理，未修改服务器业务数据库。筛选自动展开和钉钉协议唤起修复当前为本地未提交版本，待下一次候选包部署；钉钉真实绑定、客户端行为和现场浏览器交互仍待现场验证。

## 2026-11-10 current review

本节覆盖旧聊天和旧报告中的“当前状态”表述。历史验证记录仍保留，但不代表本地最新未提交代码已完成同等验收。

| Priority | Item | Status | Evidence and next action |
| --- | --- | --- | --- |
| P1 | 批量审批 | software_verified | 已改为 UUID 解析、去重并逐项报告格式错误、缺失、已处理、无权限和业务失败；真实 UUID 批准与拒绝原因专项通过。NUC/并发现场仍待验证 |
| P1 | 中文输入法 HID 扫码 | software_verified | 共享脚本已增加 composition 生命周期、完整 input.value 兜底和 Enter/Tab 重复抑制；JS 语法和相关回归通过。DS3678 中文输入法真机仍待验证 |
| P1 | ZEBRA ZT610 走纸 | software_ready_field_blocked | 已增加 RAW ZPL 主通道，显式设置 `^PW/^LL/^LH/^LT/^LS` 和 2×2 版式；仍必须用 60×40 mm 间隙纸完成真机走纸验收，旧 GDI 现场结论不作废除依据 |
| P1 | 打印客户端 worker 重启 | software_verified | 每个 worker 使用独立 stop event，重启先等待旧线程退出，超时不启动新线程，日志 handler 不重复添加；11/11 客户端专项通过 |
| P2 | 审批 HTMX 交互 | software_verified | 全选改为事件委托，HTMX 替换区域后仍可用；页面级真机浏览器回归仍待 NUC 可达时复核 |
| P2 | 依赖锁与环境口径 | software_verified_local | `requirements-lock.txt` 已转为 UTF-8，Linux 入口补充 Gunicorn/packaging；本机全量回归使用现有环境完成，全新 Python 3.12 安装验证和 NUC release 复核仍待执行 |
| P2 | 权威文档一致性 | in_progress | 本轮已补充软件验证与未验证边界；候选包、干净 Git 提交和 NUC 部署仍未完成 |

结论：原有资产、库存、申请、审批、出入库、采购、盘点、维修、V3 导入、权限、审计和运维链路已经形成技术闭环；M8 的软件阻断项已完成本机验证，剩余风险集中在 PostgreSQL 目标环境复核和两个现场设备入口。程序仍只能作为内网调试代码，当前脏工作区尚未形成可交付候选版本。

## Confirmed product rules

- 资产永久身份使用 AST；源表 SC/DZSB 原样保留。
- 无正式编码的源表行不得自动推断为资产。
- 单件资产与耗材配件分开导入和管理。
- 已领用属于可收回的长期领用。
- 有业务历史的资产不允许硬删除。
- 当前导航目标为五个一级模块，低频入口移入模块工具区或账号菜单。

## Implemented capabilities

- 账号、组合权限、登录/API 限流和安全审计；普通申请人允许多设备，管理员/审批/出库账号默认最多 2 个有效会话，用户可查看并撤销其他登录设备。
- 资产、耗材配件、仓库、库位、物品类型、厂家、供应商、项目和工单。
- AST、SC/DZSB、SN、厂家条码、二维码和别名检索。
- 申请、审批、预占、快速出入库、归还、调拨、销售、维修、退货、报废和报损。
- 采购建议、采购申请、订单、部分到货和采购入库。
- 盘点、差异复核、结果修正、维修、交接、附件、生命周期和装配。
- 标签打印作业和 Windows 打印客户端。
- 报表、通知、钉钉、备份、健康检查、一致性检查和离线待同步队列。
- 摄像头扫码和 USB HID 扫码枪；英文输入法下 Enter/Tab 后缀、普通 SN 和连续累加已有实现，中文输入法组合事件仍待 M8 修复。设备面板不展示浏览器无法可靠检测的 USB 状态。

## Current accepted work

| Priority | Work | Status | Acceptance |
| --- | --- | --- | --- |
| P0 | 治理文件收口与旧计划清理 | completed | 根目录只保留权威计划；AGENTS 强制阅读和收尾核对 |
| P1 | 五模块导航 | completed | 桌面仅五个一级模块；普通用户权限正确；移动端不退化 |
| P1 | V3 双业务页导入导出 | completed | 单件资产/耗材配件分表；预检分类明确；旧模板隐藏 |
| P1 | 导入批次撤销 | completed | 仅可撤销无后续历史的新建/更新记录；对象快照比较与事务回滚已通过 |
| P1 | 受控测试资产删除 | completed | 超级管理员、二次确认和历史阻断已完成；资产标识和网络接口完整字段快照回归与 NUC 冒烟通过 |
| P0 | NUC 部署与 PostgreSQL 冒烟 | completed | 回滚式覆盖出入库、审批、维修、采购、盘点、导入撤销和离线冲突；原数据不变 |
| P1 | ZT610 标签打印 | field_bug | 打印机已确认为 ZEBRA ZT610；渲染测试不代表走纸正确，当前额外走纸约 10 mm，按 M8 重构并重新真机验收 |
| P0 | API 权限与发布追溯加固 | completed | 申请 API 硬删除已关闭，主单/明细原子创建，成本字段按财务权限过滤，私有附件直链拦截；NUC 回归通过 |
| P1 | 审计与危险操作加固 | completed | ORM 与 PostgreSQL 触发器双层只增保护、清库入口移除、终态恢复禁止、快照失败即停和原子 release 已在 NUC 验证 |
| P0 | 特权账号会话管理 | completed | 默认最多 2 个有效会话；用户可查看/撤销设备；不显示完整 IP 或会话标识；撤销与超限写入安全审计 |
| P0 | 多账号编辑冲突保护 | completed | 资产台账校正与基础资料使用 `updated_at` 乐观锁；旧表单拒绝覆盖已变更记录；新增记录不受版本令牌影响；保留服务层事务和审计 |
| P1 | 资产布尔检索与系统设置 | completed | 支持 `! & | ()`；非财务角色不显示采购金额；管理员可维护品牌 Logo 并查看版本与缓存版本 |
| P1 | 自动化安全扫描与认证只读容量 | completed | pip-audit、Bandit、认证 DAST 和业务页 1/4/8/12 并发基线已执行；Python 3.12 升级有历史部署记录，待 NUC 恢复连接后复核 |

## Verified baseline

- 2026-11-09：修复标签打印客户端 XY 偏移"调了没反应"问题并新增标签预览。**真实根因是配置生效 bug**：「保存并启动」在运行中点击时直接 return（既不保存也不重启），实际打印作业用的是启动那一刻固化的旧配置，界面改的 XY 自然不会生效；预览因即时读取界面值所以会变。修复：`start()` 运行中点击改为自动停止旧任务并用新配置重启；`run_once` 每次打印前热加载配置文件中的最新偏移，双保险确保改后下一张标签生效。渲染模型保持"偏移画进位图内部、位图固定从 (0,0) 贴到打印 DC"——位图尺寸永远等于纸张，走纸长度固定；曾短暂改为"移动贴图原点"，但正 Y 偏移会使位图底边超出纸张导致多吐纸（真机复现多吐约 10mm），已回退。新增"预览标签"按钮，弹窗按当前配置（含偏移）渲染一页标签供肉眼核对，不耗纸。客户端测试 `9/9` 通过；新 EXE 31,341,619 字节，SHA-256 `26569ebab75dbfca0d6349c890d78ec5135f2d795429b28913a99e1c3978c4ca`。真机 XY 微调（当前 9/11）与 60×40 mm 验收仍待现场复核。
- 2026-11-09：异机加密备份演练部分完成。在 NUC 用 `pg_dump | gzip | gpg AES256` 生成加密备份（598K），传输到异机 192.168.123.5 后成功解密验证 SQL 完整性。异机为离线环境无法在线安装 PostgreSQL，完整恢复演练待异机补齐离线源或预装 PostgreSQL 后进行。备份文件已清理。
- 2026-11-09：完成本轮全部三项压测，均在 NUC 生产环境执行且未落业务数据。① Excel V3 导入阶梯压测（100/500/1000/2000 条耗材，事务包裹后回滚）：2000 条生成 117.8KB、解析 0.15s、校验 5.55s、事务导入 6.52s，回滚验证 0 残留。② 图片上传压测：JPEG 800×600 平均 8.4ms、PNG 1920×1080 平均 42.7ms、WebP 3840×2160 平均 170.7ms，50/30/20 次全部成功。③ 5 分钟真实 HTTP 稳定性压测（`tools/read_only_load_test.py` 经 Nginx→Gunicorn，admin 登录，9 条登录后业务路由，并发 6）：232 轮共 20880 次 GET 全部 200、错误率 0；吞吐中位 96.1 req/s、P95 中位 85.3ms、P99 中位 98.1ms、平均延迟中位 60.5ms；Gunicorn RSS 431→480MB（+11%，正常），PostgreSQL 峰值连接 3，无内存/连接泄漏。压测后业务计数保持 `637/31/2/0/0`。NUC 与本地临时压测脚本均已清理。
- 2026-11-09：完成 Python 3.12 与依赖安全升级并部署到 NUC。Django 5.2.9→5.2.19、DRF 3.16.1→3.18.3、Pillow 12.3.0→13.0.0、sqlparse 0.5.5→0.6.1、psycopg 3.2.9→3.3.8、Gunicorn 23.0.0→26.2.0；消除 pip-audit 报告的 104/128 条公告（剩余 12 条全部为 pip 构建工具自身）。NUC 通过 deadsnakes PPA 安装 Python 3.12.16，与现有 Python 3.10 并存；部署脚本自动检测并安装 Python 3.12。Django 全量测试 `443/443`（SQLite 跳过 8 项 PostgreSQL 专项）、专项测试 `135/135`、Pillow 13 图片处理与 qrcode 生成冒烟、DRF 3.18 API 契约、PostgreSQL 并发专项 `8/8` 全部通过。版本 `/opt/after-sales-warehouse/releases/20261109_143726-20261109-python312-upgrade-dirty-6b6fdd8b6644` 已原子部署，数据库快照为 `/srv/after-sales-warehouse/backups/pre-deploy-20261109_143726`；部署前后业务计数保持 `637/31/2/0/0/0/0`，未修改任何业务记录。有线 `192.168.123.101` 与无线 `192.168.23.41` 健康检查正常。
- 2026-11-09：完成 pip-audit、生产代码 Bandit、认证 Wapiti DAST 和登录后业务页面容量基线。PyPI/OSV 分别报告 104/128 条公告，集中于 Django 5.2.9、DRF 3.16.1、Pillow 12.3.0、sqlparse 0.5.5；Pillow 13 要求 Python 3.12，而 NUC 为 3.10.12，依赖锁未盲目升级。Bandit 生产代码高危 0、中危 0、低危误报 10；DAST 爬取 162 个 URL/表单，确认漏洞 0、Internal Server Error 0。修复超长布尔搜索、非法 UUID 过滤、二维码标签非法 ID、钉钉 URL 主机校验和密码修改多余 PUT 方法。本机专项 `13/13`、压测工具 `5/5`、Django 全量 `443/443`（SQLite 跳过 8 项 PostgreSQL 专项）通过。版本 `/opt/after-sales-warehouse/releases/20261109_131011-20261109-security-capacity-r4-dirty-45fbd472a437` 已原子部署，数据库快照为 `/srv/after-sales-warehouse/backups/pre-deploy-20261109_131010`；PostgreSQL 专项 `8/8`、回滚式全业务冒烟和安全巡检通过。部署后 NUC 八条业务路由共 640 次 GET，1/4/8/12 并发错误率均为 0；12 并发 P95 166.58 ms、P99 191.05 ms、86.12 req/s，Gunicorn RSS 合计峰值约 483 MB、进程 CPU 采样合计峰值 11.6%、PostgreSQL 峰值连接 3、根盘读/写扇区增量 0/5464。部署前后业务计数均为 `637/31/2/0/0/0/0`；临时会话、测试库、上传包和报告已删除，应用账号无建库权限。
- 2026-11-09：审查修复批次完成并部署。新增 PostgreSQL 审计防篡改触发器、钉钉持久化 outbox、PWA 七天保留与账号隔离、上传图片格式/像素限制、CSP/Referrer/Permissions Policy、独立 release 原子切换和失败恢复。本机专项 `66/66`、Django 全量 `438/438`（SQLite 跳过 8 项 PostgreSQL 专项）、浏览器 E2E、系统检查、迁移漂移、JS/Shell 语法、依赖一致性和差异格式全部通过。NUC 当前 release 为 `/opt/after-sales-warehouse/releases/20261109_121239-20261109-audit-hardening-r3-dirty-85834a540857`，数据库快照为 `/srv/after-sales-warehouse/backups/pre-deploy-20261109_121239`；隔离 PostgreSQL 专项 `8/8`、回滚式全业务冒烟和 outbox worker 通过，临时库已销毁且应用账号建库权限已撤销。部署前后保持资产 637、耗材 31、导入批次 2、申请 0、流水 0、通知/outbox 0；有线 `192.168.123.101` 与无线 `192.168.23.41` 健康检查正常。安全巡检 0 阻断、1 提醒（未安装 `pip-audit`）。
- 2026-11-09：完成首轮审查修复：关闭申请单 API 硬删除，申请/明细原子创建，收口财务字段和照片上传权限，Nginx 拦截生命周期附件直链；移除正式清库入口，阻断审计 QuerySet 更新/删除并保留安全事件，补全受控删除快照，禁止终态资产直接恢复，部署前 PostgreSQL 快照失败即停止。专项回归通过，Django 全量测试 `421/421` 通过、跳过 6 项 PostgreSQL 专项；系统检查、迁移漂移、Shell 语法和差异格式检查通过。本轮未连接 NUC、未部署、未修改任何真实数据库；依赖升级、数据库层审计保护、异步钉钉和原子发布仍未完成。
- 2026-11-09：完成提交 `bbc187c` 的全量只读审查，详细证据见 `REVIEW_REPORT.md`。Django 全量测试 `409/409` 通过、跳过 6 项 PostgreSQL 专项；系统检查、迁移漂移、Python/JavaScript 语法、部署脚本语法、标签渲染 `7/7`、依赖一致性和差异格式检查通过。隔离测试复现申请 API 硬删除、非财务角色读取成本字段和 ORM 批量改写审计记录；另确认依赖安全公告、私有媒体直链绕过、部署快照失败继续等 P1 风险。本轮未连接 NUC、未修改业务数据，当前版本只允许继续受控内网测试，不能作为正式上线准出结论。
- 2026-11-06：标签打印版式与 DPI 修正完成：二维码缩小并顶对齐、1×1 整张按比例放大、客户端读取驱动真实 DPI、浏览器打印改为 60×40 四联并隐藏页头；客户端渲染测试 `7/7`、服务端标签测试 `11/11` 通过；新 EXE 31,339,701 字节，SHA-256 `3b55901f14a3c8fda90c9e31c50e2f5331159742158b0d893d41fc7771290c12`，校准按钮改名为“一键打印校准页”，真机纸卷对齐待现场复核。
- 2026-11-06：完成资产布尔检索（`! & | ()`）、财务金额按权限收口、管理员系统设置与版本信息；修复基础资料 UUID 主键导致新增失败、标签打印刷新重复建作业、扫码结果 20 条边界和快速提交重复问题；Django 全量测试 `393/393` 通过（跳过 6 项 PostgreSQL 专用用例），系统检查、迁移漂移和差异格式检查通过；未部署、未修改本地或服务器业务数据。

- 2026-11-06：特权账号会话管理已完成并部署为 `d1ed82b`；Django 全量回归 `369/369` 通过，NUC PostgreSQL 隔离竞态测试 `6/6` 通过（含特权账号并行登录上限）。`accounts.0004` 已应用，两个内网健康入口均返回 200，部署前后业务计数保持 `637/31/2/0/0`；测试库已销毁，应用账号已撤销临时建库权限。
- 2026-11-06：建立上线前安全与质量评估报告和只读 HTTP 基线工具；登录成功、失败、限流阻断与退出现在均写入匿名化安全事件。Django 全量回归 `364/364` 通过，本机按设计跳过的 5 项 PostgreSQL 竞态测试已在 NUC 隔离测试库 `5/5` 通过，覆盖重复审批、同资产预约、同资产快速出库、耗材并发扣减和登录限流计数。测试库已销毁，应用账号已撤销临时建库权限，业务计数保持 `637/31/2/0/0`。
- 2026-11-06：NUC 安全巡检为 0 个阻断、1 个提醒；提醒为未安装 `pip-audit`。HTTP 模式仍有 HSTS、SSL Redirect 和 Secure Cookie 提醒，只允许内网小范围试运行。只读 `/health/` + `/login/` 基线为并发 8、200 次、错误率 0、P95 204.38 ms、P99 256.16 ms；未覆盖登录后业务页和资源水位，不作为正式容量结论。本机安装 `pip-audit`/Bandit 因 PyPI 网络超时，依赖 CVE 与 SAST 保留待办。
- 2026-11-06：NUC 钉钉群机器人配置检查通过，明确标记的“系统联通性测试”实发返回 `delivered=True`；低库存汇总 timer 为 enabled/active。企业内部应用 AppKey、AppSecret、AgentId 尚未配置，账号钉钉 UserId 绑定数为 0，因此个人工作通知仍待企业应用资料和账号映射完成。
- 2026-11-06：扩展 PostgreSQL 回滚式冒烟覆盖采购到货、盘点差异修正、V3 导入整批撤销、离线重复同步和状态冲突。首次实测发现盘点明细默认排序导致 PostgreSQL 对可空外连接执行 `FOR UPDATE` 失败，已改为仅锁定明细自身并按主键排序；修复后全链路通过，回滚前后保持资产 637、耗材 31、导入批次 2、申请/流水/采购/盘点/离线/维修记录均为 0。
- 2026-11-06：离线幂等键增加账号与原始内容绑定；跨账号复用，或同账号使用相同操作编号但修改时间/表单内容时明确拒绝，正常重试仍只生成一张单据和一组库存流水。
- 2026-11-06：部署运行状态追溯已补齐：安装脚本严格解析发布 manifest，并将版本、完整/短 revision、脏工作区状态和源内容 SHA-256 写入 release 的 `release-info`；缺少 manifest 的手工源码部署明确标记为未版本化。
- 2026-11-06：累计功能与治理改动已建立 Git 基线提交 `1228c04`，发布手册纳入修正提交为 `cbe99d5`。干净工作区构建验证 manifest 的 `working_tree_dirty=false`、完整 revision 与内容哈希正确，发布包包含 318 个文件和 Ubuntu 手册，未包含 `.env`、数据库、Excel、EXE、媒体、备份或构建目录。
- 2026-11-06：workflow API 已分别使用审批权限和出入库执行权限，拆分角色不能越权；通知 API 收敛为只读、未读计数和标记已读；DRF Basic Authentication 已移除。专项测试 `27/27`、Django 全量测试 `356/356`、打印渲染 `6/6`、系统检查、迁移漂移、JavaScript 语法和差异格式检查通过。
- 2026-11-06：发布脚本默认拒绝脏工作区，显式 `-AllowDirty` 构建会在包名和 manifest 中记录 `dirty`、完整 Git revision 与源文件内容哈希；Windows PowerShell 5 下中文手册通过 ASCII 通配模式可靠纳入发布包。
- 2026-11-06：Windows 打印客户端已根据最新二维码靠左/底部对齐源码重建，大小 32,395,359 字节，SHA-256 `8d436201ccba1b73aed4c8ac246e6aa90b6d6891ea88530bf19cf1dd5e19f488`；60×40 mm 真机验收仍待现场完成。
- 2026-11-05：连续扫码焦点分流与 PWA 静态缓存问题已修复。即使搜索框已获得焦点，HID 扫码枪的 Enter/Tab 也统一进入扫码队列，不再提交普通搜索；`scanner.js` 使用版本化 URL，Service Worker 对易变脚本改为网络优先并升级静态缓存。真实服务器数据只读验证 `AST-00000196/197` 可加入、`AST-00000198` 以已售出待修正返回；本地连续三码键盘事件模拟通过。Django 全量测试 `354/354`、扫码专项 `10/10`、打印客户端 `6/6`、系统检查、迁移漂移、JavaScript 语法和差异格式检查均通过。
- 2026-11-05：上述连续扫码与缓存修复已部署到 NUC。代码快照 `/opt/after-sales-warehouse/releases/20261105_185547`，数据库快照 `/srv/after-sales-warehouse/backups/pre-deploy-20261105_185547`；无数据库迁移，部署前后均为 637 件资产、31 条耗材配件、2 个导入批次、0 张申请、0 条出入库记录。Gunicorn、Nginx、PostgreSQL active，Wi-Fi 与有线健康检查均正常，新扫码脚本在应用目录和静态目录哈希一致。
- 2026-11-05：本地手工维护改造收尾（移除 V4/V1 转换器、管理员专用状态调整、采购金额财务权限、资产客户字段、admin 独占资产写权限）；扫码按页面自动启用，我的申请筛选紧凑两行，标签打印统一预览入口。
- 2026-11-05：顶部常驻网络状态已移除；正常联网时不占用界面，仅在连接中断或存在离线记录时显示“连接中断/待同步 N”。设备面板删除 USB 连接状态和服务健康，仅保留扫码监听、本页适用性及本会话成功扫码时间。资产 API 受控字段只读声明的缩进错误已修正。
- 2026-11-05：快速出入库和新建申请改为串行扫码队列；扫码值在请求发出前立即从输入框取走，避免前一次异步响应清空下一次扫码内容。本地 Django 全量自动化测试 `351/351` 通过，`manage.py check`、迁移漂移检查、JavaScript 语法检查和 `git diff --check` 通过。
- 2026-11-05：最新程序部署到 NUC `192.168.123.101`；仅应用新增可空客户外键的 `inventory.0029`，未执行数据导入或清理。代码快照 `/opt/after-sales-warehouse/releases/20261105_180300`，数据库快照 `/srv/after-sales-warehouse/backups/pre-deploy-20261105_180300`；部署前后均为 637 件资产、31 条耗材配件、2 个导入批次、0 张申请、0 条出入库记录，Gunicorn、Nginx、PostgreSQL active，健康检查正常。
- 2026-11-05：连续扫码队列与状态栏精简已部署到 NUC；本次无数据库迁移。代码快照 `/opt/after-sales-warehouse/releases/20261105_182121`，数据库快照 `/srv/after-sales-warehouse/backups/pre-deploy-20261105_182121`；部署后仍为 637 件资产、31 条耗材配件、2 个导入批次、0 张申请、0 条出入库记录，三项服务和健康检查正常。
- 2026-11-05：五模块导航已完成本地实现；普通申请人显示 2 个一级入口，仓库操作人员/管理员最多显示 5 个一级入口，旧 URL 保持可访问。
- 2026-11-05：Django 全量自动化测试 `326/326` 通过，`manage.py check`、迁移漂移检查和 `git diff --check` 通过。
- 2026-11-05：五模块导航部署到 NUC `192.168.123.101`；部署前代码和 PostgreSQL 快照成功，Gunicorn、Nginx、PostgreSQL 均为 active，`/health/` 返回 200，迁移全部应用。
- 2026-11-05：V3 导入导出本地完成；单件资产与耗材配件分表、明确新增/更新、同批事务写入、导出后再导入和旧格式隐藏已实现。专项测试 `24/24`、全量测试 `333/333` 通过。
- 2026-11-05：V3 已部署到 NUC；服务和健康检查正常，导入路由可用，服务器 PostgreSQL 环境成功生成 V3 模板，未执行真实导入、未修改库存。
- 2026-11-05：导入变更账本、整批撤销和受控测试资产删除本地完成；覆盖新增、更新、全量基准、后续修改/业务历史阻断、权限、确认文字和快照审计。专项测试 `17/17`、全量测试 `343/343` 通过。
- 2026-11-05：恢复控制已部署，迁移 `inventory.0028` 成功应用；服务器三项服务 active、健康检查正常，变更账本初始为 0，未改既有业务数据。
- 2026-11-05：删除旧计划/验收报告、旧 V1/V2 模板、旧导入输出和可再生构建缓存约 119 MB；保留本地数据库、备份、真实源表和打印客户端最终 EXE。
- 2026-11-05：最终本地全量自动化回归 `344/344` 通过；`manage.py check`、迁移漂移检查和 `git diff --check` 通过。
- 2026-11-05：移除会重建 PostgreSQL 并强制重导 11.2 基线的旧部署脚本；发布包不再携带任何清库工具。
- 2026-11-05：最新代码已部署到 NUC `192.168.123.101`；部署前代码快照位于 `/opt/after-sales-warehouse/releases/20261105_114006`，数据库快照位于 `/srv/after-sales-warehouse/backups/pre-deploy-20261105_114006`。
- 2026-11-05：NUC PostgreSQL 回滚式业务冒烟通过：快速借出/归还、申请提交/审批/出库/归还、审批驳回、维修完工回库全部成功；冒烟前后均为 637 件资产、0 张申请、0 条出入库流水、0 张维修单，未保留测试数据。
- 2026-11-05：NUC V3 全量导出生成成功（112,948 字节），Gunicorn、Nginx、PostgreSQL active，`/health/` 和 `manage.py check` 正常，迁移已应用至 `inventory.0028`。
- 2026-11-05：最终运维配置同步完成；代码快照 `/opt/after-sales-warehouse/releases/20261105_115105` 与数据库快照 `/srv/after-sales-warehouse/backups/pre-deploy-20261105_115105` 已生成，tmpfiles 不再误匹配发布 ZIP。
- 资产编码与导入专项测试 `56/56` 通过。
- 扫码枪浏览器侧行为模拟通过：普通 SN、Enter/Tab、输入框不被全局监听抢占。
- 服务器当前技术计数为 637 件资产；该数字只用于确认本次部署前后数据未变化，不代表源表业务验收结论。
- 2026-11-05：旧台账 V4 转换曾完成并部署；改名后的研发“资产编码”可读取，显式耗材按总量分组，重复 SC/DZSB、未分类、无效编码和资产/耗材冲突逐行阻断，IP 原样保留到备注。专项测试 `6/6`、Django 全量测试 `350/350`、打印渲染测试 `4/4` 通过；该转换器已于后续按手工维护方案移除。
- 2026-11-05：Windows 打印客户端新增固定 60×40 mm 校准页和完整 2×2 四格测试打印；新 EXE 为 32,392,510 字节，SHA-256 `80734fbb90199c82f4c949c4cbaacbae26f3bb41a7db5759b30234a30e285635`。
- 2026-11-05：NUC 部署前代码快照 `/opt/after-sales-warehouse/releases/20261105_130447`，数据库快照 `/srv/after-sales-warehouse/backups/pre-deploy-20261105_130448`；部署前后均为 637 件资产、31 条耗材配件、2 个导入批次，三项服务 active，健康检查 200。
- 2026-11-05：NUC 使用真实《11.2库存-修改版.xlsx》完成只读转换冒烟，输出 318 条安全资产、3 个耗材分组和 832 条逐行问题；未执行导入，转换临时文件已清理，数据库计数不变。

## Required field acceptance

- DS3678 真机连续扫描和中文输入法场景。当前中文输入法已复现不能进入物品筛选，状态为失败待修复。
- ZEBRA ZT610、60×40 mm 间隙纸：当前 2×2 打印额外走纸约 10 mm，状态为失败待修复；现场需记录驱动的实际完整名称，预期为 ZDesigner ZPL 驱动，不再沿用旧 EXE 验收结论。
- 钉钉群机器人和低库存定时任务已通过；个人工作通知仍需配置企业内部应用并逐个绑定账号钉钉 UserId。
- DS3678 和浏览器离线队列仍需现场验证中文输入法、断网恢复速度及人员实际操作体验；多账号、重复提交和冲突状态的服务端自动化与 PostgreSQL 冒烟已通过。
- 特权账号会话策略与 PostgreSQL 竞态用例已通过；剩余现场手工确认两设备共存、第三设备登录时最旧会话失效。
- 登录后业务页只读压测、Excel 导入隔离压测、图片上传压测与 5 分钟 Gunicorn CPU/RSS/PG 连接长时采样均已完成（详见 Verified baseline 2026-11-09 首条）；正式硬件容量验收时如需更长时长（如 1 小时+）与磁盘 IO 曲线可在此基础上扩展。
- `pip-audit`、Bandit 和认证 DAST 已完成；正式公网前仍需修复依赖公告、启用 HTTPS 后重跑 DAST，并安排独立人工渗透。

## Known data-input issue

- 2026-11-06 NUC 只读一致性检查为错误 0 项、警告 256 项：`outbound_without_holder` 254 件，`duplicate_serial_number` 2 组。前者需按历史领用/借用资料补录内部持有人或外部接收人，后者需核对实物标签；不得自动猜测修复。
- 当前《11.2库存-修改版.xlsx》共有 851 条源数据行、540 个格式有效的正式编码单元格、408 个唯一正式编码；其中 85 个编码重复，涉及 217 行。重复行可能包含历史出入库/维修记录，不能静默去重。
- 旧 V4 转换器实测问题仍作为人工清理依据：318 条资产缺少系统物品类型代码、221 条无编码且未填写资产属性、25 条编码格式无效、5 条“耗材但持有正式资产编码”冲突，以及 3 条状态需人工确认。
- 当前转换文件不是可直接导入的最终基准。应先按问题清单清理重复历史行、明确资产/耗材、补齐物品类型代码和当前库存数量，再上传 V3 预检。

## 2026-11-05 full-product review

- 试运行结论：账号与组合权限、资产/耗材台账、申请审批、预占、快速出入库、归还、维修、盘点、采购、导入恢复、审计、通知、报表和运维任务已形成完整技术闭环；连续扫码修复已部署，服务器回滚式业务冒烟通过且未保留测试数据。
- 发布追溯已完成：累计改动已建立可回滚 Git 基线，干净发布包能够准确记录提交和内容哈希。
- API 权限进展：workflow 审批/拒绝/执行 API 和通知 API 已按组合权限收紧并完成反向测试。
- 前端现场验收：自动化连续三码模拟已通过，但 DS3678 真机、中文输入法、快速连扫仍需用户验收。扫码结果区下一步应同时记录 HID 与相机结果，并固定显示成功、待修正、失败计数；失败条码保留且不阻断后续扫码。
- 打印交付进展：最新 EXE 已重建并更新校验值，剩余 60×40 mm 真机验收；X 轴 `-3 mm` 作为打印机配置保存，不写成全局默认。
- 运维文档偏差已修正：低库存汇总为每周一 09:00，归还提醒为每日 09:10。
- 架构维护风险：`common/views.py`、`workflow/services.py`、`inventory/models.py` 与 `web_base.html` 已明显过大。完成基线提交后按业务域拆分，保持 URL、API 和数据库契约不变，不在当前试运行阶段重写框架。
- 安全边界：当前小范围内网 HTTP 测试可继续；任何 Cloudflare Tunnel、正式域名或公网入口启用前，必须先启用 HTTPS、移除 DRF BasicAuthentication、复核 CORS/CSRF/ALLOWED_HOSTS，并完成权限矩阵回归。
- 灾备边界：本机自动备份与隔离恢复演练已具备，但备份仍与应用位于同一台 NUC；正式运行前必须增加加密异机副本和异机恢复演练。

## Long-term retained directions

- 微信小程序：保留源码和产品方向，当前阶段暂停；待 Web/PWA 流程稳定、API 契约冻结后再恢复。
- 正式域名与 HTTPS：保留并推荐作为正式远程入口；完成网络策略、域名和证书条件后替换临时 Tunnel。
- 相机扫码：移动 Web/PWA 已具备基础能力，正式使用要求可信 HTTPS 或浏览器认可的安全上下文；继续保留并纳入手机现场验收。

## Candidate backlog

- 连续扫码固定结果区：成功 / 待修正 / 失败计数，HID 与相机统一记录，失败条码不中断队列。
- 批量台账校正：多选资产后统一修改仓库、库位、物品类型或允许的状态，逐条审计并整批事务回滚。
- 数据质量中心：集中显示无库位、无物品类型、重复 SN、编码异常、状态矛盾和库存位置冲突，并提供受控修正入口。
- 完成 Windows 打印客户端 60×40 mm 标签真机验收（标签机恢复后再启动）。
- 将“离线待复核”从常驻任务标签收敛为有记录时出现，或通过试验功能开关启用，避免普通用户误解为完整离线系统。
- 仓间调拨双确认和在途状态。
- 耗材批次、效期和 FIFO 提示。
- RMA/售后维修工单闭环。
- 单据通用附件和供应商绩效。
- 外部备份副本与异机恢复演练。
- 旧台账产品名称到物品类型代码的受控映射表，减少 318 条资产逐行补码工作；映射必须人工确认后才能应用。

候选项未进入 `DEVELOPMENT_PLAN.md` 前不得直接实施。
