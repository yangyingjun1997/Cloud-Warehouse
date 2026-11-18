#!/usr/bin/env bash
# 一键本地检查：提交前跑这一个脚本即可覆盖所有静态与回归检查。
#
# 为什么需要：当前没有 CI，改动质量全靠人工记得跑哪些命令。
# 常见遗漏是「忘了跑全量测试」或「忘了查迁移漂移」，等部署后才发现。
#
# 用法：
#   bash deploy/ubuntu22/check-all.sh          # 完整检查（含全量测试）
#   bash deploy/ubuntu22/check-all.sh --fast   # 跳过全量测试，只做静态检查
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FAST=0

for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
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

# 测试需要可写临时目录；Windows 默认 TEMP 常不可写，导致 openpyxl 用例批量报错
TEST_TMP="$REPO_ROOT/.test-run-temp"
mkdir -p "$TEST_TMP"
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
