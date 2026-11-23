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

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
elif [[ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]]; then
  PY="$REPO_ROOT/.venv/Scripts/python.exe"
else
  PY="$(command -v python3 || command -v python)"
fi

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

step "Bandit SAST"
if "$PY" -m bandit --version >/dev/null 2>&1; then
  if "$PY" -m bandit -q -r "$BACKEND/apps" "$BACKEND/warehouse_backend"; then
    ok "Bandit 无阻断项"
  else
    bad "Bandit 发现阻断项"
  fi
elif [[ "$ALLOW_MISSING_SECURITY_TOOLS" == "1" ]]; then
  printf '  \033[33m!\033[0m Bandit 未安装，已按显式参数豁免\n'
else
  bad "Bandit 未安装，无法完成安全门禁"
fi

step "pip-audit 依赖审计"
if "$PY" -m pip_audit --version >/dev/null 2>&1; then
  if "$PY" -m pip_audit; then
    ok "pip-audit 未发现已知漏洞"
  else
    bad "pip-audit 发现依赖漏洞"
  fi
elif [[ "$ALLOW_MISSING_SECURITY_TOOLS" == "1" ]]; then
  printf '  \033[33m!\033[0m pip-audit 未安装，已按显式参数豁免\n'
else
  bad "pip-audit 未安装，无法完成安全门禁"
fi

step "迁移漂移"
if (cd "$BACKEND" && "$PY" manage.py makemigrations --check --dry-run >/dev/null 2>&1); then
  ok "无未生成的迁移"
else
  bad "有模型改动未生成迁移"
fi

step "依赖一致性"
if "$PY" -m pip check >/dev/null 2>&1; then
  ok "pip check 无冲突"
else
  bad "存在依赖冲突"
fi

# 锁文件一致性（2026-11-23 新增）：pip check 只查已装包之间是否冲突，
# 查不出「requirements.txt 改了范围却没重生 lock」这类漂移——
# 那种情况本机能跑、服务器却装到不同版本。
step "依赖锁一致性"
if "$PY" "$BACKEND/scripts/check_requirements_consistency.py" >/dev/null 2>&1; then
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
else
  printf '\033[31m存在失败项，请修复后再提交。\033[0m\n'
fi
exit "$fail"
