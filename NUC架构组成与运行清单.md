# NUC 架构组成与运行清单

当前部署状态（2026-09-08）：最新版本已部署到 `192.168.61.169`，PostgreSQL、Gunicorn、Nginx、定时备份及核心健康检查正常；最新备份已通过独立测试库恢复演练。临时域名暂不纳入健康判定。

## 一、访问链路

网口用途分工：

| 链路 | 用途 | 说明 |
| --- | --- | --- |
| 公司 Wi-Fi 内网地址 | 日常访问主入口 | 手机和电脑连公司 Wi-Fi 后直接访问，走 Nginx 80 端口 |
| Tunnel 域名（`current-url`） | 日常访问主入口 | `after-sales-warehouse-quick-tunnel-url` 读取 `/var/lib/cloudflared/current-url`，地址变化时会通知钉钉 |
| `192.168.123.101` | 仅调试 | 只在现场排查时使用，不作为用户日常入口，也不要写进小程序或对外文档 |
| 另一个网口 | 仅 git | 只用于拉取和推送代码，不登记为 Web 入口 |

日常使用 Wi-Fi 和域名两条路径即可；调试地址保留但不对外发放，git 专用网口保持现状，暂不改动。

公司内网访问：

```text
浏览器
  -> NUC Wi-Fi 内网地址:80（调试时为 192.168.123.101:80）
  -> Nginx
  -> Gunicorn 127.0.0.1:8000
  -> Django
  -> PostgreSQL 127.0.0.1:5432
```

外部临时访问：

```text
手机或外部电脑
  -> https://随机名称.trycloudflare.com
  -> Cloudflare 边缘网络
  -> NUC 上 cloudflared 主动建立的出站 HTTPS 隧道
  -> Nginx 127.0.0.1:80
  -> Gunicorn / Django
  -> PostgreSQL
```

NUC 不需要公网 IP，也没有在路由器上开放入站端口。外部客户端不需要安装 `cloudflared`。

## 二、常驻服务和进程

### PostgreSQL

- systemd 单元：`postgresql.service`
- 用途：正式业务数据库
- 数据库：`warehouse`
- 应用账号：`warehouse_app`
- 监听：`127.0.0.1:5432`
- 不向公司内网或公网开放数据库端口

Ubuntu 的 `postgresql.service` 可能显示 `active (exited)`，实际数据库实例由版本化单元管理。这是 Ubuntu PostgreSQL 包的正常结构。

### Django / Gunicorn

- systemd 单元：`after-sales-warehouse.service`
- 运行用户：`warehouse`
- 工作目录：`/opt/after-sales-warehouse/backend`
- Python 环境：`/opt/after-sales-warehouse/venv`
- 启动命令：Gunicorn 主进程和 3 个 worker
- 监听：`127.0.0.1:8000`
- 自动重启：失败后 5 秒重启

Django 负责账号、权限、资产、库存、审批、盘点、报表、站内通知和业务钉钉通知。

### Nginx

- systemd 单元：`nginx.service`
- 用途：内网 Web 入口、静态文件、上传文件和反向代理
- 监听：当前为 `80`
- 转发动态请求到：`127.0.0.1:8000`
- 静态目录：`/srv/after-sales-warehouse/static`
- 上传目录：`/srv/after-sales-warehouse/media`

### Cloudflare Quick Tunnel

- systemd 单元：`after-sales-warehouse-quick-tunnel.service`
- 运行用户：`cloudflared`
- 本机目标：`http://127.0.0.1:80`
- 对外连接：主动访问 Cloudflare 的 HTTPS 网络
- 自动重启：失败后 10 秒重试
- 当前地址：`/var/lib/cloudflared/current-url`
- 钉钉配置：`/etc/after-sales-warehouse/quick-tunnel-dingtalk.env`

它只改变访问入口，不保存仓库数据。

## 三、定时任务

### 每日备份

- 调度方式：systemd timer
- timer：`after-sales-warehouse-backup.timer`
- service：`after-sales-warehouse-backup.service`
- 时间：每天 `02:15`
- 命令：`/usr/local/sbin/after-sales-warehouse-backup`
- 目录：`/srv/after-sales-warehouse/backups`
- 保留：30 天
- 内容：PostgreSQL dump、上传文件压缩包和 SHA256 校验
- 完成后校验 dump 目录、媒体压缩包和 SHA256；失败发送钉钉运维告警

### 每五分钟健康检查

- timer：`after-sales-warehouse-health.timer`
- service：`after-sales-warehouse-health.service`
- 检查：PostgreSQL、Gunicorn、Nginx、端口、HTTP、磁盘、内存和已启用 Tunnel
- 通知：故障状态首次出现时通知一次，恢复时通知一次
- 手动执行：`sudo /usr/local/sbin/after-sales-warehouse-health`

### 每周安全库存汇总

- timer：`after-sales-warehouse-low-stock-summary.timer`
- service：`after-sales-warehouse-low-stock-summary.service`
- 时间：每周一 `09:00`，随机延迟最多 5 分钟
- `Persistent=true`：NUC 当时关机时，恢复开机后会补执行
- 执行方式：短时运行 Django 管理命令，完成后退出
- 不会常驻占用内存

即时预警由 Django 在库存变化提交成功后执行：

- 耗材配件：当前数量小于或等于安全库存时触发
- 单件资产：同物品类型“在库”资产总数小于安全库存时触发
- 首次进入低库存状态时通知一次
- 持续低库存不重复通知
- 恢复正常时通知一次
- 每周汇总仍未恢复的预警

### 每日借用归还提醒

- timer：`after-sales-warehouse-return-reminder.timer`
- service：`after-sales-warehouse-return-reminder.service`
- 时间：每天 `09:10`，随机延迟最多 3 分钟
- 范围：三天内到期、当天到期和已逾期且尚未归还的资产与耗材配件
- 去重：同一借用记录、提醒阶段和日期只发送一次
- 输出：借用人站内通知、仓库人员站内汇总、已绑定用户的钉钉个人通知、钉钉业务机器人群汇总

### 每小时预约过期处理

- timer：`after-sales-warehouse-reservation-expiry.timer`
- service：`after-sales-warehouse-reservation-expiry.service`
- 时间：每小时执行，随机延迟最多 5 分钟
- 范围：只关闭超过有效期且仍为“待审批”的申请
- 保护：已审批、等待仓库执行的预约不自动释放
- 结果：关闭整张申请、取消待审批任务、释放全部预约并写入流程日志与操作审计

## 四、系统账号

- `warehouse`：运行 Django、Gunicorn、迁移、静态文件收集和汇总命令
- `cloudflared`：运行临时隧道，只能读取自己的钉钉配置和写入当前地址
- `postgres`：PostgreSQL 系统管理账号
- `root`：安装、systemd、Nginx、备份和受保护配置
- `robot`：当前人工登录 NUC 的运维账号，不直接运行 Web 服务

## 五、目录和数据

```text
/opt/after-sales-warehouse/
  backend/                 已部署的 Django 程序
  backend/.env             正式环境密钥与数据库连接配置
  venv/                    Ubuntu Python 虚拟环境
  releases/                最近 10 次部署前代码快照
  current-release          当前部署或回滚记录

/srv/after-sales-warehouse/
  media/                   用户上传照片和附件
  static/                  collectstatic 生成的静态文件
  backups/                 每日备份

/etc/systemd/system/
  after-sales-warehouse.service
  after-sales-warehouse-quick-tunnel.service
  after-sales-warehouse-low-stock-summary.service
  after-sales-warehouse-low-stock-summary.timer
  after-sales-warehouse-return-reminder.service
  after-sales-warehouse-return-reminder.timer
  after-sales-warehouse-reservation-expiry.service
  after-sales-warehouse-reservation-expiry.timer
  after-sales-warehouse-backup.service
  after-sales-warehouse-backup.timer
  after-sales-warehouse-health.service
  after-sales-warehouse-health.timer

/etc/nginx/sites-available/after-sales-warehouse
/etc/after-sales-warehouse/quick-tunnel-dingtalk.env
/var/lib/cloudflared/current-url
/usr/local/sbin/after-sales-warehouse-backup
/usr/local/sbin/after-sales-warehouse-health
/usr/local/sbin/after-sales-warehouse-restore-drill
/usr/local/sbin/after-sales-warehouse-rollback
/usr/local/sbin/after-sales-warehouse-security-check
```

正式数据位于 PostgreSQL 和 `/srv/after-sales-warehouse/media`。项目源目录中的 `backend/db.sqlite3` 只用于首次迁移，不是 NUC 正式运行数据库。

## 六、Ubuntu 系统依赖

部署脚本安装：

```text
python3-venv
python3-dev
build-essential
libpq-dev
postgresql
postgresql-contrib
nginx
rsync
curl
openssl
```

另外安装：

```text
cloudflared-linux-amd64.deb
```

## 七、Python 依赖

主要依赖来自 `backend/requirements.txt`：

- Django 5.2 LTS：Web 框架
- Django REST Framework：API
- django-cors-headers：API 跨域控制
- Gunicorn：生产 WSGI 服务器
- psycopg：PostgreSQL 驱动
- Pillow：照片处理
- qrcode：资产二维码生成
- openpyxl：Excel 导入导出
- pypinyin：中文、拼音和首字母检索
- python-dotenv：环境配置加载

精确安装版本可通过运行状态脚本查看，不应仅以开发机版本为准。

## 八、没有使用的组件

当前 NUC 正式架构没有使用：

- Docker / Docker Compose
- Redis
- Celery
- MySQL
- Node.js 常驻服务
- 微信小程序服务进程
- Tailscale / ZeroTier
- FRP

## 九、通知链路

业务通知：

```text
Django 业务事务提交成功
  -> 站内 Notification
  -> 已绑定用户优先发送钉钉个人工作通知
  -> 未配置、未绑定或发送失败时回退钉钉业务机器人 Webhook
```

包括申请、审批、撤回、出入库完成、安全库存预警、库存恢复和借用归还提醒。

临时域名通知：

```text
cloudflared 生成新地址
  -> runner 保存 current-url
  -> Tunnel 专用钉钉机器人 Webhook
```

两个机器人配置相互独立。

## 十、日常检查命令

更新部署后会安装统一状态命令：

```bash
sudo /usr/local/sbin/after-sales-warehouse-status
```

也可以分别检查：

```bash
sudo systemctl status after-sales-warehouse nginx postgresql --no-pager
sudo systemctl status after-sales-warehouse-quick-tunnel --no-pager
sudo systemctl status after-sales-warehouse-low-stock-summary.timer --no-pager
sudo systemctl status after-sales-warehouse-reservation-expiry.timer --no-pager
sudo systemctl status after-sales-warehouse-backup.timer after-sales-warehouse-health.timer --no-pager
sudo systemctl list-timers after-sales-warehouse-low-stock-summary.timer --no-pager
sudo journalctl -u after-sales-warehouse -n 100 --no-pager
sudo journalctl -u after-sales-warehouse-quick-tunnel -n 100 --no-pager
curl -i http://127.0.0.1/health/
```

该状态命令不会打印数据库密码、Django Secret Key、钉钉 Webhook 或加签密钥。

## 十一、已部署 NUC 的增量更新

已经完成首次 SQLite 导入的 NUC，更新程序时不要再次使用 `--import-sqlite`：

```bash
cd ~/Cloudwarehouse
sudo bash deploy/ubuntu22/install.sh --host 192.168.61.169
```

该命令会同步代码、安装依赖、执行新增迁移、收集静态文件、更新 systemd 单元，并启用业务提醒、每日备份和健康检查 timer；不会覆盖正式 `.env`，也不会重新导入 SQLite。

`--host` 填日常访问的 Wi-Fi 内网地址或内网域名。调试地址和不提供 Web 访问的网口单独声明：

```bash
cd ~/Cloudwarehouse
sudo ip -o link show    # 确认哪一个网口只用于 git
sudo bash deploy/ubuntu22/install.sh \
  --host <Wi-Fi 内网地址或内网域名> \
  --extra-host 192.168.123.101 \
  --exclude-iface <git 专用网口名>
```

`--extra-host` 让调试地址即使当时没有连线也常驻 `ALLOWED_HOSTS` 和 Nginx；`--exclude-iface` 让 git 专用网口的地址不作为 Web 入口登记。两个参数都可以重复。

更新后配置或确认业务钉钉机器人，再手动执行一次首次低库存汇总：

```bash
sudo -u warehouse \
  /opt/after-sales-warehouse/venv/bin/python \
  /opt/after-sales-warehouse/backend/manage.py send_low_stock_summary
```

最后检查：

```bash
sudo /usr/local/sbin/after-sales-warehouse-status
sudo systemctl list-timers after-sales-warehouse-low-stock-summary.timer --no-pager
sudo systemctl list-timers after-sales-warehouse-return-reminder.timer --no-pager
sudo systemctl list-timers after-sales-warehouse-reservation-expiry.timer --no-pager
sudo systemctl list-timers after-sales-warehouse-backup.timer after-sales-warehouse-health.timer --no-pager
sudo /usr/local/sbin/after-sales-warehouse-restore-drill
```
