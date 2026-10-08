# 售后仓库管理系统

一套面向机器人本体、载荷、备件与耗材的**自托管仓库管理系统**。覆盖采购申请、审批、出入库、资产台账、维修、盘点、供应商管理、报表与通知的完整闭环。

- **后端**：Django 5.2 + Django REST Framework + PostgreSQL
- **Web 工作台**：服务端渲染 + PWA（支持离线操作与移动扫码）
- **移动端**：微信小程序（`miniprogram/`，独立目录，可选）
- **部署**：Ubuntu 22.04 实体机，Gunicorn + Nginx + systemd，无需 Docker

---

## 功能总览

| 模块 | 能力 |
| --- | --- |
| 采购闭环 | 补货建议 → 采购申请 → 审批 → 采购订单 → 到货入库 → 供应商对账 |
| 资产台账 | 单件资产 / 耗材配件双模式，二维码、生命周期、维修记录、附件归档 |
| 审批流 | 可配置审批节点，超时自动关闭，结果站内通知 + 钉钉推送 |
| 出入库 | 借用 / 领用 / 归还 / 调拨 / 维修 / 报废等全业务类型，支持批量与扫码 |
| 盘点 | 冻结快照 → 扫码核对 → 差异调整 → 审计留痕 |
| 报表 | 库存分布、呆滞分析、供应商价格、采购汇总，支持 CSV 导出 |
| 通知 | 站内通知 + 钉钉群机器人 + 钉钉个人工作通知 |
| 安全 | 登录限流、API 令牌轮换、操作审计、PWA 离线队列账号隔离 |
| 运维 | systemd 定时备份、健康检查、恢复演练、数据一致性每日巡检 |

---

## 系统架构

```
浏览器 / PWA / 小程序
        │
        ▼
      Nginx  (80/443, 反向代理 + 静态文件)
        │
        ▼
    Gunicorn  (Unix Socket, Django WSGI)
        │
        ▼
  Django 应用  ──────────────┐
        │                    │
        ▼                    ▼
   PostgreSQL          本地文件存储 (媒体/备份)
        │
        ▼
  systemd timers  (每日备份 / 一致性巡检 / 安全清理 / 低库存汇总)
```

**关键设计**：
- 所有配置通过环境变量注入，代码中无任何硬编码凭据
- 数据库备份、恢复演练、回滚脚本开箱即用
- 离线操作记录联网后进入服务器复核队列，不直接写库

---

## 快速开始（本地开发）

### 前置要求

- Python 3.10+
- Node.js 18+（仅小程序开发需要）
- PostgreSQL 14+（生产）/ SQLite（本地开发默认）

### 1. 克隆并准备环境

```bash
git clone <your-repo-url>
cd <repo-dir>

# Python 虚拟环境
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 安装依赖
pip install -r backend/requirements.txt
```

### 2. 配置环境变量

```bash
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，至少设置：

```ini
# 必填：生产环境必须是强随机值
SECRET_KEY=change-me-to-a-long-random-string

# 本地开发默认 SQLite，无需配置；生产用 PostgreSQL：
# DB_NAME=warehouse
# DB_USER=warehouse_app
# DB_PASSWORD=your-db-password
# DB_HOST=127.0.0.1
# DB_PORT=5432

# 钉钉通知（可选，不配置则仅站内通知）
# DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
# DINGTALK_SECRET=SECxxx
# DINGTALK_APP_KEY=xxx
# DINGTALK_APP_SECRET=xxx
# DINGTALK_AGENT_ID=xxx
```

完整配置项说明见 `backend/.env.example`。

### 3. 初始化数据库

```bash
cd backend
python manage.py migrate
python manage.py createsuperuser    # 创建管理员
python manage.py bootstrap_roles    # 初始化权限组
```

### 4. 启动开发服务

```bash
python manage.py runserver
```

访问：

- Web 工作台：http://127.0.0.1:8000/
- 健康检查：http://127.0.0.1:8000/health/
- 管理后台：http://127.0.0.1:8000/admin/

### 5. 准备测试数据（可选）

```bash
# 先设置 QA 账号密码
export QA_TEST_PASSWORD="YourQaPassword123"   # Windows: $env:QA_TEST_PASSWORD="..."

python manage.py prepare_qa_data
```

这会创建 7 个不同角色的测试账号和最小业务数据，用于功能验收。

---

## 生产部署（Ubuntu 22.04）

### 架构要求

- 一台 Ubuntu 22.04 服务器（物理机 / 虚拟机 / 云主机均可）
- 至少 2GB 内存，20GB 磁盘
- 固定内网 IP 或域名（记为 `<SERVER_IP>`）

### 一键部署

```bash
# 1. 预检（检查系统依赖、磁盘、网络）
sudo bash deploy/ubuntu22/preflight.sh

# 2. 构建纯净部署包（在开发机执行）
powershell -ExecutionPolicy Bypass -File .\deploy\ubuntu22\build-package.ps1   # Windows
# 或在 Linux/macOS 上手动打包 backend/ + deploy/ + miniprogram/

# 3. 上传部署包到服务器并解压，然后执行：
sudo bash deploy/ubuntu22/install.sh --host <SERVER_IP>
```

安装脚本会自动完成：

- 创建 `warehouse` 系统用户与 `/opt/after-sales-warehouse` 目录
- 安装 PostgreSQL、Nginx、Python 依赖
- 创建数据库与用户（幂等，不重建已有数据）
- 配置 Gunicorn systemd 服务、Nginx 站点
- 注册每日备份、健康检查、一致性巡检等 systemd timers
- 生成自签名 HTTPS 证书（可选 Cloudflare Tunnel）

### 首次部署后导入历史数据（可选）

```bash
sudo bash deploy/ubuntu22/install.sh --host <SERVER_IP> --import-sqlite
```

> 仅在首次部署时执行，已有数据的服务器**绝对不要**重复执行。

### 离线更新（服务器无法访问软件源时）

```bash
sudo bash deploy/ubuntu22/install.sh --host <SERVER_IP> --offline-update
```

该模式不下载系统包或 Python 包，只同步代码、跑迁移、收集静态文件。

---

## 运维操作

### 常用命令

```bash
# 服务状态总览
sudo /usr/local/sbin/after-sales-warehouse-status

# 手动健康检查
sudo /usr/local/sbin/after-sales-warehouse-health

# 手动备份
sudo systemctl start after-sales-warehouse-backup.service

# 恢复演练（在隔离测试库中进行，不影响生产）
sudo /usr/local/sbin/after-sales-warehouse-restore-drill

# 回滚到上一个版本
sudo /usr/local/sbin/after-sales-warehouse-rollback --list
```

### 定时任务

| 任务 | 时间 | 说明 |
| --- | --- | --- |
| 数据备份 | 每日 02:15 | PostgreSQL dump + 媒体文件压缩 + SHA256 校验 |
| 健康检查 | 每 5 分钟 | 服务状态、数据库连通、磁盘空间 |
| 数据一致性巡检 | 每日 03:00 | 只读检查，异常时钉钉告警 |
| 安全状态清理 | 每日 03:45 | 清理超期登录限流记录与失效 API 令牌 |
| 低库存汇总 | 每日 08:00 | 汇总低库存物品并发送钉钉通知 |
| 归还提醒 | 每日 09:00 | 检查到期 / 逾期借用并通知 |

### 日志

```bash
# 应用日志
journalctl -u after-sales-warehouse -f

# Nginx 访问/错误日志
tail -f /var/log/nginx/after-sales-warehouse-*.log

# 备份日志
journalctl -u after-sales-warehouse-backup.service
```

---

## 配置参考

### 环境变量完整列表

见 `backend/.env.example`，按功能分组：

| 分组 | 关键变量 | 说明 |
| --- | --- | --- |
| 核心 | `SECRET_KEY`、`DEBUG`、`ALLOWED_HOSTS` | Django 基础配置 |
| 数据库 | `DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT` | 不配置则用 SQLite |
| 钉钉群机器人 | `DINGTALK_WEBHOOK`、`DINGTALK_SECRET` | 群通知 |
| 钉钉企业应用 | `DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_AGENT_ID` | 个人工作通知 |
| 安全 | `API_TOKEN_EXPIRE_DAYS`、`LOGIN_RATE_LIMIT_*` | 令牌过期、限流阈值 |
| 备份 | `BACKUP_RETENTION_DAYS` | 备份保留天数 |

### 权限组

`bootstrap_roles` 命令创建以下权限组：

| 权限组 | 能力 |
| --- | --- |
| `warehouse_entry` | 仓库录入（到货登记、资料补录） |
| `warehouse_outbound` | 出库操作 |
| `warehouse_staff` | 仓库日常操作 |
| `warehouse_approval` | 审批 |
| `warehouse_reports` | 报表查看 |

---

## 项目结构

```
├── backend/                    # Django 服务端
│   ├── apps/
│   │   ├── accounts/           # 用户、权限、认证
│   │   ├── common/             # 审计日志、一致性检查、QA 数据
│   │   ├── inventory/          # 资产、库存、盘点、采购、供应商
│   │   ├── notifications/      # 站内通知、钉钉推送
│   │   └── workflow/           # 申请、审批、出入库
│   ├── templates/              # 服务端渲染模板
│   ├── warehouse_backend/      # Django 项目配置
│   ├── manage.py
│   └── requirements.txt
├── miniprogram/                # 微信小程序（可选）
├── deploy/
│   └── ubuntu22/               # Ubuntu 部署脚本与配置模板
│       ├── install.sh          # 一键安装/更新
│       ├── preflight.sh        # 部署前预检
│       ├── build-package.ps1   # Windows 构建纯净部署包
│       ├── backup.sh           # 备份脚本
│       ├── restore-drill.sh    # 恢复演练
│       ├── rollback.sh         # 版本回滚
│       ├── *.service / *.timer # systemd 单元
│       └── nginx-*.conf        # Nginx 配置模板
├── docs/                       # 公开文档
└── tools/                      # 开发辅助脚本
```

---

## 开发规范

- 代码标准见 `PROGRAM_STANDARDS.md`
- AI 协作规范见 `AGENTS.md`
- 提交前运行：`python manage.py test`（后端测试）

### 测试

```bash
cd backend
python manage.py test                    # 全部测试
python manage.py test apps.inventory     # 单模块
python manage.py test apps.common -v 2   # 详细输出
```

---

## 许可证

本项目采用 [MIT License](LICENSE) 开源。

---

## 详细文档

- 部署原理与手动部署步骤：`Ubuntu22.04完整手动部署手册.md`
- 产品与权限设计：`售后仓库管理系统产品方案.md`
- 功能蓝图与拓展路线：`系统功能蓝图与拓展路线图.md`
- 技术评估与升级规划：`当前系统产品技术评估与升级规划-20261001.md`
- 开发流程与回归验收：`产品开发计划与回归验收手册.md`
