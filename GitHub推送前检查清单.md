# GitHub 推送前检查清单

> 建立日期：2026-10-08
> 用途：将本仓库推送至 GitHub 之前，逐项确认本清单全部通过，避免敏感信息泄露。
> 每次推送（尤其私有 → 公开转换）前都应重新过一遍本清单。

---

## 一、推送策略选择（先选定）

| 策略 | 说明 | 需要做的额外工作 |
| --- | --- | --- |
| **A. 私有仓库**（推荐先走） | 仅团队可见，作为异地备份与协作 | 只需完成第二节「阻断项」 |
| **B. 公开仓库**（开源） | 任何人可见 | 阻断项 + 第三节「公开追加项」全部完成 |

> 建议：先推私有仓库跑一段时间，确认无敏感残留后，再考虑转公开或镜像一份脱敏的公开仓库。

---

## 二、阻断项（无论私有/公开都必须完成）

### 2.1 硬编码凭据清理（已完成 ✅ 2026-10-08）

以下脚本的硬编码密码已全部改为读环境变量，运行前需先设置对应变量：

| 脚本 | 环境变量 | 状态 |
| --- | --- | --- |
| `tools/deploy_20261008.py` | `NUC_SSH_PASSWORD` | ✅ 已改 |
| `tools/deploy_to_nuc.py` | `NUC_SSH_PASSWORD` | ✅ 已改 |
| `tools/verify_deploy.py` | `NUC_SSH_PASSWORD` | ✅ 已改 |
| `tools/verify_db.py` | `NUC_SSH_PASSWORD` | ✅ 已改 |
| `tools/verify_composite.py` | `NUC_SSH_PASSWORD` | ✅ 已改 |
| `tools/ssh_probe.py` | `NUC_SSH_PASSWORD` | ✅ 已改 |
| `tools/page_smoke.py` | `QA_TEST_PASSWORD`（可选 `QA_TEST_USER` / `QA_BASE_URL`） | ✅ 已改 |
| `tools/ui_audit.py` | `QA_TEST_PASSWORD`（可选 `QA_TEST_USER` / `QA_BASE_URL`） | ✅ 已改 |
| `tools/ui_screenshots.py` | `QA_TEST_PASSWORD`（可选 `QA_TEST_USER` / `QA_BASE_URL`） | ✅ 已改 |

PowerShell 使用示例：

```powershell
$env:NUC_SSH_PASSWORD = "你的服务器密码"
.venv312\Scripts\python.exe tools\deploy_20261008.py
```

### 2.2 .gitignore 覆盖确认（已完成 ✅ 2026-10-08）

已排除：

- [x] `backend/db.sqlite3`（本地开发数据库）
- [x] `backend/backups/`（本地数据库备份，含真实业务数据）
- [x] `backend/media/`（上传的附件）
- [x] `backend/staticfiles/`（收集的静态文件）
- [x] `.env` / `.env.*`（环境变量与密钥）
- [x] `.venv*/` / `venv/`（虚拟环境）
- [x] `node_modules/` / `dist/`（前端依赖与构建产物）
- [x] `artifacts/`（工具截图/审计输出）
- [x] `.codebuddy/`（内部协作数据）
- [x] `__pycache__/` / `*.pyc`（Python 缓存）

### 2.3 推送前必做的验证命令

```powershell
# 1. 确认没有真实密码残留在代码中（应只搜到环境变量读取，无 '123123' / 'Qa@Test' 字面量）
Get-ChildItem -Recurse -File -Include *.py,*.sh,*.ps1 | Where-Object { $_.FullName -notmatch '\\(\.venv|node_modules|dist)\\' } | Select-String -Pattern "123123|Qa@Test"

# 2. 确认 git 将要提交的文件清单中没有敏感文件
git status --short

# 3. 确认 .env、sqlite、backups 不会进入暂存区（应无输出）
git add -A -n | Select-String -Pattern "\.env|sqlite3|backups|media/"

# 4. （强烈建议）用 gitleaks 做一次全仓库扫描
#    winget install gitleaks  或从 https://github.com/gitleaks/gitleaks 下载
gitleaks detect --source . --verbose
```

### 2.4 服务器侧收尾（与仓库无关，但必须做）

- [ ] **修改 NUC 服务器 `robot` 账号密码**：`123123` 已在历史脚本与多次对话中出现，属事实泄露，推仓库前后都应改掉。
  ```bash
  sudo passwd robot
  ```
- [ ] 新密码只通过环境变量 `NUC_SSH_PASSWORD` 传给脚本，不再写入任何文件。

---

## 三、公开仓库追加项（仅策略 B 需要）

### 3.1 内部网络信息脱敏

以下文档含内网 IP `192.168.61.169` / `192.168.25.119`，公开前需替换为占位符（如 `<SERVER_IP>`）：

| 文档 | IP 出现次数 |
| --- | --- |
| `Ubuntu22.04完整手动部署手册.md` | 9 |
| `Ubuntu22.04本机部署手册.md` | 5 |
| `NUC架构组成与运行清单.md` | 2 |
| `项目功能进度与待办.md` | 1 |

> `tools/` 下脚本中的 IP 是参数化的部署目标，可保留或一并参数化为 `NUC_HOST` 环境变量（公开仓库建议参数化）。

### 3.2 业务数据与人员信息脱敏

- [ ] 全文检索真实客户名、人名、手机号、邮箱，替换为示例值
- [ ] 检查 `docs/`、各 `*.md` 中的截图，确认无真实业务数据入镜
- [ ] `backend/apps/common/management/commands/prepare_qa_data.py` 中的 QA 账号初始密码改为从环境变量读取或明确标注"仅本地开发用"

### 3.3 开源必备文件

- [ ] `LICENSE`：选定许可证（MIT / Apache-2.0 / GPL-3.0 等），注意项目依赖的许可证兼容性
- [ ] `README.md` 补充：项目简介（英文摘要更佳）、功能截图、快速开始、技术栈说明
- [ ] `CONTRIBUTING.md`（可选）：贡献指南
- [ ] 仓库设置：关闭 Wiki/Projects（如不用）、配置 branch protection、启用 Dependabot 安全告警

### 3.4 历史提交检查（若已有提交历史）

本仓库首次 `git init`，无历史包袱。若未来从含敏感信息的仓库迁移，需用 `git filter-repo` 或 BFG 清理历史后再推。

---

## 四、推送操作步骤（通过全部检查后）

```powershell
cd "d:\shell_file\Cloud warehouse"

# 1. 初始化（首次）
git init
git add -A
git status --short   # 人工核对清单，确认无 .env / sqlite / backups

# 2. 首次提交
git commit -m "feat: 售后仓库管理系统初始版本"

# 3. 关联远程（地址替换成你自己的）
git remote add origin git@github.com:<你的账号>/<仓库名>.git
git branch -M main

# 4. 推送（需要你本机已配置 GitHub SSH key 或凭据）
git push -u origin main
```

> 推送权限说明：AI 助手不会替你执行 `git push`，最后一步需要你在本机完成 GitHub 授权后自行推送。

---

## 五、维护约定

- 每次新增工具脚本涉及服务器/账号，一律从环境变量读取，禁止写死。
- 每次推送前重跑第二节 2.3 的验证命令。
- 钉钉 Webhook / AppSecret / 数据库密码 / `SECRET_KEY` 只允许出现在服务器 `.env`（已被 gitignore），任何代码、文档中出现即视为泄露，立即吊销并更换。
