# Ubuntu 22.04 完整手动部署手册

本文以一台 Ubuntu 22.04 服务器（地址记为 `<SERVER_IP>`）为例，说明不使用 Docker 时如何从零手工部署售后仓库系统，并解释每一步的作用。执行一键脚本前也建议先读本文，以理解脚本实际修改了哪些系统资源。

## 一、最终架构和请求链路

```text
浏览器 / PWA
  -> Nginx :80（内网入口、静态文件、上传文件）
  -> Gunicorn 127.0.0.1:8000（运行 Django）
  -> PostgreSQL 127.0.0.1:5432（保存业务数据）
```

- Nginx 不处理业务规则，只负责入口、文件和反向代理。
- Gunicorn 是生产 WSGI 服务器，负责运行多个 Django worker。
- Django 负责账号、权限、资产、审批、库存、通知、报表和审计。
- PostgreSQL 保存结构化业务数据。
- `/srv/after-sales-warehouse/media` 保存照片和附件。
- systemd 负责开机启动、失败重启和定时任务。

### 当前生产环境实际部署信息（2026-11-04）

| 项目 | 值 |
| --- | --- |
| 主机名 | `robot` |
| 系统 | Ubuntu 22.04.5 LTS |
| WiFi 网卡 | `wlp89s0` → `192.168.23.41/22`（**DHCP，地址会变**） |
| 有线 1 | `enp87s0` → `192.168.25.119/23` |
| 有线 2 | `enp88s0` → `192.168.123.101/24` |
| 默认路由 | `192.168.20.1`（走 WiFi） |
| 程序目录 | `/opt/after-sales-warehouse/releases/<版本>`；`current` 指向当前版本 |
| 数据目录 | `/srv/after-sales-warehouse/{backups,media}` 与持久化 `app.env` |
| 运行账号 | `warehouse` |
| 访问入口 | 纯内网，三个 IP 均可；**无公网入口、无隧道** |
| 地址自动登记 | `/usr/local/bin/auto-register-host.sh` + cron 每 2 分钟 |

> WiFi 是 DHCP，网段切换后 IP 会变。已部署自动登记脚本，
> 详见第十六节「DHCP 环境下的自动登记」。

## 二、部署前准备

### 1. 固定网络地址

建议在公司路由器或 DHCP 服务器中为服务器网卡保留固定地址（记为 `<SERVER_IP>`）。固定地址的作用是让 Web 入口、Nginx 主机名和用户收藏地址保持稳定。

确认地址：

```bash
ip -br address
ip route
ping -c 3 <GATEWAY_IP>
```

### 2. 检查系统和架构

```bash
cat /etc/os-release
dpkg --print-architecture
uname -a
```

Ubuntu 22.04 的 x86 服务器应显示 `amd64`。项目已通过 deadsnakes PPA 安装 Python 3.12，使用 Django 5.2 LTS 与 Pillow 13。安装脚本会自动检测并添加 PPA 源。

### 3. 处理失效 APT 源

先运行：

```bash
sudo apt update
```

如果出现旧 ROS 源没有 Release 文件，例如：

```text
mirrors.tuna.tsinghua.edu.cn/ros/ubuntu jammy Release 404
```

查找并暂时禁用：

```bash
sudo grep -Rns "mirrors.tuna.tsinghua.edu.cn/ros/ubuntu" \
  /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null
sudo mv /etc/apt/sources.list.d/ros-latest.list \
  /etc/apt/sources.list.d/ros-latest.list.disabled
sudo apt update
```

这只禁用失效的软件源，不会删除已经安装的 ROS。

### 4. 运行项目预检

在项目根目录执行：

```bash
sudo bash deploy/ubuntu22/preflight.sh
```

预检只读取 CPU、内存、磁盘、端口、网络、服务和目录状态，不安装或删除程序。重点确认 `80`、`5432` 和 `8000` 没有被未知服务占用。

## 三、复制和校验部署包

将部署包（如 `release.zip`）复制到服务器，例如 `~/app-release/`，然后执行：

```bash
cd ~/app-release
sha256sum release.zip
unzip release.zip -d app
cd app
```

SHA256 用来确认复制过程中压缩包没有损坏。纯净部署包不应包含 `db.sqlite3*`、`.env`、`media` 或开发机缓存。

如果需要把 Windows 验收库一次性迁移到 PostgreSQL，应单独、安全地复制 `backend/db.sqlite3`，不要把它长期混在部署包中。

## 四、安装系统依赖

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y \
  python3.12 python3.12-venv python3.12-dev build-essential libpq-dev \
  postgresql postgresql-contrib nginx rsync curl openssl unzip
```

各依赖作用：

- `python3.12`：Python 3.12 运行时（Pillow 13 要求）。
- `python3.12-venv`：创建项目独立 Python 环境。
- `python3.12-dev`、`build-essential`：编译个别 Python 扩展。
- `libpq-dev`：PostgreSQL 客户端开发库。
- `postgresql`：正式数据库。
- `nginx`：内网入口和静态文件服务。
- `rsync`：可重复同步程序文件。
- `openssl`：生成随机密钥和数据库密码。

## 五、创建运行账号和目录

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin warehouse \
  2>/dev/null || true
sudo install -d -o warehouse -g warehouse /opt/after-sales-warehouse
sudo install -d -o warehouse -g warehouse \
  /srv/after-sales-warehouse/media \
  /srv/after-sales-warehouse/backups
sudo install -d -m 755 /opt/after-sales-warehouse/releases
```

`warehouse` 是无交互登录权限的系统账号。Web 进程不使用 `root`，可以降低应用漏洞影响系统的风险。

目录职责：

```text
/opt/after-sales-warehouse/releases/<版本>/backend  该版本程序
/opt/after-sales-warehouse/releases/<版本>/venv     该版本 Python 环境
/opt/after-sales-warehouse/releases/<版本>/static   该版本静态文件
/opt/after-sales-warehouse/current                    当前版本符号链接
/srv/after-sales-warehouse/media    用户上传文件
/srv/after-sales-warehouse/backups  本机备份
/srv/after-sales-warehouse/app.env  跨版本持久化配置
```

## 六、创建候选 release 并同步程序代码

在解压后的项目根目录执行：

```bash
RELEASE_ID="$(date +%Y%m%d_%H%M%S)-manual"
RELEASE_DIR="/opt/after-sales-warehouse/releases/$RELEASE_ID"
sudo install -d -o warehouse -g warehouse "$RELEASE_DIR/backend"
sudo rsync -a --delete \
  --exclude '.env' \
  --exclude 'db.sqlite3*' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  backend/ "$RELEASE_DIR/backend/"
sudo chown -R warehouse:warehouse "$RELEASE_DIR"
```

`--delete` 只清理本次尚未启用的候选 release，不会覆盖当前运行目录；`.env` 和数据库被排除，避免更新程序时覆盖生产密钥或误带开发数据。

## 七、创建 Python 虚拟环境

```bash
sudo -u warehouse python3.12 -m venv "$RELEASE_DIR/venv"
sudo -u warehouse "$RELEASE_DIR/venv/bin/python" -m pip install --upgrade pip
sudo -u warehouse "$RELEASE_DIR/venv/bin/python" -m pip install \
  -r "$RELEASE_DIR/backend/requirements-linux.txt"
```

已经完整部署过的服务器如临时无法访问软件源，可在项目根目录执行 `sudo bash deploy/ubuntu22/install.sh --host <SERVER_IP> --offline-update`。该模式不下载系统包或 Python 包，只验证现有依赖并完成代码、迁移、静态文件和服务更新；不能用于首次安装或依赖清单发生变化的版本。

虚拟环境将本项目依赖与 Ubuntu 系统 Python 隔离。检查：

```bash
"$RELEASE_DIR/venv/bin/python" --version
"$RELEASE_DIR/venv/bin/python" -m django --version
```

Django 应为 `5.2.x`。如果出现 `No matching distribution found for Django>=6`，说明使用了旧的错误 requirements 文件。

## 八、创建 PostgreSQL 数据库

生成密码并暂存于当前终端：

```bash
DB_PASSWORD="$(openssl rand -base64 36 | tr -d '\n')"
echo "请立即记录数据库密码：$DB_PASSWORD"
```

进入 PostgreSQL：

```bash
sudo -u postgres psql
```

在 `psql` 中执行，把下方密码替换为刚生成的值：

```sql
CREATE ROLE warehouse_app LOGIN PASSWORD '替换为随机密码';
CREATE DATABASE warehouse OWNER warehouse_app;
\q
```

数据库只监听本机。检查：

```bash
sudo ss -lntp | grep 5432
sudo -u postgres psql -tAc "SELECT datname FROM pg_database WHERE datname='warehouse';"
```

不应在路由器、防火墙或 Cloudflare 中暴露 `5432`。

## 九、创建生产环境配置

```bash
SECRET_KEY="$(openssl rand -hex 48)"
sudo -u warehouse nano /srv/after-sales-warehouse/app.env
```

填写：

```dotenv
DEBUG=0
SECRET_KEY=替换为刚生成的SECRET_KEY
ALLOWED_HOSTS=<SERVER_IP>,127.0.0.1,localhost
TIME_ZONE=Asia/Shanghai

DB_ENGINE=django.db.backends.postgresql
DB_NAME=warehouse
DB_USER=warehouse_app
DB_PASSWORD=替换为数据库密码
DB_HOST=127.0.0.1
DB_PORT=5432

MEDIA_ROOT=/srv/after-sales-warehouse/media
STATIC_ROOT=/opt/after-sales-warehouse/current/static
CORS_ALLOW_ALL_ORIGINS=0
USE_HTTPS=0
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=0
RESERVATION_EXPIRY_HOURS=72

DINGTALK_WEBHOOK=
DINGTALK_SECRET=
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=
DINGTALK_AGENT_ID=
LOGIN_RATE_LIMIT_FAILURES=5
LOGIN_RATE_LIMIT_IP_FAILURES=20
LOGIN_RATE_LIMIT_WINDOW_SECONDS=300
LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900
API_RATE_LIMIT_IP_REQUESTS=30
API_RATE_LIMIT_ACCOUNT_REQUESTS=10
API_RATE_LIMIT_WINDOW_SECONDS=60
API_TOKEN_TTL_SECONDS=2592000
SECURITY_STATE_RETENTION_SECONDS=7776000
```

保存后限制权限：

```bash
sudo chown warehouse:warehouse /srv/after-sales-warehouse/app.env
sudo chmod 640 /srv/after-sales-warehouse/app.env
sudo -u warehouse ln -sfn /srv/after-sales-warehouse/app.env \
  "$RELEASE_DIR/backend/.env"
```

`.env` 保存密钥，不应复制到聊天、Git 或普通共享目录。

## 十、初始化数据库结构

```bash
sudo -u warehouse "$RELEASE_DIR/venv/bin/python" \
  "$RELEASE_DIR/backend/manage.py" check --deploy
sudo -u warehouse "$RELEASE_DIR/venv/bin/python" \
  "$RELEASE_DIR/backend/manage.py" migrate --plan
sudo -u warehouse "$RELEASE_DIR/venv/bin/python" \
  "$RELEASE_DIR/backend/manage.py" migrate --noinput
sudo -u warehouse "$RELEASE_DIR/venv/bin/python" \
  "$RELEASE_DIR/backend/manage.py" bootstrap_roles
```

- `check --deploy` 检查生产配置风险。
- `migrate` 按迁移文件创建或升级数据库表，不会清空已有业务数据。
- `bootstrap_roles` 创建项目规定的权限组和默认管理员。

首次部署后登录 `admin/admin`，应立即修改密码。

### 可选：一次性迁移 SQLite

只有首次迁移 Windows 验收数据时执行。正式数据库已经使用后禁止重复执行。

```bash
DB_ENGINE=django.db.backends.sqlite3 \
DB_NAME=/安全路径/db.sqlite3 \
"$RELEASE_DIR/venv/bin/python" \
"$RELEASE_DIR/backend/manage.py" dumpdata \
  --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.Permission --exclude sessions \
  > /tmp/warehouse-initial.json

sudo -u warehouse "$RELEASE_DIR/venv/bin/python" \
  "$RELEASE_DIR/backend/manage.py" loaddata \
  /tmp/warehouse-initial.json
sudo rm -f /tmp/warehouse-initial.json
```

原理是先用 SQLite 配置导出 Django 业务对象，再用 PostgreSQL 配置导入，而不是直接复制数据库文件。

## 十一、收集静态文件

```bash
sudo install -d -o warehouse -g warehouse "$RELEASE_DIR/static"
sudo -u warehouse env STATIC_ROOT="$RELEASE_DIR/static" \
  "$RELEASE_DIR/venv/bin/python" \
  "$RELEASE_DIR/backend/manage.py" collectstatic --noinput
sudo -u warehouse sh -c "cat > '$RELEASE_DIR/release-info'" <<EOF
release_version=$RELEASE_ID
created_at=$(date --iso-8601=seconds)
previous_release=$(readlink -f /opt/after-sales-warehouse/current 2>/dev/null || true)
EOF
sudo ln -sfn "$RELEASE_DIR" /opt/after-sales-warehouse/current.next
sudo mv -Tf /opt/after-sales-warehouse/current.next /opt/after-sales-warehouse/current
```

这一步把 CSS、PWA 图标、扫码组件和 Django 管理后台资源写入候选 release，并在所有检查完成后原子切换 `current`。Nginx 始终读取 `current/static`，不会看到收集到一半的目录。

## 十二、配置 Gunicorn 和 systemd

```bash
sudo cp deploy/ubuntu22/after-sales-warehouse.service \
  /etc/systemd/system/after-sales-warehouse.service
sudo systemctl daemon-reload
sudo systemctl enable --now after-sales-warehouse
```

检查：

```bash
sudo systemctl status after-sales-warehouse --no-pager
sudo journalctl -u after-sales-warehouse -n 100 --no-pager
curl -i http://127.0.0.1:8000/health/
```

该服务使用 3 个 Gunicorn worker，只监听 `127.0.0.1:8000`。外部用户不能直接绕过 Nginx 访问它。

## 十三、配置 Nginx

生成实际配置：

```bash
sed 's/__SERVER_NAME__/<SERVER_IP>/g' \
  deploy/ubuntu22/nginx-after-sales-warehouse.conf \
  | sudo tee /etc/nginx/sites-available/after-sales-warehouse >/dev/null
sudo ln -sfn /etc/nginx/sites-available/after-sales-warehouse \
  /etc/nginx/sites-enabled/after-sales-warehouse
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl reload nginx
```

检查两层入口：

```bash
curl -i http://127.0.0.1/health/
curl -i http://<SERVER_IP>/health/
```

两者都应由系统返回健康信息，而不是 Nginx 默认 404。浏览器访问：

```text
http://<SERVER_IP>/
```

## 十四、安装定时任务和备份

```bash
sudo cp deploy/ubuntu22/after-sales-warehouse-*-summary.service /etc/systemd/system/ 2>/dev/null || true
sudo cp deploy/ubuntu22/after-sales-warehouse-*-summary.timer /etc/systemd/system/ 2>/dev/null || true
sudo cp deploy/ubuntu22/after-sales-warehouse-return-reminder.service /etc/systemd/system/
sudo cp deploy/ubuntu22/after-sales-warehouse-return-reminder.timer /etc/systemd/system/
sudo cp deploy/ubuntu22/after-sales-warehouse-reservation-expiry.service /etc/systemd/system/
sudo cp deploy/ubuntu22/after-sales-warehouse-reservation-expiry.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now after-sales-warehouse-low-stock-summary.timer
sudo systemctl enable --now after-sales-warehouse-return-reminder.timer
sudo systemctl enable --now after-sales-warehouse-reservation-expiry.timer
sudo cp deploy/ubuntu22/after-sales-warehouse-consistency.service /etc/systemd/system/
sudo cp deploy/ubuntu22/after-sales-warehouse-consistency.timer /etc/systemd/system/
sudo cp deploy/ubuntu22/after-sales-warehouse-security-cleanup.service /etc/systemd/system/
sudo cp deploy/ubuntu22/after-sales-warehouse-security-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now after-sales-warehouse-consistency.timer
sudo systemctl enable --now after-sales-warehouse-security-cleanup.timer
```

安装备份：

```bash
sudo install -m 750 deploy/ubuntu22/backup.sh \
  /usr/local/sbin/after-sales-warehouse-backup
echo '15 2 * * * root /usr/local/sbin/after-sales-warehouse-backup >> /var/log/after-sales-warehouse-backup.log 2>&1' \
  | sudo tee /etc/cron.d/after-sales-warehouse-backup
sudo chmod 644 /etc/cron.d/after-sales-warehouse-backup
```

调度含义：

- 每周一 09:00 汇总低库存。
- 每天 09:10 检查即将到期和逾期借用。
- 每小时释放仍在待审批且已经超时的库存占用。
- 每天 02:15 备份 PostgreSQL、媒体文件和校验值。
- 每天 03:00 执行只读数据一致性检查；只在首次发现、问题变化或全部恢复时发送钉钉告警。

一致性检查与告警状态：

```bash
sudo -u warehouse /opt/after-sales-warehouse/current/venv/bin/python \
  /opt/after-sales-warehouse/current/backend/manage.py check_data_consistency
sudo -u warehouse /opt/after-sales-warehouse/current/venv/bin/python \
  /opt/after-sales-warehouse/current/backend/manage.py check_data_consistency --notify
sudo systemctl list-timers after-sales-warehouse-consistency.timer --no-pager
sudo systemctl status after-sales-warehouse-consistency.service --no-pager -l
```

`--notify` 使用 `.env` 中的 `DINGTALK_WEBHOOK` 和 `DINGTALK_SECRET`。状态文件默认是
`/srv/after-sales-warehouse/consistency-check.state.json`，只保存问题指纹和是否存在问题。
问题检查本身不会改库；钉钉发送失败时不会更新状态，下一次运行会继续重试。

## 十五、配置钉钉业务通知

### 1. 群机器人

在钉钉群中添加自定义机器人，启用“加签”，将 Webhook 和 `SEC...` 密钥写入 `.env`：

```dotenv
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=...
DINGTALK_SECRET=SEC...
```

然后：

```bash
sudo systemctl restart after-sales-warehouse
sudo -u warehouse /opt/after-sales-warehouse/current/venv/bin/python \
  /opt/after-sales-warehouse/current/backend/manage.py send_low_stock_summary
```

### 2. 个人工作通知

钉钉管理员创建企业内部应用并开通工作通知权限，将 AppKey、AppSecret、AgentId 写入 `.env`。随后在 Web“账号与权限”中为每个用户填写钉钉通讯录 `userid`。

站内通知始终保留；个人通知失败时会回退群机器人，不会影响业务事务提交。

## 十六、登记访问入口（IP 或域名）

服务器默认监听全部网卡的 80 端口，Django 只接受 `ALLOWED_HOSTS` 白名单里的主机名。
增加一个访问入口（新的 IP 或域名）用统一脚本登记：

```bash
# 添加
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local

# 移除
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local --remove
```

脚本会一次性完成三件事：

1. 写入 `.env` 的 `ALLOWED_HOSTS`（否则 Django 返回 400）
2. 写入 `CSRF_TRUSTED_ORIGINS`（否则网页表单提交失败）
3. 更新 nginx 的 `server_name`，然后重载 nginx 并重启应用

IP 和域名都支持，带不带 `http://` 都行：

```bash
sudo bash deploy/ubuntu22/register-host.sh 192.168.23.41
sudo bash deploy/ubuntu22/register-host.sh https://wms.bwt.local
```

**域名的前提是 DNS 能解析到本机**，脚本不改 DNS：

```bash
getent hosts wms.bwt.local    # 有输出才算配好
```

### DHCP 环境下的自动登记

如果服务器 WiFi 网卡走 DHCP（`nmcli` 的 `ipv4.method` 为 `auto`），**IP 会在租约变化或换网段时改变**，变了不登记就会返回 400。

用 `auto-register-host.sh` 自动跟上：

```bash
# 预览会改什么
sudo bash deploy/ubuntu22/auto-register-host.sh --dry-run

# 正式执行
sudo bash deploy/ubuntu22/auto-register-host.sh
```

脚本行为：

1. 采集本机所有内网 IPv4，**排除** `lo`、`utun`（VPN）、`docker0`、`br-*`、`veth*`
2. 把新地址写进 `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、nginx `server_name`
3. 只有真的发生变更才重启应用和重载 nginx

配合 cron 每 2 分钟检查一次：

```bash
( crontab -l 2>/dev/null | grep -v auto-register-host.sh; \
  echo "*/2 * * * * /usr/local/bin/auto-register-host.sh >/dev/null 2>&1" ) | crontab -
```

> **注意**：不要用 systemd timer 配这个脚本。若 service 加了 `RemainAfterExit=yes`，
> unit 会长期停在 `active`，timer 到点触发时被判定为空操作而永不执行。
> 如果一定要用 timer，service 里必须去掉 `RemainAfterExit=yes`。

### 关于内网穿透

按公司网络安全规定，**本项目不使用任何内网穿透方案**。
ZeroTier、Tailscale、Cloudflare Tunnel、frp 等一律不部署。

跨网段访问不通时，正确做法是让网络管理员开通内网路由，而不是打隧道。
如需固定 HTTPS，应由网络管理员签发证书并在 nginx 配置 TLS，不使用第三方隧道服务。

> **2026-11-04 实测记录**：曾按临时需求试用 Cloudflare Quick Tunnel，结论是不可用 ——
> 公司 DNS 拦截 `*.trycloudflare.com` 子域；绕过 DNS 直连 Cloudflare IP（`curl --resolve`）
> 同样 25 秒超时，说明整个 Cloudflare IP 段被封。隧道方案已彻底移除，
> 系统恢复纯内网运行，`cloudflared` 进程数为 0。

## 十七、防火墙和安全边界

只允许公司网段访问 HTTP：

```bash
sudo ufw allow OpenSSH
sudo ufw allow from <SUBNET_CIDR> to any port 80 proto tcp
sudo ufw enable
sudo ufw status verbose
```

不要开放 `5432` 和 `8000`，只在公司网段内开放 80。

正式固定 HTTPS 上线前再设置 `USE_HTTPS=1`。HSTS 初始保持 0，证书和域名稳定后才能启用，避免客户端永久记住错误 HTTPS 地址。

## 十八、完整验收清单

```bash
sudo systemctl status postgresql nginx after-sales-warehouse --no-pager
sudo systemctl list-timers --all | grep after-sales-warehouse
sudo nginx -t
curl -i http://127.0.0.1/health/
curl -i http://<SERVER_IP>/health/
sudo -u warehouse /opt/after-sales-warehouse/current/venv/bin/python \
  /opt/after-sales-warehouse/current/backend/manage.py check --deploy
```

Web 人工验收：

1. 登录并修改管理员密码。
2. 创建普通员工和四类仓库权限账号。
3. 新增测试资产、打印二维码并扫码检索。
4. 提交申请、审批、执行出库、归还。
5. 检查通知跳转、库存占用和审计日志。
6. 创建盘点任务并导出 Excel。
7. 手动执行一次备份并校验文件。
8. 执行一次隔离恢复演练，并检查健康监控 timer。
9. 模拟离线快速出入库，确认联网后进入复核队列且不会自动扣库存。

## 十九、日常更新程序

将新包解压到独立目录并校验 SHA-256 后，在项目根目录执行：

```bash
sudo bash deploy/ubuntu22/install.sh --host <SERVER_IP>
```

脚本先创建 PostgreSQL 快照，再在独立 release 中安装锁定依赖、执行 Django 检查、收集静态文件和迁移，最后原子切换 `current` 并检查 `/health/`。健康失败会恢复上一 release 指针。已有服务器更新时绝对不要增加 `--import-sqlite`，也不要手工覆盖当前数据库。

## 二十、备份、异机副本和恢复

手动备份：

```bash
sudo /usr/local/sbin/after-sales-warehouse-backup
ls -lah /srv/after-sales-warehouse/backups/
sudo systemctl list-timers after-sales-warehouse-backup.timer --no-pager
```

本机备份不能防止服务器被盗、硬盘损坏或整机格式化。至少每周把备份复制到另一台 NAS、电脑或受控存储。

先恢复到独立测试库验证，默认完成后自动删除测试库：

```bash
sudo /usr/local/sbin/after-sales-warehouse-restore-drill
KEEP_TEST_DB=1 sudo /usr/local/sbin/after-sales-warehouse-restore-drill \
  /srv/after-sales-warehouse/backups/时间目录
```

脚本拒绝把测试库命名为 `warehouse`。只有演练通过并完成维护审批后，才能停止应用执行正式恢复：

```bash
sudo systemctl stop after-sales-warehouse
sudo -u postgres dropdb warehouse
sudo -u postgres createdb -O warehouse_app warehouse
sudo -u postgres pg_restore -d warehouse \
  /srv/after-sales-warehouse/backups/时间目录/database.dump
sudo tar -C /srv/after-sales-warehouse -xzf \
  /srv/after-sales-warehouse/backups/时间目录/media.tar.gz
sudo systemctl start after-sales-warehouse
```

必须先在测试机演练恢复，再把流程用于正式服务器。

## 二十一、健康监控、钉钉告警与日志

```bash
sudo systemctl status after-sales-warehouse-health.timer --no-pager
sudo /usr/local/sbin/after-sales-warehouse-health
sudo journalctl -u after-sales-warehouse-health.service -n 100 --no-pager
```

健康脚本每五分钟检查服务、监听端口、PostgreSQL、本机 HTTP、磁盘和内存。状态从正常变为故障时通知一次，恢复时再通知一次。备份失败也使用同一个运维钉钉发送器；钉钉配置直接读取 `/srv/after-sales-warehouse/app.env` 里的 `DINGTALK_WEBHOOK` / `DINGTALK_SECRET`，密钥不进入发布包。业务通知先写入数据库 outbox，再由每分钟 timer 异步发送，Web 请求不会等待钉钉网络。

Nginx 使用独立的 `after-sales-warehouse.access.log` 和 `after-sales-warehouse.error.log`，由 Ubuntu 自带的 Nginx logrotate 规则轮转。Gunicorn 输出到 journald，安装脚本将系统日志总量限制为 1 GB、最长保留 14 天。临时文件通过 systemd-tmpfiles 清理。

## 二十二、常见故障定位

### Nginx 返回 404

```bash
sudo ls -l /etc/nginx/sites-enabled/
sudo nginx -T | grep -nE 'server_name|proxy_pass|listen'
sudo nginx -t
```

确认启用的是 `after-sales-warehouse`，并删除默认站点链接。

### Gunicorn 服务不存在

```bash
sudo cp deploy/ubuntu22/after-sales-warehouse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now after-sales-warehouse
```

### 页面更新后样式或扫码组件仍是旧版

```bash
sudo -u warehouse env STATIC_ROOT=/opt/after-sales-warehouse/current/static \
  /opt/after-sales-warehouse/current/venv/bin/python \
  /opt/after-sales-warehouse/current/backend/manage.py collectstatic --noinput
sudo systemctl restart after-sales-warehouse
sudo systemctl reload nginx
```

再清理手机浏览器缓存或重新打开 PWA。

### 数据库连接失败

```bash
sudo systemctl status postgresql --no-pager
sudo -u postgres psql -d warehouse -c 'SELECT 1;'
sudo journalctl -u after-sales-warehouse -n 100 --no-pager
```

### 域名可访问但登录后异常

```bash
grep '^CSRF_TRUSTED_ORIGINS=' /srv/after-sales-warehouse/app.env
grep '^ALLOWED_HOSTS=' /srv/after-sales-warehouse/app.env
```

两个配置都必须包含该域名。用 `register-host.sh` 登记可以避免漏改其中一项：

```bash
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local
```

### 域名访问返回 400 Bad Request

说明域名没进 `ALLOWED_HOSTS`，执行上面的 `register-host.sh` 即可。

## 二十三、一键脚本与手工步骤的对应关系

```bash
sudo bash deploy/ubuntu22/install.sh --host <SERVER_IP>
```

该脚本依次完成本文第四至第十四章，并安装备份 timer、健康监控、钉钉运维发送器、日志保留与恢复演练脚本。手工手册用于理解、审计和故障恢复；日常正常发布仍建议使用已测试的一键脚本，减少漏步骤。
