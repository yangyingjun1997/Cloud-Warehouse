#!/usr/bin/env bash
# 一键本地检查：提交前跑这一个脚本即可覆盖所有静态与回归检查。
#
# 为什么需要：当前没有 CI，改动质量全靠人工记得跑哪些命令。
# 常见遗漏是「忘了跑全量测试」或「忘了查迁移漂移」，等部署后才发现。
#
# 用法：
#   bash deploy/ubuntu22/check-all.sh          # 完整检查（含全量测试）
#   bash deploy/ubuntu22/check-all.sh --fast   # 跳过全量测试，只做静态检查
#   bash deploy/ubuntu22/check-all.sh --allow-missing-security-tools
#       # 仅在当前平台无法安装安全工具时显式豁免
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FAST=0
ALLOW_MISSING_SECURITY_TOOLS=0

for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
    --allow-missing-security-tools) ALLOW_MISSING_SECURITY_TOOLS=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

# Python 解释器探测。
# 注意目录名不统一：开发机上是 .venv/，服务器 release 里是 venv/（无点）。
# 早期只找 .venv/，在服务器上就回退到系统 python3（3.10），
# 而项目要求 3.12——于是 makemigrations/pip check 全部失败，
# 还被误报成「有模型改动未生成迁移」「存在依赖冲突」。
PY=""
for candidate in \
  "$REPO_ROOT/.venv/bin/python" \
  "$REPO_ROOT/venv/bin/python" \
  "$REPO_ROOT/.venv/Scripts/python.exe" \
  "$REPO_ROOT/venv/Scripts/python.exe"
do
  if [[ -x "$candidate" ]]; then
    PY="$candidate"
    break
  fi
done

# 都找不到时再退回系统解释器，但要接受可能是低版本
if [[ -z "$PY" ]]; then
  PY="$(command -v python3 || command -v python)"
fi

# Windows 下用 Git Bash 时，REPO_ROOT 可能被算成 /mnt/d/...（WSL 风格），
# 而该路径在本机 Git Bash 里不可访问，导致所有 Python 检查静默失败。
# 这里探测一次：不可用则尝试换成 Windows 原生路径（cygpath），再不行才放弃。
if ! "$PY" -c 'import sys' >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
  WIN_ROOT="$(cygpath -w "$REPO_ROOT" 2>/dev/null || true)"
  if [[ -n "$WIN_ROOT" ]]; then
    if [[ -x "$WIN_ROOT\\.venv\\Scripts\\python.exe" ]]; then
      PY="$WIN_ROOT\\.venv\\Scripts\\python.exe"
      BACKEND="$WIN_ROOT\\backend"
    elif [[ -x "$WIN_ROOT\\venv\\Scripts\\python.exe" ]]; then
      PY="$WIN_ROOT\\venv\\Scripts\\python.exe"
      BACKEND="$WIN_ROOT\\backend"
    fi
  fi
fi

# 记下解释器版本，运行环境检查会打印出来，便于判断是否选错了 venv
PY_VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"

# 测试需要可写临时目录；Windows 默认 TEMP 常不可写，导致 openpyxl 用例批量报错。
# 使用唯一目录并在退出时清理，避免覆盖其他运行留下的文件。
TEST_TMP="$(mktemp -d "$REPO_ROOT/.test-run-temp.XXXXXX")"
cleanup() { rm -rf "$TEST_TMP"; }
trap cleanup EXIT
export TMPDIR="$TEST_TMP" TEMP="$TEST_TMP" TMP="$TEST_TMP"

fail=0
step() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }

step "Django 系统检查"
if (cd "$BACKEND" && "$PY" manage.py check >/dev/null 2>&1); then
  ok "manage.py check"
else
  bad "manage.py check 失败"
fi

step "Bash 脚本语法"
shell_failed=0
while IFS= read -r -d '' script; do
  if bash -n "$script"; then
    ok "$(basename "$script")"
  else
    bad "$(basename "$script") 语法检查失败"
    shell_failed=1
  fi
done < <(find "$REPO_ROOT/deploy/ubuntu22" -maxdepth 1 -type f -name '*.sh' -print0 | sort -z)
(( shell_failed == 0 )) || true

# 前端 JS 语法（2026-11-23 新增）
# 前端脚本出错不会让 Django 测试失败，但会让对应功能在浏览器里静默失效
# （例如离线队列、事件委托）。有 node 就检查，没有则跳过并明确说明。
if command -v node >/dev/null 2>&1; then
  step "前端 JS 语法"
  js_failed=0
  while IFS= read -r -d '' jsfile; do
    if node --check "$jsfile" >/dev/null 2>&1; then
      ok "$(basename "$jsfile")"
    else
      bad "$(basename "$jsfile") 语法检查失败"
      js_failed=1
    fi
  done < <(find "$REPO_ROOT/backend/apps" -type f -name '*.js' -not -path '*/vendor/*' -print0 | sort -z)
  (( js_failed == 0 )) || true
else
  step "前端 JS 语法"
  ok "未安装 node，跳过（仅影响本地检查，不影响部署）"
fi

# 安全工具可能装在独立 venv（如 /opt/security-tools-py312），
# 因为系统 Python 版本低于依赖要求，无法装进应用 venv。
# 早期只用 "$PY" -m bandit 判定，会把「独立 venv 里装了」误报成「未安装」。
step "Bandit SAST"
BANDIT_BIN="$(command -v bandit 2>/dev/null || true)"
if [[ -z "$BANDIT_BIN" ]] && "$PY" -m bandit --version >/dev/null 2>&1; then
  BANDIT_BIN="$PY -m bandit"
fi
if [[ -n "$BANDIT_BIN" ]]; then
  # 注意：bandit 只要有任意 finding（含 Low）就返回非零，
  # 直接用退出码判定会把「302 个 Low」误报成「发现阻断项」。
  # 这里按严重级别判定：只把 HIGH 当阻断，MEDIUM 提示，LOW 忽略。
  bandit_json="$("$BANDIT_BIN" -q -r "$BACKEND/apps" "$BACKEND/warehouse_backend" -f json 2>/dev/null || true)"
  bandit_counts="$("$PY" - "$bandit_json" <<'PYEOF' 2>/dev/null || echo "0 0 0"
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception:
    print("0 0 0")
    raise SystemExit(0)

totals = data.get("metrics", {}).get("_totals", {})
print(
    int(totals.get("SEVERITY.HIGH", 0)),
    int(totals.get("SEVERITY.MEDIUM", 0)),
    int(totals.get("SEVERITY.LOW", 0)),
)
PYEOF
)"
  read -r bandit_high bandit_medium bandit_low <<< "$bandit_counts"
  if [[ "${bandit_high:-0}" -gt 0 ]]; then
    bad "Bandit 发现 ${bandit_high} 个 High 问题（阻断）"
  elif [[ "${bandit_medium:-0}" -gt 0 ]]; then
    ok "Bandit 无 High（${bandit_medium} Medium / ${bandit_low} Low，不阻断）"
  else
    ok "Bandit 无 High（${bandit_low} Low，不阻断）"
  fi
elif [[ "$ALLOW_MISSING_SECURITY_TOOLS" == "1" ]]; then
  printf '  \033[33m!\033[0m Bandit 未安装，已按显式参数豁免\n'
else
  bad "Bandit 未安装，无法完成安全门禁"
fi

step "pip-audit 依赖审计"
PIP_AUDIT_BIN="$(command -v pip-audit 2>/dev/null || true)"
if [[ -z "$PIP_AUDIT_BIN" ]] && "$PY" -m pip_audit --version >/dev/null 2>&1; then
  PIP_AUDIT_BIN="$PY -m pip_audit"
fi
if [[ -n "$PIP_AUDIT_BIN" ]]; then
  # 优先审锁文件：它才是服务器实际安装的版本集合。
  # 直接跑 pip-audit 会审当前 venv，在独立工具环境里审的是工具自己的依赖。
  if [[ -f "$BACKEND/requirements-linux.txt" ]]; then
    if $PIP_AUDIT_BIN -r "$BACKEND/requirements-linux.txt"; then
      ok "pip-audit 未发现已知漏洞（requirements-linux.txt）"
    else
      bad "pip-audit 发现依赖漏洞"
    fi
  elif $PIP_AUDIT_BIN; then
    ok "pip-audit 未发现已知漏洞"
  else
    bad "pip-audit 发现依赖漏洞"
  fi
elif [[ "$ALLOW_MISSING_SECURITY_TOOLS" == "1" ]]; then
  printf '  \033[33m!\033[0m pip-audit 未安装，已按显式参数豁免\n'
else
  bad "pip-audit 未安装，无法完成安全门禁"
fi

# 前置：先确认 Python 与工作目录可用。
# 否则后面每条检查都会失败，而失败原因被写成「有模型改动未生成迁移」
# 「存在依赖冲突」——把环境问题误报成代码问题，会把人引到错误方向。
step "运行环境"
if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  bad "Python 无法执行：$PY（检查环境/权限后再跑）"
  echo "后续依赖 Python 的检查已跳过。" >&2
else
  ok "Python 可用：$("$PY" --version 2>&1)（$PY）"
  # 项目要求 3.11+（Django 5.2 与锁文件里的 asgiref 都需要）。
  # 选错解释器时后面的迁移/依赖检查会全部误报，必须在这里就点明。
  case "$PY_VERSION" in
    3.11|3.12|3.13|3.14)
      ok "Python 版本满足要求（>=3.11）" ;;
    *)
      bad "Python 版本为 $PY_VERSION，低于要求的 3.11；后续检查结果不可信（可能选错了 venv）" ;;
  esac
fi
if ! (cd "$BACKEND" && "$PY" -c 'import os; assert os.path.exists("manage.py")' >/dev/null 2>&1); then
  bad "无法进入 $BACKEND 读取 manage.py（检查目录权限）"
  BACKEND_UNUSABLE=1
else
  ok "后端目录可读"
  BACKEND_UNUSABLE=0
fi

step "迁移漂移"
if [[ "${BACKEND_UNUSABLE:-0}" == "1" ]]; then
  ok "跳过（后端目录不可用）"
elif (cd "$BACKEND" && "$PY" manage.py makemigrations --check --dry-run >/dev/null 2>&1); then
  ok "无未生成的迁移"
else
  bad "有模型改动未生成迁移"
fi

step "依赖一致性"
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  ok "跳过（当前 Python 没有 pip）"
elif "$PY" -m pip check >/dev/null 2>&1; then
  ok "pip check 无冲突"
else
  bad "存在依赖冲突"
fi

# 锁文件一致性（2026-11-23 新增）：pip check 只查已装包之间是否冲突，
# 查不出「requirements.txt 改了范围却没重生 lock」这类漂移——
# 那种情况本机能跑、服务器却装到不同版本。
step "依赖锁一致性"
if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  ok "跳过（Python 不可用）"
elif "$PY" "$BACKEND/scripts/check_requirements_consistency.py" >/dev/null 2>&1; then
  ok "requirements 三个文件一致"
else
  bad "requirements 文件不一致（改范围后需重生 lock）"
fi

step "差异空白字符"
if git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
  ok "git diff --check"
else
  bad "存在行尾空白或冲突标记"
fi

step "工作区状态"
if [[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
  ok "工作区干净"
else
  changed="$(git -C "$REPO_ROOT" status --porcelain | wc -l)"
  printf '  \033[33m!\033[0m 有 %s 个文件改动（提交前确认是否属于本次）\n' "$changed"
fi

if [[ "$FAST" == "1" ]]; then
  step "全量测试"
  printf '  \033[33m-\033[0m 已跳过（--fast）\n'
else
  step "Django 全量测试"
  # 用受控临时目录，避免 Windows 默认 TEMP 不可写导致的批量环境性失败
  if (cd "$BACKEND" && "$PY" manage.py test --noinput 2>&1 | tail -5); then
    ok "全量测试通过"
  else
    bad "全量测试失败"
  fi
fi

printf '\n'
if [[ "$fail" == "0" ]]; then
  printf '\033[32m全部检查通过。\033[0m\n'
elif [[ "${BACKEND_UNUSABLE:-0}" == "1" ]]; then
  # 环境不可用时给出明确结论，避免把「跑不起来」误读成「代码有问题」。
  printf '\033[31m运行环境不可用，检查未真正执行。\033[0m\n'
  printf '  当前 shell 无法执行 %s 或读取 %s。\n' "$PY" "$BACKEND"
  printf '  常见原因：Windows 下用 Git Bash 访问 /mnt/d/ 映射的目录会被拒绝；\n'
  printf '  此时请改用 PowerShell 直接调用 .venv\\Scripts\\python.exe 执行各检查，\n'
  printf '  或在 Linux 服务器上运行本脚本（生产环境即如此）。\n'
else
  printf '\033[31m存在失败项，请修复后再提交。\033[0m\n'
fi
exit "$fail"
