# 资产标签打印客户端部署说明

当前状态（2026-11-16）：本地源码已完成 ZT610 RAW ZPL 主通道和 worker 生命周期修复，打印客户端专项 `11/11` 通过；尚未构建新的候选 EXE，也未完成 ZT610 真机验收。下面列出的 2026-11-06 EXE 仅作历史追溯，不是当前推荐版本。

历史验证构建：`BwtLabelClient.exe`，32,395,359 字节，SHA-256
`8d436201ccba1b73aed4c8ac246e6aa90b6d6891ea88530bf19cf1dd5e19f488`。

## 方案说明

服务器**不直接连接打印机**，也不向打印机电脑开放入站端口。流程是：

```
仓管在网页勾选资产
        ↓ 创建打印作业（待打印）
仓库服务器（Ubuntu）
        ↓ 客户端每 5 秒来问一次
贴纸打印电脑（Windows）→ 出纸 → 回报结果
```

这样做的好处：

- **服务器不需要装打印机驱动**，换打印机不影响服务器。
- **不需要端口映射或共享打印机的安全例外**。
- **打印机电脑关机或断网不会丢作业**，作业留在服务器上，开机后自动继续。
- 客户端只持有自己的 API Token，权限仅限打印作业。

## 当前实际网络拓扑（2026-11-04）

```
服务器 192.168.23.41  ─┐
本机 Win11 192.168.22.192 ─┼── 同一 WiFi（192.168.20.0/22）
打印电脑 Win11 192.168.20.235 ─┤
仓管电脑 ─┘
```

四台设备都在 `192.168.20.0/22` 这一个网段内（掩码 /22 覆盖 192.168.20.0 ~ 192.168.23.255），**直接互通，无需任何额外配置**。

### 连接方向是单向的

```
打印电脑  ──── 主动 HTTP 请求 ───→  服务器
          ←──── 只在响应里返回数据 ─────
```

服务器**从不主动连接打印电脑**，也不开任何入站端口。只要打印电脑能访问服务器，客户端就能工作。

### 服务器可用地址

| 网卡 | IP | 说明 |
| --- | --- | --- |
| `wlp89s0`（WiFi） | **`192.168.23.41`** | **当前推荐用这个**，与打印电脑同网段 |
| `enp88s0`（有线） | `192.168.123.101` | 另一网段 |
| `enp87s0`（有线） | `192.168.25.119` | 另一网段 |

> 服务器 WiFi 走 DHCP，IP 可能变化。已部署自动登记脚本，
> 新 IP 会在 2 分钟内自动写进白名单，不用人工改配置。

### 第一步：先测通不通

在打印电脑上打开浏览器访问：

```
http://192.168.23.41/
```

能打开登录页就说明通了。

也可以用命令测试（不依赖浏览器）：

```bat
BwtLabelClient.exe --check --server http://192.168.23.41
```

正常会打印 `目标地址: http://192.168.23.41` 和 `[OK] 连接正常（HTTP 200）`。

### 常见报错对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `getaddrinfo failed` / 无法解析 | 用了域名但 DNS 解析不了 | 改用 IP |
| `WinError 10061` 连接被拒绝 | 服务器没开或 nginx 没起 | 在服务器上 `systemctl status nginx` |
| 连接超时 | 不在同一网段或被防火墙拦 | 确认四台设备同一 WiFi；`ping 192.168.23.41` |
| HTTP **400** Bad Request | 该 IP 不在 `ALLOWED_HOSTS` | 在服务器跑 `auto-register-host.sh` |
| HTTP **401** | 令牌无效 | 检查是否误用 `drf_create_token`，见第三节 |

### 第二步：如果要用域名

如果公司内网有 DNS，能把这个服务器解析成一个域名（例如 `wms.bwt.local`），
那么客户端**直接填域名即可**，不需要改任何代码：

```
服务器地址： wms.bwt.local
或：        wms.bwt.local:8000
或：        http://wms.bwt.local
```

带不带 `http://`、带不带端口、末尾有没有斜杠，都能识别。

**但域名必须在服务器上登记过白名单**，否则 Django 会返回 400。
在服务器上执行一次即可：

```bash
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local
```

这个脚本会同时处理三件事：
- 把域名加进 `ALLOWED_HOSTS`（Django 只接受名单内的 Host）
- 加进 `CSRF_TRUSTED_ORIGINS`（网页表单提交需要）
- 更新 nginx 的 `server_name`

移除用 `--remove`：

```bash
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local --remove
```

> 域名解析（DNS 或 hosts）需要网络管理员配合，本脚本不改 DNS。

### 第三步：跨网段时怎么办（当前用不到）

> **当前四台设备都在同一 WiFi 的 /22 网段内，本节不需要执行。**
> 保留此节仅备将来网段调整时参考。

按公司规定，**不使用任何内网穿透方案**（ZeroTier、Tailscale、Cloudflare Tunnel 等一律不用）。
如果确实跨了网段导致不通，可选：

1. **让网络管理员开通一条内网路由**，让打印电脑所在网段能访问服务器网段。
   这是最干净的做法，也不违反安全规定。
2. **在打印电脑上配一条静态路由**（需要管理员权限）：

   ```bat
   :: 查看当前网关
   ipconfig

   :: 假设打印电脑在 192.168.20.x，网关 192.168.20.1
   :: 让 192.168.23.0/24 的流量走该网关
   route add 192.168.23.0 mask 255.255.255.0 192.168.20.1 -p
   ```

   `-p` 表示永久生效。这条命令需要管理员身份运行。
3. **改用浏览器打印**（见文末），完全不依赖网络配置。

## 一、准备工作（Windows 那台机器）

1. 确认贴纸打印机装好驱动，并且**能正常打印测试页**。
2. 记下打印机名称：`控制面板 → 设备和打印机`，右键 → 属性，看顶部名称。
3. 安装 Python 3.10 或以上（<https://www.python.org/downloads/>），安装时勾选 **Add Python to PATH**。

## 二、部署客户端

把 `tools/label_printer_client` 整个文件夹拷到打印电脑，例如 `C:\label-client`：

```bat
cd C:\label-client
pip install -r requirements.txt
```

## 三、获取 API Token

> ⚠️ **不要用 `drf_create_token`**。系统用的是自定义的 `ExpiringTokenAuthentication`，
> 认证查的是 `ApiAccessToken` 表，而 `drf_create_token` 写的是 DRF 默认的 `authtoken_token` 表。
> 用它生成的令牌调接口会一直返回 **401 认证令牌无效**。

### 1. 建一个打印机专用账号

只给「标签打印」权限，不要用管理员账号：

```bash
cd /opt/after-sales-warehouse/backend
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python manage.py shell <<'PY'
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
user, created = User.objects.get_or_create(
    username='label-printer',
    defaults={'is_active': True, 'is_staff': False, 'is_superuser': False},
)
if created:
    user.set_unusable_password()   # 只走令牌，不设密码
    user.save()
group = Group.objects.filter(name='warehouse_label_print').first()
if group:
    user.groups.add(group)
print('账号就绪:', user.username, '权限组:', list(user.groups.values_list('name', flat=True)))
PY
```

### 2. 签发长期令牌

打印机固定部署，用长期令牌（`ttl_seconds=0`，`expires_at` 为空，永不自然过期）：

```bash
cd /opt/after-sales-warehouse/backend
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python manage.py shell <<'PY'
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.common.token_services import issue_token
from apps.common.models import ApiAccessToken

user = get_user_model().objects.get(username='label-printer')

# 撤销旧令牌，避免残留
for old in ApiAccessToken.objects.filter(user=user, revoked_at__isnull=True):
    old.revoked_at = timezone.now()
    old.save(update_fields=['revoked_at', 'updated_at'])

token, raw = issue_token(user, ttl_seconds=0)
print('API Token:', raw)
print(' expires_at:', token.expires_at, '(None = 长期)')
PY
```

**令牌原文只显示这一次**，数据库只存 SHA-256 哈希。抄下来填进客户端。

### 3. 验证令牌

```bash
curl -s -X POST http://192.168.23.41/api/inventory/label-jobs/claim/ \
  -H "Authorization: Token <令牌>" \
  -H "Content-Type: application/json" \
  -d '{"client_name":"probe"}'
```

- 返回 `{"job": null}` + HTTP 200 → 正常（当前没有待打印作业）
- 返回 401 → 令牌无效，检查是不是误用了 `drf_create_token`
- 返回 400 → `ALLOWED_HOSTS` 里没有你访问的那个地址

### 关于令牌有效期

| 类型 | `ttl_seconds` | `expires_at` | 适用场景 |
| --- | --- | --- | --- |
| 普通令牌 | 不传（默认 30 天） | 具体时间 | 临时调试、人工操作 |
| **长期令牌** | `0` | `None` | **固定部署的打印机，推荐** |

长期令牌只能通过撤销或轮换失效。系统有定时清理任务会删除过期令牌，
已确保不会误删长期令牌（`expires_at__isnull=False` 过滤），并有测试覆盖。

## 四、首次运行（推荐用图形界面）

```bat
python label_client.py --gui
```

界面里填三项就能用：

| 字段 | 填什么 |
| --- | --- |
| 服务器地址 | IP 或域名，例如 `192.168.123.101` 或 `wms.bwt.local`（详见第九节） |
| API Token | 上一步生成的 Token |
| 打印机 | 从下拉框选（列表来自系统已装打印机） |
| DPI | ZT610 选 **600**；其他机器先点“测试打印”看效果 |
| 版式 | `2x2` = 一张 60×40mm 出 4 个 30×20mm 小标签 |
| 打印通道 | ZT610 选 `ZPL`；非 ZPL 设备或排障才选 `GDI` |

点 **测试连接** 先确认网络通，再点 **保存并启动**。
配置会存到 `%APPDATA%\BwtWarehouse\label_client.json`，下次双击直接用。

先点 **预览标签**确认内容，再点 **打印校准页**：输出固定 60×40 mm 外框和 30×20 mm 四格中线，用直尺确认纸张尺寸和偏移。软件预览或 ZPL 测试通过不等于真实走纸通过。
确认外框后点 **四格测试打印**，四个格子都会打印独立测试标签，不消耗正式作业。

### 命令行方式（可选）

```bat
python label_client.py --server 192.168.123.101 --token <TOKEN> --printer "Zebra ZT610" --dpi 600 --once
```

- 提示“没有待打印作业”= 连接正常。
- 去网页勾选资产并发送到打印机，再跑一次就会出纸。

其它参数：

| 参数 | 作用 |
| --- | --- |
| `--check` | 只检查服务器连通性，不动打印机 |
| `--list-printers` | 列出系统所有打印机名称 |
| `--detect-dpi` | 尝试从驱动读取实际 DPI |
| `--once` | 只跑一轮，方便排查 |
| `--gui` | 打开图形界面 |

## 五、设置为开机自启

新建 `start-label-client.bat`：

```bat
@echo off
cd /d C:\label-client
start "" pythonw label_client.py --gui
```

然后：

1. 按 `Win + R`，输入 `shell:startup`，回车。
2. 把 `start-label-client.bat` 的快捷方式放进这个文件夹。

> 用 `pythonw` 而不是 `python`，这样不会留一个黑色命令行窗口。

## 六、调整版式

**优先在图形界面里改**，不用动代码。配置文件字段说明：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `dpi` | 600 | 打印机分辨率，最关键 |
| `sheet_width_mm` | 60 | 整张纸宽 |
| `sheet_height_mm` | 40 | 整张纸高 |
| `columns` | 2 | 横向几个小标签 |
| `rows` | 2 | 纵向几个小标签 |
| `gap_mm` | 0 | 小标签之间留缝 |
| `margin_mm` | 0 | 整页四周留白 |

60×40 切成 4 联 → `columns=2, rows=2`，每格 30×20mm。
如果要打整张 60×40 单标签（不切），改成 `columns=1, rows=1`。

**DPI 算错会导致内容缩放不对**：
- 600 dpi → 60×40mm = 1417×945 px
- 203 dpi → 60×40mm = 480×320 px

校准顺序必须是：先确认整张 60×40 mm 物理尺寸，再确认中线分别位于 30 mm 和 20 mm，
最后使用 X/Y 偏移修正机械原点。四联标签按四个 30×20 mm 单元直接渲染，不是把完整 60×40 标签缩小四倍。

## 七、标签版式

每个 30×20mm 小标签的布局（前缀独占一行，值在下一行）：

```
八维通              ┌──────┐
                    │      │
宇树 A2 pro          │  QR  │
资产编码：           │      │
SC-A2-YS-0004       └──────┘
序列号：
A2-pro-10642
```

地址太长时会自动截断，不会溢出到相邻标签。

## 八、常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `未安装 pywin32` | 缺依赖 | `pip install pywin32` |
| 打印机下拉框是空的 | 同上，pywin32 缺失 | 装完 pywin32 重开界面 |
| 打印出来是空白 | 打印机不认位图 | 换驱动自带的中性纸张设置，或改用浏览器打印 |
| 内容偏小/偏移 | DPI 与实际不符 | 界面里改 DPI，或跑 `--detect-dpi` |
| 4 个标签挤在一起 | 版式没设 2x2 | 界面里把版式改成 `2x2` |
| 中文变方块 | 字体缺失 | 确认 `C:\Windows\Fonts\msyh.ttc` 存在 |
| 作业一直“已领取” | 客户端上报失败 | 查 `%APPDATA%\BwtWarehouse\label_client.log` |
| 连不上服务器 | 网络不通 | 浏览器访问服务器地址试；参考「当前实际网络拓扑」一节 |
| **闪退 + `'gbk' codec can't encode character`** | **旧版 exe 在中文 Windows 控制台下打印 `✓`/`✗` 崩溃** | **用 2026-11-04 之后重新打包的 exe** |
| 双击没反应也不报错 | `--windowed` 无控制台 | 现在会弹窗显示异常；或命令行加 `--check` 看输出 |

### 关于 GBK 崩溃（已在 2026-11-04 修复）

打包成 exe 后 stdout 跟随系统代码页（cp936/GBK）。旧版代码在结果输出里用了 `✓`、`✗`，
这两个字符 GBK 无法表示，会抛 `UnicodeEncodeError` 让程序**直接退出**，表现为闪退。

现在的修复（`label_client.py`）：

1. 符号全部换成 `[OK]` / `[FAIL]`
2. 启动时 `_force_utf8_stdio()` 把 stdout/stderr 强制为 UTF-8
3. `safe_print()` 兜底，编码失败也不崩
4. 全局异常处理：未捕获异常写日志 + 弹窗，不再吐乱码 traceback

**判断手里 exe 是否是新版**：跑 `BwtLabelClient.exe --check --server http://192.168.23.41`，
看到中文正常显示、结尾是 `[OK]` 或 `[FAIL]` 就是新版。

## 九、服务器地址怎么填

支持 **IP** 和 **域名** 两种写法，下面这些都能直接识别，不用管格式：

| 你填的 | 实际请求的地址 |
| --- | --- |
| `192.168.23.41` | `http://192.168.23.41` |
| `192.168.23.41:8080` | `http://192.168.23.41:8080` |
| `wms.bwt.local` | `http://wms.bwt.local` |
| `wms.bwt.local:8000` | `http://wms.bwt.local:8000` |
| `https://wms.bwt.local` | `https://wms.bwt.local` |
| `192.168.23.41/` | `http://192.168.23.41`（自动去掉斜杠） |
| `wms.bwt.local/warehouse` | `http://wms.bwt.local`（只保留主机） |

界面上的 **测试连接** 按钮会直接告诉你结果：

- `[OK] 连接正常（HTTP 200）` — 可以直接用了
- `[FAIL] 服务器可达，但拒绝了域名 xxx` — 域名或 IP 没在服务器登记，去跑 `auto-register-host.sh`
- `[FAIL] 无法连接：timed out` — 网络不通，检查是否同一 WiFi
- `[FAIL] 认证失败` — Token 无效，见第三节

### 域名需要额外登记

Django 默认只接受 `ALLOWED_HOSTS` 里的主机名，所以域名要在服务器上登记一次：

```bash
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local
```

登记后浏览器访问和客户端访问都会正常。移除：

```bash
sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local --remove
```

### 关于内网穿透

**本项目不使用任何内网穿透方案。** 按公司网络安全规定，
ZeroTier、Tailscale、Cloudflare Tunnel、frp 等一律不部署，服务器上相关的组件已移除。

如果跨网段确实不通，正确做法是让网络管理员开通内网路由，而不是打隧道。

## 十、备用方案：浏览器打印

如果打印电脑不允许装任何程序，可以直接用浏览器：

在打印电脑打开标签页，按 `Ctrl + P`，选贴纸打印机，纸张设 60×40mm，缩放 100%，
边距选“无”，打印后把整张纸剪成 4 份。版式与客户端一致。
