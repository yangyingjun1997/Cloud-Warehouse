# Ubuntu 22.04 本机部署手册

## 目标架构

本方案不使用 Docker。NUC 直接运行 PostgreSQL、Gunicorn、Nginx 和 systemd：

- Nginx 对公司内网开放 `80` 端口；启用内网 HTTPS 时同时使用 `443` 端口。
- Gunicorn 只监听 `127.0.0.1:8000`。
- PostgreSQL 仅供本机应用访问，不对内网开放 `5432`。
- 图片、静态文件和每日备份存于 `/srv/after-sales-warehouse/`。

这套架构适合当前约 30 名售后人员、公司内网访问的场景。建议 NUC 使用有线网络；仅能用 Wi-Fi 时，务必在 DHCP 中保留固定地址并关闭睡眠。

## 部署前检查

1. 安装 Ubuntu Server 22.04，创建一个具有 `sudo` 权限的运维账号。
2. 在公司网络中为 NUC 分配固定 IPv4 或 DHCP 保留地址，例如 `192.168.10.50`。
3. 先分清三个链路的用途，再决定哪些地址会写进 `ALLOWED_HOSTS`：

   ```text
   Wi-Fi 内网地址        日常访问主入口，必须登记
   Tunnel 临时域名       日常访问主入口，current-url 记录，值随重启变化
   192.168.123.101      仅调试使用，不对外发放
   另一个网口            仅 git 使用，不登记为 Web 入口
   ```
4. 将整个项目目录复制到 NUC，例如 `/home/operator/Cloud warehouse`。不要只复制 `backend`，部署脚本位于项目根目录下。
5. 先运行只读预检，确认端口 `80`、`443`、`5432`、`8000` 和已有服务均无冲突：

```bash
sudo bash deploy/ubuntu22/preflight.sh
```

6. 若要保留 Windows 验收库中的现有资产数据，确保项目中的 `backend/db.sqlite3` 已一并复制。

## 生成纯净部署包

在 Windows 工作目录中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\ubuntu22\build-package.ps1
```

脚本会在 `dist/` 目录生成 `after-sales-warehouse-版本.zip`、同名 `.sha256` 校验文件，并在压缩包内写入 `release-manifest.json`。部署包会排除 `.env`、`backend/db.sqlite3`、虚拟环境、缓存文件、上传图片和静态收集目录，适合拷贝到 NUC。

在 NUC 上解压前可校验：

```bash
sha256sum -c after-sales-warehouse-版本.zip.sha256
```

## 一键部署

在 NUC 的项目根目录执行：

```bash
sudo bash deploy/ubuntu22/install.sh --host 192.168.10.50 --import-sqlite
```

首次部署且没有任何本地验收数据时可省略 `--import-sqlite`：

```bash
sudo bash deploy/ubuntu22/install.sh --host 192.168.10.50
```

脚本默认采集全部对外 IPv4 作为入口。当 NUC 同时接了调试链路和 git 专用链路时，显式声明用途：

```bash
sudo bash deploy/ubuntu22/install.sh \
  --host 192.168.10.50 \
  --extra-host 192.168.123.101 \
  --exclude-iface <git 专用网口名>
```

- `--host`：日常访问的 Wi-Fi 内网地址或内网域名。
- `--extra-host`：调试地址，即使当时没有连线也会写进 `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS` 和 Nginx `server_name`。可重复。
- `--exclude-iface`：该网口的地址不作为 Web 入口，例如只用于 git 的网口；用 `ip -o link show` 查看网口名。可重复。

脚本会安装系统依赖、创建 PostgreSQL 数据库、创建应用用户、安装 Python 依赖、执行迁移、导入 SQLite 数据一次、收集静态文件、配置 Nginx/systemd，并配置每日 02:15 校验备份和每 5 分钟健康检查。

每次覆盖程序前，脚本会把当前 `/opt/after-sales-warehouse/backend` 保存为代码快照，位于 `/opt/after-sales-warehouse/releases/`，默认保留最近 10 个快照。`.env`、SQLite、上传文件和静态收集文件不会放入代码快照。

部署结束后访问：

```text
http://192.168.10.50/
```

首次登录后立即修改 `admin` 账号密码。

## 服务运维

```bash
sudo systemctl status after-sales-warehouse nginx postgresql
sudo systemctl restart after-sales-warehouse
sudo journalctl -u after-sales-warehouse -f
sudo nginx -t
sudo /usr/local/sbin/after-sales-warehouse-status
```

状态命令会同时列出“已登记的 Web 入口”（来自 `ALLOWED_HOSTS`）和“临时域名入口”（即 `current-url` 中的 Tunnel 地址）。日常给用户的是 Wi-Fi 内网地址或临时域名，`192.168.123.101` 只在现场调试时使用。

更新程序时，将新的项目目录同步到 NUC 后，再次执行同一条部署命令。脚本不会覆盖已存在的 `.env`，不会重复导入 SQLite 数据。

如果 NUC 已经成功部署过、此次只更新程序代码，但临时无法访问 Ubuntu 软件源或 PyPI，可使用离线更新模式：

```bash
sudo bash deploy/ubuntu22/install.sh --host 192.168.61.169 --offline-update
```

离线更新会跳过 `apt` 和 `pip` 下载，并验证现有系统命令、Python 包及依赖一致性；验证失败时会立即停止。首次部署或 `requirements.txt` 发生变化时不能使用该参数，必须恢复外网后执行普通安装。

## 代码回滚与安全巡检

查看可回滚代码快照：

```bash
sudo /usr/local/sbin/after-sales-warehouse-rollback --list
```

回滚到最近一次部署前代码：

```bash
sudo /usr/local/sbin/after-sales-warehouse-rollback latest
```

也可以指定快照目录名：

```bash
sudo /usr/local/sbin/after-sales-warehouse-rollback 20260920_153000
```

回滚命令会先执行一次数据库和媒体备份，然后只恢复程序代码、重装 Python 依赖、收集静态文件并重启服务。它不会回滚 PostgreSQL 数据库；如果问题涉及数据库迁移或业务数据，需要按“备份与恢复”章节执行正式恢复。

部署后执行基础安全巡检：

```bash
sudo /usr/local/sbin/after-sales-warehouse-security-check
```

巡检会检查 `DEBUG`、`SECRET_KEY`、CORS、PostgreSQL 配置、`.env` 权限、Django 检查、依赖冲突、端口监听、服务状态和 `/health/`。当前内网测试阶段允许 HTTP 相关提醒存在；公网或正式大范围使用前必须再处理 HTTPS、HSTS、CSRF Cookie 安全标记等生产项。

更新已有 NUC 到批量申请版本时执行：

```bash
cd ~/Cloudwarehouse
sudo bash deploy/ubuntu22/install.sh --host 192.168.61.169
```

不要再次添加 `--import-sqlite`。安装脚本会自动执行数据库迁移，新增个人耗材借用余额台账，并从历史“已完成、非快速办理”的耗材借用和归还记录计算尚未归还量；该迁移不会改变资产数量或耗材总库存。

## 申请与出入库单据

申请列表、我的审批和出入库记录支持按关键词、类型、状态、仓库、项目、工单和日期范围筛选。申请日期、预计归还日期和出入库业务日期默认当天；系统同时保留不可修改的真实操作时间用于审计。

新建申请使用 `REQ-YYYYMMDD-000001` 编号，实际出入库使用 `IO-YYYYMMDD-000001` 编号。同一申请的所有出入库明细共用一个出入库单号，出入库单详情基础信息中会显示“关联申请单”。历史申请号不会被改写。

一张申请或快速出入库单可以同时包含多个单件资产和耗材配件。单件资产每条固定为 1 件，耗材配件每条独立填写数量。耗材借用执行后会形成申请人的尚未归还余额；普通员工只能申请归还本人实际借用的余额，领用和出售不会形成可归还余额。

新增页面：

```text
http://192.168.61.169/transactions/
```

## 盘点任务

盘点功能使用以下 Web 地址：

```text
http://192.168.61.169/inventory-checks/
```

系统管理员在页面创建任务并选择仓库或库位，任务会冻结创建时的账面快照。仓库人员开始任务后，可使用扫码枪输入、手工输入资产编码、原厂 SN、二维码内容或物料编码；提交后由管理员复核差异。

本次新增的数据库迁移会由部署脚本中的 `manage.py migrate --noinput` 自动执行。更新已有 NUC 时只需要同步整个项目目录并重新执行部署命令，不要再次加 `--import-sqlite`，避免重复导入旧 SQLite 数据：

```bash
cd ~/Cloudwarehouse
sudo bash deploy/ubuntu22/install.sh --host 192.168.61.169
```

盘点调整不会直接改写普通出入库流水，而会生成独立的“盘点差异调整台账”和操作审计记录。备份脚本会随 PostgreSQL 数据库一起备份这些记录。

任务详情页右上角的“导出 Excel”会生成四个工作表：任务摘要、盘点明细、扫描记录和差异调整。已完成任务仍然可以导出，但不允许再修改；待复核任务需要管理员填写退回原因后才能重盘。

## 备份与恢复

每日备份位于 `/srv/after-sales-warehouse/backups/`，保留 30 天，包含：

- `database.dump`：PostgreSQL 可恢复备份。
- `media.tar.gz`：资产照片等上传文件。
- `SHA256SUMS`：备份完整性校验。

手动执行备份：

```bash
sudo /usr/local/sbin/after-sales-warehouse-backup
```

先在独立测试库执行恢复演练，脚本不会修改生产数据库：

```bash
sudo /usr/local/sbin/after-sales-warehouse-restore-drill
```

只有恢复演练通过、已确认备份目录且生产库确实损坏时，才进入正式恢复维护窗口：

```bash
sudo systemctl stop after-sales-warehouse
sudo -u postgres dropdb warehouse
sudo -u postgres createdb -O warehouse_app warehouse
sudo -u postgres pg_restore -d warehouse /srv/after-sales-warehouse/backups/YYYY-MM-DD_HHMMSS/database.dump
sudo tar -C /srv/after-sales-warehouse -xzf /srv/after-sales-warehouse/backups/YYYY-MM-DD_HHMMSS/media.tar.gz
sudo systemctl start after-sales-warehouse
```

本地备份不能防范 NUC 损坏、被盗或误格式化。每周至少将备份目录同步到另一台 NAS 或受控服务器；钉钉只发送备份失败告警，不承载数据库备份文件。

备份和健康检查状态：

```bash
sudo systemctl list-timers after-sales-warehouse-backup.timer after-sales-warehouse-health.timer --no-pager
sudo systemctl start after-sales-warehouse-backup.service
sudo /usr/local/sbin/after-sales-warehouse-health
sudo journalctl -u after-sales-warehouse-backup.service -n 100 --no-pager
sudo journalctl -u after-sales-warehouse-health.service -n 100 --no-pager
```

健康检查覆盖 PostgreSQL、Gunicorn、Nginx、80/8000/5432 监听、本机健康页、磁盘、内存和已启用的 Tunnel。故障与恢复各通知一次，避免每五分钟重复刷屏。运维通知复用 `/etc/after-sales-warehouse/quick-tunnel-dingtalk.env`，不把 Webhook 写入脚本。

仅在人工排查核心内网服务、明确暂不检查临时域名时，可执行 `sudo CHECK_TUNNEL=0 /usr/local/sbin/after-sales-warehouse-health`。systemd 定时任务默认仍会检查已启用的 Tunnel。

## 离线出入库与业务基础资料

- 断网前需至少打开过直接办理页面并选好物品；断网提交后记录只保存在当前浏览器 IndexedDB。
- 联网后同一客户端 UUID 幂等同步到 `/offline/operations/`，进入待复核而非直接扣库存。
- 复核时使用服务器当前资产状态、预约和耗材数量重新校验，冲突不会生成出入库流水。
- 清除浏览器数据会删除尚未同步的本机记录，不能把 PWA 本地队列当作正式备份。
- 系统管理员从“仓库管理 -> 业务基础资料”维护客户、项目、工单、供应商和维修服务商；旧文本字段继续兼容已有数据。

## 网络与安全

- 使用 UFW 时只开放内网网段的 `80` 端口；启用 HTTPS 后再开放 `443`，例如：`sudo ufw allow from 192.168.10.0/24 to any port 80`。
- 不开放 PostgreSQL 的 `5432` 端口。
- 不要将 NUC、路由器端口映射或系统后台直接暴露到公网。
- 将 `/opt/after-sales-warehouse/backend/.env` 纳入运维备份，但不要提交到 Git。
- 生产环境的 `DEBUG` 必须保持为 `0`。
- 配置内网 HTTPS 证书后，将 `.env` 中的 `USE_HTTPS=1`。确认所有客户端可通过 HTTPS 访问一段时间后，再把 `SECURE_HSTS_SECONDS` 设为 `31536000`；启用 HSTS 前必须由运维确认，避免客户端被永久锁定到错误域名或证书。

## 钉钉通知

系统站内通知始终可用。要把新申请、审核结果和处理完成同步到钉钉，在后台 `.env` 增加钉钉群机器人的 Webhook；机器人开启“加签”时同时填写密钥：

```text
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=...
DINGTALK_SECRET=SEC...
```

修改后执行 `sudo systemctl restart after-sales-warehouse`。这是仓库管理员群的事件通知，不会泄露数量库存给普通员工。

若需要一对一通知，钉钉管理员需创建“企业内部应用”，启用工作通知权限，并将凭据写入后台 `.env`：

```text
DINGTALK_APP_KEY=应用的 AppKey
DINGTALK_APP_SECRET=应用的 AppSecret
DINGTALK_AGENT_ID=应用的 AgentId
```

然后在 Web 的“账号与权限”页面，为每个系统账号填写钉钉企业通讯录中的 `userid`。配置完整且账号已绑定时，申请、审批、预约和归还提醒优先发送个人工作通知；未配置、未绑定或发送失败时，站内通知仍会保留，并回退到群机器人。系统不会把 Access Token 写入数据库，只在应用进程内短期缓存。

### 安全库存钉钉预警

业务机器人同时接收安全库存预警。耗材配件按“实存减有效预占”后的可申请数量判断；单件资产按物品类型汇总“在库且未被预约”的数量判断。系统只在进入低库存和恢复正常时各通知一次，并向管理员及报表人员写入站内通知。

每周一 `09:00` 由 `after-sales-warehouse-low-stock-summary.timer` 汇总仍未恢复的低库存项目。更新已部署的 NUC 后可手动完成首次同步和测试：

```bash
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python \
  /opt/after-sales-warehouse/backend/manage.py send_low_stock_summary
sudo systemctl list-timers after-sales-warehouse-low-stock-summary.timer --no-pager
```

### 数据一致性自动巡检

安装脚本会安装并启用 `after-sales-warehouse-consistency.timer`，默认每日 `03:00`
执行只读一致性检查。首次发现问题、问题内容变化和全部恢复会发送钉钉群机器人通知，
相同问题不会重复通知；发送失败不会更新状态，下一次运行会重试。

```bash
sudo systemctl list-timers after-sales-warehouse-consistency.timer --no-pager
sudo systemctl status after-sales-warehouse-consistency.service --no-pager -l
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python \
  /opt/after-sales-warehouse/backend/manage.py check_data_consistency --notify
```

告警状态文件默认为 `/srv/after-sales-warehouse/consistency-check.state.json`，只保存问题
指纹和当前是否存在问题，不保存库存明细；检查不会自动修复业务数据。

即时通知和每周汇总复用业务机器人配置，不使用临时域名专用机器人。

### 借用归还提醒

`after-sales-warehouse-return-reminder.timer` 每天 `09:10` 检查三天内到期、当天到期及已逾期的资产和耗材借用。借用人收到可跳转原申请单的站内通知，仓库人员收到站内汇总，钉钉业务机器人收到群汇总；同一天重复执行不会重复发送。

```bash
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python \
  /opt/after-sales-warehouse/backend/manage.py send_return_due_reminders
sudo systemctl list-timers after-sales-warehouse-return-reminder.timer --no-pager
```

### 预约有效期与自动释放

待审批预约默认有效 72 小时，可在 `.env` 调整：

```text
RESERVATION_EXPIRY_HOURS=72
```

`after-sales-warehouse-reservation-expiry.timer` 每小时检查一次，只会关闭仍处于“待审批”的超时申请。已经审批通过、等待仓库执行的预约不会静默释放。审批人员也可在 `/reservations/` 页面延长预约，或填写原因后关闭整张申请并释放全部预约。

```bash
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python \
  /opt/after-sales-warehouse/backend/manage.py expire_inventory_reservations
sudo systemctl list-timers after-sales-warehouse-reservation-expiry.timer --no-pager
```

## 二维码与打印

仓库管理页的“资产台账”可勾选一个或多个资产并点击“打印所选二维码”。系统会生成含资产名称、系统资产编码和原厂 SN 的标签页；点击“打印标签”后在浏览器中选择普通打印机或标签打印机。

每个资产新建时会自动得到唯一系统二维码内容，默认等于系统资产编码；因此不需要手动填写二维码文本后才能打印。日常在“仓库管理”中点击资产的“查看 / 编辑”，页面顶部也会显示该资产二维码和扫码内容。扫码枪可将内容直接输入系统顶部查询或“资产检索”页面。

申请和快速出入库页面同时支持模糊搜索、扫码枪精确输入和手机摄像头连续扫码。摄像头接口要求 HTTPS 或 localhost；通过 Cloudflare 临时 HTTPS 地址测试时，外部手机不需要安装 `cloudflared`。

## 零成本临时远程访问

如果公司网络允许 NUC 发起外部 HTTPS 连接，但不允许 Tailscale 或 ZeroTier，可以使用 Cloudflare Quick Tunnel 进行移动 Web 测试。它不需要公网 IP、路由器端口映射或域名；但每次 Tunnel 重启后地址可能变化，只适合测试，不适合作为正式生产入口，也不能作为微信小程序的固定合法请求域名。

在项目根目录执行：

```bash
sudo bash deploy/ubuntu22/enable-quick-tunnel.sh
```

脚本会：

- 安装 Cloudflare 官方发布的 `cloudflared`；
- 创建独立的 `cloudflared` 系统用户；
- 创建并启用 `after-sales-warehouse-quick-tunnel.service`；
- 将外部 HTTPS 请求转发到本机 Nginx 的 `127.0.0.1:80`；
- 自动允许 `trycloudflare.com` 临时域名通过 Django 的 CSRF 来源校验。

查看当前临时访问地址：

```bash
sudo /usr/local/sbin/after-sales-warehouse-quick-tunnel-url
```

查看状态和日志：

```bash
sudo systemctl status after-sales-warehouse-quick-tunnel --no-pager
sudo journalctl -u after-sales-warehouse-quick-tunnel -f
```

Quick Tunnel 只改变访问入口，不改变 PostgreSQL 数据库、资产照片或附件目录。NUC 重启后服务会自动尝试恢复，但临时地址可能变化。地址变化后需要更新手机收藏夹或桌面快捷方式；不要把该地址写入正式小程序配置或永久二维码。

临时公网入口仍然必须使用系统账号登录和业务权限控制。真实库存数据上线前，应改用公司批准的 VPN 或固定域名、Named Tunnel，并配置额外的访问控制。
