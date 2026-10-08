# NUC 服务器部署方案

## 部署结论

使用 Ubuntu Server 22.04 NUC 作为公司 Wi-Fi 内网服务器可行。本项目采用实体机直接部署，不使用 Docker：

```text
公司内网用户 -> Nginx:80/443 -> Gunicorn:127.0.0.1:8000 -> PostgreSQL:127.0.0.1:5432
```

只有 Nginx 面向公司内网开放；Gunicorn 和 PostgreSQL 不对局域网开放。

## 推荐资源

| 项目 | 最低 | 推荐 |
| --- | --- | --- |
| CPU | 4 核 | 6 核以上 |
| 内存 | 4 GB | 8 GB 以上 |
| 存储 | 64 GB SSD | 256 GB SSD 以上 |
| 网络 | 固定内网 IPv4 | 有线网络优先，Wi-Fi 作为备选 |

约 500 名员工、30 名售后部门成员的日常仓储工作负载，对这类 NUC 没有性能压力。重点是电源、网络稳定性和备份，而不是容器化。

## 上线前条件

1. 给 NUC 设置固定 DHCP 租约或静态 IPv4，例如 `192.168.10.50`。
2. 公司网络允许同一 Wi-Fi 或网段访问该 IP 的 HTTP/HTTPS 服务。
3. 为 NUC 配置自动开机、BIOS 断电恢复和稳定电源；条件允许时使用 UPS。
4. 准备公司内网 HTTPS 证书。纯 HTTP 可用于首次内部验收，但手机浏览器调用摄像头扫码通常需要 HTTPS。
5. 备份不能只放在 NUC 本机。至少每天将 `/srv/after-sales-warehouse/backups/` 同步到 NAS、受控文件服务器或加密移动硬盘。

## 安装与运维

详细命令、数据迁移、Nginx、systemd、备份和故障恢复见 `Ubuntu22.04本机部署手册.md`。

先执行只读检测：

```bash
sudo bash deploy/ubuntu22/preflight.sh
```

检测会检查端口 `80`、`443`、`5432`、`8000` 是否已被其他程序占用。任何“阻断”项未处理前，都不要执行安装脚本。

首次部署命令：

```bash
sudo bash deploy/ubuntu22/install.sh --host 192.168.10.50 --import-sqlite
```

`--import-sqlite` 仅在将当前 Windows 验收数据迁入第一台 PostgreSQL 服务器时使用一次。后续升级不应重复导入。
