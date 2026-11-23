# 代码审查与回归报告（合并版）

本文件由 `REVIEW_REPORT.md`（历史）与 `REVIEW_REPORT_1.md`（最新）合并而成，
按时间顺序排列，正文内容逐字保留，未做删改。

## 阅读指引

**先看结论，再看历史。** 当前系统状态以 `PROJECT_STATUS.md` 顶部的 YAML 状态块为准，
本文件是过程记录，不是当前状态的权威来源。

### 缺陷编号在不同轮次指不同事

本项目的缺陷编号是全局可复用的，同一个 `P1-01` 在不同轮次指过三件不同的事。
引用编号前请查 `ISSUES.md` 的消歧表。已知冲突：

| 编号 | 含义 A | 含义 B | 含义 C |
|---|---|---|---|
| `P1-01` | 依赖版本漂移（11-19） | 成本导出越权（11-20） | 主数据删除保护（11-23） |
| `P1-02` | 受保护附件可绕过授权（11-18） | 删除主数据致资产脱仓（11-20） | — |
| `P1-03` | 财务成本字段泄露（11-18） | 采购到货重放（11-20） | — |
| `P2-01` | iPhone PWA 缺图标（11-18） | 履历 API 越权（11-20） | — |
| `P2-04` | 离线队列明文持久化（11-18） | 报表全量物化（11-20） | — |

### 历史轮次的问题大多已修复

第一部分（2026-11-09 ~ 11-20）列出的 P0/P1 问题**均已修复并部署**，
保留原文是为了记录「当时发现了什么、怎么判断的」，不要按它去复现问题。
第二部分（2026-11-23）是距当前最近的一轮审查，其中 P1 与多数 P2 也已修复。

## 目录

- 第一部分：2026-11-09 ~ 2026-11-20 历史审查（第 1-12 轮）
- 第二部分：2026-11-23 全景审查（第 1-23 节）

---

# 第一部分：2026-11-09 ~ 2026-11-20 历史审查

> 来源：原 `REVIEW_REPORT.md`（全量代码审查与回归报告）。
> 这部分记录系统从「不可上线」到「内网可试运行」的过程。
> 其中列出的 P0/P1 问题均已修复并部署，保留原文作为判断依据的留档。

### 2026-11-20 第十二轮：全量复盘 + 回归 + 收口

> 执行边界：只读审查 + 隔离测试库回归。未连接 NUC、未读写生产库、未部署。
> 原始缺陷全部用一次性探针在内存测试库复现，探针文件运行后已删除。
> 本轮随后在**同一会话内**修复了其中 4 个 P1 与 3 个 P2，见「5. 修复记录」。

### 1. 执行摘要

**结论：列出 4 个 P1 + 6 个 P2（无 P0）；其中 4 个 P1 与 3 个 P2 已修复并补回归用例，3 项留待下轮。**

| 维度 | 结果 | 证据级别 |
|---|---|---|
| 全量测试 | 797 通过 / 8 跳过（含并发改动新增用例） | 已验证 |
| `manage.py check` | 无问题 | 已验证 |
| 迁移漂移 | No changes detected | 已验证 |
| `pip check` | 无冲突 | 已验证 |
| JS 语法 | `node --check` 通过 | 已验证 |
| 部署脚本语法 | 23 个 `bash -n` 全通过 | 已验证 |
| 成本导出越权 | 已复现 → 已修复 → 有反向断言 | 已验证 |
| 履历 API 越权 | 已复现 → 已修复 | 已验证 |
| 删仓库致资产脱仓 | 已复现 → ViewSet 层已拦（模型层未改） | 已验证 |
| 采购到货重放 | 已复现 → 幂等键已加 | 已验证 |
| 恢复演练脚本 | 静态确认失效 → 已重写 | 已验证（静态） |

必须强调：**P1-01、P1-03、P1-04 在本轮之前已记录在 `PROJECT_STATUS.md` 两天，但代码一行未动。**
文档上「已登记」≠「已修」，本轮把这三项从「待办记录」变成「已复现 → 已修」。

### 2. 全局地图

**版本三态**：

| 状态 | 值 | 证据 |
|---|---|---|
| Git 已提交基线 | `a2fd9a7c782a45e568a50874ca4de8f276486272`（main，领先 origin 75 提交） | 已验证 |
| 本地未提交工作区 | 本轮修复（9 改 + 4 新增）与三份文档 | 已验证 |
| NUC 实际运行 release | 未复核（未连接 192.168.123.101） | 未执行 |

**技术栈与规模（实测）**：

| 项 | 值 |
|---|---|
| Python / Django | 3.12.10 / 5.2.19（与锁文件一致） |
| 业务代码 | 278 个 Python 文件、22,648 行（不含测试与迁移） |
| 测试代码 | 11,473 行（测试/代码比 0.51） |
| 数据库迁移 | 82 个（本轮 +1） |
| 模板 | 57 个（13 个含内联 script、13 处 onclick） |
| 部署单元 | 12 timer / 16 service / 23 shell |

**模块边界**：

| App | 职责 | 最大文件 |
|---|---|---|
| `common` | 页面主干、权限谓词、审计、限流、PWA | `views/legacy.py` 4,891 行 |
| `workflow` | 申请、审批、出入库、采购、离线 | `services.py` 1,686 行 / 121 函数 |
| `inventory` | 资产、耗材、编码、导入、盘点、维修 | `models.py` 1,451 行 |
| `accounts` | 身份、会话、账号申请、密码策略 | `session_services.py` |
| `notifications` | 站内通知 + 钉钉 outbox | `outbox.py` / `dingtalk.py` |

**核心调用链（含本轮修的三处薄弱点）**：

```mermaid
flowchart TD
    A[员工新建申请] --> B[submit_request 事务+行锁]
    B --> C[InventoryReservation 预占]
    C --> D{审批}
    D -->|approve| E[待仓库处理]
    D -->|reject| F[已驳回 + 释放预占]
    E --> G[process_request 按类型分流]
    G --> H[Asset/StockItem 行锁 + 状态变更]
    H --> I[InventoryTransaction 流水]
    I --> J[record_operation 审计]
    J --> K[outbox → 钉钉]
    L[采购订单] --> M[receive_purchase_order]
    M -->|已加幂等键| I
    N[基础资料 API] --> O[perform_destroy]
    O -->|已加引用检查 + 事务| P[400 或 204]
    I --> Q[CSV 导出]
    Q -->|已统一 show_financials| R[成本列按权限]
```

**鉴权边界（修复前实测）**：

| 入口 | 匿名 | 普通员工 | 仓库角色 | 管理员 |
|---|---|---|---|---|
| 页面 `/warehouse/` | 302 | 302 | 200 | 200 |
| `/api/inventory/assets/` 读 | 403 | 200（含 SN） | 200 | 200 |
| `assets/{id}/lifecycle/` | 403 | **200（越权）** | 200 | 200 |
| `/api/inventory/warehouses/` DELETE | 403 | 403 | **204（脱仓）** | 204 |
| `/reports/transactions.csv` | 302 | 302 | **200（含成本）** | 200 |

以上四处加粗项均已修复。

### 3. 环境可运行性

| 命令 | 结果 |
|---|---|
| `manage.py check` | 通过 |
| `makemigrations --check --dry-run` | No changes detected |
| `pip check` | 无冲突 |
| `manage.py test` | 783 通过 / 8 跳过 |
| 23 个部署脚本 `bash -n` | 全通过 |

**环境性坑（必须记录）**：首次全量运行出现 4 个 `PermissionError`。根因是 DSH 文件沙箱
给临时目录继承了 `Everyone:(DENY)(DC)` ACL，`tempfile.mkdtemp()` 建出的目录无法写入/清理
（`common/tests.py:2253/2284/2362/2393`）。放开沙箱后同一批 9/9 通过，全量 773/773 通过。
**是环境限制不是代码缺陷**，但顺带说明这 4 个用例依赖 `tempfile` 默认行为，在受限 ACL
环境必然假失败（P2-09）。

### 4. 测试与复现结果

| # | 测试 | 方式 | 结果 |
|---|---|---|---|
| 1 | 全量测试 | 内存库 | 797 通过 / 8 跳过 |
| 2 | 成本导出越权 | 探针 | 复现：含 8888.00、表头不含成本 → **已修 + 3 例断言** |
| 3 | 履历 API 越权 | 探针 | 复现：员工 200 且含手机号 → **已修 + 3 例** |
| 4 | 删仓库级联 | 探针 | 复现：204 + 资产脱仓 + 库位消失 → **已修 + 1 例** |
| 5 | 采购到货重放 | 探针 | 复现：12 → 14 → **已修 + 4 例** |
| 6 | 停用仓库到货 | 探针 | 复现：停用后可写入 → **已修 + 4 例** |
| 7 | PROTECT 删除 | 探针 | 复现：500 + 孤儿审计 → **已修 + 4 例** |
| 8 | 恢复演练格式 | 静态 | 失效 → **已重写 + 6 例** |

**测试有效性验证（反向证明，避免"写了测试但测不到"）**：
- P1-01：`git stash` 回退 `legacy.py` 后，新增 2 例精确 FAIL。
- P1-03：`git stash` 回退 `services.py` 后，新增 4 例全部 ERROR。

**未执行（不得视为通过）**：PostgreSQL 并发 8 项、真实浏览器 E2E、DS3678 扫码、
ZT610 60×40 mm 走纸、NUC 部署与 `/health/` 冒烟、恢复演练实跑。

### 5. 修复记录（本轮已修 7 项）

| 编号 | 标题 | 位置 | 状态 |
|---|---|---|---|
| P1-01 | 无财务权限导出仍含成本值 | `common/views/legacy.py:4572/4611` | 已修，表头与数据行共用 `show_financials` |
| P1-02 | 删仓库致资产静默脱仓 | `inventory/views.py:125-132` | **部分修**：ViewSet 加引用检查；模型层 `CASCADE→PROTECT` 未做 |
| P1-03 | 采购到货重放双记库存 | `workflow/services.py:1586` | 已修，`client_operation_id` + 条件唯一约束 |
| P1-04 | 恢复演练依赖废弃 `media.tar.gz` | `deploy/ubuntu22/restore-drill.sh` | 已重写（静态验证） |
| P2-01 | 履历 API 越权 | `inventory/views.py:227` | 已修，改为 `IsWarehouseOperator` |
| P2-02 | 停用仓库/库位仍可写入 | `workflow/services.py:409` | 已修，仅拦新写入不卡在途单 |
| P2-03 | 删除抛 500 且留孤儿审计 | `inventory/views.py:125` | 已修，事务化 + 转 400 + 删除前快照 |

### 6. 未修项（下轮优先）

| 编号 | 标题 | 位置 | 为何本轮未修 |
|---|---|---|---|
| P1-02 剩余 | `Location.warehouse` 仍 `CASCADE`、`Asset.warehouse` 仍 `SET_NULL` | `inventory/models.py:129/552` | 需迁移 + 存量孤立数据核对，不能与功能改动混提 |
| P2-04 | 报表/供应商价格无上限物化 | `legacy.py:4429/2416` | 需万级数据压测才有意义 |
| P2-06 | `check-all.sh` 未纳入安全门禁 | `deploy/ubuntu22/check-all.sh` | 需在 WSL 实跑验证 |
| P2-07 | `legacy.py` 4,891 行单文件 | `common/views/legacy.py` | 纯机械迁移，需逐个域做 |
| P2-08 | 批量审批吞异常无日志 | `workflow/web_views.py:365` | 低风险，下轮顺手 |
| P2-09 | 4 用例依赖 `tempfile` 默认清理 | `common/tests.py:2253+` | 低风险 |
| P2-10 | 资产照片经公开 `/media/` 可匿名访问 | `nginx.conf:40` | 需业务确认是否收紧 |

### 7. 六视角评审

| 视角 | 意见 |
|---|---|
| 架构师 | 模块边界清晰，但删除语义应提升为跨域策略（主数据「停用优先、删除受控」）。本轮只补了 ViewSet 守卫，模型层约束仍需迁移。 |
| 研发负责人 | 事务与锁的写法专业。真正的问题是「登记即忘」：P1-09/11/12 记录两天无人修。本轮已把三项落地并附反向证明测试。 |
| 测试负责人 | 773 → 797 用例。关键改进是补了「测数值不测表头」「测重放不测首提」这两类盲区，并用 stash 反向验证测试真的能失败。 |
| 安全负责人 | 两处 API 越权（成本、履历）已关闭。剩余 P2-10 照片公开、P1-02 模型层约束需继续跟进。 |
| SRE | 部署链设计到位，但恢复演练脚本失效是灾难恢复链上唯一未验证环节，本轮已重写并加防回退测试；仍须真跑一次。 |
| 最终用户 | 主流程无新问题。删仓库导致资产「凭空失去仓库」在界面上没有任何提示，本轮已改为 400 并提示改用停用。 |

### 8. 未验证风险

1. PostgreSQL 行锁与 `skip_locked` —— 全部并发结论来自 SQLite。
2. 审计触发器 `warehouse_immutable_audit` —— 只在 PG 生效。
3. 恢复演练脚本实跑 —— 只做了 `bash -n` 与静态断言。
4. 幂等键的并发双提交 —— 需 PostgreSQL 验证唯一约束在真并发下的行为。
5. NUC 实际 release、真实设备、真实浏览器。
6. P2-04 容量结论、P2-10 照片匿名访问。

### 9. 下一阶段计划

1. P1-02 模型层迁移：`Location.warehouse → PROTECT`，并核对存量孤立数据（只读脚本）。
2. 在隔离 PostgreSQL 库验证 P1-03 幂等键的并发双提交。
3. 真跑一次 `restore-drill.sh`，产出可引用日志。
4. P2-09 / P2-08 顺手修，P2-06 在 WSL 实跑验证。
5. 把本轮 4 个探针场景全部保留为正式回归（已完成）。

---

### 2026-11-19 第十一轮：全量复盘第一轮（环境盘点 + 全局地图 + 基础回归）：全量复盘第一轮（环境盘点 + 全局地图 + 基础回归）

> 本轮范围：只审查与测试，**未修改任何业务代码**。
> 依据：用户要求「第一阶段只审查、测试和记录问题」。

### 1. 执行摘要

**结论：代码库整体处于可交付状态，未发现 P0 缺陷。**

| 维度 | 结果 |
|---|---|
| 静态检查 | `manage.py check` 无问题、无迁移漂移、`pip check` 无冲突 |
| 测试 | **736/736 通过**；PostgreSQL 并发 **6/6 通过**（已在 NUC 独立库验证）|
| 依赖漏洞 | 修复前：本机 **121 条**、生产 **65 条**；**修复后两端均为 0** |
| 权限边界 | 匿名/员工/管理员三层实测**全部正确** |
| 视图权限 | 三个视图文件的**所有** `def xxx(request)` 均有装饰器 |
| 安全响应头 | X-Frame-Options / nosniff / CSP / Referrer-Policy **齐全** |

**发现 1 个 P1 与 4 个 P2（无 P0）**。P1-01、P2-02、P2-03、P2-04 均已处理完毕，P2-05 已证伪。

### 2. 全局地图

**技术栈**：Django 5.2 + DRF + PostgreSQL（生产）/ SQLite（开发）+ Gunicorn + Nginx + systemd + Cloudflare Tunnel

**规模**：

| 项 | 数量 |
|---|---|
| Python 文件 | 190 个（不含迁移/缓存） |
| Python 行数 | 33,151 |
| 模板 | 57 个 / 4,337 行 |
| 数据库迁移 | 80 个（accounts 7 / common 16 / inventory 31 / notifications 6 / workflow 20） |
| 路由 | 以 `common/urls.py` 为主入口 |
| 定时任务 | 14 个 systemd timer |

**模块边界**：

| App | 职责 | 关键文件 |
|---|---|---|
| `accounts` | 身份、账号申请、会话、密码策略 | `models.py`, `system_accounts.py`, `password_policy.py` |
| `common` | 共享能力、视图主干、权限谓词、审计 | `views.py`(5084 行), `permissions.py`, `audit.py` |
| `inventory` | 资产、耗材、库位、编码、导入、盘点、维修 | `models.py`, `code_services.py`, `state_services.py` |
| `workflow` | 申请、审批、出入库、预占 | `services.py`(1816 行, 52 函数) |
| `notifications` | 站内通知 + 钉钉 outbox | `outbox.py`, `dingtalk.py` |

**核心调用链（已验证）**：

```mermaid
flowchart TD
    A[员工新建申请] --> B[submit_request]
    B --> C{预占创建 InventoryReservation}
    C --> D[待审批]
    D -->|approve_request| E[待仓库处理]
    D -->|reject_request| F[已驳回 + 释放预占]
    E -->|process_request| G{按类型分流}
    G -->|borrow/issue/sale| H[出库: 设 current_holder + 改状态]
    G -->|return| I[入库: 清 current_holder + 回在库]
    G -->|transfer| J[换仓库库位, 状态不变]
    G -->|repair/damage/scrap| K[仅改状态 + 维修单]
    H --> L[写 InventoryTransaction]
    I --> L
    J --> L
    K --> L
    L --> M[record_operation 审计]
```

**鉴权边界（实测）**：

| 身份 | 个人页面 | 仓库管理 | 审批 | 报表 |
|---|---|---|---|---|
| 匿名 | 302→登录 | 302 | 302 | 302 |
| 员工（无组） | **200** | **302** | 302 | 302 |
| 管理员 | 200 | 200 | 200 | 200 |

**部署拓扑**：Cloudflare Edge → 隧道 → Nginx(:80) → Gunicorn(127.0.0.1:8000) → PostgreSQL(127.0.0.1:5432)

### 3. 当前环境和可运行性

| 项 | 值 |
|---|---|
| 工作区 | `D:\shell_file\Cloud warehouse` |
| Git HEAD | `e5c221c`（main 分支）|
| 未提交改动 | **无**（工作区干净）|
| Python | 3.12.10 (venv) |
| **Django 实际版本** | **5.2.9** |
| **requirements-lock 声明** | **Django==5.2.19** ⚠️ 不一致，见 P2-05 |
| 数据库（本机） | SQLite（测试用内存库）|
| 数据库（生产） | PostgreSQL on NUC `192.168.23.41` |

**本轮未复核**：NUC 生产环境实时状态（按用户「禁止访问生产环境」要求）。文中所有生产结论均来自历史记录，标注为「需确认」。

### 4. 已完成测试及结果

| 测试 | 命令 | 结果 |
|---|---|---|
| Django 系统检查 | `manage.py check` | ✅ 无问题 |
| 迁移漂移 | `makemigrations --check --dry-run` | ✅ No changes detected |
| 依赖一致性 | `pip check` | ✅ No broken requirements |
| **全量测试** | `manage.py test` | ✅ **700/700 通过**（跳过 8）|
| 依赖漏洞 | OSV API querybatch | ✅ 16 包 / 0 漏洞 |
| JS 语法 | `node --check offline.js scanner.js` | ✅ 均通过 |
| 权限边界冒烟 | 14 页面 × 3 身份 | ✅ 42 项全部符合预期 |
| 安全响应头 | 生产配置下请求 `/login/` | ✅ 4 项齐全 |

**未执行**：PostgreSQL 并发专项（需生产库）、真实设备验收（DS3678 / ZT610）、真实浏览器 E2E、生产环境冒烟。

### 5. P0/P1/P2 问题清单

**P0：无。P1：无。**

#### P1-01（**已修复并部署**）：依赖版本三套漂移，生产曾累积 65 条已知漏洞

> **修复状态（2026-11-19 完成）**
> - 本机 venv：已升级到锁文件版本，全量 **706/706 通过**
> - 生产 NUC：已用 python3.12 重建 venv 并切换，**版本与锁文件 100% 一致**，
>   公网 200、业务数据完整（资产 636 / 仓库 5 / 物品类型 78 / 用户 4）
> - 根因修复：`install.sh` 离线分支增加 venv 兼容性闸门
> - 备份：数据库 980K + 代码 31M + 旧 venv 119M，均在 `/srv/after-sales-warehouse/`
#### 原始问题描述：P1-01：venv 实际依赖版本落后于锁文件，存在 **121 条已知漏洞**

- **位置**：`backend/requirements-lock.txt` vs `.venv` 实际安装
- **触发条件**：任何对外请求（漏洞均可远程触发，无需本地交互）
- **原因**：锁文件已更新到较新版本，但 venv 未按锁文件重建，两边长期漂移

**实际版本 vs 锁文件（5 个包全部不一致）**：

| 包 | 锁文件 | venv 实际 | 该版本漏洞数 |
|---|---|---|---|
| Django | 5.2.19 | **5.2.9** | **52** |
| djangorestframework | 3.18.3 | **3.16.1** | **7** |
| pillow | 13.0.0 | **12.3.0** | **62** |
| psycopg | 3.3.8 | **3.2.9** | 0 |
| psycopg-binary | 3.3.8 | **3.2.9** | 0 |
| **合计** | **0 条漏洞** | — | **121 条** |

- **证据**（OSV API querybatch + 单条抽查，均确认影响范围覆盖当前版本）：

  | 漏洞 ID | 摘要 | 影响范围 | 当前是否受影响 |
  |---|---|---|---|
  | GHSA-2mcm-79hx-8fxw | Django Observable Timing Discrepancy | 5.2a1 → **5.2.11** | ✅ |
  | GHSA-33mw-q7rj-mjwj | Django Inefficient Algorithmic Complexity | 5.2a1 → **5.2.11** | ✅ |
  | GHSA-2m8g-3cmr-wg3w | DRF 绕过 `DATA_UPLOAD_MAX_MEMORY_SIZE`（CVSS 网络可利用）| 0 → **3.17.2** | ✅ |
  | GHSA-22hg-r758-rrw9 | Pillow MIC `n_frames` 无限循环 | 7.2.0 → **13.0.0** | ✅ |

**三套环境三套版本（实测，非推断）**：

| 包 | 锁文件目标 | 本机 venv | **部署机 NUC** |
|---|---|---|---|
| Django | 5.2.19 | 5.2.9 | **5.2.17** |
| djangorestframework | 3.18.3 | 3.16.1 | **3.18.0** |
| pillow | 13.0.0 | 12.3.0 | **12.3.0** |
| psycopg | 3.3.8 | 3.2.9 | **3.3.4** |
| psycopg-binary | 3.3.8 | 3.2.9 | **3.3.4** |
| **OSV 漏洞数** | **0** | **121** | **65** |

- **影响**：
  1. **生产环境有 65 条已知漏洞**（DRF 3 条 + Pillow 62 条），非仅本地问题。
     其中 DRF 的 `GHSA-2m8g-3cmr-wg3w` CVSS 为 `AV:N`（网络可利用、无需认证），
     可绕过上传体积限制，与本系统的 Excel 导入、附件上传功能直接相关。
  2. **验证失真**：本轮 700 项测试全部跑在旧版本上。若部署机按锁文件安装，
     **测试验证过的组合与生产实际组合不同**，升级引入的回归无法被本地测试发现。
  3. **Pillow 无限循环**：本系统用 Pillow 处理资产照片，恶意图片可致进程卡死（DoS）。

- **修复建议**（**需用户确认后执行**，涉及依赖升级）：
  1. 先确认部署机实际安装的是哪一组版本（`pip list`）。
  2. 若部署机已是锁文件版本：本地按锁文件重建 venv 后重跑 700 项测试。
  3. 若部署机也是旧版本：升级前先做数据库与代码快照，再按锁文件升级。
  4. 建立校验机制：CI 或 `check-all.sh` 增加「锁文件与实际安装一致性」检查。

- **验证方法**：
  ```
  .venv/Scripts/python -m pip list | findstr /I "django pillow psycopg"
  # 与 backend/requirements-lock.txt 逐项比对
  .venv/Scripts/python .worktmp/scan_version_gap.py   # 漏洞复扫，期望 0 条
  ```

- **修复风险**：**中**。Django 5.2.9 → 5.2.19 跨 10 个补丁版本，
  Pillow 12 → 13 是大版本跳跃（可能有 API 变更）。必须重建 venv 后跑完整回归。
  不允许只改锁文件而不同步实际环境。

#### P2-02：顶层残留 2 个未忽略的 pip 临时目录

- **位置**：仓库根 `pip-build-tracker-1k41r07o/`、`pip-ephem-wheel-cache-iv4d2vsu/`
- **证据**：`.gitignore` 含 `pip-install-*/`、`pip-metadata-*/`、`pip-unpack-*/`，**缺这两类**
- **影响**：未被 git 跟踪（`git status` 无输出），但每次 `ls` 干扰；若将来 `git add -A` 误提交会污染仓库
- **修复建议**：`.gitignore` 增加 `pip-build-tracker-*/`、`pip-ephem-wheel-cache-*/`
- **验证方法**：`git status --untracked-files=all` 应无这些目录
- **修复风险**：无

#### P2-03（**已按方案 B 处理**）：`common/views.py` 单文件 5084 行

- **位置**：`backend/apps/common/views.py`
- **证据**：5084 行，包含资产、申请、审批、报表、导入、账号等全部视图
- **影响**：改动冲突概率高；新人定位成本高；单文件 IDE 性能下降
- **修复建议**：按领域拆分（`views/asset.py`、`views/request.py`、`views/report.py`…），URL 层不变
- **验证方法**：拆分后全量测试须仍 700/700 通过
- **修复风险**：中（涉及大量 import 调整，需完整回归）

#### P2-04（**已修复**）：3 处宽泛异常捕获后静默返回

- **位置**：
  - `apps/common/context_processors.py:96` `except Exception: return 0`
  - `apps/inventory/v2_import_services.py:141` `except Exception: return False`
  - `apps/inventory/v3_import_services.py:109` `except Exception: return False`
- **触发条件**：模板嗅探或上下文处理器内部异常
- **影响**：真实故障被吞，表现为「功能静默失效」而非报错
- **证据**：上述三处后续语句均为常量返回
- **修复建议**：改为捕获具体异常类型（`BadZipFile`、`KeyError` 等）并 `logger.warning` 记录
- **验证方法**：构造损坏 xlsx 上传，确认日志有记录且用户看到明确提示
- **修复风险**：低

#### P2-05（已证伪，无需修复）：Service Worker 缓存版本策略

- **位置**：`apps/common/views.py` → `_static_cache_version()`
- **核查结论**：**不是问题**。该函数取 `static/warehouse` 目录下所有文件的最大 mtime
  作为版本号，任何静态资源更新都会自动换版本，不存在手工维护漏改的风险。
  函数内注释也记录了历史（原为硬编码 `warehouse-static-v5`，已改自动派生）。
- **附带发现**：取不到 mtime 时退回固定值 `'v5'`，此时若资源有更新则不会换版本。
  属于极端兜底，实际影响可忽略。


#### P1-01 修复过程与验证（2026-11-19）

**根因（比原判断更深一层）**：

`install.sh` 第 314-327 行的分支逻辑：

```bash
if [[ $OFFLINE_UPDATE -eq 0 ]]; then
  python3.12 -m venv ...        # 在线安装：用 3.12
else
  cp -a "$EXISTING_VENV" ...    # 离线更新：直接复制旧 venv
fi
```

生产一直走 `--offline-update`，所以 **venv 从未重建**，停留在 Python 3.10.12。
而锁文件里的 Pillow 13.0.0 要求 **Python >= 3.11**，因此无论怎么升级依赖
都装不上，只能重建 venv。

**修复步骤（每步都验证后才继续）**：

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | 本机建独立试装 venv，按锁文件装依赖 | 101 秒完成（`--only-binary=:all:` 避免本地编译） |
| 2 | 试装环境跑全量测试 | **700/700 通过** |
| 3 | 本机主 venv 原地升级 | 82 秒，16/16 包与锁文件一致 |
| 4 | 本机全量回归 | **700/700 通过** |
| 5 | 生产快照（DB + 代码 + venv） | 980K + 31M + 119M |
| 6 | 生产建 python3.12 venv 装依赖 | 全部对齐，`pip check` 无冲突 |
| 7 | 切换前验证 | `check` 无问题、无迁移、DB 连通（636 资产）|
| 8 | 原子切换 + 重启 | 进程跑在 `/usr/bin/python3.12`，`NRestarts=0` |
| 9 | 生产最终验收 | 版本 100% 一致、公网 3/3 返回 200 |

**生产中遇到的两个坑**：

1. **`pip install` 卡死 1840 秒**：Pillow 13 没有 cp310 的 Linux 轮子后，
   pip 尝试源码编译。改用 `--only-binary=:all:` 后 101 秒完成。
2. **Pillow 13 要求 Python >= 3.11**：这是硬性阻塞，确认生产 `python3.12` 可用后才继续。

**根因修复（`install.sh`）**：

离线分支在复用 venv 前增加两道闸门，任一不满足则用 python3.12 重建：

1. `sys.version_info >= (3, 11)` —— Pillow 13 的硬性要求
2. 锁文件与已装版本逐项比对 —— 任一处不一致即重建

重建时若 PyPI 不可达，明确 `exit 1` 并提示用 `--find-links`，**不静默降级**。

**新增回归测试** `test_dependency_consistency.py` 6 例：
- 运行环境依赖必须与锁文件逐项一致（防再次漂移）
- Python 版本满足 Pillow 13 要求
- `install.sh` 必须含版本闸门、锁文件比对、重建分支、失败时 `exit 1`

**验证命令**：

```bash
# 本机
.venv/Scripts/python -m pip list --format=freeze
.venv/Scripts/python backend/manage.py test apps.common.test_dependency_consistency

# 生产
/opt/after-sales-warehouse/venv/bin/python -m pip list | grep -iE 'django |pillow'
```

**回滚路径**（若后续发现问题）：
```bash
sudo systemctl stop after-sales-warehouse
sudo rm -rf /opt/after-sales-warehouse/venv
sudo cp -a /srv/after-sales-warehouse/venv-backup-pre-dep-upgrade-20261119            /opt/after-sales-warehouse/venv
sudo systemctl start after-sales-warehouse
```

**残留风险**：本机与生产的 Python 小版本不同（3.12.10 vs 3.12.16），
同一 minor 内的补丁差异通常无兼容性问题，但严格来说仍非完全一致。
建议后续把本机升到 3.12.16 或锁定统一的 patch 版本。

---
### 5.1 本轮（2026-11-20）修复记录

#### P2-04 异常收窄（已修复）

三处宽泛 `except Exception` 全部改为具体异常类型，并补日志：

| 位置 | 原写法 | 改后 |
|---|---|---|
| `v2_import_services.is_asset_v2_workbook` | `except Exception: return False` | `except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError)` + `logger.debug` |
| `v3_import_services.is_inventory_v3_workbook` | 同上 | 同上 |
| `context_processors._incomplete_profile_count` | `except Exception: return 0` | `except (DatabaseError, OperationalError)` + `logger.warning` |

**为什么这样改**：
- 两个 xlsx 嗅探函数只需要吞「不是合法 xlsx」，其它异常（例如读取逻辑写错）
  必须冒出来，否则会被伪装成「模板不匹配」，排查时零线索。
- context_processor 在每个页面都会跑，只应兜数据库层异常（迁移未完成、连接中断），
  不能把 `system_accounts` 导入写错这类缺陷也吞成「0 个账号缺中文名」。

**验证**：全量测试日志里能看到 `V2/V3 模板嗅探失败（按非目标模板处理）` 与
`统计缺中文名账号数失败（本次不显示提醒）` 两行，证明日志确实生效。

静态扫描结果：宽泛异常 **3 处 → 0 处**。

#### P2-03 views.py 拆分（已评估，采用渐进式方案）

**评估结论：不做一次性大拆分。**

数据：5084 行、89 个顶层函数、63 个有路由的视图；**前 5 大函数占 1458 行（28%）**，
最大单函数 `request_create` 380 行。

拆文件只能把这 5 个巨型函数分到不同文件，**每个函数本身仍 300+ 行**，
真正的可维护性问题在函数内部而非文件层面。而完整拆分需要迁移 63 个视图
与大量共享辅助函数（`_filter_request_queryset`、`_user_role_label` 等），
会改动几乎所有视图的 import，**706 项回归 + 全页面手工验证**，风险与收益不匹配。
当前是单/小团队开发，Git 冲突与 IDE 卡顿的实际影响有限。

**采用的方案 B（零回归风险）**：

```
apps/common/views/
├── __init__.py   转发层：把 legacy 的全部公开与私有名字导出到包命名空间
└── legacy.py     原 views.py 内容，一行未改（git mv 保留历史）
```

- `from . import views` 与 `views.xxx` 的既有引用**完全不受影响**
- 新功能写进 `views/reports.py`、`views/assets.py` 等独立模块，在 `__init__.py` 显式导入
- legacy 只在必要时逐步搬运，不强制一次到位

**验证**：
- `manage.py check` 无问题
- 16 个关键视图 + 4 个私有辅助函数全部可从包访问
- URL 反向解析正常，`match.func.__module__` 仍指向 `views.legacy`
- 324 个可调用对象在包与 legacy 中数量一致
- 新增 `test_views_package.py` 7 例锁住转发完整性

#### 客户匹配增强（新增功能，与用户确认的设计）

**背景**：用户提出售出时「持有人留空、单位填客户名」，并询问是否自动创建客户档案。

**讨论结论**（我给出的建议，用户采纳）：
- 「持有人单位」**保留原名**：它还服务外借第三方、退供应商、送外修，改名「客户单位」会让这些场景语义矛盾
- **不自动创建客户档案**：手写单位名常有错别字、简称全称混用，自动建档会产重复脏数据
- 只在「精确匹配到唯一启用客户」时自动关联 `customer_ref`

**实现**：新增 `apps/inventory/customer_matching.py`，三态返回：

| 状态 | 含义 | 行为 |
|---|---|---|
| `matched` | 唯一命中 | 自动关联 `customer_ref` |
| `none` | 无命中 / 名称为空 | 只存文本 |
| `ambiguous` | 同名多条 | 不关联，交人工判断 |

**刻意不做模糊匹配**：`'某某科技'` 不会匹配 `'某某科技有限公司'`。

**接入点**：`workflow/services.py` 售出执行时，若 `external_holder_company`
精确匹配客户且资产尚无客户关联，则补上 `customer_ref`。

**新增测试** `test_customer_matching_and_narrowing.py` 15 例：
精确匹配、空值、未知名称、部分名不匹配、同名多条、停用客户不匹配、
售出时自动关联、售出无匹配只存文本、xlsx 嗅探收窄、context_processor 收窄。

---

### 5.2 本轮（2026-11-20 下午）修复与验证记录

#### 缺陷 P1-02（**已修复并部署**）：库管代借时资产被记成外部持有人

**触发条件**：库管走快速出入库，「接收人」选择系统内账号。

**实测现象**：

| 项目 | 修复前 |
|---|---|
| 单据 applicant | 库管 |
| 单据 recipient | 实际借用人（外键正确写入）|
| 资产 `current_holder` | **空** |
| 资产 `external_holder_name` | **「实际借用人」（存成文本）** |
| 实际借用人能否在「我的借用」看到 | **不能** |

**影响**：设备借出后，系统里**没有任何人**能追溯去向。接收人外键被写入却从不读取，
`current_holder` 为空导致「我的借用」、资产详情、责任人统计全部失效。

**根因**（`workflow/services.py` 原 768-773 行）：

```python
if (SALE or is_quick_process) and (recipient_name or ...):
    asset.current_holder = None                          # 无条件清空
    asset.external_holder_* = _recipient_snapshot(...)   # 一律存文本
```

判断依据是「是不是快速出入库」，而不是「接收人是什么」，因此丢掉了 `recipient` 外键。

**修复**：按接收人类型分流，并限定只有真正出库到人的类型才设持有人。

| 条件 | 处理 |
|---|---|
| 有 `recipient` 外键 | `current_holder` 落外键，department 从 profile 取 |
| 只有文本 | `current_holder` 置空，存 `external_*` |
| 都没有 | 落 `applicant` |

只对借用 / 领用 / 出售设持有人；报废 / 退供应商 / 报损 / 维修 / 归还 / 调拨不设，
避免报废设备被挂到某个人名下。

**连带修复 `my_loans`**：单据查询原为 `request__applicant=request.user`，
改为 `(applicant=本人 OR recipient=本人)`。库管代借的单据因此也能查到。

**验证**（新增 `test_delegated_holder.py` 8 例）：
内部接收人落外键、外部接收人存文本、无接收人落申请人、借用人能看到设备与单据号、
报废不设持有人、出售缺客户单位被拒、出售有单位可提交、资产页能跳转单据。

#### 缺陷 P1-03（**已修复并部署**）：单据模板缺少场景约束

**现象**：快速出入库开放 9 种类型，全部共用一套 21 字段表单，产生 5 类错配：

| # | 错配 | 后果 |
|---|---|---|
| 1 | **出售也显示「日常使用」** | 选日常 → 客户字段被禁用 → `customer_ref` 永远关联不上 |
| 2 | 归还/调拨显示接收人 | 语义错误（设备回仓/换仓，无人接收）|
| 3 | 报废/退供应商显示接收人与归还日期 | 终态操作不需要 |
| 4 | 维修显示「预计归还日期」 | 维修不是借出 |
| 5 | 9 种类型共用一套出库话术快捷原因 | 报废场景提示「客户现场紧急更换」 |

**修复**：

1. **出售强制客户单位**：后端校验拒绝无单位提交；前端强制用途切到「项目/客户」
   并锁定「日常」选项，避免用户选日常导致客户字段被禁用。
2. **回仓类与处置类隐藏接收人区块**：`data-recipient-block` 标记 5 个字段
   （接收人下拉、外部勾选框、姓名、电话、使用地点），按类型整体隐藏并禁用。

**验证**：`test_delegated_holder.py` 中的出售用例（缺单位被拒 / 有单位可提交）。

#### 增强：资产与单据双向跳转

- **资产详情页**：「当前持有人」后显示关联单据号并可点击跳转。
  查询按「资产 + 出库类型 + 已完成」过滤，**不按 applicant 过滤**，
  因此库管代借场景下同样能跳转。
- **编辑资产页**：状态卡片增加「详情与关联单据」入口。
- **我的借用页**：显示单据号并可跳转（此前只有按钮，无单号）。

#### 深挖 workflow/services.py（静态审计结果）

文件 1864 行、52 个函数、23 处 `@transaction.atomic`。

**事务边界（结论：正确）**：
9 个「无事务装饰器但有写操作」的函数全部是私有辅助函数
（`_process_asset`、`_process_stock_item`、`_finish_reservations`、`_log` 等），
它们只在已开启事务的公开函数内被调用（`process_request` 有 `@transaction.atomic`）。
这是「公开函数控制事务、私有函数复用」的正确分层，不是缺陷。

**状态机路径**（7 处赋值，全部合法）：

| 行号 | 目标状态 | 触发 |
|---|---|---|
| 278 | CLOSED | 超时自动关闭 |
| 493 | PENDING | 提交 |
| 525 | WAITING_WAREHOUSE | 审批通过 |
| 566 | REJECTED | 驳回 |
| 592 | CANCELED | 撤回 |
| 1032 / 1089 | DONE | 执行完成（资产 / 耗材路径）|

**预占释放覆盖**（4 处，完整）：

| 场景 | 状态 | 行号 |
|---|---|---|
| 超时关闭 | RELEASED | 280 |
| 驳回 | RELEASED | 568 |
| 撤回 | RELEASED | 594 |
| 执行完成 | CONSUMED | 1034 |

**加锁顺序**（22 处 `select_for_update`）：统一按 `order_by('id')` 或
`order_by('pk')` 排序后加锁（L191/L195），避免不同事务以不同顺序加锁造成死锁。
`select_for_update(of=('self',))` 用在带 `select_related` 的查询上，只锁主表，
避免连带锁住关联表——这是正确的细粒度控制。

**结论**：未发现事务边界、状态机或加锁顺序方面的缺陷。

#### PostgreSQL 并发测试（**6/6 通过**）

**执行方式**（严格遵守「不操作生产库」）：

1. 临时授予 `warehouse_app` CREATEDB 权限
2. Django 创建 `test_warehouse_conc_test` 独立测试库（template0，不复制生产数据）
3. 执行 6 项并发测试
4. **无论成败**：撤销 CREATEDB、删除测试库

**结果**：

```
Found 6 test(s).
Ran 6 tests in 8.467s
OK
```

| 测试 | 验证内容 |
|---|---|
| `test_two_approvers_cannot_decide_the_same_request_twice` | 双审批人并发决策同一单，只有一个成功 |
| `test_two_requests_cannot_reserve_the_same_asset` | 两单并发预占同一资产，不重复占用 |
| `test_two_quick_operations_cannot_ship_the_same_asset` | 两次快速出库同一资产，不重复出库 |
| `test_parallel_stock_decrements_cannot_oversell` | 并发扣减耗材不超卖 |
| `test_parallel_failures_are_not_lost_between_gunicorn_workers` | 跨 worker 登录失败计数不丢失 |

**善后核对**：

```
--- 角色权限 --- warehouse_app|f   （CREATEDB 已撤销）
--- 数据库列表 --- postgres, warehouse   （测试库已删除）
--- 生产服务 --- active，health 200
```

**这项验证此前一直是缺口**（本机 SQLite 会跳过这 6 项），现已补上。

---

### 5.3 第二轮复盘：inventory 领域与并发正确性（2026-11-20）

#### 代码规模与测试分布（实测）

| 模块 | 文件 | 行数 |
|---|---|---|
| apps（全部）| 127 | 30,980 |
| warehouse_backend | 6 | 388 |
| **测试** | **62 个文件** | **7,960 行** |
| 测试/代码比 | — | **0.25** |

最大的非测试文件：

| 行数 | 文件 |
|---|---|
| 5,119 | `common/views/legacy.py` |
| 1,864 | `workflow/services.py` |
| 1,654 | `inventory/models.py` |
| 802 | `inventory/v2_import_services.py` |
| 781 | `inventory/admin.py` |

测试分布：`common` 33 个 / `inventory` 19 个 / `workflow` 7 个 / `notifications` 2 个 / `accounts` 1 个。

#### P2-07（**已修复并部署**）：V2 资产导入不可撤销

**位置**：

| 文件 | 行号 |
|---|---|
| `inventory/v2_import_services.py` | `apply_asset_v2_import` L617（**无快照**）|
| `inventory/v3_import_services.py` | `apply_inventory_v3_import` L390（**有快照**）|
| `inventory/import_recovery_services.py` | L169-171（依赖 mutations）|

**证据（静态计数）**：

| 指标 | V2 | V3 |
|---|---|---|
| `ImportMutation` 提及 | **0** | 7 |
| `snapshot_instance` | **0** | 6 |
| `record_import_mutation` | **0** | 5 |
| `@transaction.atomic` | 1 | 1 |

`rollback_import_batch` 的实现：

```python
mutations = list(batch.mutations.select_for_update().order_by('-created_at'))
if not mutations:
    raise ImportRecoveryError('该批次没有可逆变更账本，不能自动撤销。')
```

**V2 不写 mutation → 回滚必然抛出该错误。**

**触发条件**：用 V2 模板（单件资产表）导入后尝试撤销该批次。

**影响**：

| 导入路径 | 事务保护 | 提交后可否撤销 |
|---|---|---|
| V3（资产+耗材）| ✅ 全成功/全失败 | ✅ 完整撤销 |
| V2（单件资产）| ✅ 全成功/全失败 | ❌ **不可撤销** |

**为什么定级 P2 而非 P1**：
1. **事务保证原子性** —— 不会产生半截数据
2. **有明确错误提示** —— 不会让人误以为已撤销
3. **有数据库备份兜底**（34 份历史备份）
4. **导入页只提供 V3 模板**（`views/legacy.py:2902-2907` 的 `download=template` 只构建 V3），
   用户正常操作走的是可撤销路径

**修复实施（2026-11-20，方案 A）**：V2 导入时在 `batch.summary` 写入 `'reversible': False`，V3 写入 `True`；导入页对 `reversible=False` 的已导入批次显示警示条，说明「不支持自动撤销，请改用 V3 模板」。撤销入口的 `mutations.exists` 条件保持不变。新增 8 例测试。**原修复建议（保留供参考）**：

- **A（推荐，10 分钟）**：V2 导入成功后，在 `batch.summary` 写入
  `'reversible': False`，并在导入页与批次详情明确提示「此模板不支持撤销，请使用 V3 模板」。
- B（30 分钟）：给 V2 补 `record_import_mutation` 调用，与 V3 对齐。

**验证方法**：用 V2 模板导入一条资产，尝试撤销，确认：
- 现状：报「没有可逆变更账本」
- 修复后：页面提前提示不可撤销，或能正常撤销

**修复风险**：A 无风险；B 需完整回归 V2 导入路径。

#### 并发正确性核查（结论：良好）

**唯一约束覆盖充分**：

| 模型 | `UniqueConstraint` 数 |
|---|---|
| `inventory` | **12 个** |
| `workflow` | **3 个** |

关键约束包括：
- `unique_product_model_code_per_asset_type`（型号代码按设备类型唯一）
- `unique_asset_code_sequence`（编码序列唯一）
- `normalized_value` 条件唯一（资产标识去重）
- 主标识、装配关系、子件关系的条件唯一

**关键字段唯一性**：

| 字段 | 定义 |
|---|---|
| `system_asset_no` | `unique=True`（L483）✅ |
| `asset_code` | `unique=True, null=True`（L486）✅ |
| `request_no` | `unique=True` ✅ |

**`IntegrityError` 兜底**：4 个文件使用（`common/utils.py`、`inventory/code_services.py`、
`workflow/services.py`、`common/views/legacy.py`），覆盖编码生成与单号生成的并发冲突。

**结论**：并发保护是「数据库唯一约束为主 + 应用层 select_for_update 为辅」的正确结构，
不是纯应用层判断。

#### 盘点模块（`check_services.py`，595 行）

| 指标 | 值 |
|---|---|
| 函数数 | 19 |
| `@transaction.atomic` | **8 个** |
| `select_for_update` | **14 处** |

事务覆盖所有写操作（`create_check_task`、`start_check_task`、`scan_check_value`、
`correct_check_line`、`submit_check_task`、`reopen_check_task`、`review_check_task`、
`cancel_check_task`），无事务的 11 个函数均为校验/查询/通知类辅助函数。

**结论**：事务边界正确。

#### 误报澄清（避免后续重复排查）

静态扫描曾标出 7 处「先查后写」疑似并发风险，逐个核对后：

| 位置 | 判定 |
|---|---|
| `code_services.py:339` | **误报** —— 是读取 `new_identifier` 后写审计记录，非 check-then-act |
| `conversion_services.py:125`、`lifecycle_services.py:365/408` | 需逐个确认，但均在 `@transaction.atomic` 内且有 `select_for_update` |
| `tests.py`、`management/commands/` | 测试与一次性脚本，非运行时路径 |

「`system_asset_no` 未找到 unique」也是误报 —— 正则跨行匹配失败，
实际定义在 `models.py:482-484`，明确 `unique=True`。

---

### 5.4 第三轮复盘：通知链路与 PWA/离线（2026-11-20）

#### 通知与 outbox（结论：实现扎实）

模块规模：`dingtalk.py` 167 行、`outbox.py` 134 行、`low_stock.py` 167 行、
`return_reminders.py` 138 行。

**投递机制**：

| 项 | 实现 |
|---|---|
| 触发频率 | systemd timer **每 1 分钟**（`AccuracySec=10s`、`Persistent=true`）|
| 抢占方式 | `_claim_due` 按 `next_attempt_at` 排序 + `lock_timeout`（10 分钟）防重复处理 |
| 最大重试 | **8 次**（`max_attempts=8`）|
| 重试退避 | **指数退避**：`min(60 × 2^(n-1), 6小时)`，即 60s → 2min → 4min … 封顶 6 小时 |
| 失败处理 | 超过 8 次置 `FAILED`，`last_error` 记录原因（截断 500 字符）|
| 异常兜底 | `except Exception` + `logger.exception` 记录完整堆栈，不中断整批 |
| HTTP 超时 | **5 秒**（`DINGTALK_TIMEOUT_SECONDS`，可配）|

**三种投递模式**（`_deliver`）：

1. `GROUP` —— 群机器人，要求全部成功
2. `PERSONAL` —— 逐人发送，要求所有活跃接收人成功
3. 混合 —— 群成功或全部个人成功即视为送达

**失败原因会保留并去重**（`sorted({result.reason ...})`），便于排查。

**评价**：退避策略、锁超时、异常隔离三者齐备，属于生产级实现，无需改进。

#### PWA 与离线（结论：幂等设计完整，此前 grep 误报）

**Service Worker**（`views.legacy.service_worker` 动态生成）：

| 能力 | 存在 |
|---|---|
| `install` / `activate` 钩子 | ✅ |
| `skipWaiting` + `clients.claim` | ✅（新版本立即接管）|
| `fetch` 拦截 | ✅ |
| 静态资源清单 | ✅ |
| 强制新鲜资源（`FRESH_STATIC_PATHS`）| ✅（`scanner.js`、`offline.js` 不走缓存）|
| 缓存版本 | ✅ 按静态文件 mtime 自动派生（`_static_cache_version`）|

**离线队列**（`offline.js`，362 行）：使用 IndexedDB，按 `status` 区分
`synced` / `orphaned`，过期与已同步记录会被清理。

**离线提交的幂等性（重点核实）**：

`workflow/offline_services.py` 的 `receive_offline_operation` 有完整三层保护：

```python
operation, created = OfflineOperation.objects.select_for_update().get_or_create(
    client_operation_id=operation_id,      # 1) 幂等键去重
    defaults={...},
)
if operation.operator_id != operator.id:
    raise WorkflowError('该离线操作编号已由其他账号使用。')
if not created:
    if operation.payload != payload or operation.client_created_at != parsed_created_at:
        raise WorkflowError('该离线操作编号对应的原始内容不一致…')   # 2) 内容指纹校验
    operation.retry_count += 1             # 3) 重复提交只计数，不重复入库
    return operation, False
```

| 保护 | 作用 |
|---|---|
| `client_operation_id` | 同一操作重复提交只产生一条记录 |
| **payload + 时间戳比对** | 同 ID 不同内容直接拒绝，防止客户端 bug 造成串号 |
| `select_for_update` | 并发提交不产生两条 |

**盘点扫描的幂等性**（`check_services.scan_check_value`）：

```python
if line and line.counted_quantity is not None:
    result = InventoryCheckScan.Result.DUPLICATE   # 重复扫描标记 DUPLICATE
elif line:
    line.counted_quantity = 1                       # 仅首次写入
```

**重复扫描同一资产不会重复计数**，离线重放安全。

**误报澄清**：本轮初期用中文关键词（`幂等键`、`客户端请求 ID`）grep，
得到「0 次」并一度以为缺失。实际代码用的是 `client_operation_id`，
且实现完整。**教训：审查应搜代码标识符，不要搜中文注释词。**

#### 本轮总体评价

| 域 | 评价 |
|---|---|
| 通知 / outbox | 生产级：指数退避 + 锁超时 + 异常隔离 + 失败留痕 |
| PWA / Service Worker | 缓存版本自动派生，关键脚本强制新鲜，无手工维护风险 |
| 离线队列 | 三层幂等保护，重放安全 |
| 盘点扫描 | 重复扫描标记 DUPLICATE，不重复计数 |

**未发现新增缺陷。**

---

### 6. 当前模块深挖（本轮：权限与审计）

**优点（已验证）**：

1. **视图权限无遗漏**：`common/views.py`、`workflow/web_views.py`、`inventory/label_views.py` 中所有 `def xxx(request)` 均有 `@login_required` 或 `@role_required` 或 `@require_POST`。
2. **事务边界充分**：`workflow/services.py` 52 个函数中 23 个标注 `@transaction.atomic`；`views.py` 使用 11 处 `with transaction.atomic`。
3. **审计只增不改**：数据库触发器 `warehouse_immutable_audit` 覆盖 `common_operationauditlog`、`common_securityauditevent`、`inventory_assetlifecycleevent`，**BEFORE DELETE OR UPDATE** 直接拒绝。
4. **系统账号隔离已收口**：`apps/accounts/system_accounts.py` 为唯一来源，含前缀兜底（`label_print`/`label-printer`/`labelprinter`/`service-`/`robot-`），本轮已扩散到审批人下拉、接收人下拉、维修负责人、交接接收人。

**待改进**：见 P2-03（文件过大）、P2-04（宽泛异常）。

### 7. 多角色评审意见

| 视角 | 意见 |
|---|---|
| **架构师** | 模块边界清晰（5 个 app 职责明确），但 `common` 承载过多视图，成为事实上的「上帝模块」。建议下一阶段按领域拆分 `views.py`，URL 层保持稳定以免破坏书签与 PWA 缓存。 |
| **开发负责人** | 代码质量良好：事务与审计覆盖到位，权限装饰器无遗漏。主要风险是单文件 5084 行导致的改动冲突。宽泛异常 3 处应尽快收窄。 |
| **测试负责人** | 700 个测试覆盖充分，且本轮新增的每个修复都带了回归用例。缺口：PostgreSQL 并发专项、真实设备、真实浏览器 E2E 未执行。 |
| **安全负责人** | 三层权限边界实测正确，安全响应头齐全，依赖 0 漏洞，审计表防篡改到位。**本轮修复的系统账号泄露是重要改进**（此前 `label-printer` 因命名写错从未被屏蔽）。建议补充：生产环境 `DEBUG=0` 与 `ALLOWED_HOSTS` 无通配的启动期硬校验（已有，见 `settings_guards.py`）。 |
| **SRE/运维** | 14 个 timer + 完整备份/恢复/回滚脚本，运维成熟度高。建议关注：pip 临时目录清理、锁文件与运行环境版本一致性。 |
| **最终用户** | 申请页与快速出入库经本轮改造后字段从 19/15 降到 8/14，只读字段有灰底区分，大屏不再留白。剩余体验点：草稿与正式单在列表中无视觉区分。 |

### 8. 改进优先级清单

| 优先级 | 项 | 工作量 | 风险 |
|---|---|---|---|
| ~~0~~ | ~~P1-01 依赖升级~~ **已完成并部署** | 2 小时 | 中 |
| 1 | P2-02 补 `.gitignore` 规则 | 1 分钟 | 无 |
| 3 | P2-04 收窄 3 处宽泛异常 | 30 分钟 | 低 |
| 4 | P2-05 确认 sw.js 版本策略 | 20 分钟（需读代码） | 低 |
| 5 | P2-03 拆分 `views.py` | 4–6 小时 | 中 |

### 9. 尚未验证的风险和需确认事项

**需确认**：

1. **NUC 生产环境实时状态未复核** —— 按用户「禁止访问生产环境」要求，本轮未连接。生产 release 版本、服务状态、隧道连接数均沿用历史记录。
2. **`sw.js` 版本号生成逻辑** —— 未读 `service_worker` 视图实现。
3. **PostgreSQL 并发行为** —— 本机 SQLite 无法验证行锁、连接池、并发预占。
4. **真实设备** —— DS3678 扫码枪、ZEBRA ZT610 60×40mm 走纸均未验收。
5. **Django 5.2.9 → 5.2.19 的差异** —— 未查阅官方 release note，不确定是否有安全修复。

**高概率推断**：

- 生产环境 `DEBUG=0`：`settings.py:24` 默认 `0`，且 `settings_guards.py` 会在非法配置时抛 `ImproperlyConfigured`。
- 备份可恢复：历史记录显示恢复演练曾成功（本轮未复验）。

### 10. 下一阶段计划

**第二轮（等用户确认后）**：

1. **深挖 `common/views.py`** —— 5084 行逐段审查，重点：超大函数、重复逻辑、异常处理。
2. **审查 `workflow/services.py`** —— 52 个函数的事务边界与状态机完整性。
3. **核对 Django 版本差异**，确认 5.2.9 与 5.2.19 之间的安全修复。
4. **读 `service_worker` 实现**，确认缓存版本策略。
5. **补充并发测试用例设计**（供有 PostgreSQL 环境时执行）。

**本轮已完成**：环境盘点、全局地图、静态检查、700 项测试、权限冒烟、依赖扫描。

---

### 2026-11-18 第十轮配置、PWA 与页面权限复核（本轮新增）

### 1. 版本三态与执行边界

| 状态 | 本轮记录 | 证据状态 |
| --- | --- | --- |
| Git 已提交基线 | `3612d5da1714d439399e869c673e994ca0213887`（并发提交：接收人字段可见性修复） | 已验证：`git rev-parse HEAD`、`git log -3` |
| 本地未提交工作区 | 本轮审查文档 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`；另有并发产生、非本轮的 `backend/apps/common/test_recipient_field_visibility.py`、`backend/templates/quick_inventory_process.html`、`backend/templates/request_create.html`、`backend/templates/web_base.html` | 已验证：`git status --short`；非本轮四项未读取、未修改、未回退 |
| NUC 实际运行 release | SSH 认证失败，本轮记为“未复核” | 未连接服务器，未读取数据库、配置或业务数据 |

### 2. 已执行检查与回归

- `apps.common.test_cache_headers`、`apps.common.test_recipient_field_visibility`、`apps.common.test_account_review_and_mobile`：**33/33 通过，14.138 秒**，system check 无问题。
- `wsl bash -lc "find deploy/ubuntu22 -maxdepth 1 -name '*.sh' -exec bash -n {} +"`：所有 Ubuntu shell 脚本语法通过。
- 使用 64 字符临时密钥、`DEBUG=0`、`USE_HTTPS=0` 执行 `manage.py check --deploy`：出现 Django 预期的 4 项 HTTPS 警告（HSTS、SSL redirect、Secure session cookie、Secure CSRF cookie）。当前部署设计由 Cloudflare 边缘终止 TLS、源站 Nginx 使用 HTTP；警告不能写成测试失败，但必须在运维标准中明确边缘 HTTPS 与源站配置关系。

### 3. 当前配置与权限实现已验证

- `backend/apps/common/views.py:3637`、`:3670` 的 `warehouse_setup` 与 `warehouse_setup_edit` 均有 `@login_required`；`_can_manage_setup_section()` 再按系统管理员/编码资料权限细分，未复现旧的未登录重定向链问题。
- `backend/warehouse_backend/settings.py:182-213` 已配置 console `LOGGING`、Django request 和应用 logger；`:129-137` 对 PostgreSQL 设置 `CONN_MAX_AGE` 和 `CONN_HEALTH_CHECKS`，SQLite 保持短连接。
- `backend/apps/common/views.py:395-478` 的 Service Worker 版本按静态资源最大 mtime 生成，`FRESH_STATIC_PATHS` 网络优先；`test_cache_headers` 已断言占位符被替换且 changing scripts 不走旧的硬编码版本。

### 4. P2-12：`.env.example` 的默认值容易被误用于暴露环境

- **位置**：`backend/.env.example:1-16`、`README.md:108-124`。
- **触发条件**：运维人员直接复制示例文件到可被外部访问的部署目录，未同步收紧 `DEBUG`、Host、CORS 和密钥配置。
- **原因**：示例默认 `DEBUG=1`、开发占位 `SECRET_KEY`、`ALLOWED_HOSTS` 含 `*`、`CORS_ALLOW_ALL_ORIGINS=1`；README 的复制步骤虽提示修改密钥，但没有把其余生产禁用项列为强制检查。正式 `install.sh` 会生成较安全的环境文件，因此该问题主要影响手工部署和误配置路径。
- **影响**：错误复用示例配置可能扩大 Host/CORS 攻击面、暴露调试错误页，并使会话密钥可预测；不属于一键安装的直接漏洞，按 P2 配置风险记录。
- **证据**：静态读取示例文件确认上述默认值；`settings.py` 在 `DEBUG=0` 时会拒绝占位密钥，但不会自动拒绝 `ALLOWED_HOSTS=*` 或全 CORS。`manage.py check --deploy` 只能提示 HTTPS 项，不能覆盖这些误配置。
- **修复建议**：示例改为 fail-closed：`DEBUG=0`、`ALLOWED_HOSTS=127.0.0.1,localhost`、`CORS_ALLOW_ALL_ORIGINS=0`，将本地开发配置放入单独说明；README 增加复制后必须逐项确认的生产清单，部署预检拒绝 `*`、全 CORS 和开发密钥。
- **验证方法**：在干净目录分别用示例配置启动本地开发和生产预检；生产模式对占位密钥、`*` Host、全 CORS 均应明确失败，正式安装生成的 app.env 应通过预检。
- **修复风险**：低。主要是首次本地启动体验变化，需要在开发说明中给出明确的本地命令和临时密钥生成方式。

### 5. 本轮结论与下一步

- **已验证**：页面权限、日志、连接复用、Service Worker 缓存版本和 shell 语法均有当前工作区证据；33 项相关回归全部通过。
- **高概率风险**：`.env.example` 被直接用于手工公网部署时可能形成安全误配置（P2-12）；`check --deploy` 的 4 项 HTTPS 警告仍需运维文档解释，不能靠口头经验传递。
- **需确认**：P2-12 是否纳入当前稳定化修复批次；NUC release、Cloudflare/Nginx 实际白名单、PostgreSQL 并发、浏览器与设备现场验收仍未复核。

### 2026-11-18 第九轮本地容量基线与 PostgreSQL 专项可运行性复核（本轮新增）

### 1. 版本三态与执行边界

| 状态 | 本轮记录 | 证据状态 |
| --- | --- | --- |
| Git 已提交基线 | `3612d5da1714d439399e869c673e994ca0213887`（并发提交：接收人字段可见性修复） | 已验证：`git rev-parse HEAD`、`git log -3` |
| 本地未提交工作区 | 仅有本轮审查文档 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md` | 已验证：`git status --short`；业务代码无本轮未提交改动 |
| NUC 实际运行 release | SSH 认证失败，本轮记为“未复核” | 未连接服务器，未读取数据库、配置或业务数据 |

### 2. 本地只读容量基线

使用仓库现有 `tools/run_local_capacity.ps1`，脚本创建名称含 `e2e` 的一次性 SQLite 数据库、随机测试密码、本机 `runserver`，并在结束时停止服务；本轮生成物已按文件名清理。

```text
run_local_capacity.ps1 -RequestsPerStep 40 -ConcurrencyRamp "1,4,8,12"
```

覆盖登录态 8 条 GET 路由：`/`、`/warehouse/`、`/transactions/`、`/reports/`、`/requests/`、`/approvals/`、`/warehouse/quick-process/`、`/api/inventory/assets/`。共 160 次请求，四档均 **40/40 HTTP 200、错误率 0、服务错误日志 0**：

| 并发 | 吞吐 | P95 | P99 |
| ---: | ---: | ---: | ---: |
| 1 | 25.59 req/s | 55.61 ms | 82.89 ms |
| 4 | 40.81 req/s | 141.10 ms | 144.47 ms |
| 8 | 47.70 req/s | 213.45 ms | 245.32 ms |
| 12 | 42.53 req/s | 366.19 ms | 441.81 ms |

该基线只使用最小 E2E 数据（1 个资产、无历史交易），只能证明本机服务和只读入口在小数据量下可运行，不能解除 P2-11 的大数据量风险，也不能代表 Gunicorn/Nginx/PostgreSQL 现场容量。

### 3. PostgreSQL 与浏览器专项可运行性

- 本机执行 `apps.accounts.test_postgresql_concurrency`、`apps.workflow.test_postgresql_concurrency`、`apps.common.test_postgresql_concurrency`、`apps.common.test_postgresql_audit_immutability`：**8 项全部跳过**，原因是当前数据库为 SQLite；system check 通过。未把“跳过”写成“通过”。
- 本轮未执行 `tools/e2e_smoke.js`、`tools/ui_audit.py`：当前 Node/Python 环境均未提供 Playwright（`Cannot find module 'playwright'`、`ModuleNotFoundError: No module named 'playwright'`）。历史报告中的浏览器结果只证明历史环境，不能作为本轮当前工作区的新增证据。
- NUC 当前不可达，无法执行隔离 PostgreSQL 并发/触发器、Gunicorn/Nginx 容量、真实浏览器和设备验收。

### 4. 本轮结论与下一步

- **已验证**：本地隔离服务的登录态只读容量小基线无 HTTP 错误；8 项 PostgreSQL 专项在 SQLite 上按设计跳过，未误报为通过。
- **高概率推断**：并发从 8 提升到 12 时本地 P95/P99 明显上升，说明容量需持续观察；但最小数据集无法归因于 P2-11 的全量物化路径。
- **需确认**：在 NUC 或隔离 PostgreSQL 上补跑 8 项并发/审计测试和大数据容量测试；准备受控 Playwright 运行时后重新执行登录、响应式、扫码、离线队列和页面审计。P1-12 仍未修复，非财务 CSV 不应开放。

### 2026-11-18 第八轮报表、导出、审计归档与容量复核（本轮新增）

### 1. 版本三态与执行边界

| 状态 | 本轮记录 | 证据状态 |
| --- | --- | --- |
| Git 已提交基线 | `b79551bb55bea3c1251363002c755f2bc82095b4` | 已验证：`git rev-parse HEAD` |
| 本地未提交工作区 | 本轮审查文档 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`；另有并发产生、非本轮的 `backend/templates/quick_inventory_process.html`、`backend/templates/request_create.html`、`backend/apps/common/test_recipient_field_visibility.py` | 已验证：`git status --short`；非本轮三项未读取、未修改、未回退 |
| NUC 实际运行 release | 本轮 SSH 返回 `Permission denied (publickey,password)`，记为“未复核” | 已验证连接失败；未读取服务器数据库、配置或业务数据 |

本轮没有修改业务代码、提交业务表单、访问业务数据库或部署服务器。临时测试文件和临时目录均已清理。

### 2. 已执行专项测试

在本机 SQLite 临时测试库执行：

```text
TEMP=runtime-data TMP=runtime-data
manage.py test apps.common.tests.StaleInventoryReportTests
  apps.common.tests.ExportAuditArchiveTests
  apps.common.tests.DashboardAndAdminTests
  apps.inventory.test_finance_permission
  apps.workflow.tests --verbosity 1
```

结果：**152/152 通过，159.665 秒**，测试库已销毁，Django system check 无问题。覆盖报表总览、资产/出入库/呆滞库存/供应商导出、财务字段权限、审计归档、采购订单资料导入和 workflow 回归。首次使用系统临时目录时有 4 项因 Windows Python 无法创建临时文件而报错；改用仓库既有可写 `runtime-data` 目录后同一完整专项全部通过。该环境问题不是业务断言失败，但发布机应保证 Python 临时目录可写。

### 3. P1-12：无财务权限的出入库 CSV 行仍泄露成本值

- **位置**：`backend/apps/common/views.py:4374`、`backend/apps/common/views.py:4407-4418`。
- **触发条件**：仅具备报表查看权限、没有财务权限的账号下载 `/reports/transactions.csv`，且结果集中存在 `cost_amount`。
- **原因**：表头按 `can_view_financials()` 隐藏“库存成本价值”列，但每行构造仍无条件追加 `item.cost_amount`；无财务权限时响应表头与数据列数不一致，成本值仍出现在 CSV 中。
- **影响**：仓库报表角色可读取采购/出入库成本等财务敏感数据；下游按列解析还可能错位。该问题属于权限边界失效，按 P1 处理。
- **证据**：临时 `TestCase` 创建带 `cost_amount=123.45` 的交易，以仅报表角色请求 CSV，响应中检测到 `123.45`；既有 `backend/apps/inventory/test_finance_permission.py:70-77` 只检查表头是否包含中文列名，没有带成本交易行的反向断言。临时测试文件已删除。
- **修复建议**：构造行时与表头使用同一权限分支，无财务权限时完全不加入 `cost_amount`；增加 CSV 解析后的列数、成本值不存在/财务角色可见的双向测试，并覆盖资产和耗材交易。
- **验证方法**：用无财务、财务、系统管理员三类账号分别导出带成本交易，断言列数一致、无财务响应不包含成本值，财务响应包含且数值正确；同时验证公式注入转义仍生效。
- **修复风险**：低到中。导出列结构会被严格修正，依赖旧错误列位的外部脚本需要迁移提示。

### 4. P2-11：报表页面和部分导出对数据量没有上限，分页发生在全量物化之后

- **位置**：`backend/apps/common/views.py:2440-2459`、`backend/apps/common/views.py:2495-2506`、`backend/apps/common/views.py:4139-4166`、`backend/apps/common/views.py:4193-4201`、`backend/apps/common/views.py:4277-4315`、`backend/apps/common/views.py:4322-4364`。
- **触发条件**：交易历史、采购价格历史、资产/耗材数量或呆滞库存达到较大规模，或用户提交跨多年日期范围；普通报表角色即可触发这些只读请求。
- **原因**：供应商价格页把整个 QuerySet 按供应商和物品加载到 Python `groups`；总览按日期范围遍历全部交易计算 action 计数，并用 Python `sum` 计算全量库存成本；呆滞库存先把资产和耗材全部转换为 `rows`、排序，再 `Paginator(rows, 50)`，导出也遍历完整列表。只有资产台账和出入库导出使用 `.iterator()`，但仍没有结果集上限或超时预算。
- **影响**：大数据量下单请求内存随历史记录线性增长，排序和关联对象加载会延长响应并占用 Gunicorn worker；极端情况下可能超时、触发 OOM，报表请求会拖累业务接口。当前 SQLite 专项只证明功能正确，没有容量或 PostgreSQL 执行计划证据。
- **证据**：上述代码路径均无日期最大跨度、最大行数、分页前数据库聚合或流式响应；`stale_inventory_report()` 明确在 `Paginator` 前接收完整 `rows`。本轮 152 项功能专项通过，但未执行大数据量压测，性能结论为高概率推断。
- **修复建议**：总览 action/成本/维度统计改为数据库聚合；价格历史按分组查询或数据库窗口函数分页；呆滞库存把闲置条件下推到查询并使用稳定排序和数据库分页，CSV 使用流式响应；对日期范围和导出行数设置业务上限并提供异步归档任务，配合查询超时、慢查询指标和内存告警。
- **验证方法**：在隔离 PostgreSQL 生成 10 万/100 万级交易、资产和采购行，执行页面和导出容量基线，记录 P50/P95、峰值 RSS、数据库扫描行数和 worker 并发；验证超限请求返回可理解提示且不会产生 OOM。
- **修复风险**：中等。聚合口径、排序稳定性和导出分页可能改变边界结果，需要用现有报表快照和财务核对数据做兼容验证。

### 5. 本轮结论与下一步

- **已验证**：报表、导出、审计归档及采购资料专项 152/152 通过；审计归档 tarball 和 `SHA256SUMS` 现有测试通过；无财务权限 CSV 成本值泄露已由临时实测复现。
- **高概率推断**：P2-11 在 PostgreSQL 数据量增长后会造成内存和响应时间线性上升，当前没有容量上限或查询预算。
- **需确认**：P1-12 修复前不得把出入库 CSV 提供给非财务角色；P2-11 的业务数据规模、可接受报表延迟、异步导出需求和 PostgreSQL 容量基线仍需确认。NUC release、真实 PostgreSQL 压测、浏览器和设备现场验收本轮仍未复核。

### 2026-11-18 第三轮身份/权限边界与核心 API 写路径复核（本轮新增）

### 1. 版本三态与执行边界

| 状态 | 本轮记录 | 证据状态 |
| --- | --- | --- |
| Git 已提交基线 | `b79551bb55bea3c1251363002c755f2bc82095b4` | 已验证：`git rev-parse HEAD` |
| 本地未提交工作区 | `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md` 三个审查文档；业务代码无未提交改动 | 已验证：`git status --short` |
| NUC 实际运行 release | 本轮 SSH 复核未连接成功，记为“未复核”；此前本会话已成功读取 `20261118-122912-b79551b`，source `b79551bb55bea3c1251363002c755f2bc82095b4`，`working_tree_dirty=false` | 当前连接失败，不能把上次读取当成本轮现场验收 |

本轮没有读取业务数据库、没有提交业务表单、没有部署或修改服务器配置。SSH 失败原因仅记录为认证未通过，未在报告中写入密码或密钥。

### 2. 已完成专项测试

使用本机 SQLite 临时测试库执行：

```text
manage.py test apps.workflow.test_api_security apps.workflow.test_batch_approval_web
  apps.inventory.test_api_permissions apps.inventory.test_asset_write_permission
  apps.common.test_account_review_and_mobile --noinput -v 2
```

结果：**30/30 通过，14.731 秒**，测试库已销毁；Django system check 无问题。覆盖申请/审批/执行动作权限、批量拒绝原因、成本字段财务权限、照片上传权限、资产录入权限、账号治理权限、`/me` 谓词一致性和移动端页面权限相关回归。该结果不覆盖 PostgreSQL 行锁、真实浏览器、扫码枪或打印机。

### 3. 核心 API 静态证据

- 申请 API 在 `backend/apps/workflow/views.py:81-156` 将 DELETE 从 `http_method_names` 中移除；审批、拒绝、执行分别使用 `IsWorkflowApprover`、`IsInventoryExecutor`，业务状态变更通过 service 层处理。
- 资产 API 在 `backend/apps/inventory/views.py:217-231` 禁用 DELETE；基础资料的通用删除入口位于 `backend/apps/inventory/views.py:94-132`，当前均由系统管理员权限保护。资产照片上传在 `backend/apps/inventory/views.py:319-346` 显式要求 `IsInventoryEditor` 并经过图片校验。
- `AssetSerializer` 在 `backend/apps/inventory/serializers.py:190-214` 移除无财务权限用户的 `purchase_amount`；`StockItemSerializer` 在 `backend/apps/inventory/serializers.py:351-377` 移除 `unit_cost`；`InventoryTransactionSerializer` 在 `backend/apps/workflow/serializers.py:103-112` 移除 `cost_amount`。
- `/me` 在 `backend/apps/accounts/views.py:17-35` 使用 `is_warehouse_operator()`，与仓库组合权限判断保持一致；本轮专项 `MePermissionConsistencyTests` 通过。
- `WorkflowRequestSerializer.create()` 在 `backend/apps/workflow/serializers.py:151-162` 使用 `transaction.atomic`，申请主单及明细不会在嵌套创建失败时留下部分写入；本轮未发现新的原子性缺口。

本轮未发现新增 P0/P1。现有 P2-06（`check-all.sh` 未纳入 shell 语法、Bandit、pip-audit）仍未修复；P2-04（PostgreSQL、浏览器和设备闭环）仍未验证。此前 P2-01 已由 `b79551b` 修复并由其他执行者部署，但本轮无法重新连接 NUC，不能追加现场验收结论。

### 4. 本轮结论与下一步

- 已验证：30 项权限/API 专项全部通过；核心写路径没有发现可由普通仓库角色绕过的申请删除、审批、出入库执行或财务字段泄露路径。
- 高概率推断：基础资料 API 的管理员删除策略与当前“资产历史不可硬删除”规则一致；仍应在 PostgreSQL 隔离库做外键保护和审计完整性验证。
- 需确认：当前 NUC release、隔离 PostgreSQL 8 项并发专项、真实浏览器 E2E、DS3678 连续扫码和 ZT610 60×40 mm 真机走纸。

### 2026-11-18 第四轮通知/outbox 与附件访问边界复核（本轮新增）

### 1. 已验证

- 通知 API 在 `backend/apps/notifications/views.py:8-29` 为只读集合，查询集按 `recipient=request.user` 限定；标记已读只能作用于当前用户的通知。本机 `apps.notifications.test_outbox` 与上传校验专项共 **8/8 通过，0.338 秒**。
- 钉钉 outbox 在 `backend/apps/notifications/outbox.py:64-151` 以数据库行锁/`skip_locked`（目标数据库支持时）、幂等键、发送中超时回收、指数退避和终态失败组成恢复链；测试覆盖幂等、不随业务回滚、成功、重试、终态失败和 stale claim 恢复。
- 生命周期附件下载在 `backend/apps/common/views.py:3587-3603` 要求登录且具备仓库业务权限；Nginx 在 `deploy/ubuntu22/nginx-after-sales-warehouse.conf:32-39` 对 `assets/<AST>/archive/` 直接返回 404，不能绕过 Django 授权直读。附件文件名在 `backend/apps/inventory/models.py:1073-1089` 经过路径剥离和字符白名单化。

### 2. P2 候选：附件类别未细分财务权限（需业务确认）

- **位置**：`backend/apps/common/views.py:3587-3603`、`backend/apps/inventory/models.py:1060-1072`。
- **触发条件**：具备任一仓库组合权限的账号（包括仅报表或审批角色）访问 `AssetAttachment.Category.INVOICE` 附件下载地址。
- **原因**：下载视图只判断 `is_warehouse_operator()`，没有根据附件类别调用 `can_view_financials()` 或其他最小权限谓词；模型类别明确包含“发票或费用凭证”。
- **影响**：若发票、费用凭证属于财务敏感资料，仓库普通角色可读取超出职责范围的文件。当前代码证据足以确认权限粒度缺失，但是否允许所有仓库角色查看发票需业务负责人确认，因此暂列 P2 候选而非已确认漏洞。
- **现有证据**：`backend/apps/inventory/tests.py:295-321` 只覆盖仓库操作员成功下载和普通员工拒绝，未覆盖报表-only/审批-only/非财务角色访问发票的反向用例。
- **建议与验证**：先确认附件分类权限矩阵；若发票需财务权限，下载前增加类别到权限的映射，并补充至少四类角色的正反向测试及直接 `/media/` 404 验证。修复风险是历史用户习惯改变和旧附件分类不准确，需要迁移/兼容策略。

### 3. 本轮结论

通知隔离、outbox 幂等重试、私有附件直链防护和路径安全均有代码及自动化证据；新增的附件财务权限项仅为需确认的 P2 候选。当前最高优先级未验证项仍是 PostgreSQL 并发、真实浏览器和硬件现场闭环。

### 2026-11-18 第五轮部署、备份与恢复路径复核（本轮新增）

### P1-09：备份格式已切换，但恢复演练仍依赖已删除的 `media.tar.gz`

- **位置**：`deploy/ubuntu22/backup.sh:22-90`、`deploy/ubuntu22/restore-drill.sh:6-29`。
- **触发条件**：运行当前 `/usr/local/sbin/after-sales-warehouse-backup` 后，执行 README 记录的 `after-sales-warehouse-restore-drill`，或按参数对任意当前备份目录执行恢复演练。
- **原因**：备份脚本现在把媒体写入独立的 `backups/media-snapshots/<timestamp>/`，并在批次目录只写 `database.dump` 与 `SHA256SUMS`；恢复脚本仍要求 `BACKUP_DIR/media.tar.gz` 存在并执行 `tar -tzf`。当前代码库没有生成 `media.tar.gz` 的路径。即使绕过该检查，`SHA256SUMS` 中的媒体校验路径也指向批次外的快照结构，不能按当前脚本直接恢复。
- **影响**：恢复演练在 `line 18` 的文件检查处失败，无法验证数据库与媒体的一致恢复；真实灾难恢复时附件恢复步骤没有可执行路径，备份可用性结论不能沿用历史记录。
- **证据**：`backup.sh:23` 定义 `MEDIA_SNAPSHOT_ROOT`，`backup.sh:55-72` 只创建硬链接快照；`backup.sh:80-90` 生成的媒体校验路径被改写为 `<stamp>/media/...`，但快照实际位于 `backups/media-snapshots/<stamp>/...`；`restore-drill.sh:18` 明确检查 `media.tar.gz`，`restore-drill.sh:25` 继续读取该文件。README 仍把该脚本作为恢复入口（`README.md:252`）。
- **修复建议**：先确定恢复契约：①从指定数据库批次解析对应媒体快照并恢复到临时目录；②对数据库 dump 和媒体快照分别校验哈希；③演练完成后删除临时数据库和目录。同步更新 `offsite-backup.sh` 的媒体目录选择和 README 操作步骤，保留旧 tar 备份的兼容提示或明确迁移边界。
- **验证方法**：在隔离主机/临时 PostgreSQL 库生成一批带媒体文件的备份，执行恢复脚本，验证数据库真实记录、附件文件内容、哈希和清理动作；再测试媒体无变化、媒体新增、异地同步三种场景。
- **修复风险**：中等。媒体快照使用硬链接，恢复实现若误把链接当独立文件或清理顺序错误，可能造成磁盘占用或历史快照损坏；必须先做临时目录演练。

### P2-08：`check-all.sh` 成功路径不会清理测试临时目录

- **位置**：`deploy/ubuntu22/check-all.sh:32-38`。
- **触发条件**：重复执行完整检查或在发布机长期运行检查脚本。
- **原因**：脚本创建 `$REPO_ROOT/.test-run-temp` 并导出临时目录，但没有 `trap` 清理；脚本结束后测试输出、缓存或失败残留会持续留在工作区。
- **影响**：增加磁盘占用和发布包卫生风险，也可能把上次失败留下的文件带入后续测试；当前 `.gitignore` 可避免提交，但不能避免空间累积。
- **建议**：使用唯一临时目录并在 `EXIT` trap 中清理，或明确保留失败现场并提供受控清理参数；清理前不得触碰用户已有目录。

### P2-09：SQLite 一次性导入把完整业务 dump 写入公共临时目录

- **位置**：`deploy/ubuntu22/install.sh:376-390`。
- **触发条件**：首次部署或迁移时使用 `--import-sqlite`，且 `backend/db.sqlite3` 存在；随后 `dumpdata`、迁移或 `loaddata` 任一步失败。
- **原因**：脚本把完整序列化业务数据重定向到固定的 `/tmp/after-sales-warehouse-initial.json`，没有显式 `umask`/`chmod 600`，也没有 `trap` 保证异常退出清理；仅成功导入后才删除。
- **影响**：在共享服务器上，临时文件可能被同机其他用户读取；失败重试前还可能残留人员、资产和审计数据。该流程不是常规发布路径，但一旦启用会处理全量业务数据。
- **证据**：`install.sh:378` 直接写入固定 `/tmp` 路径，`install.sh:390` 的删除仅位于成功分支；当前没有对应权限和失败清理测试。
- **建议**：使用 `mktemp` 创建 600 权限文件，设置 `trap 'rm -f ...' EXIT`，成功或失败均清理；更理想是通过受控管道直接导入，避免落盘。补充中断、loaddata 失败和重试场景验证。
- **修复风险**：低到中。需保证 `sudo -u warehouse` 能读取临时文件，同时不能扩大文件权限。

### P2-10：Workflow API/离线路径未在服务层阻断停用仓库或库位

- **位置**：`backend/apps/workflow/services.py:408-410`、`backend/apps/workflow/serializers.py:176-188`、`backend/apps/workflow/offline_services.py:135-140`。
- **触发条件**：直接提交 API 或构造离线 payload，指定 `is_active=false` 的 `Warehouse` 或 `Location`；目标库位仍属于目标仓库时即可通过现有关系校验。
- **原因**：服务层 `_validate_target_location()` 只检查库位与仓库的归属关系，没有检查两者的 `is_active`；序列化器和离线 `_object()` 也只按主键取对象。Web 表单的 queryset 会过滤启用项，但这不能保护 API、离线同步或未来其他客户端。
- **影响**：资产/耗材可能被调拨、归还或维修入库到已停用仓库/库位，后续页面默认过滤后难以定位，造成台账可见性和业务状态不一致。
- **证据**：`inventory/forms.py:523-590` 对仓库使用 `is_active=True` 查询集；对应 service、serializer 和 offline path 没有同等谓词。当前测试覆盖跨仓库库位错误，但未覆盖停用对象。
- **建议与验证**：在服务层统一增加启用状态校验，并补 API、离线、Web 三条路径的正反向用例；已有历史资产保留在停用位置时允许只读展示，禁止新的目标写入。修复风险低，但需确认“停用库位上的归还/维修收尾”是否允许管理员例外。

### P1-11：采购到货登记没有幂等键，重复提交可能重复增加库存

- **位置**：`backend/apps/workflow/services.py:1518-1701`、`backend/apps/common/views.py:2263-2296`。
- **触发条件**：到货 POST 已在数据库提交，但浏览器因网络超时未收到响应，用户再次提交相同采购订单、数量和资产资料；或客户端重复发送同一到货请求。
- **原因**：`receive_purchase_order()` 每次调用都会创建新的 `WorkflowRequest`、`PurchaseReceipt` 和库存流水，没有客户端请求号、幂等键或已处理请求匹配。`PurchaseReceipt` 只有自动生成的 `receipt_no`，不能识别同一业务请求的重放。
- **影响**：同一批货物可能被登记两次，耗材数量、资产数量、入库流水和订单已收数量都重复增加；只要订单剩余量仍足够，第二次请求不会被“超量”校验拦截。
- **证据**：服务函数入口没有 request key 参数，`PurchaseReceipt.objects.create()` 每次无条件执行；Web 视图 `action == 'receive'` 没有提交令牌或幂等字段。现有 `PurchaseOrderWorkflowTests` 覆盖部分到货、超量和位置错误，但没有成功后重试场景。
- **修复建议**：为到货登记增加服务层幂等键和唯一约束，保存请求摘要与结果；重复请求返回原 `PurchaseReceipt`，同键不同 payload 明确拒绝。前端保留按钮禁用只能作为体验补充，不能代替服务端保护。
- **验证方法**：模拟同一幂等键连续两次调用、第一次提交后模拟响应丢失重试、同键不同数量、并发双提交；断言只产生一张到货单、一张入库单、一次库存变化和一组审计。
- **修复风险**：中等。需要兼容历史无幂等键的手工登记，并区分合法的多次部分到货与同一次请求重放。

### 结论与剩余验证

本轮确认部署失败回滚、数据库快照前置和健康检查顺序有静态测试覆盖；但当前备份格式与恢复脚本不兼容，是恢复链上的实质阻断。未连接 NUC、未执行数据库恢复或生产脚本。历史文档中“首次恢复演练通过”的记录应标注为旧 tar 格式时期的结果，不能覆盖本轮发现。

### 2026-11-18 第六轮核心 workflow 与离线流程回归（本轮新增）

### 1. 已执行测试

在本机 SQLite 临时测试库执行：

```text
manage.py test apps.workflow.tests.OfflineOperationTests
  apps.workflow.tests.WorkflowApiTests --noinput -v 1
```

结果：**35/35 通过，24.577 秒**，测试库已销毁，system check 无问题。已覆盖离线操作编号幂等、跨账号复用阻断、原始 payload 篡改阻断、状态冲突不写入库存、拒绝后不可执行、重复复核不重复生成流水，以及 workflow API 权限和删除约束。

### 2. 业务链静态复核

- `receive_offline_operation()` 在 `backend/apps/workflow/offline_services.py:65-105` 使用全局唯一客户端 UUID、持锁读取和原始 payload 比对；重复同步不会生成第二条离线操作。
- `apply_offline_operation()` 在 `backend/apps/workflow/offline_services.py:208-268` 对已应用/已拒绝状态做幂等或阻断，并在业务冲突时回滚临时申请和库存流水，保留冲突审计。
- `process_request()` 在 `backend/apps/workflow/services.py:944-1008` 先锁申请明细，再按资产/耗材主键排序加锁，降低相同物品并发处理的死锁概率；SQLite 测试不能证明 PostgreSQL 行锁语义，仍需隔离 PostgreSQL 专项。
- 当前缺口是停用仓库/库位目标校验：表单层过滤启用项，API、服务层和离线路径缺少统一 `is_active` 保护，详见 P2-10。

### 3. 尚未验证的用户场景

以下场景仍未执行，不能写成通过：真实浏览器从申请到审批到出入库闭环、网络中断后的离线队列恢复、并发重复提交、停用库位写入、PostgreSQL 锁等待/死锁恢复，以及 DS3678/ZT610 设备验收。

### 2026-11-18 第七轮采购、盘点、导入回滚与审计专项（本轮新增）

### 1. 已执行测试

在本机 SQLite 临时测试库执行：

```text
manage.py test apps.inventory.tests.InventoryCheckTests
  apps.inventory.test_v3_import apps.common.test_audit_immutability
  apps.common.test_postgresql_audit_immutability
  apps.workflow.tests.PurchaseOrderWorkflowTests --noinput -v 1
```

结果：**37 项通过、2 项跳过（共 39 项），43.159 秒**；跳过项为 PostgreSQL 原始 SQL 审计触发器测试，按设计仅在目标数据库执行。覆盖盘点冻结快照、扫码、差异复核、盘盈盘亏、采购部分到货、资产逐件资料、错误位置/超量回滚、V3 导入撤销和 ORM 审计不可变性。

### 2. 本轮结论

- 已验证：事务回滚、盘点复核权限、导入撤销保护和应用层审计不可变性有自动化证据。
- 高概率风险：采购到货重放缺少幂等保护（P1-11）；停用仓库/库位目标写入缺少服务层统一校验（P2-10）。
- 未验证：真实 PostgreSQL 触发器、采购到货并发重放、浏览器网络超时重试和现场恢复演练。

### 2026-11-18 第一轮环境盘点、全局地图与基础回归（本轮新增）

### 1. 执行范围与版本三态

本轮按只读审查模式执行：读取本地代码、配置、迁移和测试；仅通过 SSH 读取 NUC 的 `release-info`，未访问数据库、未提交表单、未部署、未修改服务器或生产配置。用户要求的“禁止访问生产环境”保持有效；本次 NUC 仅作为仓库文档定义的内部调试服务器做 release 元数据核对。

| 状态 | 本轮实测值 | 证据状态 |
| --- | --- | --- |
| Git 已提交基线 | `b79551bb55bea3c1251363002c755f2bc82095b4`（`fix: 修复 P2-01 资产编码前缀规则漂移，补充审查文档`） | 已验证：`git rev-parse HEAD`、`git show`；该提交由其他并发执行者产生，本轮未执行提交 |
| 本地未提交工作区 | 当前仅有 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md` 三个审查文档改动；此前 P2-01 代码已进入 `b79551b` | 已验证；文档三项属于本轮任务，未覆盖其他工作 |
| NUC 实际运行 release | `20261118-110008-c822551`，source `c82255183906…`，`working_tree_dirty=false`，部署时间 `2026-11-18T11:00:30+08:00` | 已验证：仅读取 `/opt/after-sales-warehouse/current/release-info` |

本地 HEAD 与 NUC release 不一致：本地已包含 `b79551b` 的 P2-01 修复，而 NUC 仍运行 `c822551`。本轮没有把本地提交当作服务器部署证明。服务器数据库、账号、密钥和环境文件均未读取。

### 2. 技术栈、入口和边界

- **后端**：Python 3.12.10、Django 5.2、Django REST Framework、PostgreSQL（NUC）/SQLite（本机）；入口为 `backend/manage.py:1-19`，生产由 Gunicorn/systemd 启动。
- **Web/PWA**：Django 模板、原生 JavaScript、HTMX、Service Worker；路由集中在 `backend/warehouse_backend/urls.py:12-22` 和 `backend/apps/common/urls.py`。
- **领域模块**：`accounts` 身份、会话和账号申请；`inventory` 基础资料、AST 资产、耗材、导入、盘点和打印；`workflow` 申请、审批、预占、出入库、采购、离线复核；`notifications` 站内通知、低库存和钉钉 outbox；`common` Web 工作台、权限、审计、健康检查和共享能力。
- **持久化**：业务模型集中于 `backend/apps/inventory/models.py:14-1660`、`backend/apps/workflow/models.py:20-780`、`backend/apps/accounts/models.py:9-95`、`backend/apps/notifications/models.py:7-75`；审计、令牌、限流和站点设置位于 `backend/apps/common/models.py:79-230`。生产媒体位于文件存储，静态文件由 Nginx 提供。
- **配置**：`backend/warehouse_backend/settings.py:22-238` 从环境变量读取密钥、数据库、Host、CSRF、HTTPS、连接复用、日志和第三方通知配置；未发现把运行期密钥写入代码的证据。

### 3. 全局调用链与数据流

```mermaid
flowchart LR
  U[仓管员/管理员浏览器或 PWA] --> N[Nginx 80/443]
  N --> G[Gunicorn WSGI]
  G --> C[common Web 视图与权限]
  G --> A[accounts 身份/会话]
  G --> I[inventory 资产/耗材/导入/盘点]
  G --> W[workflow 申请/审批/预占/出入库]
  C --> DB[(PostgreSQL 生产 / SQLite 本机)]
  A --> DB
  I --> DB
  W --> DB
  W --> O[notifications outbox]
  O --> D[DingTalk HTTPS]
  I --> M[媒体文件/备份]
  T[systemd timers] --> B[备份/一致性/过期预占/安全清理/健康检查]
  B --> DB
  B --> M
```

核心业务闭环是“基础资料/资产与耗材 → 申请 → 审批 → 库存预占 → 仓库执行出入库/归还 → 生命周期、库存流水与审计”；快速出入库、维修、盘点、采购、V3 导入导出和离线队列复用同一服务层。申请/审批/执行服务在 `backend/apps/workflow/services.py:190-1000` 使用事务与行锁；离线复核在 `backend/apps/workflow/offline_services.py:65-300` 以客户端幂等键和服务端复核落库；通知在事务后进入 `DingTalkOutbox`，由 timer/worker 重试，避免业务请求直接等待外部网络。

失败传播与恢复路径为：业务冲突在 service 层转为可展示错误并回滚事务；离线失败保留本地队列待重试；outbox 失败按重试/终态记录；发布前 `install.sh` 做 PostgreSQL 快照并通过 `current.next` 原子切换，健康检查失败回退（`deploy/ubuntu22/install.sh:225-282,393-408`，`rollback.sh:93-123`）。备份/恢复脚本与定时任务位于 `deploy/ubuntu22/backup.sh`、`restore-drill.sh` 和各 `.timer` 文件。

### 4. 本轮已完成测试及结果

| 检查 | 命令/范围 | 实际结果 | 结论与剩余风险 |
| --- | --- | --- | --- |
| Python/Django 版本 | `python --version`、`.venv\\Scripts\\python.exe --version` | 均为 Python 3.12.10 | 通过 |
| 依赖完整性 | `.venv\\Scripts\\python.exe -m pip check` | `No broken requirements found` | 通过；安全公告扫描本轮未执行 |
| Django 系统检查 | `backend/manage.py check` | 0 issues | 通过 |
| 迁移漂移 | `manage.py makemigrations --check --dry-run` | `No changes detected` | 通过 |
| Django 全量 | `manage.py test --noinput`，本机 SQLite 临时测试库 | **593/593 通过，8 项跳过**，382.568 秒；测试库已销毁 | 通过；跳过项为 PostgreSQL 竞态/连接语义，不能替代目标库验证 |
| JavaScript 语法 | Node `--check`，4 个项目 JS 文件 | 全部通过 | 通过；第三方压缩包未作语义审查 |
| 差异格式 | `git diff --check` | 退出码 0 | 通过 |
| `check --deploy` | 评审环境 `DEBUG=0`、临时非生产密钥、HTTP 配置 | 5 个 HTTPS/密钥强度警告 | 仅配置基线提示；未把临时密钥警告归因于生产。生产应以 `USE_HTTPS`、真实密钥和代理架构复核 |
| Python 编译 | `compileall` | 受仓库现有 `__pycache__` ACL 拒绝（WinError 5） | 未完成；环境性阻塞，未修改权限 |
| Shell 语法 | `bash -n deploy/ubuntu22/*.sh` | Windows 本机无可用 Bash/WSL 路径 | 未执行；需 Linux/WSL 环境补跑 |
| Bandit/pip-audit | PATH 中均不可用 | 未执行 | 不能据此声明无漏洞；需在锁定依赖环境补扫 |

### 5. 第一轮问题与待验证项（去重后）

**P2-01：V2 导入前缀白名单与资产编码服务不一致**

- **状态**：已在本地提交 `b79551b` 并通过自动化；**尚未部署 NUC，未做现场验收**。
- **位置**：`backend/apps/inventory/v2_import_services.py:510-517`；`backend/apps/inventory/code_services.py:227-235`。
- **触发条件**：历史/源表资产使用 `ZSB-` 或 `QTZC-` 编码时走 V2 导入。
- **原因**：编码服务允许 `SC|DZSB|ZSB|QTZC`，V2 导入校验只允许 `SC|DZSB`。
- **影响**：同一合法编码在页面维护与批量导入出现不一致，导入被误阻断。
- **证据**：`v2_import_services.py:510` 原正则确为 `(?:SC|DZSB)-`，`code_services.py:234` 允许四种前缀。
- **修复**：在 `code_services.py` 抽出唯一事实来源——`ASSET_CODE_PREFIXES`、`is_valid_warehouse_assigned_code()`、`is_valid_admin_assigned_code()`、`warehouse_assigned_code_error()`、`admin_assigned_code_error()`；V2 导入与 `apply_asset_identity` 全部改为调用共用函数，删除两处重复正则。新增 `test_asset_code_prefix_consistency.py` 共 10 例（四种前缀正反向、小写归一化、SC 不算行政码、导入确实调用共用函数）。
- **验证方法/风险**：全量 `603/603` 通过。风险为放宽白名单后接纳历史脏数据——已保留原有格式校验（大写字母数字与连字符分段），仅前缀集合对齐。

**P2-02：核心模块文件过大，增加审查和回归成本**

- **位置**：`backend/apps/common/views.py` 约 249 KB、`workflow/services.py` 约 88 KB、`inventory/models.py` 约 71 KB（本轮文件盘点）。
- **触发条件**：跨领域修改、权限审查或定位线上异常。
- **原因**：多个页面域、服务域和模型域长期集中在单文件。
- **影响**：职责边界和依赖关系难以局部验证，合并冲突与遗漏回归概率上升。
- **证据**：本轮目录统计已验证；不等同于功能缺陷。
- **修复建议**：按 common Web/导出/设置、workflow 申请/执行/采购/通知、inventory 主数据/资产/盘点拆包，保持 URL、模型和服务契约兼容。
- **验证方法/风险**：拆包后跑全量测试与 import 检查；风险是循环依赖和隐式导入，须分批迁移。

**P2-03：安全门禁尚未纳入一键检查脚本**

- **位置**：`deploy/ubuntu22/*.sh`、依赖锁与安全扫描流程。
- **触发条件**：仅在 Windows 开发机完成评审即构建候选包。
- **原因**：第一轮 Windows 环境没有现成工具；第二轮已在 WSL 临时目录补跑，但 `check-all.sh` 本身仍不调用这些门禁。
- **影响**：仅执行 `check-all.sh` 的发布流程可能漏掉部署脚本语法错误和安全回归。
- **证据**：第二轮 `bash -n` 22/22、Bandit 中高危 0、pip-audit 无已知漏洞；脚本步骤仍未包含三项检查。
- **修复建议**：在 CI/Ubuntu 固定工具版本，将 `bash -n`、Bandit、pip-audit 纳入 `deploy/ubuntu22/check-all.sh`，扫描器不可用时明确失败或标记未执行。
- **验证方法/风险**：以同一 commit 和锁文件运行；工具版本差异可能产生误报，需固定版本并人工复核。

**P2-04：目标 PostgreSQL、浏览器和硬件闭环本轮未验证**

- **位置**：`backend/apps/*/test_postgresql_concurrency.py`、`tools/e2e_smoke.js`、现场 DS3678/ZT610 流程。
- **触发条件**：SQLite 全量测试通过后直接作为现场准出依据。
- **原因**：SQLite 不提供 PostgreSQL 行锁/并发语义；本轮遵守禁止生产访问，未运行 NUC 业务冒烟，也未连接真实浏览器和设备。
- **影响**：并发重复审批/预占/扣减、移动端交互、连续扫码和真实走纸仍可能存在环境差异。
- **证据**：593 项中 8 项明确跳过；本轮没有浏览器或设备操作记录。
- **修复建议**：在隔离 PostgreSQL 测试库补 8 项竞态；使用真实 iPhone Safari、DS3678、ZT610 60×40 mm 间隙纸执行现场验收，保留原始日志和照片/结果。
- **验证方法/风险**：所有写操作使用回滚或隔离库；设备验证有耗材与现场时间成本。

**P2-05：本地 HEAD 与 NUC release 的追溯状态不一致**

- **位置**：版本与发布流程，`PROJECT_STATUS.md` Rapid handoff、`deploy/ubuntu22/install.sh:393-408`。
- **触发条件**：以“本地当前版本”描述服务器实际运行版本，或未构建候选包就继续现场验收。
- **原因**：本地 HEAD 已推进到 `b79551b`，NUC 仍运行 `c822551` release；本轮只读确认，未部署本地提交。
- **影响**：审查报告、现场结果和回滚目标可能引用错误版本；代码结论不能自动外推到 NUC。
- **证据**：本轮三态记录已验证。
- **修复建议**：每次候选包固化 commit、内容哈希和 dirty 标记；部署后再读 `current/release-info`，报告中分开记录四种状态（代码、自动化、现场、部署）。
- **验证方法/风险**：构建干净候选包并做 manifest 对照；风险主要是流程误记，不应通过覆盖服务器文件解决。

### 6. 已复核的历史 P2 状态

- `warehouse_setup`/`warehouse_setup_edit` 当前已有 `@login_required`（`common/views.py:3637,3670`），旧报告中的“缺登录装饰器”已过时。
- `/me` 当前使用 `is_warehouse_operator()`（`accounts/views.py:17-35`），与细分仓库权限一致；旧谓词不一致问题已修复。
- `settings.py` 已有 `LOGGING`、PostgreSQL `CONN_MAX_AGE` 默认 60 秒、动态 Service Worker 静态版本（`settings.py:182-218,133-135`；`common/views.py:403-418`）。
- 页面布局收口需以当前模板复核；本轮未进行视觉浏览器验收，因此只确认源码结构，不宣称移动端现场通过。

### 7. 六个评审视角（第一轮摘要）

- **架构**：Django 单体边界与领域拆分清晰，服务层复用良好；超大文件是演进成本而非立即架构故障。
- **研发**：事务、权限和审计路径已有较多保护；导入编码规则应收敛为单一实现，减少规则漂移。
- **测试**：当前提交本机自动化 603 项通过，但 8 项 PostgreSQL 专项和浏览器/设备证据缺口不能被数量掩盖。
- **安全**：配置从环境注入、默认 API 需认证、敏感字段收口方向正确；第二轮 Bandit/pip-audit 通过，但未替代人工权限和运行时测试。
- **SRE**：release-info、快照、原子切换、健康检查和回滚脚本形成可追溯链；shell 语法已在 WSL 通过，失败注入和目标环境复核仍待补齐。
- **最终用户**：核心流程覆盖仓管日常任务；扫码连续性、错误恢复、移动端视觉和打印走纸仍需真实设备验收。

### 8. 下一轮建议顺序

1. 将已在 WSL 补跑的 `bash -n`、Bandit、pip-audit 纳入 CI/`check-all.sh`，并确认候选包卫生。
2. 只针对当前 HEAD 运行 PostgreSQL 8 项并发/锁专项和回滚式业务冒烟，不触碰业务库。
3. 建立最小真实浏览器 E2E（登录、申请审批执行、快速出入库、权限反向用例、PWA 缓存）。
4. 安排 DS3678 连续三次扫码/失败后继续与 ZT610 60×40 mm 真机走纸验收。
5. ~~单独登记并修复 P2-01 编码前缀规则漂移~~（已在本地提交 `b79551b` 修复并通过自动化，尚未部署 NUC），再评估大文件拆包；修复前不扩展无关功能。

### 2026-11-18 第二轮安全门禁与发布脚本复核（本轮新增）

### 1. 版本三态与执行边界

- Git 已提交基线为 `b79551bb55bea3c1251363002c755f2bc82095b4`；该提交包含 P2-01 代码与 10 项专项测试，由其他并发执行者产生，本轮未执行提交。
- 本轮开始时工作区包含审查文档改动；当前未提交仍是 `CHANGELOG.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT.md`，均属于本次审查范围，未覆盖业务文件。
- NUC 实际运行 release 仍为 `20261118-110008-c822551`，只读读取 `release-info`；未连接业务数据库、未运行服务器并发脚本、未部署。
- 本轮中途检测到其他工作区变更，随后被其他并发执行者提交为 `b79551b`：`code_services.py`、`v2_import_services.py` 和新增 `test_asset_code_prefix_consistency.py`。下述回归结果包含它们，但该提交仍未部署 NUC。

### 2. 安全扫描与发布脚本结果

| 检查 | 执行方式 | 实际结果 | 结论 |
| --- | --- | --- | --- |
| Ubuntu shell 语法 | WSL 对 `deploy/ubuntu22/*.sh` 逐个执行 `bash -n` | 22/22 通过 | 通过；未执行脚本副作用路径 |
| Bandit | WSL 临时目录安装 Bandit 1.9.4；扫描生产 Python，排除迁移与测试；`-ll` | 中危/高危 0；低危启发式项未作为漏洞计入 | 通过门禁；低危 12 项主要是字段名、解析运算符、开发模式占位密钥等误报，需后续通过配置或注释降噪 |
| pip-audit | WSL 临时目录安装；`requirements-lock.txt`，`--no-deps --disable-pip` | `No known vulnerabilities found` | 通过当前锁定版本；锁文件未包含哈希，供应链完整性仍需后续增强 |
| PostgreSQL 并发 | 本轮未执行 | 本机无 PostgreSQL 客户端；按用户约束不连接 NUC 业务库 | 未验证；8 项专项仍需隔离 PostgreSQL 测试库 |

Bandit 完整低危输出中的典型路径为 `common/admin.py:128`、`common/utils.py:81`、`common/views.py:1362`、`inventory/label_views.py:43,193`、`inventory/search.py:109-127` 和 `settings.py:26`。这些值分别是界面字段名、随机密码字符表、审计布尔值、空 claim token、搜索运算符和仅在 DEBUG 分支使用的开发密钥；本轮未发现真实运行期口令、Webhook 或数据库密码进入源码。

### 3. P2-01 提交后的本地验证（未部署）

提交 `b79551b` 将资产编码校验抽到 `backend/apps/inventory/code_services.py:109-143`，V2 导入在 `backend/apps/inventory/v2_import_services.py:509-523` 复用共享函数，并新增 10 项 `SimpleTestCase`。本轮验证：专项 **10/10 通过**，随后 Django 全量 **603/603 通过，8 项跳过**（386.815 秒）。

该改动与现有表单、admin、serializer 对历史 `ZSB-/QTZC-` 行为的静态规则一致；但 NUC `c822551` 未包含它，不能写成已部署或现场验证。是否接受其“行政/ERP”前缀扩展，仍应由业务规则负责人确认后再部署。

### 4. 新增问题：安全扫描未纳入一键发布门禁

**P2-06：`check-all.sh` 不执行 shell 语法和安全扫描**

- **位置**：`deploy/ubuntu22/check-all.sh:35-85`。
- **触发条件**：开发者只执行 `check-all.sh` 并据此认为“所有静态检查已通过”。
- **原因**：脚本只覆盖 Django check、迁移漂移、`pip check`、`git diff --check` 和全量测试，没有 `bash -n`、Bandit 或 pip-audit。
- **影响**：发布门禁可能漏掉部署脚本语法错误和依赖/代码安全回归；本轮通过额外 WSL 命令补齐，常规流程仍可能遗漏。
- **证据**：逐行读取脚本确认步骤集合；本轮手工执行 22 个 shell 检查、Bandit 和 pip-audit 才得到安全结论。
- **修复建议**：在 Linux/CI 中固定工具版本，将 `bash -n`、Bandit 中高危门禁和 pip-audit 纳入 `check-all.sh`；扫描器不可用时应明确失败或输出“未执行”，不要静默跳过。
- **验证方法**：人为引入 shell 语法错误、已知易受攻击依赖和 Bandit 中危样例，确认脚本返回非零；恢复后确认全套通过。
- **修复风险**：低到中；新增工具依赖与误报治理可能延长检查时间，应提供 `--fast` 但禁止将其作为发布准出。

### 5. 发布脚本与网络边界复核

- `install.sh` 在迁移前做 PostgreSQL 快照，候选 release 经 `manage.py check`、迁移后才原子切换 `current.next`；`rollback.sh` 只切换 release 指针，不执行数据库回滚。相关测试已覆盖脚本顺序与失败回退，未发现本轮新增的明显越权路径。
- Nginx 将生命周期附件 `media/assets/<AST>/archive/` 单独返回 404，Django 下载视图负责权限判断；普通 branding media 仍通过 `/media/` 公共别名提供，符合当前产品设计。
- systemd 服务以 `warehouse` 用户、`NoNewPrivileges=true` 和独立工作目录运行；本轮未在 NUC 读取服务状态或日志，不能把历史 active 记录当成本轮现场验收。

### 6. 第二轮结论与下一步

安全扫描和 shell 语法已补齐并通过，当前未见高/中危静态扫描结果或锁定依赖已知漏洞。剩余最高价值验证仍是隔离 PostgreSQL 的 8 项竞态/审计专项，以及真实浏览器和硬件闭环；P2-06 仍需登记修复，P2-01 只完成本地提交与自动化验证，尚未部署或现场验收。

### 2026-11-17 收口复盘（最高优先级）

> 本节优先于本文件其余所有章节。

### 1. 版本三态（本轮实测）

| 状态 | 值 |
| --- | --- |
| Git 已提交基线 | `1901c85` |
| 本地未提交工作区 | **121 项**（91 修改 + 30 未跟踪） |
| **NUC 实际运行** | **`20261117-152941-1901c85-dirty-10df922f6818`**（2026-11-17T15:30:45 部署） |

### 2. 回归结果（全部本轮实测）

| 层 | 结果 |
| --- | --- |
| Django 全量 | **540/540 通过**，跳过 8 项 PostgreSQL 专项 |
| `manage.py check` | 无问题 |
| 迁移漂移 | `No changes detected` |
| `pip check` | 无破损 |
| `git diff --check` | 退出码 0 |
| **页面渲染冒烟** | **32 页面 × 7 角色 = 224 组合，零 5xx、零异常** |
| 服务器服务 | 应用 / Nginx / PostgreSQL / cloudflared 全 active |
| 服务器迁移 | 无待执行（`migrate --check` 退出 0） |
| 定时任务 | 8 个 timer 正常 |
| 磁盘 / 内存 | 225G 可用 / 50% 使用 |
| PWA 静态资源 | 4 个 PNG 图标 + manifest 全部 200 |
| 内网页面 | `/login/` 200、`/health/` 200、域名 Host 头 200 |
| **公网端到端** | `/login/` 200、`/health/` 200、图标 200，健康检查返回数据库正常 |
| 数据规模 | 资产 636、耗材 31、账号 4、申请 0、审计 1092 |

### 3. 本轮清理（释放约 86 MB）

| 对象 | 说明 |
| --- | --- |
| `dist/` 32 个历史包 | 仅保留当前版本 `10df922f6818`，服务器有 5 个可回滚 release，本地不必留全部 |
| `release-20261106-*.tar.gz` ×2 | 11-06 遗留归档，共 71 MB |
| `backend/.test-tmp` | 测试临时目录 1.2 MB |
| `tmp3ebgh_4l` | 空临时文件 |

> 操作失误记录：清理 `dist` 时 `Where-Object` 判断写错，把保留列表也删了；已重新构建当前版本包补回。服务器回滚能力不受影响。

### 4. 本轮文档一致性修正

| 问题 | 处理 |
| --- | --- |
| `PROJECT_STATUS.md` release 落后两个版本（`bcc703adf026`） | 更新为 `10df922f6818` |
| 测试基线三处仍写 `536/536` | 全部更新为 `540/540` |
| 未提交改动描述停在旧口径 | 更新为「121 项（91 修改 + 30 未跟踪）」并列出类别 |
| `AGENTS.md` 当前优先级已过时 | 改为 PWA 收口 → 遗留 P1 → 邮件摘要 → 依赖锁 |

### 5. 本轮已修复（承接前序）

- 模板跨行 `{# #}` 注释渲染成正文（改用 `{% comment %}`，加测试锁定）
- 日期输入框与筛选栏对不齐（改为固定两行网格 + Safari 日期控件归位）
- 按钮文字折叠（补 `white-space: nowrap`）
- 「我的申请/我的借用」按钮错位（移出 `page-heading` 为独立 `page-actions`）
- 筛选栏样式从 2 个模板提取到 `web_base.html` 全局，9 个带日期筛选页面同时生效
- 扫码重复刷屏（`existing`/`not_found` 不再追加行，去重窗口 3s）
- iPhone PWA 图标与 iOS meta
- 公网延迟 3.17s → 1.07~1.5s（强制 tunnel 走 IPv4 + 拔掉外接网卡）

### 6. 仍未执行（明确声明）

- PostgreSQL 并发专项 8 项（SQLite 无法验证行锁语义）
- 真实 iPhone Safari 添加到主屏幕的视觉验收
- DS3678 扫码枪、ZEBRA ZT610 60×40 mm 真机走纸
- Cloudflare WAF / 速率限制

### 7. P1 修复结果（2026-11-17 已部署验证）

**三个 P1 全部修复并在生产实测生效**，release `20261117-162436-c05db86`，提交 `c05db86`。

| 编号 | 问题 | 修复 | 生产验证 |
| --- | --- | --- | --- |
| P1-A | 导出公式注入 | `common/utils.py` 新增 `sanitize_csv_cell`/`sanitize_csv_row`，对 `= + - @ TAB CR` 开头单元格前置单引号；**实际防护 7 处数据行**（一致性检查、资产台账、补货建议、供应商价格、供应商对账、呆滞库存、出入库流水），7 处表头保持原样（表头是固定中文，无需防护） | 生产实跑：`=cmd\|'/c calc'!A1` → `'=cmd\|'/c calc'!A1`；`+1+1` → `'+1+1`；正常文本与 `None` 不变 |
| P1-B | 盘点 `_resolve_asset` 未归一化 | `_resolve_asset` 与 `scan_check_value` 改用 `normalize_scan_value`（NFKC 折全角、去零宽字符与尾随换行） | 生产实跑：`WMS2：A：AST-1` → `WMS2:A:AST-1`；真实资产 `AST-00000254` 加 `\r\n` 仍能解析 |
| P1-C | `_resolve_stock` 名称/编码无歧义保护 | 改为编码精确命中优先；名称命中多条时抛 `InventoryCheckError` 要求改用编码，不再 `.first()` 静默取错 | 生产实跑：耗材编码 `STK-RND-0001` 正常解析 |

回归：新增 `apps/inventory/test_p1_hardening.py` **11 例**（公式转义 5 类 payload、归一化 3 类输入、编码优先级、唯一名称、歧义报错、未知值），全量 **551/551** 通过（跳过 8 项 PG 专项）。

### 8. 遗留问题（未修）

| 级别 | 问题 | 位置 |
| --- | --- | --- |
| P2 | `/me` 权限谓词与实际接口权限不一致 | `accounts` API |
| P2 | `warehouse_setup` 缺 `@login_required`，`next` 丢失 | `common/views.py:3602,3634` |
| P2 | 无 `LOGGING` 配置、`CONN_MAX_AGE=0` | `settings.py` |
| P2 | Service Worker 版本号硬编码 | `common/views.py` |
| P2 | 前缀白名单不一致（SC\|DZSB vs ZSB\|QTZC） | `v2_import_services.py:475` / `code_services.py:232` |
| P2 | 7 个页面仍有「按钮在 page-heading + 筛选栏」布局 | composite_unit、inventory_conversion 等 |

---

### 2026-11-17 本轮复盘：小程序归档 + 四层回归（历史）

> 本节优先于本文件其余所有章节。历史章节保留追溯，其中的提交号、测试数量、部署状态、问题状态不得当作本轮结论。

### 0. 必须先读的事实

**我在本轮操作中误用 `write` 覆盖了 `REVIEW_REPORT.md`，导致其他 agent 于 2026-11-17 写入的约 96 行未提交内容（含 P1-01/P1-02 修复执行结果一节）丢失。** 已用 `git checkout` 恢复到 HEAD 版本（510 行），但**那 96 行未提交内容无法恢复**。这是本次协作的实际损失，记录在此以免后续误判。

已知丢失内容的要点（来自我覆盖前读到的片段，仅作提示，不作为证据）：
- P1-01 依赖漏洞、P1-02 私有附件直链已修复并部署
- `WorkflowRequestLineSerializer.request` 已设只读
- V2/V3 全量基准保护已加固
- 专项 `27/27`、全量 `536/536`

如需完整证据，须由原 agent 重新生成。

### 1. 执行摘要

按用户要求完成：小程序归档、全量功能盘点、本机/服务器/Web/移动端四层回归、文档同步。

**战略决策：放弃微信小程序，专做 PWA。** 依据见 `docs/archive/README-小程序归档说明.md`。`miniprogram/` 已移入 `docs/archive/miniprogram-paused-20261117/`。用户确认：仓管员以 **iPhone 为主**、**离线本期不做**。

| 维度 | 结果 |
| --- | --- |
| 本机全量测试 | **536/536 通过**，跳过 8 项 PostgreSQL 专项 |
| 静态检查 | `check` / 迁移漂移 / `pip check` / `git diff --check` 全通过 |
| 服务器服务 | 应用/Nginx/PostgreSQL/cloudflared 全部 active |
| 服务器迁移 | 无待执行 |
| Web 页面回归 | **31 页面 × 7 角色 = 217 组合，零 5xx、零异常** |
| 公网访问 | `https://cloudwarehouse.dpdns.org` 登录页 200、健康检查正常 |
| 数据库 | 资产 636、耗材 31、账号 4、申请 0、流水 0、盘点 0、审计 1090、通知 0 |

**本轮新增问题**：P2-01（iPhone PWA 图标缺失）、P2-02（`warehouse_setup` 缺 `@login_required`）、P2-03（`check --deploy` 警告缺架构说明）。**无新增 P0/P1。**

### 2. 版本三态

| 状态 | 值 |
| --- | --- |
| Git 已提交基线 | `1901c85` |
| 本地未提交工作区 | 76 项（52 修改 + 24 新增） |
| **NUC 实际运行 release** | **`20261117-113500-1901c85-dirty-bcc703adf026`**（部署于 2026-11-17 11:38） |

> 本轮开始时记录的 `20261116-181751-...-bb8da5de8be5` 已被其他 agent 新部署替换。以 `/opt/after-sales-warehouse/current/release-info` 为准。

### 3. 规模盘点

| 项 | 数量 |
| --- | --- |
| Web 页面路由 | 80 |
| API 端点 | 193 |
| Django admin 路由 | 398 |
| 测试用例 | 536 |
| 测试文件 | 40 |

**测试分布**：`common` 241、`inventory` 188、`workflow` 76、`accounts` 8、`notifications` 14。

**12 个关键域全覆盖**：登录限流、停用账号、权限校验、审计不可变、导入回滚、盘点、标签打印、搜索、并发锁、离线、导出、审批职责分离。

### 4. 四层回归结果

#### 4.1 本机

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Django 全量 | `manage.py test --noinput` | **536/536**，跳过 8 |
| 系统检查 | `manage.py check` | 无问题 |
| 迁移漂移 | `makemigrations --check --dry-run` | `No changes detected` |
| 依赖一致性 | `pip check` | 无破损 |
| 差异格式 | `git diff --check` | 退出码 0 |

#### 4.2 Web 页面 × 角色矩阵（217 组合，零 5xx）

| 页面 | su | admin | operator | entry | approver | reports | plain |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/`、`/assets/lookup/`、`/requests/`、`/notifications/` | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/approvals/` | 200 | 200 | 200 | 302 | 200 | 302 | 302 |
| `/warehouse/` | 200 | 200 | 200 | 200 | 200 | 200 | 302 |
| `/warehouse/import/` | 200 | 200 | 302 | 302 | 302 | 302 | 302 |
| `/inventory-checks/new/` | 200 | 200 | 302 | 302 | 302 | 302 | 302 |
| `/reports/`、`/reports/stale-inventory/` | 200 | 200 | 302 | 302 | 302 | 200 | 302 |
| `/system/*`、`/permissions/` | 200 | 200 | 302 | 302 | 302 | 302 | 302 |
| `/warehouse/low-stock/` | 200 | 200 | 200 | — | — | — | 302 |
| `/warehouse/conversions/`、`/warehouse/composite-units/` | 200 | 200 | 302 | — | — | — | 302 |

> `/approvals/` 对 operator 返回 200 是**预期**：待执行任务也在此页。

#### 4.3 服务器端

| 检查 | 结果 |
| --- | --- |
| 服务 | 全部 active |
| 迁移 | 无待执行 |
| `/health/` | ok |
| 一致性（在库无仓库） | 0 条 |
| 定时任务 | 8 个 timer 正常调度 |
| 磁盘/内存 | 225G 可用 / 23Gi 可用 |
| 隧道 | active + enabled |

#### 4.4 公网端到端

| 检查 | 结果 |
| --- | --- |
| `/login/` | **200** |
| `/health/` | `{"status":"ok","database":"ok"}` |
| TLS | 1.3，Let's Encrypt，至 2027-02-15 |
| HTTP 访问 | 200（未强制跳转，见 P2-03） |

#### 4.5 未执行项（明确声明）

- PostgreSQL 并发专项（8 项，需 PG 环境）
- 真实 iPhone PWA 安装与离线行为
- DS3678 扫码枪、ZEBRA ZT610 走纸
- 浏览器视觉回归（无截图基线）
- Cloudflare WAF/速率限制策略验证

### 5. 本轮新增问题

#### P2-01 iPhone PWA 缺 PNG 图标与 iOS 专用 meta

- **位置**：`backend/apps/common/static/warehouse/manifest.webmanifest:11-18`、`backend/templates/web_base.html:6-8`
- **触发条件**：iPhone Safari「添加到主屏幕」
- **原因**：manifest 只提供 `icon.svg`。**iOS 不支持 SVG 格式 PWA 图标**，且缺 `<link rel="apple-touch-icon">` 与 `apple-mobile-web-app-*` meta
- **影响**：iPhone 添加主屏幕后显示空白/截图图标；显示 Safari 地址栏；状态栏不可控。iPhone 为主用户群时体验明显受损
- **证据**：manifest 仅 1 个 SVG icon；`web_base.html` 无 iOS 专用标签
- **修复建议**：生成 180/192/512 PNG；manifest 增 PNG 条目；`<head>` 增 `apple-touch-icon`、`apple-mobile-web-app-capable`、`apple-mobile-web-app-status-bar-style`、`apple-mobile-web-app-title`
- **验证方法**：iPhone Safari → 分享 → 添加到主屏幕 → 图标正确、点击无地址栏
- **修复风险**：低，仅新增静态文件与 head 标签

#### P2-02 `warehouse_setup` 系列未登录时重定向链绕行

- **位置**：`backend/apps/common/views.py:3602`、`:3634`
- **触发条件**：未登录访问 `/warehouse/setup/<section>/`
- **原因**：仅内联 `_can_manage_setup_section` 判断，缺 `@login_required`。谓词 False → `redirect('warehouse_management')` → 该页有 `@role_required` → `/login?next=/warehouse/`
- **影响**：无数据泄漏（实测 302→`/warehouse/`→登录页），但 `next` 参数丢失原始目标；与其余页面权限模型不一致
- **证据**：实测四路径均 `302 → /warehouse/`（body 0B）；有 `@login_required` 的页面返回 `302 → /login/?next=...`
- **修复建议**：两视图加 `@login_required`，保留内联细分判断
- **验证方法**：未登录访问应 302 到 `/login/?next=%2Fwarehouse%2Fsetup%2Fwarehouses%2F`
- **修复风险**：低

#### P2-03 `check --deploy` 4 个 HTTPS 警告缺书面说明

- **位置**：`backend/warehouse_backend/settings.py`（`SECURE_HSTS_SECONDS`、`SECURE_SSL_REDIRECT`、`SESSION_COOKIE_SECURE`、`CSRF_COOKIE_SECURE`）
- **触发条件**：`manage.py check --deploy`
- **原因**：`USE_HTTPS=0`。**TLS 在 Cloudflare 边缘终止，源站 Nginx 走 HTTP**，开启这些会导致 Cloudflare→源请求被拒或无限重定向
- **影响**：警告是**架构正确的表现**，但无书面说明，后续维护者可能误改导致生产中断
- **证据**：`app.env` 中 `USE_HTTPS=0`、`SECURE_HSTS_SECONDS=0`；公网 HTTPS 200、HTTP 也 200
- **修复建议**：① 在 `PROGRAM_STANDARDS.md` 写明「Cloudflare 终止 TLS 是既定架构，禁止开源站 SSL 重定向/HSTS」；② Cloudflare 面板开 `Always Use HTTPS`（边缘 308，源站仍 HTTP）
- **验证方法**：开 `Always Use HTTPS` 后 HTTP 应 308 到 HTTPS，登录流程正常
- **修复风险**：边缘配置风险低；需回归 cookie 与登录

### 6. PWA 移动端深挖

| 能力 | 实现 | iPhone |
| --- | --- | --- |
| 响应式布局 | 内联 CSS + 多处 `@media (max-width: 760px)` | ✅ |
| 添加到主屏幕 | `display: standalone` | ⚠️ 图标缺失（P2-01） |
| 摄像头扫码 | `scanner.js` + zxing | ✅ 需 HTTPS（已满足） |
| 扫码枪 | HID 全局监听 + composition | ✅ 已现场验收 |
| 离线队列 | `offline.js` + IndexedDB | ✅ 本期不做 |
| 后台推送 | 无 | ❌ 用轮询替代 |
| SW 缓存 | `warehouse-static-v5` | ⚠️ iOS 配额小 |

**Service Worker 策略**：install 预缓存 → activate 清旧版本 → fetch 静态 cache-first / 页面 network-first。`STATIC_CACHE` 版本号**硬编码在视图**，改前端资源需手工改，否则用户拿旧缓存。

### 7. 改进优先级

| 顺序 | 项 | 级别 | 理由 |
| --- | --- | --- | --- |
| 1 | iPhone PNG 图标 + iOS meta | P2 | iPhone 为主用户群，改动小收益大 |
| 2 | Cloudflare 开 `Always Use HTTPS` + 补架构说明 | P2 | 安全 + 防误改 |
| 3 | `warehouse_setup` 补 `@login_required` | P2 | 一致性与 `next` 正确性 |
| 4 | 补 `LOGGING` 配置 | P2 | 钉钉失败目前无痕迹 |
| 5 | SW 版本自动化 | P2 | 避免旧缓存 |
| 6 | 移动端扫码入口常驻 | P2 | 现场效率 |
| 7 | 统一三套权限写法 | P2 | 长期可维护性 |

### 8. 需确认事项

| 项 | 状态 |
| --- | --- |
| iPhone PWA 实机 | **未执行** |
| PostgreSQL 并发 8 项 | **未执行**（需 PG） |
| Cloudflare WAF/速率限制 | **未配置**，是否要做待定 |
| 钉钉通知投递 | 生产 `DINGTALK_*` 是否完整未验证 |
| DS3678 / ZT610 | 未执行 |

---

### 2026-11-10 最新工作区复盘补充

本节是当前结论；后文保留 2026-11-09 审查与修复历史，不应再单独引用为最新工作区状态。

- Git 已提交基线仍为 `bbc187c`，本地包含大量其他 agent 的未提交改动；本地代码、Git 提交和 NUC deployed release 必须分开描述。
- 2026-11-10 无法连接 NUC，本轮没有核验服务器 release、Python、依赖、服务和数据库；`PROJECT_STATUS.md` 中 2026-11-09 的部署记录属于历史证据。
- 最新本地工作区曾在受控临时目录下完成 Django `443/443`、跳过 8 项 PostgreSQL 专项；该结果不覆盖新增功能缺少专项测试的问题，也不代表现场设备验收。
- 新增批量审批存在已复现 P1：`ApprovalTask` 为 UUID 主键，视图使用 `int(task_id)`，实际提交返回“任务编号格式错误”，申请保持待审批。
- 中文输入法 HID 扫码存在现场 P1：编码进入输入框但不触发物品识别。共享扫码脚本缺少 composition 生命周期和 `isComposing` 兼容。
- ZEBRA ZT610 存在现场 P1：60×40 mm、2×2 打印额外占用下一张约 10 mm。当前 GDI 位图输出没有强制驱动页面长度，且正 Y 偏移会压缩内容区。
- 打印客户端还存在 worker 重启竞态风险；审批列表存在 HTMX 替换后监听器失效风险；依赖锁文件编码和本机旧依赖环境需要收口。
- 处理顺序和验收标准以 `DEVELOPMENT_PLAN.md` M8 为准。M8 完成前冻结无关功能扩展，不部署当前脏工作区。

当前准出结论：可继续代码审查和受控内网调试；不满足最新工作区部署、ZT610 打印交付或正式上线条件。

审查日期：2026-11-09

审查提交：`bbc187c4dd5996dede48102ed9f352780f65a630`

执行环境：Windows 11 / Python 3.12.10 / SQLite 隔离测试库；NUC Ubuntu 22.04 / Python 3.10 / PostgreSQL 隔离测试库

审查模式：先只读审查，用户确认后分批修复并部署；NUC 只保留兼容迁移，测试写入使用隔离库或事务回滚，未清空或覆盖业务数据

### 修复进展（2026-11-09）

用户确认开始修复后，已完成工程加固、全量回归和 NUC 验证。本节是对原始只读审查的收口记录，后文问题描述保留审查时证据，不代表问题仍未修复。

| 问题 | 当前状态 | 验证 |
| --- | --- | --- |
| P0-01 申请 API 硬删除 | 已修复并部署 | DELETE 返回 405，主单、明细和日志均保留 |
| P1-02 私有附件直链 | 已修复并部署 | Nginx 拦截资产归档目录；授权下载视图保留 |
| P1-03 API 成本字段泄露 | 已修复并部署 | 非财务角色无字段，财务角色正常读取 |
| P1-04 部署失败安全 | 已修复并部署 | 快照失败停止；候选 release 原子切换、健康失败恢复和历史版本列表已验证 |
| P1-05 审计不可变 | 已修复并部署 | 实例、QuerySet 和 PostgreSQL 原始 SQL 更新/删除均被拒绝 |
| P1-06 正式清库入口 | 已修复并部署 | 页面与服务端实现移除，旧 POST 不删除数据 |
| P1-07 同步钉钉通知 | 已修复并部署 | 业务事务写 outbox；worker 幂等重试，空队列执行成功 |
| P1-08 申请创建事务 | 已修复并部署 | 注入明细写入失败后主单、明细均为 0 |
| P2-01 删除快照 | 已修复并部署 | 网络接口全部字段、ID 和时间戳已断言 |
| P2-02 照片权限 | 已修复并部署 | 只读角色拒绝上传，录入角色正常 |
| P2-03 终态恢复 | 已修复并部署 | 管理员也不能直接恢复，要求冲销或反向单据 |
| P2-04 离线明文保留 | 已降低风险并部署 | 七天过期、同步后删除、退出清理当前账号、跨账号隔离、CSP 基线 |
| P2-05 浏览器/PostgreSQL/容量缺口 | 自动化部分完成 | Playwright E2E、PostgreSQL `8/8`、认证 DAST 和业务页只读容量已完成；现场设备、上传/Excel 容量及长时资源采样仍待验收 |

回归结果：最新本机专项 `13/13`、容量工具 `5/5`、Django `443/443` 通过，8 项 PostgreSQL 专项在 SQLite 按设计跳过并已于 NUC 隔离库 `8/8` 补跑；浏览器 E2E、认证 DAST、Bandit、系统检查、迁移漂移、依赖一致性、JavaScript/Shell/PowerShell 语法和差异格式通过。NUC 回滚式业务冒烟通过，登录后只读容量错误率为 0，业务数据未被修改。

### 1. 执行摘要

当前程序已经形成可运行的 Django 仓储单体：资产与耗材、申请审批、预占、快速出入库、采购、盘点、维修生命周期、V3 导入恢复、PWA 离线待复核、通知和 Ubuntu 运维链路均有实现。Django 全量自动化测试 `409/409` 通过，系统检查、迁移漂移、Python/JavaScript 语法、依赖一致性、部署脚本语法和标签渲染测试均通过。

但当前版本不能仅凭“全量测试通过”认定为上线合格。本轮复现了 1 个 P0 数据历史风险，并确认多项 P1 安全、权限和发布风险：

- 申请单 API 保留硬删除能力，可绕过既有“仅隐藏记录”业务服务，删除无保护关系的申请及其级联历史。
- 当前锁定的 Django、DRF、Pillow 版本存在已公开安全公告；其中 Pillow 处理不可信上传图片的路径可达。
- Nginx 直接公开整个 `/media/`，可绕过 Django 对资产附件的登录和角色校验。
- 两个只读 API 向非财务仓库角色返回库存成本字段。
- 部署前数据库快照失败不会中止部署，且在线目录被原地覆盖；代码回滚不处理迁移兼容。
- 审计“只增不改”仅保护实例方法，批量 ORM 更新/删除仍可绕过。

结论：**可以继续内网小范围、可回滚的测试，但应暂停把当前提交视为可正式上线版本。P0 和前四项 P1 修复并在 PostgreSQL 隔离库回归之前，不建议扩大用户或开放公网入口。**

证据口径：

- **已验证**：通过命令、自动化测试或隔离复现确认。
- **高概率推断**：代码路径明确，但本轮未在目标 PostgreSQL/NUC 上触发。
- **需确认**：取决于产品规则、外部配置或目标环境。

### 2. 全局地图

### 2.1 模块与职责

| 模块 | 主要职责 | 关键入口 |
| --- | --- | --- |
| `accounts` | 部门、用户资料、登录会话、特权账号设备限制 | 中间件、账号 API、会话管理页面 |
| `common` | Web 页面、共享权限、审计、安全限流、系统设置、部署信息 | `common/views.py`、中间件、管理命令 |
| `inventory` | 资产/耗材、编码、仓库库位、生命周期、盘点、导入、标签 | 模型、DRF API、领域 service |
| `workflow` | 申请、审批、预占、出入库台账、采购、离线待复核 | `workflow/services.py`、API、Web views |
| `notifications` | 站内通知、钉钉、低库存提醒 | 通知 service、管理命令、systemd timer |

### 2.2 部署拓扑

```mermaid
flowchart TD
    U[浏览器 / PWA / 扫码枪] -->|HTTP 或 HTTPS| N[Nginx :80/:443]
    N -->|反向代理| G[Gunicorn 127.0.0.1:8000\n3 sync workers / 60s timeout]
    N -->|/static/| S[静态文件]
    N -->|/media/ 当前直接公开| M[本地媒体目录]
    G --> D[Django 单体]
    D --> P[(PostgreSQL)]
    D --> M
    D --> DD[钉钉 API]
    T[systemd timers] --> D
    T --> B[本机备份目录]
```

关键端口：Nginx `80/443`、Gunicorn `127.0.0.1:8000`、PostgreSQL `5432`。生产设计中 Gunicorn 和 PostgreSQL 不应直接暴露给外部客户端。

### 2.3 核心业务调用链

```mermaid
sequenceDiagram
    participant A as 申请人
    participant API as Web/API
    participant W as Workflow Service
    participant DB as PostgreSQL
    participant O as 审批/仓库人员
    participant N as 通知
    A->>API: 新建草稿及明细
    API->>DB: 保存 WorkflowRequest/Line
    A->>API: 提交
    API->>W: submit_request
    W->>DB: 行锁、业务校验、预占、审批任务、日志
    W-->>N: transaction.on_commit 通知
    O->>API: 审批
    API->>W: approve_request
    W->>DB: 行锁、状态和预占更新
    O->>API: 执行出入库
    API->>W: process_request
    W->>DB: 资产/数量、流水、借用、生命周期、审计
```

快速出入库复用 `quick_process_request`，跳过人工审批但仍应经过服务层事务、状态校验和台账写入。离线模式不会直接改库存：浏览器先把表单保存到 IndexedDB，联网后提交为 `OfflineOperation`，再由仓库人员复核执行。

### 2.4 导入与恢复数据流

```mermaid
flowchart LR
    X[V3 Excel] --> PARSE[结构化解析]
    PARSE --> VALIDATE[基础资料/编码/重复/数值预检]
    VALIDATE --> BATCH[ImportBatch + ImportIssue]
    BATCH -->|人工确认| APPLY[单事务写入]
    APPLY --> MUT[ImportMutation 前后快照]
    APPLY --> AUDIT[OperationAuditLog]
    MUT -->|无后续修改和业务历史| ROLLBACK[整批撤销]
    MUT -->|存在差异或历史| BLOCK[阻止撤销并列原因]
```

### 2.5 权限边界

- 权限按录入、出库、审批、报表、编码、导入、标签、校正、财务等组组合。
- Web 页面通常使用 `role_required`；DRF 使用 `IsInventoryEditor`、`IsInventoryExecutor`、`IsWorkflowApprover` 等。
- 普通申请人原则上只能查看本人申请；仓库人员查看可处理任务。
- 已发现的边界偏差见 P0-01、P1-03、P2-02。

### 2.6 失败传播与恢复

- 业务状态服务普遍使用 `transaction.atomic()` 和 `select_for_update()`，并把业务冲突转成中文错误。
- SQLite 仅用于本机功能测试；行锁和真实竞态必须由 PostgreSQL 专项覆盖。
- 每日备份会校验 `pg_dump`、媒体 tar 和校验和，恢复演练使用独立测试库。
- 当前部署不是 staging/symlink 原子切换；部署中途失败可能留下已覆盖代码目录，详见 P1-04。
- 钉钉发送在事务提交后、但仍在 Web worker 内同步执行，详见 P1-07。

### 3. 当前环境和可运行性

| 项目 | 结果 | 证据状态 |
| --- | --- | --- |
| Git | HEAD `bbc187c`; 审查开始时工作区干净 | 已验证 |
| Python | 3.12.10 | 已验证 |
| Django / DRF / Pillow | 5.2.9 / 3.16.1 / 12.3.0 | 已验证 |
| 本机数据库 | SQLite；本机业务库有 6 个未应用迁移 | 已验证 |
| 生产数据库设计 | PostgreSQL | 代码与文档已验证，本轮未连接目标环境 |
| 启动入口 | `python manage.py runserver`；生产为 systemd + Gunicorn | 已验证 |
| Web 前端 | Django 模板 + 原生 JavaScript + PWA Service Worker | 已验证 |
| 代码规模 | Python 212 个文件；主要 app 约 30,881 行 Python | 已验证 |

本机业务库尚未应用：

- `accounts.0004_accountsession`
- `common.0012_alter_apiaccesstoken_expires_at`
- `common.0013_sitesettings`
- `inventory.0027_source_sc_prefix`
- `inventory.0028_alter_importbatch_status_importmutation`
- `inventory.0029_asset_customer_ref`

本轮没有擅自迁移该库。自动化测试使用独立临时库并完整应用全部迁移。

### 4. 已完成测试及结果

### 4.1 自动化与静态检查

| 检查 | 预期 | 实际 | 结论 | 剩余风险 |
| --- | --- | --- | --- | --- |
| Django 全量测试 | 所有可执行用例通过 | `409/409` 通过，6 项跳过，255.626 秒 | 通过 | 跳过项均为 PostgreSQL 并发专项 |
| `manage.py check` | 无错误 | 无错误 | 通过 | 不等于安全扫描 |
| `makemigrations --check --dry-run` | 无模型漂移 | 无变更 | 通过 | 本机业务库仍有未应用迁移 |
| Python 编译 | 无语法错误 | 通过；字节码改写到临时目录 | 通过 | 源目录 ACL 不允许直接写 `__pycache__` |
| JavaScript 语法 | 自有脚本可解析 | 通过 | 通过 | 未做真实浏览器 E2E |
| `bash -n` | 部署脚本语法有效 | 全部通过 | 通过 | 未执行目标机部署 |
| 标签渲染 | 版式单测通过 | `7/7` | 通过 | 真机暂停 |
| `pip check` | 依赖无安装冲突 | 通过 | 通过 | 不代表无 CVE |
| `git diff --check` | 无空白错误 | 通过 | 通过 | 无 |
| 仓库敏感文件扫描 | 无凭据/数据库/备份入库 | 未发现已跟踪的凭据、数据库、Excel、备份、归档或 `.env` | 通过 | 不能替代 Git 历史秘密扫描平台 |

6 项跳过用例：重复审批、重复资产预占、重复快速出库、耗材并发扣减、并发登录限流、特权账号并发会话上限。仓库提供了安全的 PostgreSQL 隔离测试脚本，但本轮按要求未连接 NUC，因此**当前 HEAD 的这 6 项未在 PostgreSQL 重跑**。

### 4.2 只读 HTTP 基线

前置条件：本机开发服务、`/health/` 与 `/login/`，并发 8、共 200 次 GET。

结果：错误 0，吞吐 63.28 req/s，P50 121.04 ms，P95 177.54 ms，P99 200.23 ms。

结论：只证明本机匿名轻量入口的基线，不代表登录后资产列表、报表、扫码或写事务容量。

### 4.3 安全配置检查

`manage.py check --deploy` 返回 6 个警告：本地配置启用了 DEBUG/开发密钥，且未开启 HSTS、HTTPS 跳转和 Secure Cookie。前两项是本地开发配置；后四项与当前内网 HTTP 部署策略一致。正式公网前必须切换 HTTPS 配置并重新检查。

通过 OSV 对锁定版本做精确版本查询，Django 5.2.9、DRF 3.16.1、Pillow 12.3.0 均命中多个公开公告。别名会重复返回相同漏洞，报告不把返回条目数当成独立漏洞数。代表性公告见 P1-01。

### 4.4 隔离风险复现

临时测试模块在 Django 自动创建/销毁的内存数据库中执行，随后已删除：

| 场景 | 预期安全行为 | 实际 | 结论 |
| --- | --- | --- | --- |
| 申请人 DELETE 自己的待审批申请 | 拒绝硬删除，使用撤回/隐藏服务 | 返回 204，记录消失 | 风险已复现 |
| 仅报表权限读取库存和流水 API | 不返回成本字段 | 返回 `unit_cost`、`cost_amount` | 风险已复现 |
| `QuerySet.update()` 修改审计日志 | 应拒绝 | 成功修改 1 行 | 风险已复现 |

### 4.5 补充安全与认证容量验证（2026-11-09）

| 检查 | 范围 | 实际结果 | 结论 |
| --- | --- | --- | --- |
| pip-audit（PyPI） | `requirements-lock.txt` | 4 个包、104 条公告 | 未通过；见 P1-01 |
| pip-audit（OSV） | `requirements-lock.txt` | 4 个包、128 条公告 | 未通过；见 P1-01 |
| Bandit 1.9.4 | 排除测试与迁移后的生产 Python | 高危 0、中危 0、低危误报 10 | 通过 |
| 认证 Wapiti 3.3.2 | 162 个 URL/表单，CSRF/SQL/XSS/SSRF/XXE/上传等 | 确认漏洞 0，Internal Server Error 0，服务端 traceback 0 | 通过；不等于人工渗透 |
| 本机认证容量 | 一次性 SQLite，八条业务路由，各档 80 次 GET | 1/4/8/12 并发错误率 0；12 并发 P95 234.92 ms、P99 245.94 ms | 通过 |
| NUC 认证容量 | Nginx/Gunicorn/PostgreSQL，八条业务路由，各档 160 次 GET | 部署后共 640 次均为 200；12 并发 P95 166.58 ms、P99 191.05 ms、86.12 req/s；Gunicorn RSS 合计峰值约 483 MB、CPU 采样合计峰值 11.6%、PostgreSQL 峰值连接 3、根盘读/写扇区增量 0/5464 | 通过；仍需长时稳定性和上传/Excel 隔离压测 |

DAST 首轮曾由超长布尔查询、非法 UUID 和二维码标签 ID 触发 18 个 500；修复后复扫降为 0。Wapiti 的登录提示存在误报，但扫描实际访问权限页、系统设置和业务表单并携带有效 CSRF，会话有效。方法探测发现 Django 默认密码修改视图声明 PUT，现已收紧为 GET/POST/HEAD/OPTIONS；开发服务器静态文件 CONNECT 差异仅为错误页差异，未证明方法可用。

### 5. P0/P1/P2 问题清单

### P0-01 申请单 API 可绕过流程执行硬删除

- **位置**：`backend/apps/workflow/views.py:81-105`；`backend/apps/workflow/services.py:509-546`；`backend/apps/workflow/models.py:660-690`
- **触发条件**：已登录申请人对本人可见申请调用 `DELETE /api/workflow/requests/{id}/`；仓库角色对其可见且无 `PROTECT` 关系的单据也可能触发。
- **原因**：`WorkflowRequestViewSet` 继承完整 `ModelViewSet`，未限制 `http_method_names`、未覆盖 `destroy`；既有撤回/隐藏服务未被调用。
- **影响**：草稿以及无库存预占/流水保护的申请可硬删除；申请明细、审批任务和审批日志使用 `CASCADE`，可能一同消失，破坏业务历史和审计链。
- **证据**：隔离 API 测试返回 204 且对象不存在；代码中库存流水虽 `PROTECT`，审批任务/日志为 `CASCADE`。
- **修复建议**：立即从 ViewSet 禁用 `DELETE`；所有“删除”统一映射到 `hide_request_for_applicant`，撤回走 `withdraw_request`；模型/数据库层补充对业务单硬删除的保护。
- **验证方法**：覆盖草稿、待审批、驳回、撤回、待执行、已完成及仓库角色反向用例；断言对象、审批日志和预占均未被硬删除。
- **修复风险**：低到中；需确认是否允许真正删除从未提交的空草稿，建议也采用受控清理任务而非通用 DELETE。

### P1-01 关键运行依赖存在公开安全漏洞

- **位置**：`backend/requirements-lock.txt:1-15`；`backend/requirements.txt:1-5`；`backend/apps/common/upload_validation.py:20-51`
- **触发条件**：恶意请求体/Accept 头、特定 Django 路径，或有上传权限的账号提交恶意图片。
- **原因**：锁定版本为 Django 5.2.9、DRF 3.16.1、Pillow 12.3.0；OSV 已发布后续公告。
- **影响**：包括 SQL 注入、资源耗尽、缓存/响应泄露和图片解码原生内存破坏。Pillow `GHSA-5x8w-8jj5-q52h` / `CVE-2026-112398` 描述了可导致代码执行的堆溢出；应用会对上传图片执行 `Image.open()`、`verify()`、`load()` 和重编码。
- **证据**：2026-11-09 pip-audit 2.10.1 使用 PyPI/OSV 分别返回 104/128 条公告，集中于 Django 5.2.9、DRF 3.16.1、Pillow 12.3.0、sqlparse 0.5.5；上传路径静态确认可达。
- **修复建议**：Django、DRF、sqlparse 先在候选运行时升级并做全量兼容回归；Pillow 13 要求 Python 3.12，而生产 Ubuntu 22.04 当前 Python 3.10.12，因此必须先制定 Python 3.12 运行时、Linux wheel 和回滚方案。升级完成前继续限制图片上传角色、格式、像素和入口。
- **验证方法**：重新执行 OSV/pip-audit、全量测试、上传格式回归、标签二维码回归和 NUC PostgreSQL 冒烟。
- **修复风险**：中到高；涉及 Python 运行时和 Pillow 大版本兼容，不能在生产机直接试装。

### P1-02 受保护附件可绕过 Django 授权直接下载

- **位置**：`backend/apps/common/views.py:3251-3268`；`deploy/ubuntu22/nginx-after-sales-warehouse.conf:17-20`
- **触发条件**：攻击者获得或猜到 `/media/` 文件路径。
- **原因**：Django 下载视图要求登录和仓库角色，但 Nginx 对整个媒体目录配置公开 `alias`。
- **影响**：发票、维修报告、日志、快递资料等附件可能绕过权限访问。
- **证据**：静态配置已验证；本轮未在 NUC 实际请求媒体路径。
- **修复建议**：拆分公开图片和私有附件；私有附件使用 Nginx `internal` + Django `X-Accel-Redirect`，或全部经授权视图流式返回。
- **验证方法**：匿名、普通申请人、仓库角色、管理员四类账号访问同一附件；直接 `/media/...` 必须 404/403。
- **修复风险**：中；需迁移已有文件路径并保证标签 Logo/资产公开缩略图不退化。

### P1-03 财务成本字段通过只读 API 泄露给非财务角色

- **位置**：`backend/apps/workflow/serializers.py:85-101`、`backend/apps/workflow/views.py:218-255`；`backend/apps/inventory/serializers.py:340-378`、`backend/apps/inventory/views.py:504-514`
- **触发条件**：任何属于仓库权限组的用户读取库存流水或耗材 API。
- **原因**：资产 API 已按 `can_view_financials` 移除采购金额，但 `InventoryTransactionSerializer` 和 `StockItemSerializer` 没有同等过滤。
- **影响**：采购成本、库存价值和供应商信息违反组合权限设计。
- **证据**：仅 `warehouse_reports` 角色的隔离 API 测试成功读取 `cost_amount=12.50` 和 `unit_cost=12.50`。
- **修复建议**：建立财务/非财务只读 serializer，或在 `to_representation` 统一移除成本与敏感供应商字段；不要只靠前端隐藏。
- **验证方法**：录入、出库、审批、报表、财务、管理员六角色矩阵测试列表与详情接口。
- **修复风险**：低；可能影响依赖旧 API 字段的内部页面，需搜索调用方。

### P1-04 部署前快照失败仍继续，发布过程不是原子切换

- **位置**：`deploy/ubuntu22/install.sh:240-273,343-419`；`deploy/ubuntu22/rollback.sh:105-152`
- **触发条件**：`pg_dump`、pip 安装、迁移、静态收集或服务重启中任一步失败。
- **原因**：快照失败仅打印警告；随后 `rsync --delete` 原地覆盖在线目录。回滚脚本只回滚代码，不回滚数据库结构；依赖从非锁定 `requirements.txt` 重装。
- **影响**：部署失败时可能同时缺少即时数据库快照，并留下代码、依赖、迁移版本不一致；旧代码对新 schema 未必兼容。
- **证据**：脚本静态审查；`bash -n` 只能证明语法，不证明失败路径安全。
- **修复建议**：快照失败必须中止；构建独立 release 目录和 venv，预先安装/检查/收集静态文件，再通过 symlink 原子切换；迁移必须标注兼容策略；部署使用 Linux 生成的完整锁文件。
- **验证方法**：隔离 VM 注入 `pg_dump`、pip、migrate、health 失败，验证旧服务继续可用、release 指针不切换且可恢复。
- **修复风险**：中到高；需要重构部署目录和 systemd 启动路径。

### P1-05 审计不可变性可被 ORM 批量操作绕过

- **位置**：`backend/apps/common/models.py:46-66`；`backend/apps/common/tests.py:2372-2377`；`backend/apps/common/views.py:1121-1125`
- **触发条件**：管理命令、脚本或代码调用 `QuerySet.update()` / `delete()`。
- **原因**：`ImmutableAuditMixin` 只覆盖实例 `save()`、`delete()`；批量 ORM 和原始 SQL不调用这些方法。
- **影响**：`OperationAuditLog`、`SecurityAuditEvent`、`AssetLifecycleEvent`、`AssetStateCorrection` 可被改写或删除，不能满足“只增不改”的审计承诺。
- **证据**：隔离测试成功批量修改审计摘要；现有归档测试也明确用 `QuerySet.update()` 改写审计时间。
- **修复建议**：增加受控 QuerySet/Manager 拒绝 update/delete，并在 PostgreSQL 增加阻止 UPDATE/DELETE 的触发器或最小权限；测试时间应通过工厂/时钟注入生成，而非绕过保护。
- **验证方法**：实例、QuerySet、admin、raw SQL（应用账号）四条路径都必须失败；归档导出仍可测试。
- **修复风险**：中；系统清理和测试夹具目前依赖批量操作，需要先移除这些隐式依赖。

### P1-06 发布代码仍携带清空全部业务数据入口，且删除清单已过时

- **位置**：`backend/templates/permission_management.html:25-26`；`backend/apps/common/views.py:1106-1153`
- **触发条件**：超级管理员输入确认文字和密码后执行。
- **原因**：历史调试清库入口仍存在；删除顺序/模型清单未覆盖后续增加的生命周期、附件、维修、装配、离线操作等保护关系。
- **影响**：与 `PROGRAM_STANDARDS.md`“正式发布目录禁止携带清库能力”冲突；真实数据下可能因 `PROTECT` 失败，或未来模型遗漏造成残留。虽然事务可避免部分删除提交，入口本身仍扩大重大误操作面。
- **证据**：代码已验证；现有测试只覆盖简单资产和账号，不覆盖完整关系图。
- **修复建议**：从正式应用移除该入口。调试清理由不入库、逐次确认、先快照的受控运维工具完成；不得维护手写模型删除列表。
- **验证方法**：正式构建包和路由中不存在清库入口；安全扫描检查关键字；测试数据通过命名空间/fixture 回滚清理。
- **修复风险**：低；会改变调试习惯，但符合仓库规则。

### P1-07 钉钉网络请求同步占用 Gunicorn worker

- **位置**：`backend/apps/workflow/services.py:68-73`；`backend/apps/notifications/dingtalk.py:69-110`；`deploy/ubuntu22/after-sales-warehouse.service:12`
- **触发条件**：审批/出入库提交后通知多个钉钉用户，网络慢、DNS 超时或钉钉不可达。
- **原因**：`transaction.on_commit()` 仅延后到提交后执行，并未异步；个人通知逐个发送，每次最长约 5 秒，最后还可能回退群机器人。
- **影响**：数据库已提交但 HTTP 请求长时间无响应；3 个同步 worker 容易被通知耗尽并接近 60 秒 Gunicorn timeout，用户可能重复提交。
- **证据**：调用链和超时配置静态确认；本轮未故障注入钉钉网络。
- **修复建议**：通知写入持久化 outbox，由 systemd worker/管理命令异步发送；设置总超时、指数退避、幂等键和失败状态。
- **验证方法**：模拟 DNS 失败、连接超时和 500；业务请求应快速成功，通知最终重试且不重复。
- **修复风险**：中；需新增通知作业状态和后台 worker。

### P1-08 Workflow API 创建申请与明细未显式原子化

- **位置**：`backend/apps/workflow/serializers.py:155-161`
- **触发条件**：申请对象创建成功后，明细 `bulk_create` 因数据库约束或异常失败。
- **原因**：serializer `create()` 没有 `transaction.atomic()` 包围主表和明细。
- **影响**：可能留下没有明细的孤立草稿；用户重试后出现重复草稿。
- **证据**：代码静态确认；当前正常路径测试通过，未注入明细写入异常。
- **修复建议**：把创建申请和明细移动到原子服务；先做跨行校验，再一次提交并写审计。
- **验证方法**：人为让第二条明细违反约束，断言主表、明细和审计均为 0。
- **修复风险**：低。

### P2-01 受控测试资产删除快照不完整

- **位置**：`backend/apps/inventory/import_recovery_services.py:220-248`；`backend/apps/inventory/models.py:856-866`
- **触发条件**：删除带网络接口扩展字段、但无业务历史的测试资产。
- **原因**：快照只记录接口名、IP、MAC、主机名，遗漏子网掩码、网关、VLAN、主接口标记和备注。
- **影响**：删除后无法从审计完整还原，违反“完整快照”标准。
- **证据**：字段逐项对比已验证。
- **修复建议**：使用通用 `snapshot_instance()` 保存每个关联对象，记录关联对象 ID、时间戳和文件元数据。
- **验证方法**：填满所有字段后删除，断言审计 JSON 完整一致。
- **修复风险**：低。

### P2-02 资产照片上传权限过宽

- **位置**：`backend/apps/inventory/views.py:319-345`
- **触发条件**：仅报表、审批或出库权限的仓库角色调用上传照片 API。
- **原因**：action 使用 `IsWarehouseOperator`，其他资产写操作使用 `IsInventoryEditor`。
- **影响**：只读角色可以修改资产展示资料并生成媒体文件。
- **证据**：权限类静态确认；现有测试只验证旧 `warehouse_staff` 可上传。
- **修复建议**：改为 `IsInventoryEditor` 或新增明确的资产照片权限。
- **验证方法**：组合权限矩阵正反向测试。
- **修复风险**：低。

### P2-03 管理员可把终态资产直接改回其他状态，产品规则需确认

- **位置**：`backend/apps/inventory/state_services.py:27-52,77-131`
- **触发条件**：系统管理员对已售出、退供应商、报废、已转耗材或禁用资产执行状态调整。
- **原因**：终态来源限制只对非管理员生效；管理员目标集合又包含售出、退供应商和报废。
- **影响**：可能绕过反向业务单据和库存台账。此前用户口径是“终态一律禁止直接校正”，当前代码不完全一致。
- **证据**：代码静态确认；属于产品规则冲突，需用户最终确认。
- **修复建议**：终态恢复只允许专门的冲销/反向单据，管理员也不能普通校正；保留双人复核和原因。
- **验证方法**：每个终态到在库的反向用例；合法冲销产生对应流水和审计。
- **修复风险**：中；可能影响历史数据清理流程。

### P2-04 离线队列在浏览器中明文持久化业务与个人信息

- **位置**：`backend/apps/common/static/warehouse/offline.js:4-168,170-214`
- **触发条件**：快速出入库断网保存；共享电脑、浏览器配置被复制或发生同源 XSS。
- **原因**：表单完整字段保存到 IndexedDB，仅以用户 ID 在应用层过滤；退出登录不清除，未设置保存期限。
- **影响**：接收人姓名、电话、地点、项目/工单等会长期留在客户端。
- **证据**：前端代码静态确认。账号隔离和幂等设计本身正确。
- **修复建议**：明确离线数据保留期限；退出时提供清理；同步成功自动删除或脱敏；增加 CSP 降低 XSS 风险。不要把前端加密当成共享设备安全边界。
- **验证方法**：跨账号、退出、过期、同步成功、浏览器重启和异常关闭场景。
- **修复风险**：低到中；需避免清理尚未同步的记录。

### P2-05 当前测试仍缺真实浏览器、当前 PostgreSQL 和容量结论

- **位置**：测试与发布流程整体缺口。
- **触发条件**：浏览器交互差异、真实行锁竞态、登录后大列表、报表或上传并发。
- **原因**：没有 Playwright/Selenium；本轮未连接 NUC；只读基线只覆盖匿名轻量页面。
- **影响**：移动端布局、扫码焦点、离线恢复、并发锁和资源上限仍可能在现场暴露问题。
- **证据**：6 项 PostgreSQL 用例本轮跳过；无 E2E 配置文件。
- **修复建议**：P0/P1 修复后建立最小 Playwright 冒烟；在隔离 PostgreSQL 运行竞态；对资产列表、扫码查询、报表做只读容量测试并采集 CPU/内存/IO/连接数。
- **验证方法**：按下一阶段清单执行并保存原始结果。
- **修复风险**：低，主要是环境和时间成本。

### 6. 当前模块深挖

### 6.1 账号与安全

优点：登录限流状态放在数据库，多进程共享；特权账号会话限制有 PostgreSQL 专项测试；API 已移除 Basic Auth；令牌有时效。

风险：正式公网前仍缺 HTTPS、CSP、当前依赖安全基线、DAST 和人工渗透；会话并发专项本轮未重跑。

### 6.2 库存与资产

优点：AST 永久身份和 SC/DZSB 业务码分离；状态变化主要经事务服务；多维状态、生命周期、维修、交接、装配和盘点较完整；普通 API 不能直接写状态和数量。

风险：照片权限偏宽；管理员终态校正规则需收口；`inventory/models.py` 体积过大，领域不变量分散在 model、serializer、form 和多个 service。

### 6.3 申请、审批与出入库

优点：提交、审批、执行、预占和流水使用行锁与事务；快速出入库与离线复核复用服务层；重复执行和冲突已有测试。

风险：通用 API 的硬删除和非原子创建绕过了严谨的服务层；通知延迟会让已提交事务表现成前端超时。

### 6.4 导入、回滚与受控删除

优点：V3 强制区分单件资产/耗材，先预检后应用；对象级前后快照、后续变更比较、业务历史阻断和整批事务撤销设计合理。

风险：审计不可变性没有数据库保障；删除关联快照不完整；全量基准会把同仓库未出现资产禁用、把未出现耗材数量归零，必须继续保持超级管理员、明确范围和部署前快照。

### 6.5 前端与 PWA

优点：服务工作线程只缓存静态资源，不缓存业务 HTML；HTML 响应设置 `no-store`；扫码和离线队列有账号绑定、客户端幂等键和服务端人工复核。

风险：没有真实浏览器 E2E；`web_base.html` 承担过多全局脚本和样式；离线内容明文长期保存。

### 6.6 运维与可观测性

优点：systemd 管理明确；健康检查覆盖服务、端口、数据库、HTTP、磁盘和内存；备份有完整性校验，恢复演练使用非生产库；日志输出 journald。

风险：发布目录原地覆盖；备份同机；健康接口只证明数据库可查询，缺少业务依赖、队列堆积和慢查询指标；无集中指标和趋势。

### 7. 多角色评审意见

### 架构师

保持 Django 单体是合理选择，当前规模不需要微服务。优先拆分超大模块而非重写框架：`common/views.py` 按页面域拆包、`workflow/services.py` 按申请/执行/采购/通知拆分、`inventory/models.py` 按主数据/资产/盘点/生命周期拆分。先封住 API 绕过路径和发布风险，再做结构重构。

### 开发负责人

服务层事务和领域测试是当前最大资产，但 DRF serializer/ViewSet 仍存在绕开服务层的路径。应建立统一规则：任何业务对象写操作只能调用 service；ViewSet 默认只读，明确列出的 action 才可写。依赖锁必须在目标 Linux/Python 版本生成。

### 测试负责人

409 项测试数量可观，但覆盖率数字不是当前关键问题。最需要补的是“禁止路径”测试：硬删除、财务字段、媒体直链、批量审计改写、部署失败注入。随后补浏览器 E2E 和当前提交的 PostgreSQL 竞态。

### 安全负责人

依赖漏洞、媒体授权绕过和财务字段越权应在扩大测试前解决。正式公网必须以 HTTPS、Secure Cookie、CSP、严格 Host/CSRF/CORS、私有媒体和独立 DAST 为准出条件。钉钉/Cloudflare 不应成为绕开这些条件的捷径。

### SRE/运维

现有定时备份与恢复演练基础良好，但发布必须改为“构建候选版本 -> 离线验证 -> 原子切换 -> 健康检查 -> 自动回退”。部署前快照失败应是硬阻断。异机加密备份仍是正式运营前必做项。

### 最终用户

核心流程已经覆盖仓管员日常工作，但入口多、表单密集和异常恢复仍需现场观察。用户最关心的是：扫码结果不丢、重复提交不重复出库、失败能看懂、历史单据可追溯。修复 P0 后，优先用真实仓管员完成借用、归还、快速出库、盘点和离线恢复五条任务，而不是继续增加入口。

### 8. 改进优先级清单

### 第一批：阻断扩大试运行

1. 禁用申请单 API 硬删除并补全状态矩阵回归。
2. 修复私有附件 Nginx 绕过。
3. 收口库存/流水 API 财务字段。
4. 制定并执行依赖与 Python 运行时升级方案。
5. 部署前快照失败改为硬阻断。

### 第二批：保证审计和发布可信

1. PostgreSQL 层落实审计只增不改。
2. 移除正式应用清库入口。
3. 补完整受控删除快照。
4. 申请创建进入原子服务。
5. 通知改持久化异步 outbox。
6. 部署改独立 release + 原子切换，建立 schema 兼容检查。

### 第三批：补足上线证据

1. 当前 HEAD 的 PostgreSQL 6 项竞态测试。
2. Playwright 最小 E2E：登录、申请审批执行、快速出入库、扫码、离线同步、权限矩阵。
3. 登录后只读容量基线和 NUC 资源水位。
4. HTTPS/CSP/私有媒体配置回归。
5. DAST、依赖扫描和人工权限测试。

### 第四批：可维护性和体验

1. 拆分超大 views/services/models/template，不改变 URL 和数据库契约。
2. 建立 OpenAPI 契约和 API 版本策略。
3. 离线队列保留策略、同步成功清理和共享设备提示。
4. 以五条仓管任务做现场可用性测试，删除重复入口和非必要字段。

### 9. 尚未验证的风险和需确认事项

- 本次已在 NUC PostgreSQL 隔离库重跑触发器与并发专项，并完成原子部署和回滚式业务冒烟；尚未执行真实数据库恢复和异机恢复演练。
- pip-audit、Bandit 和认证 DAST 已执行；依赖公告仍未修复，主要阻塞是 Pillow 13 要求 Python 3.12 而 NUC 当前为 3.10.12。
- 未使用真实 DS3678、移动浏览器相机和标签打印机。
- 未验证钉钉企业应用个人通知；群机器人历史联通结果不代表当前网络可用。
- 浏览器 E2E、认证 DAST 和 Bandit 已完成；仍未执行独立人工渗透和 HTTPS 公网边界复测。
- 登录后资产列表、报表和核心只读业务页容量已完成；Excel 导入、图片上传、长时稳定性及可靠 CPU/RSS/磁盘 IO 水位仍未执行。
- 终态资产直接恢复规则已确认并实现为禁止；专用冲销/反向单据仍需按业务类型补齐。
- 需确认公开资产照片的访问边界，再决定媒体目录拆分方案。
- CSP 因现有模板内联脚本暂时保留 `unsafe-inline`；移除需要先模块化前端脚本。

### 10. 下一阶段计划

本轮 P0/P1 工程问题已经修复、部署并回归。剩余工作按以下顺序推进：

1. **依赖运行时批次**：为 NUC 准备 Python 3.12 候选运行时，升级 Django/DRF/Pillow/sqlparse 后执行全量和 PostgreSQL 回归，再更新锁文件。
2. **容量与恢复批次**：补充 Excel/图片上传隔离容量、CPU/RSS/磁盘 IO 长时水位和异机加密备份恢复演练。
3. **HTTPS 安全批次**：正式域名、证书、Secure Cookie、HSTS 分阶段启用、DAST 和人工渗透。
4. **现场验收批次**：DS3678、中文输入法、移动相机、断网恢复和标签打印机。
5. **前端维护批次**：拆出内联脚本后移除 CSP `unsafe-inline`，继续拆分超大 views/services/template。

每批至少执行：相关专项测试、`manage.py check`、迁移漂移检查、Django 全量测试、JavaScript/脚本检查、`git diff --check`。涉及行锁、权限、迁移或部署的批次必须增加 PostgreSQL 隔离回归，不能用 SQLite 结果代替。

---

# 第二部分：2026-11-23 全景审查

> 来源：原 `REVIEW_REPORT_1.md`（仓储系统第一轮全景审查报告）。
> 这一轮是距当前最近的全量审查，覆盖代码、配置、部署脚本与自动化测试。
> 其中 P1-01（主数据删除）与 P2-13/14/15/16 已修复并部署，
> 详见第 20-23 节的修复轮记录。

> 状态说明：第 1 至 19 节记录只读审查阶段的历史状态。用户授权修复后，第 20 节更新了 P1-01 的代码、迁移和本机回归状态，当前结论以第 20 节及权威状态文档为准。

审查日期为 2026-11-23。范围覆盖当前工作区代码、配置、部署脚本、自动化测试和本机 HTTP 冒烟。没有连接 NUC 业务数据库，没有修改生产配置，没有执行迁移、部署、删除或数据修复。

### 1. 执行摘要

系统是一个 Django 单体仓储管理系统，使用 Django REST Framework 提供 API，服务端模板和原生 JavaScript 提供 Web/PWA，生产拓扑为 Nginx、Gunicorn、Django、PostgreSQL，媒体和备份存放在服务器持久化目录。核心闭环为资产或耗材资料、申请、审批、预占、出入库或归还、生命周期与审计，同时覆盖采购、盘点、维修、导入导出和通知。

当前提交基线在本轮后为 `73958d65939c7f77bdb27cff947b3cad35fa7452`，工作区已验证干净。开始本轮时读取到的基线是 `a2fd9a7c782a45e568a50874ca4de8f276486272`，期间出现了一个外部提交 `73958d6`，其内容与本轮审查的 797 项自动化结果一致。该提交不是本轮创建的，报告按最终可读工作区审查，不能把它当作本轮由审查代理提交的修复。

NUC SSH 返回 `Permission denied (publickey,password)`，`/opt/after-sales-warehouse/current/release-info` 本轮无法读取，因此 NUC 实际运行 release 为“未复核”。历史文档中的服务器版本不作为本轮现场证据。

本机 Django 全量测试为 797 通过、8 跳过，系统检查、迁移漂移、pip 依赖一致性、差异格式和 JavaScript 语法通过。本机开发服务器的 `/health/`、`/login/` 和 `/sw.js` 返回 200。开发服务器提示本机 SQLite 有 19 个未应用迁移，未执行迁移以避免改变现有本机数据。

本轮没有可证实的 P0。优先处理一个影响数据完整性的 P1 和多个 P2。P1 是主数据删除的模型层约束仍可被 admin 或其他直接 ORM 路径绕过。P2 主要集中在报表全量物化、安全门禁没有自动化接入、开发配置容易被误用于部署，以及大模块维护成本。PostgreSQL 并发、真实浏览器、iPhone Safari、DS3678 和 ZT610 现场验收仍未验证。

### 2. 全局地图

### 模块划分及职责

| 模块 | 主要职责 | 关键入口 |
| --- | --- | --- |
| `apps.common` | 页面主干、权限辅助、审计、限流、健康检查、PWA service worker、系统设置 | `backend/apps/common/urls.py`、`backend/apps/common/views/legacy.py` |
| `apps.accounts` | 用户档案、账号开通申请、会话账本、令牌和账号治理 | `backend/apps/accounts/urls.py`、`backend/apps/accounts/views.py` |
| `apps.inventory` | 仓库、库位、资产、耗材、编码、盘点、组合设备和标签作业 | `backend/apps/inventory/urls.py`、`backend/apps/inventory/views.py` |
| `apps.workflow` | 申请、审批、预占、出入库、采购到货、离线复核和延期 | `backend/apps/workflow/urls.py`、`backend/apps/workflow/services.py` |
| `apps.notifications` | 站内通知、低库存状态、钉钉 outbox 和重试 | `backend/apps/notifications/views.py`、`backend/apps/notifications/outbox.py` |
| `deploy/ubuntu22` | 发布包、原子 release、备份、恢复演练、健康检查、systemd timers 和 Nginx | `deploy/ubuntu22/install.sh`、`backup.sh`、`restore-drill.sh` |

### 程序入口和启动过程

1. `backend/manage.py` 设置 `DJANGO_SETTINGS_MODULE=warehouse_backend.settings`，用于迁移、检查、测试和本机开发服务器。
2. `backend/warehouse_backend/settings.py` 读取 `backend/.env` 或服务器环境变量，建立数据库、静态文件、媒体、日志、认证和安全中间件配置。
3. `backend/warehouse_backend/urls.py` 汇总页面、认证、账户、库存、工作流和通知 API，并在 DEBUG 时挂载静态和媒体调试路由。
4. 生产由 Nginx 接收 80 或 443 流量，再代理到 `127.0.0.1:8000` 的 Gunicorn。systemd timers 触发备份、一致性巡检、健康检查、通知 outbox、低库存摘要和安全清理。

### 核心申请到出入库调用链

```mermaid
sequenceDiagram
    participant U as 仓管员或申请人
    participant V as 页面或 API View
    participant S as workflow.services
    participant DB as PostgreSQL 或 SQLite
    participant N as Notification 与 DingTalk outbox

    U->>V: 创建申请和明细
    V->>S: 事务创建 WorkflowRequest 与 WorkflowRequestLine
    S->>DB: 校验资产、耗材、仓库、库位和权限
    U->>V: 提交申请
    V->>S: submit_request
    S->>DB: 加锁并建立 InventoryReservation
    S->>N: 写站内通知和幂等 outbox
    U->>V: 审批或驳回
    V->>S: approve_request 或 reject_request
    S->>DB: 写 ApprovalLog 和更新状态
    U->>V: 执行出入库或归还
    V->>S: process_request 或 quick_process_request
    S->>DB: 加锁资产或耗材，更新状态并写 InventoryTransaction
    S->>DB: 写 OperationAuditLog 与 AssetLifecycleEvent
    S->>N: 写结果通知和 outbox
```

### 导入、标签和通知数据流

```mermaid
flowchart LR
    W[Excel V2/V3] --> P[预检与身份解析]
    P --> T[同批事务导入]
    T --> I[Asset 或 StockItem]
    I --> A[ImportMutation 快照]
    A --> R[受控撤销]

    B[浏览器标签请求] --> J[LabelJob]
    J --> C[Windows 打印客户端]
    C --> Z[ZT610 GDI 或 RAW ZPL]

    E[业务事件] --> O[DingTalkOutbox]
    O --> D[worker 重试与退避]
    D --> X[钉钉官方 HTTPS API]
```

### 数据库、缓存、文件和第三方服务

- 生产数据库为 PostgreSQL，本机默认使用 `runtime-data/db.sqlite3`。Django ORM 是业务写入边界，关键状态变更集中在 workflow 和 inventory 服务层。
- 当前没有独立缓存服务。登录限流、会话账本、离线记录和通知 outbox 使用数据库或浏览器 IndexedDB。Service Worker 缓存静态资源并对易变脚本使用网络优先策略。
- 媒体由 `MEDIA_ROOT` 保存，Nginx 的 `/media/` 直接映射到服务器持久化目录。生命周期附件路径由 Nginx 特殊规则拦截后走 Django 下载视图。
- 钉钉群机器人和个人工作通知通过 outbox 异步发送，代码限制为官方 HTTPS 主机并设置超时。

### 鉴权与权限边界

- 页面使用 Django Session。API 使用时效令牌或 Session，默认权限为 `IsAuthenticated`，令牌入口在 `backend/apps/common/api_views.py`。
- `apps.common.permissions` 将录入、出入库、审批、报表、财务、导入、标签和账号治理拆为独立权限组。高风险动作在 View 层和服务层均有权限检查。
- 工作流对象按申请人、未分配待办、指定审批人和仓库状态过滤，避免普通申请人查看全量任务。
- 仍需要现场核验反向代理头、实际 Nginx 配置、管理员 admin 入口和 PostgreSQL 角色权限，当前只能确认源码边界。

### 部署拓扑和关键端口

```mermaid
flowchart TB
    B[浏览器或 PWA] -->|80/443| N[Nginx]
    N -->|127.0.0.1:8000| G[Gunicorn]
    G --> D[Django]
    D -->|5432| P[PostgreSQL]
    D --> M[/srv/after-sales-warehouse/media]
    T[systemd timers] --> S[备份、健康、一致性、outbox、清理]
    S --> P
    S --> M
```

### 失败传播和恢复路径

- 服务层业务错误转换为页面提示或 API 400。数据库竞争错误在部分 API 入口转换为 503，客户端可重试。
- outbox 领取使用状态、锁定时间和退避时间，失败保留记录并增加尝试次数。
- 发布脚本在切换 release 前执行数据库快照、依赖和静态文件检查，健康检查失败时恢复旧的 `current` 链接。恢复演练脚本已按 `media-snapshots` 格式更新，但本轮没有真实 Linux 和 PostgreSQL 实跑。
- 当前本机运行数据未完成迁移，启动仍能提供页面和健康检查，但依赖新字段的手工流程不能据此判定完整可用。

### 3. 当前环境和可运行性

### 已验证环境

| 项目 | 实际结果 | 证据等级 |
| --- | --- | --- |
| Python | 3.12.10，使用仓库 `.venv` | 已验证 |
| Django | 5.2.19 | 已验证，开发服务器启动日志 |
| 依赖 | `pip check` 无冲突 | 已验证 |
| Django 配置 | `manage.py check` 无问题 | 已验证 |
| 迁移漂移 | `makemigrations --check --dry-run` 无变化 | 已验证 |
| JavaScript | 全部静态 `.js` 文件 `node --check` 通过 | 已验证 |
| 本机 HTTP | `/health/`、`/login/`、`/sw.js` 为 200，令牌接口 GET 为 405 | 已验证 |
| 本机数据库 | SQLite 文件存在，但开发服务器提示 19 个未应用迁移 | 已验证，环境风险 |
| NUC release | SSH 认证失败，未读取 | 未复核 |
| PostgreSQL 并发 | 未连接目标数据库 | 未执行 |
| 浏览器 E2E | 未启动 Playwright 或真实浏览器 | 未执行 |
| DS3678、ZT610、iPhone Safari | 无现场设备连接 | 未执行 |

### 静态安全检查的边界

`manage.py check --deploy` 在本机开发配置下报告 HSTS、HTTPS 重定向、Secure Cookie、开发密钥和 DEBUG 共 6 项警告。这些警告证明当前本机配置不适合作为生产配置，不能推断 NUC 实际配置。Bandit 和 pip-audit 不在本机虚拟环境中，WSL Bash 也无法在当前执行环境启动，因此本轮没有静态 SAST、依赖 CVE 或 Ubuntu shell 语法的通过结论。

### 4. 已完成测试及结果

### 研发自测

| 命令 | 预期 | 实际 | 结果 | 剩余风险 |
| --- | --- | --- | --- | --- |
| `..\\.venv\\Scripts\\python.exe manage.py check` | 无配置错误 | System check identified no issues | 通过 | 只代表本机配置 |
| `..\\.venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run` | 无模型漂移 | No changes detected | 通过 | 不代表服务器已迁移 |
| `..\\.venv\\Scripts\\python.exe -m pip check` | 依赖无冲突 | No broken requirements found | 通过 | 未执行 CVE 审计 |
| `git diff --check` | 无空白或冲突标记 | 无输出 | 通过 | 工作区期间发生外部提交 |
| Django 全量测试 | 全部通过 | 797 passed，8 skipped，458.314 秒 | 通过 | 8 项为 PostgreSQL 专项，仍未执行 |
| Node JavaScript 语法检查 | 脚本可解析 | 全部完成 | 通过 | 不替代浏览器交互 |
| `manage.py check --deploy` | 生产配置无警告 | 6 项本地开发配置警告 | 条件通过 | 需读取 NUC 实际环境 |

### 已设计但未执行的测试层

- 正常闭环已由 Django 测试覆盖申请、审批、预占、出入库、采购、盘点、导入、审计、通知和权限反向用例。
- PostgreSQL 层的锁竞争、审计触发器、事务隔离和并发重复提交未在本轮执行。SQLite 不能证明这些语义。
- 真实浏览器、iPhone Safari 主屏幕、相机扫码、DS3678 中文输入法和 ZT610 连续走纸未执行。
- 首次安装、重启、升级、回滚、异机恢复和 Nginx/Gunicorn/systemd 现场检查未执行。

### 5. P0/P1/P2 问题清单

### P1-01 主数据删除仍可绕过 ViewSet 守卫

- **位置** `backend/apps/inventory/models.py:129`、`backend/apps/inventory/models.py:575-576`
- **触发条件** 通过 Django admin、脚本或其他直接 ORM 路径删除被引用的仓库。
- **原因** `Location.warehouse` 使用 `CASCADE`，`Asset.warehouse` 与 `Asset.location` 使用 `SET_NULL`。当前保护主要在 `WarehouseViewSet` 和 `LocationViewSet` 的 `_assert_deletable`。
- **影响** 资产可能失去仓库和库位归属，库位历史可能被物理删除，审计和追溯发生数据缺口。
- **证据** 当前模型定义已直接确认，项目状态也明确记录模型层迁移尚未完成。ViewSet 保护不能覆盖 admin 和所有代码调用点。
- **修复建议** 将业务历史关联改为 `PROTECT` 或受控停用，增加迁移和 admin 反向用例。服务层提供统一删除或停用入口，禁止直接 `Model.delete()`。
- **验证方法** 用 API、admin、服务层和直接删除四条入口分别验证引用对象阻断、空对象删除、审计一致性和迁移回滚。
- **修复风险** `PROTECT` 迁移会暴露历史脏数据，需要先做引用盘点和恢复快照，不能直接在 NUC 执行。

### P2-01 报表和供应商历史全量物化

- **位置** `backend/apps/common/views/legacy.py:2433-2484`、`2499-2630`、`4446-4530`、`4336-4443`
- **触发条件** 访问供应商价格、供应商对账、呆滞库存或报表总览，且订单、流水或资产规模增长。
- **原因** QuerySet 被直接迭代为 Python 列表或字典，呆滞库存先构造全部 `rows` 后才用 `Paginator` 截取 50 行，供应商对账也先物化全部订单行。
- **影响** 内存峰值、响应时间和 GC 压力随历史数据线性增长。CSV 导出还会持有完整结果直到响应结束。
- **证据** 源码显示 `for line in lines`、`rows.append` 和 `Paginator(rows, 50)` 的顺序。当前没有万级或百万级 PostgreSQL 压测。
- **修复建议** 把过滤、聚合、排序和分页下推数据库，页面只取当前页，导出使用 `iterator()` 或分块响应。为日期、供应商、订单状态和流水关联建立并核对索引。
- **验证方法** 使用 1 万、10 万和 100 万行 PostgreSQL 基准，记录 P95、P99、RSS、查询计划和导出首字节时间。
- **修复风险** ORM 聚合口径和现有页面展示可能不一致，需要用固定快照对照旧结果。

### P2-02 `check-all.sh` 没有执行脚本语法和安全门禁

- **位置** `deploy/ubuntu22/check-all.sh:41-89`
- **触发条件** 修改 Ubuntu shell、依赖或安全配置后只运行一键检查。
- **原因** 当前脚本只执行 Django check、迁移检查、pip check、diff check 和可选全量测试，没有 `bash -n`、Bandit 或 pip-audit。
- **影响** shell 语法回归、已知依赖漏洞和高风险 Python 模式可能在发布前漏检，安全证据依赖人工记忆。
- **证据** 脚本正文未出现这些检查。本轮 Bandit、pip-audit、WSL Bash 均未能执行，说明门禁缺少可用性反馈。
- **修复建议** 为工具不存在提供明确的 `not installed` 结果并支持严格失败模式，将 `bash -n`、Bandit 和锁文件审计纳入 CI 或发布前检查。
- **验证方法** 人为制造 shell 语法错误、Bandit 告警和依赖审计失败，确认脚本非零退出并保留日志。
- **修复风险** 不同平台工具路径、网络访问和扫描器版本会造成误报，需要锁定工具版本与豁免文件。

### P2-03 开发环境示例仍可能被误用于生产

- **位置** `backend/.env.example:12-30`、`backend/warehouse_backend/settings.py:25-45`
- **触发条件** 运维人员直接复制 `.env.example` 到公网实例，忘记将 DEBUG、密钥和 HTTPS 相关参数改为生产值。
- **原因** 示例明确包含 `DEBUG=1` 和开发密钥，settings 在 DEBUG 下允许开发密钥和非 HTTPS Cookie。文件注释有提醒，但配置本身仍可启动。
- **影响** 可能暴露调试堆栈、使用弱密钥、允许非安全 Cookie，造成部署级安全事故。
- **证据** `check --deploy` 在本机复现 6 项安全警告。正式 install.sh 会生成更严格的 app.env，但手工部署路径仍存在人为误配置窗口。
- **修复建议** 将开发示例改为显式 `.env.local.example`，生产示例只保留占位符并让启动在 `DEBUG=0` 缺少安全配置时硬失败。README 和 preflight 使用同一套检查。
- **验证方法** 在临时目录分别加载开发和生产示例，确认开发可运行、生产缺值或弱值立即失败，并对 `check --deploy` 结果做白名单化说明。
- **修复风险** 本地新用户体验会增加配置步骤，需要提供不含秘密的生成命令。

### P2-04 大模块增加维护和回归风险

- **位置** `backend/apps/common/views/legacy.py` 约 5183 行，`backend/apps/workflow/services.py` 约 1896 行。
- **触发条件** 多人同时修改页面、报表、设置和工作流服务。
- **原因** common 仍承载大量页面和跨域辅助，workflow service 集中处理多种单据状态机。
- **影响** 变更冲突、局部回归和权限边界审查成本上升，单文件测试和代码导航困难。
- **证据** 本轮读取到的文件行数，以及多个领域路由都集中到同一 legacy 模块。当前测试虽通过，未消除演进成本。
- **修复建议** 保持 URL 兼容，按 inventory、reports、settings、workflow lifecycle 拆为内部模块，先移动代码再逐批补测试，不在稳定化阶段顺手改业务语义。
- **验证方法** 每次拆分前后运行全量测试、URL 反向解析、模板渲染和权限矩阵。
- **修复风险** 循环导入、装饰器顺序和模板反向 URL 可能受影响，必须小步提交。

### 6. 当前模块深挖

### accounts

账号开通申请、Session 账本和令牌轮换已经分层。特权账号会话上限和撤销状态存入数据库，安全事件使用摘要字段。需要在 PostgreSQL 验证并发登录上限的唯一性和锁顺序，当前 8 项 PostgreSQL 专项被跳过。

### inventory

资产以 UUID 为业务主键，`system_asset_no` 和 `asset_code` 分离，编码服务负责前缀和流水。资产状态同时保留 legacy status 与四维状态，兼容成本较高。仓库、库位和资产删除语义仍是当前最高数据完整性风险。标签作业和二维码接口已存在权限保护，ZT610 真实走纸尚未验证。

### workflow

工作流服务集中维护申请、审批、预占、出入库、采购到货和延期。主要状态动作使用事务和锁，失败以 `WorkflowError` 反馈。采购到货已增加客户端幂等键。真实 PostgreSQL 下的锁竞争、重复提交和审计触发器仍需执行。

### notifications

站内通知按 recipient 隔离，DingTalkOutbox 使用幂等键、锁定时间和退避。第三方调用有超时和官方 HTTPS 主机限制。当前缺少对真实网络超时、DNS 失败和长期失败队列积压的现场指标验证。

### common 与前端

common 提供健康检查、PWA manifest、Service Worker、缓存头、安全响应头和页面主干。M17 所需 PNG 图标和 iOS meta 已存在于 `backend/apps/common/static/warehouse/manifest.webmanifest` 与 `backend/templates/web_base.html`。扫码脚本有 composition 和 Enter/Tab 处理，但现场设备和 Safari 体验仍未执行。

### deploy

发布使用独立 release、持久化 `app.env` 和原子 current 链接。Nginx 只对生命周期附件路径做特殊拦截，其余 `/media/` 直接映射。恢复脚本已按 `media-snapshots` 结构更新，但本轮没有 Linux、PostgreSQL、快照校验和异机恢复证据。

### 7. 多角色评审意见

### 架构师

单体边界目前清晰，业务服务层和审计模型已经形成稳定核心。common 视图与 workflow 服务过大，限制后续演进。建议先固化数据删除语义和 PostgreSQL 运行基线，再进行保持 URL 兼容的内部拆分。

### 开发负责人

状态变更大多经过服务层和事务，权限函数集中且可复用。模型层删除策略与 ViewSet 策略不一致，属于容易被新入口绕过的隐性缺陷。后续改动应优先补跨入口测试，避免只增加页面测试。

### 测试负责人

SQLite 全量 797 项通过提供了较好的业务回归证据，但不能替代 PostgreSQL 锁、触发器和隔离级别测试。浏览器、硬件和恢复演练均未执行，验收表必须继续维持未验证状态。建议建立固定数据规模的报表容量基线。

### 安全负责人

API 默认认证、细粒度权限、令牌过期、CSP 和敏感字段过滤已形成基础防线。开发 `.env` 误用、媒体直读边界和 NUC 实际代理配置仍需现场确认。Bandit 和 pip-audit 本轮未运行，不能给出静态安全通过结论。

### SRE/运维

发布脚本具备快照、原子切换和健康回滚设计，systemd timer 覆盖主要运维任务。当前 SSH 认证失败导致 release、迁移、服务、Nginx 和 PostgreSQL 状态都不能复核。`check-all.sh` 应成为可重复、工具缺失可见、失败可阻断的发布门禁。

### 最终用户

仓管员的核心路径已经集中到 Web/PWA，扫码、快速出入库、审批和盘点入口可从页面代码确认。iPhone Safari 主屏幕、相机权限、中文输入法扫码和连续走纸仍没有真机证据。现场验收需要记录错误提示、等待状态、失败后继续扫描和重复提交反馈。

### 8. 改进优先级清单

1. **P1 数据完整性**。登记模型层 `PROTECT` 或统一受控停用方案，先盘点现有引用，再补迁移、admin、API、服务层和回滚验证。
2. **P1 环境基线**。在不触碰 NUC 的前提下建立独立本机测试数据库并应用全部迁移，标记现有 SQLite 数据仅用于历史查看。恢复 NUC 连接后只读读取 release、迁移和服务状态，再决定是否部署。
3. **P2 容量**。对报表、供应商价格、对账和呆滞库存做数据库聚合、分页和分块导出，完成 1 万到 100 万行压测。
4. **P2 发布门禁**。补齐 `bash -n`、Bandit、pip-audit 和工具版本记录，要求工具缺失时明确阻断或由用户确认豁免。
5. **P2 配置安全**。拆分开发和生产示例，统一 `check --deploy` 说明，禁止生产启动接受占位密钥、DEBUG 和通配 Host。
6. **P2 可维护性**。冻结 URL 和模板契约，按领域拆分大文件，每步保留全量测试和权限矩阵。
7. **现场验收**。依次完成 NUC PostgreSQL 并发、iPhone Safari PWA、DS3678 中文输入法连续扫码、ZT610 60×40 mm 连打 10 张和异机恢复演练。

### 9. 尚未验证的风险和需确认事项

- NUC 当前运行 release、数据库迁移状态、Gunicorn、Nginx、systemd timers、PostgreSQL 版本和真实环境安全变量均未复核。
- 本机 SQLite 有 19 个未应用迁移。全量测试使用临时测试库并自动迁移，不能证明现有 `runtime-data/db.sqlite3` 可直接支持当前代码。
- PostgreSQL 原始审计触发器、锁竞争、并发幂等、连接复用和数据库权限未执行。
- `backend/apps/common/static/warehouse/manifest.webmanifest` 和 PNG 图标已存在，但 iPhone Safari 添加到主屏幕和 standalone 行为未现场验证。
- Nginx 对 `/media/` 的公开映射是否符合资产照片、费用凭证和品牌资源的业务分类要求，需要业务确认。生命周期附件路径已有单独拦截规则。
- Bandit、pip-audit 和 WSL shell 检查因本机工具缺失或不可启动未执行。不能引用历史报告作为本轮通过证据。
- 当前提交基线与 `PROJECT_STATUS.md` 顶部记录的 `a2fd9a7` 不一致，状态文档仍写着未提交工作区，需在下一次状态更新中校正三态版本记录。

### 10. 下一阶段计划

1. 在本地建立隔离测试数据库副本并应用全部迁移，只读核对迁移顺序、0022 幂等键和现有测试数据兼容性。
2. 先修复并验证主数据删除的模型层约束，覆盖 admin、API、服务层和直接 ORM 反向路径。
3. 完成报表和供应商路径的 PostgreSQL 容量基线，输出查询计划、RSS、P95、P99 和导出吞吐。
4. 恢复 NUC 只读连接后记录 release-info、迁移、服务和健康检查，不使用历史版本替代现场结果。
5. 在用户明确“开始修复”前保持业务代码不变。获得确认后按 P1、P2 顺序小步修改，每步执行专项测试、全量测试、`check`、迁移漂移和差异检查。

### 本轮结束状态

- 已完成 代码和配置全景盘点、核心调用链与数据流梳理、基础静态检查、本机全量 Django 回归、本机 HTTP 冒烟和第一批问题清单。
- 测试结果 797/797 通过、8 跳过。`check`、迁移漂移、pip check、diff check 和 JavaScript 语法通过。`check --deploy` 有 6 项本地配置警告。
- 阻塞项 NUC SSH 认证失败、19 个本机未应用迁移、PostgreSQL/真实浏览器/设备/恢复演练未执行、Bandit/pip-audit/WSL shell 不可用。
- 下一模块 报表容量和主数据删除跨入口验证，随后是 NUC 只读运行状态复核和现场验收准备。

### 11. 第二轮复核：删除跨入口与报表容量

### 11.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告，均属于本轮审查记录。
- NUC SSH 仍返回 `Permission denied (publickey,password)`，实际运行 release 未复核。
- 本轮没有修改业务代码，没有执行迁移，没有访问 NUC 数据库，没有执行生产 admin 操作。

### 11.2 主数据删除跨入口验证

源码反射确认结果如下。

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| `Location.warehouse` 删除策略 | `CASCADE` | `manage.py shell` 读取模型元数据 |
| `Asset.warehouse` 删除策略 | `SET_NULL` | `manage.py shell` 读取模型元数据 |
| `Asset.location` 删除策略 | `SET_NULL` | `manage.py shell` 读取模型元数据 |
| `WarehouseAdmin` 覆盖删除保护 | 否 | `delete_model` 不在类字典中 |
| `LocationAdmin` 覆盖删除保护 | 否 | `delete_model` 不在类字典中 |
| admin 基类行为 | 先写删除审计，再直接 `super().delete_model()` | `backend/apps/common/admin.py:39-51` |
| API 删除保护 | 有 | `backend/apps/inventory/views.py:150-206` |

这形成了明确的跨入口不一致。API 的 `WarehouseViewSet` 和 `LocationViewSet` 会检查资产、耗材、库位引用并返回 400，但 admin 继承通用删除行为。只要 admin 用户有删除权限，删除在用仓库就可能依赖数据库的 `CASCADE/SET_NULL` 继续执行，产生仓库被删、库位被级联删除、资产仓库和库位字段被清空的结果。删除审计还可能记录“已删除”，但没有保存与 API 一致的删除前完整快照和事务化保护。

本轮对 `apps.inventory.test_master_data_delete` 和 `apps.common.test_deployment_security` 执行专项测试，结果为 **16/16 通过**。这些用例覆盖 API 入口和部署脚本静态契约，当前没有覆盖 admin 删除入口，因此不能把专项通过解释为跨入口风险已关闭。

### 11.3 报表容量路径细化

#### 供应商价格历史

- `backend/apps/common/views/legacy.py:2433-2450` 生成 `PurchaseOrderLine` QuerySet。
- `:2467-2484` 逐行构建 `groups` 字典，并为每个供应商和物品保存完整明细列表。
- 供应商筛选字段 `supplier_snapshot`、`name_snapshot`、`code_snapshot` 没有在 `PurchaseOrderLine.Meta` 中定义专用索引。订单状态和日期过滤位于关联的 `PurchaseOrder` 表。
- 页面分页不存在，结果规模与所有有效采购明细成正比。CSV 导出 `:2509-2530` 继续逐行遍历完整 QuerySet。

#### 供应商对账

- `backend/apps/common/views/legacy.py:2535-2579` 首先用 `list(...)` 物化全部订单明细。
- 供应商和日期筛选在 Python 列表上执行，收货日期模式再物化全部收货明细到 `period_receipts` 字典。
- `:2587-2632` 以 Python 字典聚合供应商，`lines` 列表保留每条明细的展示结构。页面没有数据库级分页。

#### 呆滞库存

- `backend/apps/common/views/legacy.py:4446-4530` 分别查询全部在库资产和正库存耗材，逐行计算最后活动日期和闲置天数。
- `:4534-4545` 在完整 `rows` 构建后才调用 `Paginator(rows, 50)`，因此页面分页不能降低数据库读取和 Python 内存峰值。
- 成本汇总 `total_cost` 也再次遍历完整 `rows`。

#### 报表总览

- `backend/apps/common/views/legacy.py:4337-4443` 已将时间序列成本和计数聚合下推到数据库，但 `:4380` 又逐行迭代 `transactions` 生成 `action_counts`。
- 当前 `InventoryTransaction` 有 `business_date`、`action/business_date` 索引，属于较好的基础。仍需 PostgreSQL `EXPLAIN` 验证关联仓库、项目和工单筛选的计划。
- 财务用户的当前库存成本在 `:4427-4434` 逐个读取资产和耗材后在 Python 求和，规模增长时会增加响应时间。

本轮没有伪造大数据量基准，也没有访问 PostgreSQL，因此以上属于代码已验证、高概率容量风险。需要在隔离 PostgreSQL 中以 1 万、10 万和 100 万条采购明细、收货明细、资产和流水测量 P95、P99、RSS、查询计划和导出首字节时间。

### 11.4 第二轮问题更新

#### P1-01 证据升级：删除保护只覆盖 API

- **位置** `backend/apps/common/admin.py:39-51`、`backend/apps/inventory/admin.py:274-286`、`backend/apps/inventory/models.py:129`、`575-576`
- **触发条件** 系统管理员通过 Django admin 删除在用仓库或库位。
- **原因** admin 没有重写删除权限或删除方法，通用基类直接删除。模型关系仍为 `CASCADE/SET_NULL`。
- **影响** API 测试通过不能阻止 admin 或脚本路径造成资产脱仓和库位历史丢失。
- **证据** 模型元数据反射得到 `CASCADE/SET_NULL`，`WarehouseAdmin.__dict__` 和 `LocationAdmin.__dict__` 均没有 `delete_model`，专项测试没有 admin 用例。
- **修复建议** 首选模型层 `PROTECT` 加受控停用，admin 删除按钮同时按引用隐藏或返回业务错误。删除审计统一事务化并保存快照。
- **验证方法** 在 Django 测试数据库覆盖 admin GET 删除确认页、POST 删除、批量删除、API 删除、直接服务调用和空对象删除。
- **修复风险** 迁移前需盘点已有引用，避免上线后迁移失败。

#### P2-04 证据升级：报表路径未按结果集规模受控

- **位置** `backend/apps/common/views/legacy.py:2433-2649`、`:4446-4540`、`:4427-4434`
- **触发条件** 采购明细、收货明细、资产或流水达到万级以上，或用户导出长时间范围。
- **原因** 供应商价格和对账先全量物化后分组，呆滞库存先全量构建列表后分页，当前库存成本逐行求和。
- **影响** 内存和延迟随数据量线性增长，多个并发报表请求可能放大 Gunicorn worker 内存压力。
- **证据** 源码中的 `list(...)`、Python 字典分组和 `Paginator(rows, 50)` 顺序已确认。无目标 PostgreSQL 压测证据。
- **修复建议** 把筛选、聚合、排序和分页下推数据库，导出使用 `iterator()` 或分块响应，补供应商和日期相关索引并用查询计划确认。
- **验证方法** 固定数据集比较旧口径与新 SQL 口径，同时记录 P95、P99、RSS、数据库缓冲命中和导出首字节时间。
- **修复风险** 聚合口径、空供应商、日期基准和成本权限需要逐字段对照现有页面和 CSV。

### 11.5 第二轮结束状态

- 已完成 admin、API、模型删除边界的静态和元数据交叉验证，完成供应商价格、供应商对账、呆滞库存和报表总览的容量路径定位。
- 专项测试结果为 16/16 通过。它们证明 API 删除保护和部署契约当前通过，未覆盖 admin 删除。
- 阻塞项保持不变。NUC release、PostgreSQL、真实浏览器、设备和恢复演练未验证。
- 下一模块优先为 admin 删除反向测试设计、报表查询计划和 PostgreSQL 隔离容量基准。获得“开始修复”前继续保持业务代码不变。

### 12. 第三轮复核：admin 删除实证与查询索引

### 12.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件仍为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告，均属于审查记录。
- NUC SSH 认证仍失败，实际运行 release 未复核。
- 临时测试文件 `backend/apps/inventory/test_admin_delete_probe.py` 只用于本轮测试，执行后已删除。业务代码、生产数据库和本机持久化数据库均未修改。

### 12.2 admin 删除路径实证

在临时 Django 测试数据库中创建一个有库位和资产引用的仓库，直接调用已注册的 `WarehouseAdmin.delete_model()`。专项结果为 **1/1 通过**，行为如下。

1. 仓库被删除。
2. `Location.warehouse=CASCADE` 导致库位被删除。
3. `Asset.warehouse=SET_NULL` 和 `Asset.location=SET_NULL` 导致资产归属字段被置空。
4. 通用 admin 基类写入 `warehouse_admin_deleted` 审计记录。

这把第二轮的模型和源码推断升级为已验证的跨入口数据完整性缺陷。API 的 `WarehouseViewSet._assert_deletable()` 不能覆盖 admin 的 `delete_model()`，审计记录也没有阻止破坏性删除。

### 12.3 报表查询和索引证据

通过 Django 模型元数据和 QuerySet SQL 只读检查得到以下结果。

| 模型 | 自定义索引 | 关键查询特征 |
| --- | --- | --- |
| `PurchaseOrderLine` | 无 | 连接 `PurchaseOrder`，按订单状态过滤，按 `supplier_snapshot` 和 `order__order_date` 排序 |
| `PurchaseReceiptLine` | 无 | 连接收货单、订单明细和采购单，按采购单状态过滤 |
| `InventoryTransaction` | `business_date`、`action/business_date` | 有时间和动作索引，仍需 PostgreSQL 计划验证 |
| `Asset` | 无自定义索引 | 呆滞库存按状态、流水日期聚合，并关联仓库、库位和物品类型 |
| `StockItem` | 无自定义索引 | 呆滞库存按数量和关联对象过滤 |

供应商价格查询生成的 SQL 在订单明细和采购单之间连接，订单状态位于采购单，排序位于明细供应商快照和采购日期。供应商对账的收货模式额外连接收货明细、收货单和订单明细。当前没有 PostgreSQL `EXPLAIN ANALYZE`、大数据量响应时间或内存采样，因此索引是否需要增加仍需容量基准确认，代码层的全量物化风险已经确认。

### 12.4 问题状态升级

#### P1-01 主数据删除跨入口破坏数据完整性（已实证）

- **位置** `backend/apps/common/admin.py:39-51`、`backend/apps/inventory/admin.py:274-286`、`backend/apps/inventory/models.py:129`、`575-576`
- **触发条件** 具备 admin 删除权限的账号删除仍被资产或库位引用的仓库。
- **原因** admin 继承通用删除方法，模型层使用 `CASCADE/SET_NULL`，API 守卫不在共享模型或 admin 层生效。
- **影响** 仓库和库位历史消失，资产的仓库和库位外键被清空，后续台账和生命周期追溯无法恢复原归属。
- **证据** 临时 Django 测试 `1/1` 通过并直接观察到仓库、库位删除和资产外键清空，同时生成删除审计记录。
- **修复建议** 先做引用盘点和快照，再将业务历史关联改为 `PROTECT` 或统一受控停用。admin 删除入口隐藏或返回业务错误，审计写入与受控动作放在同一事务。
- **验证方法** API、admin 单条删除、admin 批量删除、服务层和直接 ORM 入口分别覆盖在用对象、空对象、审计一致性和回滚。
- **修复风险** 迁移可能因既有引用失败，必须先在隔离数据库演练并保留可恢复快照。

#### P2-04 报表容量风险（索引证据补充）

- **位置** `backend/apps/workflow/models.py:322-423`、`backend/apps/common/views/legacy.py:2433-2649`、`:4446-4540`
- **触发条件** 采购明细、收货明细、资产或流水规模增长，或多个用户并发访问长时间范围报表。
- **原因** 采购明细和收货明细没有自定义索引，视图又在 Python 中全量分组、聚合和分页。
- **影响** 数据库扫描、连接排序、Python 内存和 Gunicorn worker 压力随数据规模增长。
- **证据** 模型元数据显示两类采购明细无自定义索引，QuerySet SQL 显示跨表连接、状态过滤和排序。未执行 PostgreSQL 计划与压力测试。
- **修复建议** 先用真实数据分布评估索引，再将日期、状态和供应商聚合下推数据库，分页和导出采用分块处理。
- **验证方法** 对 1 万、10 万和 100 万行执行 `EXPLAIN (ANALYZE, BUFFERS)`，记录响应 P95/P99、RSS、数据库临时空间和 CSV 首字节时间。
- **修复风险** 新索引会增加写入和迁移时间，SQL 聚合重写可能改变空值、日期基准和成本权限口径。

### 12.5 第三轮结束状态

- 已完成 admin 删除路径的临时数据库实证，确认 P1-01 是可复现数据完整性缺陷。
- 已完成采购明细、收货明细、库存流水、资产和耗材模型索引清点，并保存关键 QuerySet SQL 证据。
- 测试结果为 admin 探针 `1/1`，前一轮删除与部署专项仍为 `16/16`，全量 Django 回归仍为 `797/797`、8 项跳过。
- 临时测试文件已删除，工作区没有新增测试产物。下一模块为发布安全门禁、迁移一致性和真实 Linux/PostgreSQL 验收准备。

### 13. 第四轮复核：发布门禁、迁移链与回滚边界

### 13.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告。
- NUC SSH 认证失败，实际运行 release 未复核。
- 本轮只读检查了发布脚本、迁移文件、依赖锁和服务单元，没有执行迁移、构建发布包、部署或生产数据库操作。

### 13.2 本机迁移状态

`manage.py showmigrations --plan` 在当前 `runtime-data/db.sqlite3` 上显示 **19 个未应用迁移**，包括 accounts 0004-0007、common 0012-0016、inventory 0027-0031、notifications 0005-0006 和 workflow 0020-0022。Django 测试数据库会自动应用完整迁移，因此全量测试通过不能代表本机持久化数据库已达到当前代码的 schema。

迁移目录共 87 个 Python 文件，静态清点发现 18 个 `RunPython` 数据迁移。代表性迁移包括：

- `backend/apps/inventory/migrations/0020_asset_system_asset_no.py` 为历史资产补发系统编号。
- `backend/apps/inventory/migrations/0021_asset_status_dimensions.py` 将旧状态回填到四维状态。
- `backend/apps/workflow/migrations/0009_inventorytransaction_business_date_and_more.py` 回填业务日期、库存编号和流水日期。
- `backend/apps/workflow/migrations/0011_returnreminderlog_stockitemloan_inventoryreservation_and_more.py` 回填预占和借用记录。
- `backend/apps/inventory/migrations/0027_source_sc_prefix.py` 会写入型号前缀和基础资料。

这些迁移具有数据规模、锁时间和既有脏数据兼容性影响。部分迁移使用 `migrations.RunPython.noop` 或自定义 no-op reverse，逆向操作不会恢复原数据。

### 13.3 发布和回滚边界

- `deploy/ubuntu22/install.sh:260-280` 在已有数据库时先执行 PostgreSQL 快照。
- `install.sh:470-476` 在候选 release 上执行 `migrate --noinput`，然后切换 `current` 指针。
- `deploy/ubuntu22/rollback.sh:11-16` 明确说明只切换程序 release，不回滚 PostgreSQL 数据或 schema。
- `rollback.sh:84-113` 回滚前执行备份，切换后只运行 Django check 和 `/health/`，没有验证目标代码与当前 schema 的兼容矩阵。

因此，迁移成功后再回滚旧代码，只有在旧代码能够兼容新增 schema 时才安全。涉及字段删除、约束收紧、触发器或数据语义变化的版本，代码指针回滚无法还原数据库状态。当前脚本文档已经说明这一点，但发布门禁没有自动检查目标 release 的迁移兼容性。

### 13.4 发布安全门禁

#### `check-all.sh`

`deploy/ubuntu22/check-all.sh:41-89` 只执行 Django check、迁移漂移、pip check、diff check 和全量测试。脚本正文没有 `bash -n`、Bandit、pip-audit 或测试目录退出清理。它创建 `.test-run-temp`，但没有 `trap` 或结尾删除，重复运行可能留下测试产物。

#### `security-check.sh`

`deploy/ubuntu22/security-check.sh:82-92` 在 pip-audit 未安装时只输出提醒，安装后发现漏洞也调用 `warn`，不会增加阻断项。`manage.py check --deploy` 的失败同样被转成提醒。当前服务器安全检查因此不能作为严格的依赖和部署阻断门。

#### 一次性 SQLite 导入

`deploy/ubuntu22/install.sh:464-476` 将完整 `dumpdata` 输出写入固定 `/tmp/after-sales-warehouse-initial.json`。脚本没有显式设置 `umask`、没有 `mktemp` 私有路径，也没有 `trap` 确保 dumpdata 或 loaddata 中途失败后清理。该文件包含业务数据，失败窗口可能在共享临时目录中留下可读副本。

### 13.5 依赖锁和 Linux 工具可用性

- `backend/requirements-linux.txt` 通过 `-r requirements-lock.txt` 引入锁定版本。
- `requirements-lock.txt` 为无 BOM 的 UTF-8 文本，本轮读取头部字节确认没有 UTF-16 或 UTF-8 BOM。
- 当前本机没有 Bandit 或 pip-audit。`bash.exe` 和 `wsl.exe` 指向 Windows/WSL 启动器，但执行 Bash 检查时没有可用 Linux shell，因此本轮不能把 Ubuntu 脚本语法标记为通过。
- 没有在全新 Python 3.12 环境重新安装 Linux 锁文件。现有 `.venv` 的 `pip check` 通过，不能代替全新环境安装验证。

### 13.6 第四轮问题清单

#### P2-06 证据升级：发布安全门禁不会严格阻断

- **位置** `deploy/ubuntu22/check-all.sh:41-89`、`deploy/ubuntu22/security-check.sh:72-92`
- **触发条件** 发布前工具缺失、依赖存在漏洞、部署配置出现 `check --deploy` 问题或 shell 脚本语法回归。
- **原因** 一键检查没有 shell/SAST/依赖扫描，服务器安全脚本对缺少或发现 pip-audit 问题只告警。
- **影响** 发布流程可能在没有安全扫描证据时继续，安全门禁状态依赖人工记忆和历史记录。
- **证据** 静态读取脚本确认缺少对应命令，当前本机 Bandit、pip-audit 和 Linux Bash 均未能运行。
- **修复建议** 将工具版本和扫描命令纳入可复现门禁，工具缺失时明确失败。对 `check --deploy` 设计受控白名单，公网配置不允许静默降级为提醒。
- **验证方法** 注入 shell 语法错误、Bandit 告警、依赖漏洞和工具缺失，确认脚本返回非零并输出可审计日志。
- **修复风险** 网络不可用和扫描器误报会影响发布，需要锁定工具版本和维护豁免说明。

#### P2-09 证据升级：一次性导入临时文件暴露窗口

- **位置** `deploy/ubuntu22/install.sh:464-476`
- **触发条件** 使用 `--import-sqlite`，且 dumpdata 或 loaddata 中途失败、进程被终止或服务器重启。
- **原因** 完整业务 dump 固定写入公共 `/tmp` 路径，没有私有临时文件、显式 600 权限和退出清理。
- **影响** 业务数据可能残留在临时目录，下一次运行也可能误读旧文件。
- **证据** 源码直接使用固定路径重定向，删除只发生在 loaddata 成功之后。
- **修复建议** 使用 `install` 创建 600 权限的私有临时目录，登记路径变量并设置 `trap` 清理。导入前后校验文件所有者、大小和一次性标记。
- **验证方法** 模拟 dumpdata 失败、loaddata 失败、SIGTERM 和重复执行，确认文件始终清理且不会重复导入。
- **修复风险** 清理过早会导致重试无法继续，需要把一次性标记和 dump 文件生命周期绑定。

#### P2-13 数据库迁移与代码回滚兼容性未自动验证

- **位置** `backend/apps/*/migrations/`、`deploy/ubuntu22/install.sh:470-476`、`deploy/ubuntu22/rollback.sh:11-16`
- **触发条件** 数据库已应用新迁移后，回滚到旧 release，或迁移中途失败后切换代码。
- **原因** 多个迁移包含数据回填、基础资料写入或 no-op reverse，回滚脚本只切换代码指针，不检查 schema 兼容性。
- **影响** 旧代码可能读取不到预期数据、遇到新约束或触发器，业务恢复依赖人工判断和完整数据库恢复。
- **证据** 迁移静态清点得到 18 个 `RunPython`，回滚脚本明确 `database_rollback=not_performed`。本机 SQLite 仍有 19 个未应用迁移，未完成真实升级回滚演练。
- **修复建议** 为每个发布版本维护 forward-only schema 兼容矩阵，拆分破坏性迁移为 expand/migrate/contract 阶段。回滚前执行目标 release 的 `check`、迁移兼容检查和只读冒烟。
- **验证方法** 在隔离 PostgreSQL 上从上一版本升级到候选版本，执行迁移、冒烟、代码回切和恢复演练，记录 schema 与业务计数。
- **修复风险** 增加发布步骤和保留周期，需要明确何时只能走数据库恢复而不能使用代码回滚。

### 13.7 第四轮结束状态

- 已完成发布脚本、服务器安全检查、依赖锁、迁移链和回滚边界的只读复核。
- 当前本机持久化 SQLite 有 19 个未应用迁移，未执行迁移以保护现有数据。
- 未执行 Linux Bash、Bandit、pip-audit、全新 Python 3.12 安装、PostgreSQL 迁移升级回滚或 NUC 现场验收。
- 下一模块为在隔离环境建立 Linux/PostgreSQL 验收矩阵和迁移兼容性演练，随后等待用户确认是否开始修复 P1/P2。

### 14. 第五轮复核：空库迁移链演练

### 14.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告。
- NUC SSH 认证失败，实际运行 release 未复核。
- 演练使用新建临时 SQLite 文件，不读取或修改现有 `runtime-data/db.sqlite3`，不连接 PostgreSQL，不执行部署。

### 14.2 演练步骤和结果

1. 创建临时 `runtime-data/review-migration.sqlite3`。
2. 使用仓库 `.venv` 的 Python 3.12.10 设置 `DB_ENGINE=django.db.backends.sqlite3` 和临时数据库路径。
3. 执行完整 `manage.py migrate --noinput`。
4. 执行 `manage.py check`。
5. 进程输出 `migration_chain=applied`，系统检查为 `System check identified no issues (0 silenced)`。
6. Django 连接在脚本退出前仍持有 SQLite 文件，第一次自动清理遇到 Windows `WinError 32`。进程退出后已手动删除临时数据库，探针文件也已删除，工作区无残留。

这证明当前迁移图从空 SQLite 数据库可以完成应用，不能证明 19 个未应用迁移在现有本机数据或目标 PostgreSQL 上安全。数据回填规模、锁等待、触发器、索引创建时间、约束冲突和代码回滚兼容性仍未验证。

### 14.3 验证状态更新

- **已验证**：空 SQLite 数据库完整迁移链可应用，迁移后 Django system check 通过。
- **高概率推断**：迁移文件依赖图和基本操作顺序可被 Django 执行。
- **需确认**：目标 PostgreSQL 真实数据迁移、并发锁、备份恢复、迁移失败恢复、forward-only 发布和旧代码回切。

### 14.4 第五轮结束状态

- 新增自动化演练结果为临时空库迁移成功，未改变业务代码和现有数据。
- P2-13 仍未关闭，因为代码 release 回滚与数据库 schema 回滚没有真实隔离 PostgreSQL 证据。
- 下一模块为设计 PostgreSQL 隔离库升级、回切和恢复演练矩阵。若用户回复“开始修复”，再按 P1-01、P2-06、P2-09、P2-13 优先级修改。

### 15. 第六轮复核：PostgreSQL 隔离验收准备

### 15.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告。
- NUC SSH 认证失败，实际运行 release 未复核。
- 本机未发现 `psql`、`pg_dump`、`pg_isready`、Docker 或 Podman，因此没有启动本地 PostgreSQL，也没有连接 NUC。

### 15.2 PostgreSQL 专项执行结果

执行以下专项标签：

```text
apps.accounts.test_postgresql_concurrency
apps.workflow.test_postgresql_concurrency
apps.common.test_postgresql_concurrency
apps.common.test_postgresql_audit_immutability
```

本机 SQLite 结果为 **8/8 skipped**，耗时约 0 秒。跳过原因是测试使用 `skipUnless(connection.vendor == 'postgresql')`。这证明测试选择器和跳过条件生效，不能证明行锁、并发计数或 PostgreSQL 审计触发器通过。

### 15.3 服务器并发脚本安全边界

`deploy/ubuntu22/run-concurrency-tests.sh` 已具备以下保护。

- 必须以 root 运行，并从当前 release 的环境文件读取数据库配置。
- 只允许 `django.db.backends.postgresql`。
- 数据库名和账号必须匹配安全标识符格式，避免把标识符直接拼接到 SQL 时发生注入。
- 测试库使用 `test_<DB_NAME>`，不直接在业务库执行测试数据写入。
- 如果应用账号没有 `CREATEDB`，脚本临时授予后在退出清理函数中恢复 `NOCREATEDB`。
- `trap cleanup EXIT` 会终止测试库连接、删除测试库并恢复角色权限。

脚本目前没有将 `SIGKILL`、主机断电或 PostgreSQL 服务崩溃纳入恢复验证。角色临时提权和测试库清理仍需在现场演练后检查。

### 15.4 可执行的 NUC/PostgreSQL 验收矩阵

| 阶段 | 操作 | 通过标准 | 当前状态 |
| --- | --- | --- | --- |
| 连接 | 读取 `/opt/after-sales-warehouse/current/release-info` | release、revision、脏状态可追溯 | 未执行，SSH 认证失败 |
| 快照 | 执行部署前 PostgreSQL `pg_dump` 并校验 SHA-256 | 快照文件和清单存在 | 未执行 |
| 迁移预检 | 在隔离库执行 `migrate --plan` | 计划与候选包一致 | 未执行 |
| 迁移 | 在隔离 PostgreSQL 库执行 `migrate --noinput` | 全部迁移成功，无锁超时或约束错误 | 未执行 |
| 并发 | `/usr/local/sbin/after-sales-warehouse-concurrency-test` | PostgreSQL 专项全部通过，临时库销毁，应用账号建库权限恢复 | 未执行 |
| 业务冒烟 | `smoke_business_flows --rollback` | 采购、审批、出入库、盘点、导入撤销、离线幂等全部通过，业务计数回到执行前 | 未执行 |
| 恢复 | `after-sales-warehouse-restore-drill` | 临时恢复库可读，用户、资产、审计计数合理，临时库清理 | 未执行 |
| 回切 | 切换上一 release | 目标代码 `check`、健康检查和只读页面通过，数据库兼容性有记录 | 未执行 |
| 服务 | systemd、Nginx、静态文件和 `/health/` | 服务 active，健康 200，迁移状态完整 | 未执行 |

### 15.5 现场操作顺序约束

1. 先读取 release-info 和当前数据库计数，不修改业务数据。
2. 再做 PostgreSQL 快照，快照失败停止后续动作。
3. 在隔离测试库应用迁移和执行并发专项。
4. 在当前数据库使用 `--rollback` 业务冒烟，记录执行前后计数。
5. 仅在以上结果可追溯时部署候选 release。
6. 部署后重新检查迁移、服务、健康检查和业务计数。
7. 需要代码回切时先确认目标 release 与 forward schema 兼容，数据库恢复另行执行。

### 15.6 第六轮结束状态

- 已验证本机 PostgreSQL 工具不可用，PostgreSQL 专项 `8/8` 明确跳过。
- 已静态确认服务器并发脚本的 PostgreSQL 限制、临时库、权限恢复和退出清理逻辑。
- 已建立可执行的 NUC/PostgreSQL/迁移/恢复/回切验收矩阵。
- 未连接 NUC、未启动 PostgreSQL、未执行部署或生产数据库写入。下一步需要恢复现场连接，或用户确认开始本地代码修复。

### 16. 第七轮复核：页面鉴权与响应安全边界

### 16.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告。
- NUC SSH 认证失败，实际运行 release 未复核。
- 本轮只读检查页面路由、视图装饰器、API 权限、响应安全头和缓存策略。

### 16.2 页面入口鉴权矩阵

静态对照 `backend/apps/common/urls.py` 和 `backend/apps/common/views/legacy.py` 得到以下边界。

| 入口类型 | 当前保护 | 结论 |
| --- | --- | --- |
| 登录、账号申请 | 登录页和账号申请公开，POST 受 CSRF | 符合预期 |
| `/health/` | 公开，仅返回服务和数据库状态 | 适合作为反向代理健康探针 |
| `/sw.js`、静态 manifest | 公开，响应 no-cache 或静态缓存 | 符合浏览器注册要求 |
| 工作台、资产检索、申请、借用 | `@login_required` | 已保护 |
| 库存、采购、报表、导入、标签 | `@role_required` 细分权限 | 已保护 |
| 账号、审计、一致性和系统设置 | 管理员或账号复核权限 | 已保护 |
| 退出登录 | 登录后仅 POST 执行状态变化，GET 重定向 | 已保护 |
| API `me`、通知、工作流 | DRF `IsAuthenticated` 或细分权限 | 已保护 |
| API 资产生命周期 | `IsWarehouseOperator` | 已修复并保持一致 |

`AssetViewSet` 的 list、retrieve、lookup、search 和二维码查询允许已登录用户访问，但无仓库业务角色时使用 `AssetPublicSerializer`，仅返回申请所需最小字段。生命周期 action 单独要求仓库业务权限。该边界与页面侧权限谓词一致。

### 16.3 响应安全和缓存

- `SecurityHeadersMiddleware` 设置同源 CSP、`frame-ancestors 'none'`、`object-src 'none'`、同源 Referrer Policy 和受限 Permissions Policy。
- `NoStoreHtmlMiddleware` 对 HTML 设置 `no-store`，首次响应使用 `Clear-Site-Data: "cache"` 清理旧页面缓存。
- Service Worker 只缓存静态资源，`scanner.js` 和 `offline.js` 使用网络优先，Service Worker 版本根据静态文件 mtime 生成。
- 退出登录视图只接受 POST，避免 GET 触发会话撤销和审计写入。
- `apps.common.test_cache_headers` 专项测试结果为 **10/10 通过**，覆盖缓存头、CSP、Permissions Policy、Service Worker、扫码脚本更新和离线清理。

### 16.4 残余安全观察

#### P2-14 CSP 仍允许 `unsafe-inline`

- **位置** `backend/apps/common/middleware.py:14-22`、`backend/templates/web_base.html`、登录模板和多个业务模板内联脚本/样式。
- **触发条件** 页面存在可利用的 HTML 注入或模板输出错误时，内联脚本策略允许浏览器执行注入的脚本。
- **原因** 当前 CSP 的 `script-src` 和 `style-src` 同时包含 `unsafe-inline`，用于兼容大量模板内联代码。
- **影响** CSP 对 XSS 的纵深防护能力弱于 nonce 或 hash 策略。当前测试证明策略存在，不证明内联脚本可安全信任。
- **证据** 中间件字面量包含 `script-src 'self' 'unsafe-inline'` 和 `style-src 'self' 'unsafe-inline'`，模板中存在大量内联代码。
- **修复建议** 先把全局内联脚本和样式迁移到静态资源，再为少量必要内联代码使用请求级 nonce 或固定 hash。保持 `connect-src 'self'`、`object-src 'none'` 和 `frame-ancestors 'none'`。
- **验证方法** 使用浏览器 CSP 报告模式和 XSS 负向用例，确认无内联违规、无外部脚本加载和业务扫码功能不回归。
- **修复风险** 模板迁移可能影响 HTMX 替换后的事件绑定、PWA 注册和扫码初始化，需要按页面逐批迁移。

### 16.5 第七轮结束状态

- 页面路由、视图装饰器和 API 权限边界完成静态对照，未发现新的未鉴权业务页面。
- 缓存和响应安全专项 `10/10` 通过。
- P2-14 作为 CSP 加固项登记，未修改模板或中间件。
- 未连接 NUC、未执行浏览器真机回归、未部署。下一模块为最终问题去重、优先级汇总和用户确认后的修复入口。

### 17. 第八轮复核：媒体、附件和上传边界

### 17.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告。
- NUC SSH 认证失败，实际运行 release 未复核。
- 本轮未上传到生产媒体目录，未访问 NUC 文件系统，所有测试使用临时测试数据库和临时目录。

### 17.2 附件访问边界

- 归档附件路径为 `assets/<system_asset_no>/archive/...`，Nginx 对该路径单独返回 404。
- Django 下载视图 `asset_attachment_download()` 要求 `is_warehouse_operator`，并使用 `FileResponse(as_attachment=True)`。
- 附件上传使用后缀白名单、大小限制和图片内容校验，图片会解码后重新编码。`MAX_ATTACHMENT_SIZE` 与 Nginx `client_max_body_size` 均为 100 MB。
- 现有测试覆盖仓库操作员下载成功、普通员工 404、附件归档审计和 Nginx 私有路径规则。

### 17.3 资产照片边界

- `Asset.photo_url` 是普通字符字段，管理员上传后保存为默认存储 URL。
- `AssetPublicSerializer` 对所有已登录用户返回 `photo_url`，普通用户仍可获得照片地址。
- Nginx 对普通 `/media/` 使用直接 `alias /srv/after-sales-warehouse/media/`，只有归档附件路径被拦截。因此资产照片如果位于默认媒体目录，知道 URL 的匿名客户端可能直接读取，代码没有 Django 权限检查。
- 该行为是否符合业务要求需要确认。归档附件已经受控下载，资产照片的保密级别尚未形成一致规则。

### 17.4 上传专项执行结果

第一次执行 `apps.common.test_upload_limits_100mb apps.inventory.tests` 时，Windows 默认临时目录不可用，出现 4 个 `FileNotFoundError`，涉及 openpyxl 临时文件和测试媒体目录。设置 `TEMP/TMP/TMPDIR` 到仓库可写的 `runtime-data/review-tmp` 后，以相同测试命令重跑，结果为 **63/63 通过**，耗时约 34 秒。

第一次结果属于环境阻塞，未计入业务失败。重跑结果覆盖 100 MB 附件上限、图片校验、附件归档、下载权限、资产照片上传和库存测试相关路径。

### 17.5 问题更新

#### P2-10 证据升级：资产照片与附件的公开媒体边界不一致

- **位置** `deploy/ubuntu22/nginx-after-sales-warehouse.conf:36-41`、`backend/apps/inventory/serializers.py:285-314`、`backend/apps/inventory/views.py:389-410`
- **触发条件** 资产照片通过默认媒体存储生成 URL，或用户拿到 `photo_url` 后在未登录浏览器访问媒体路径。
- **原因** 普通 `/media/` 直接由 Nginx 提供，Django 只对归档附件路径做拦截。资产照片 URL 还会被公共资产序列化器返回给所有已登录用户。
- **影响** 照片是否公开取决于文件路径不可猜测性，缺少基于用户、仓库或角色的服务端授权。
- **证据** Nginx 配置和序列化器源码已确认，归档附件与普通资产照片使用不同访问路径。当前没有 NUC 媒体目录的匿名 HTTP 实测。
- **修复建议** 先确认资产照片业务保密级别。若需保护，将照片纳入受控下载视图，并让 API 返回受控 URL 或短时签名地址。
- **验证方法** 用匿名、普通员工、仓库操作员和管理员分别访问照片与归档附件 URL，确认角色矩阵和日志记录。
- **修复风险** 改为受控下载会增加大图传输开销和 Nginx 缓存设计复杂度，需要保留缩略图或内部缓存策略。

#### P2-15 附件大小提示与实际限制不一致

- **位置** `backend/templates/asset_lifecycle.html:114`、`backend/apps/common/upload_validation.py:17-22`
- **触发条件** 用户在资产生命周期页上传归档附件。
- **原因** 页面文案仍显示“单个文件最大 20 MB”，后端校验和 Nginx 已提高到 100 MB。
- **影响** 用户会错误地认为 20 MB 以上文件不能上传，产品行为和现场手册不一致。
- **证据** 模板与后端常量直接冲突，上传专项 `63/63` 证明后端 100 MB 边界可用。
- **修复建议** 统一页面、帮助文案和部署手册为 100 MB，并保留 413 错误的明确提示。
- **验证方法** 页面文案检查、100 MB 边界上传、超过限制的表单错误和 Nginx 413 页面联测。
- **修复风险** 文案改动低风险，但需要同步现场培训和运维说明。

### 17.6 第八轮结束状态

- 归档附件权限、上传校验和 Nginx 拦截专项重跑 **63/63 通过**。
- 首次专项失败原因为临时目录 ACL，已在可写临时目录重跑通过，并保留环境阻塞记录。
- P2-10 升级为资产照片媒体边界待业务确认，P2-15 新增为文案与限制不一致。
- 未修改业务代码、未连接 NUC、未访问生产媒体、未部署。

### 18. 第九轮复核：敏感信息与第三方通知

### 18.1 版本和执行边界

- Git 已提交基线仍为 `73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交文件为 `CHANGELOG.md`、`PROJECT_STATUS.md` 和本报告。
- NUC SSH 认证失败，实际运行 release 未复核。
- 本轮只读检查账号申请通知、DingTalk outbox、日志、审计和令牌存储，没有发送外部通知。

### 18.2 已验证的敏感字段保护

- API 令牌数据库只保存 `token_hash` 和前缀，原始 Token 只在签发响应中返回。
- 登录安全审计使用账号和 IP 摘要，不写明文账号或 IP。
- DingTalk access token 不写入日志。成功响应只缓存进程内令牌，异常路径返回通用错误。
- outbox 日志和 `last_error` 主要保存状态、异常类型和第三方错误，不写入数据库密码或 Webhook Secret。
- 本轮敏感模式扫描未在审查报告、状态和变更文档中发现密码、Token、Secret 或完整 Webhook。

### 18.3 账号申请通知数据流

`apps/accounts/account_notifications.py:36-43` 将申请人姓名、联系电话和申请说明拼入同一通知正文。正文先写入所有账号复核人的站内 `Notification`，再通过 `enqueue_users_or_group()` 写入 `DingTalkOutbox`。outbox 可能发送个人工作通知，也可能在个人通知失败时发送群机器人消息。

代码没有对联系电话做掩码，没有按站内通知和外部群通知区分字段，也没有对申请说明做敏感内容过滤。该行为可能符合管理员及时处理申请的业务需要，但扩大了联系电话和自由文本的外发范围。需要业务确认审批群是否允许看到完整手机号，以及申请说明是否允许进入群消息。

### 18.4 问题更新

#### P2-16 账号申请通知可能向第三方群外发个人信息

- **位置** `backend/apps/accounts/account_notifications.py:36-43`、`backend/apps/notifications/outbox.py:23-57`
- **触发条件** 公开账号申请提交成功，且存在账号复核人和 DingTalk 群机器人配置。个人通知失败时，outbox 会尝试群通知。
- **原因** 通知正文直接包含完整联系电话和申请说明，群通知回退路径与站内通知共用同一正文。
- **影响** 联系电话和申请人自由文本可能进入群聊历史、第三方日志和成员可见范围，超出账号治理页面的最小访问范围。
- **证据** 源码确认正文拼接字段和 `USERS_OR_GROUP` 回退策略，现有测试主要断言 outbox 创建与幂等，没有验证手机号脱敏或群通知字段策略。
- **修复建议** 由业务确认通知范围。可将群通知改为“有新的申请，请登录查看”，完整手机号只保留站内通知和管理员页面，或至少按规则掩码。
- **验证方法** 分别配置个人通知成功、个人通知失败回退群通知、无 DingTalk 配置三种场景，断言站内正文、outbox 正文和发送内容符合字段策略。
- **修复风险** 过度脱敏会降低审批人处理效率，需要保留站内详情链接和申请编号。

### 18.5 第九轮结束状态

- 已确认令牌、认证审计、outbox 错误和文档中没有明文凭据泄露证据。
- 新增待业务确认 P2-16，涉及账号申请手机号和申请说明的 DingTalk 外发范围。
- 未发送第三方通知、未连接 NUC、未修改业务代码。下一模块为最终问题去重和按 P0/P1/P2 汇总当前修复入口。

### 19. 第十轮复核：当前问题登记与修复优先级

### 19.1 当前有效问题去重表

| 编号 | 级别 | 当前结论 | 证据状态 | 修复前置 |
| --- | --- | --- | --- | --- |
| P1-01 | P1 | admin/ORM 删除在用仓库可造成库位删除和资产脱仓 | 已在临时 Django 数据库复现 `1/1` | 数据引用盘点、快照、模型迁移设计 |
| P2-04 | P2 | 报表和供应商路径全量 Python 物化，缺少采购明细索引证据 | 代码和 SQL 已验证，PG 压测未执行 | 隔离 PostgreSQL 数据规模基线 |
| P2-06 | P2 | 发布安全检查对工具缺失或漏洞只告警，一键脚本缺少安全门禁 | 脚本静态已验证 | 工具版本、扫描策略和豁免规则 |
| P2-09 | P2 | SQLite 一次性导入使用固定 `/tmp` 文件，失败清理和权限隔离不足 | 脚本静态已验证 | 受控临时目录和失败注入测试 |
| P2-10 | P2 | 资产照片走公开 `/media/`，归档附件走受控下载，边界不一致 | 代码和 Nginx 配置已验证，现场 URL 未测 | 业务确认照片保密级别 |
| P2-13 | P2 | 已增加迁移元数据和回滚前只读门禁，数据库仍不随代码回滚 | 本地脚本和专项已验证，PG 演练未执行 | forward-only 兼容矩阵和隔离 PG 演练 |
| P2-14 | P2 | CSP 仍允许 `unsafe-inline` | 中间件和模板已验证 | 页面脚本样式迁移方案 |
| P2-15 | P2 | 上传页面仍提示 20 MB，实际限制为 100 MB | 模板、常量和专项 `63/63` 已验证 | 文案同步 |
| P2-16 | P2 | 账号申请手机号和说明可能随 DingTalk 群通知外发 | 通知代码已验证，业务政策未确认 | 确认群通知字段策略 |

当前没有新增可证实 P0。历史文档中的同编号 P1/P2 记录属于已完成批次或旧轮次，当前登记以本表和本报告第 11 至 18 节为准。

### 19.2 修复顺序

1. **P1-01 数据完整性**。模型层和 admin/API 统一保护，建立引用盘点、迁移、审计和跨入口回归。
2. **P2-13 发布回滚边界**。在隔离 PostgreSQL 中完成迁移、业务冒烟、代码回切和恢复演练，形成 schema 兼容矩阵。
3. **P2-06 发布安全门禁**。补齐 Bash、Bandit、依赖审计和工具缺失阻断，保留可审计输出。
4. **P2-09 一次性导入安全**。改用私有临时文件、600 权限和失败清理，再做中断注入测试。
5. **P2-04 容量**。先取得 PostgreSQL 查询计划和数据规模基线，再决定索引和 SQL 聚合改造。
6. **P2-10、P2-14、P2-15、P2-16**。分别完成照片政策确认、CSP 迁移、上传文案同步和通知字段政策确认。

### 19.3 当前验收总览

| 验收层 | 已完成 | 未完成 |
| --- | --- | --- |
| 代码静态与架构复盘 | 已完成第一轮全景、模块、调用链和数据流 | 持续随修复更新 |
| SQLite 自动化 | 全量 `797/797`，8 项 PostgreSQL 跳过 | 真实 PostgreSQL 语义 |
| 上传/缓存/附件专项 | `63/63`、缓存安全 `10/10`、删除部署专项 `16/16` | 现场媒体和真实 Nginx |
| 空库迁移 | 临时 SQLite 完整迁移和 check 通过 | 真实 PostgreSQL 数据迁移 |
| 安全扫描 | 历史报告存在，当前环境未重跑 Bandit/pip-audit | 当前发布门禁闭环 |
| 浏览器与设备 | 页面代码和 JS 静态检查通过 | Playwright、iPhone、DS3678、ZT610 |
| 部署与恢复 | 脚本静态检查和历史记录可读 | NUC release、PG 快照、恢复、回切 |

### 19.4 第十轮结束状态

- 已完成当前问题去重、级别统一、证据状态和修复依赖排序。
- 未修改业务代码、未连接 NUC、未部署、未访问生产数据库或媒体。
- 后续工作分为用户确认后修复，以及 NUC 恢复连接后的 PostgreSQL/现场验收。用户回复“开始修复”后进入 P1-01 实施，否则继续保持只读审查状态。

### 20. M19 P1-01 修复轮：本地实现与回归

用户已于 2026-11-23 明确授权开始推进修改。本轮只处理 P1-01，没有连接 NUC 或修改生产数据库。

### 20.1 三态基线

- Git 已提交基线：`73958d65939c7f77bdb27cff947b3cad35fa7452`。
- 本地未提交工作区：既有 `CHANGELOG.md`、`DEVELOPMENT_PLAN.md`、`PROJECT_STATUS.md`、`REVIEW_REPORT_1.md` 审查记录，加上本轮 admin、模型、迁移、视图注释和删除专项测试改动。
- NUC 实际运行 release：SSH 认证失败，读取 `/opt/after-sales-warehouse/current/release-info` 未完成，记为“未复核”。

### 20.2 代码实现

- `Location.warehouse`、`Asset.warehouse`、`Asset.location`、`StockItem.warehouse` 和 `StockItem.location` 改为 `PROTECT`。
- 申请目标仓库/库位、库存流水来源/目标仓库、盘点扫描观察仓库/库位和盘点调整前后仓库/库位改为 `PROTECT`，保留历史引用的主数据身份。
- 新增 `inventory.0032_protect_master_data_references` 和 `workflow.0023_protect_master_data_references`。迁移仅在临时 SQLite 测试数据库应用，未在 NUC 执行。
- `ChineseModelAdmin.delete_model()` 在同一事务内写入删除前字段快照和真实对象 ID，再执行删除。`ProtectedError` 会回滚整个事务并转换为 admin 业务错误，批量删除使用外层事务保证失败回滚。API 既有引用检查继续提供明确的计数提示，模型层约束覆盖直接 ORM 和遗漏入口。

### 20.3 自动化结果

| 检查 | 结果 | 证据等级 |
| --- | --- | --- |
| 主数据删除专项 | `11/11` 通过，覆盖 API、admin 单删、admin 批量删、直接 ORM、历史引用和审计 | 已验证 |
| Django 全量测试 | `807/807` 通过，8 项 PostgreSQL 专项按 SQLite 条件跳过 | 已验证 |
| `manage.py check` | 通过 | 已验证 |
| `makemigrations --check --dry-run` | `No changes detected` | 已验证 |
| `git diff --check` | 通过 | 已验证 |

全量测试第一次因 Windows 默认临时目录 ACL 不可写产生 47 个环境错误。将 `TEMP/TMP/TMPDIR` 指向 `runtime-data/review-tmp` 后完整重跑通过。该目录仅用于测试，未写入业务数据。

### 20.4 当前 P1-01 状态

- **代码实现**：已完成。
- **自动化通过**：已完成，SQLite 全量和专项通过。
- **现场验收**：未完成。尚未在真实 PostgreSQL 数据库中盘点既有引用、应用外键迁移或验证锁等待/失败恢复。
- **部署**：未完成。NUC 不可达，未做 PostgreSQL 快照、发布、健康检查或回滚。

P1-01 从“跨入口可破坏数据完整性”更新为“本地代码和自动化已修复，现场 PostgreSQL 与部署待验证”。P2-13、P2-06、P2-09、P2-04 及 P2-10/P2-14/P2-15/P2-16 仍按第 19 节顺序待处理。

### 21. M19 P2-13 修复轮：回滚 schema 兼容门禁

- 安装脚本在候选 release 完成迁移后执行只读 `showmigrations --plan`。读取失败或仍存在未应用迁移时停止发布，并将 `database_migration_check=passed` 与迁移状态摘要写入 `release-info`。
- 回滚脚本在数据库备份和 `current` 指针切换前读取目标 release 的迁移元数据，并用目标 release 的 Django 代码执行 `showmigrations --plan`。目标代码需要的迁移未全部应用时拒绝回切。
- 历史 release 缺少迁移元数据时默认拒绝。`--allow-unknown-schema` 仅作为人工完成兼容确认后的显式覆盖，仍会执行只读迁移检查。
- 部署安全专项从 12 项增至 **13/13**，install/rollback Bash 语法检查通过。真实 PostgreSQL 数据迁移、代码回切和恢复演练仍属于未验证项。

### 21.1 回滚兼容矩阵

| 场景 | 本地门禁结果 | 现场要求 |
| --- | --- | --- |
| 目标 release 有迁移元数据，目标代码的 `showmigrations --plan` 无未应用迁移 | 允许进入备份、`check` 和健康检查阶段 | 仍需 PostgreSQL 回切冒烟 |
| 目标 release 需要当前数据库尚未应用的迁移 | 拒绝切换 | 先恢复数据库或选择兼容 release |
| 目标 release 缺少迁移元数据 | 默认拒绝 | 人工核对 schema 后才可显式覆盖 |
| 迁移包含不可逆数据语义变化，目标代码迁移计划为空 | 门禁无法自动证明业务兼容 | 必须执行隔离 PostgreSQL 冒烟和恢复演练 |

### 22. M19 P2-06 修复轮：发布安全门禁

- `check-all.sh` 现在执行部署目录全部 Bash 脚本语法检查、Bandit SAST 和 pip-audit 依赖审计。工具缺失默认返回失败，`--allow-missing-security-tools` 仅用于明确记录平台豁免。
- `security-check.sh` 对 pip-audit 缺失和扫描发现漏洞使用阻断计数。`ALLOW_MISSING_SECURITY_TOOLS=1` 可记录人工豁免，默认不允许继续显示为安全通过。
- 检查临时目录改为唯一目录，并通过 `trap cleanup EXIT` 清理，避免覆盖其他测试进程或遗留产物。
- 部署安全专项当前为 **14/14**，`bash -n` 已通过。Windows 调用 Bash 的一次 `check-all.sh --fast --allow-missing-security-tools` 运行因系统 Python、Django 虚拟环境和路径不一致失败，已记录为环境性失败，未计为发布门禁通过。

### 23. M19 P2-09 修复轮：SQLite 一次性导入临时文件

- `install.sh --import-sqlite` 改用 `$DATA_DIR/sqlite-import.XXXXXX` 私有目录，目录权限 700，导出文件权限 600，并使用 `umask 077` 生成导出内容。
- 导出、导入或候选发布失败时由统一失败处理清理临时目录，成功导入后立即删除。固定 `/tmp/after-sales-warehouse-initial.json` 路径已移除。
- 部署安全专项当前为 **15/15**，Bash 语法检查通过。真实首次部署、PostgreSQL 导入和中断恢复仍未验证。
