#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER=warehouse
APP_DIR=/opt/after-sales-warehouse
BACKEND_DIR="$APP_DIR/backend"
ENV_FILE="$BACKEND_DIR/.env"
PYTHON="$APP_DIR/venv/bin/python"
PIP="$APP_DIR/venv/bin/pip"
ERRORS=0
WARNINGS=0

ok() { printf '[通过] %s\n' "$1"; }
warn() { printf '[提醒] %s\n' "$1"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[阻断] %s\n' "$1"; ERRORS=$((ERRORS + 1)); }
section() { printf '\n==== %s ====\n' "$1"; }

get_env() {
  local key="$1"
  [[ -r "$ENV_FILE" ]] || return 0
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

section '配置文件'
if [[ ! -f "$ENV_FILE" ]]; then
  fail "缺少配置文件：$ENV_FILE"
else
  ok "已找到 .env。"
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo unknown)"
  owner="$(stat -c '%U:%G' "$ENV_FILE" 2>/dev/null || echo unknown)"
  printf '.env 权限：%s %s\n' "$mode" "$owner"
  [[ "$mode" =~ ^6[04]0$|^4[04]0$ ]] && ok '.env 权限未对其他用户开放。' || warn '.env 建议权限为 640 或 600。'
fi

DEBUG_VALUE="$(get_env DEBUG || true)"
if [[ "$DEBUG_VALUE" == "0" || "$DEBUG_VALUE" == "False" || "$DEBUG_VALUE" == "false" ]]; then
  ok 'DEBUG 已关闭。'
else
  fail "DEBUG 当前为 '${DEBUG_VALUE:-未设置}'，生产/测试服务器应为 0。"
fi

SECRET_VALUE="$(get_env SECRET_KEY || true)"
if [[ -z "$SECRET_VALUE" ]]; then
  fail 'SECRET_KEY 未设置。'
elif [[ "$SECRET_VALUE" == *dev-only* || "$SECRET_VALUE" == *change-me* || ${#SECRET_VALUE} -lt 32 ]]; then
  fail 'SECRET_KEY 看起来是占位值或长度过短。'
else
  ok 'SECRET_KEY 已配置且不是明显占位值。'
fi

CORS_VALUE="$(get_env CORS_ALLOW_ALL_ORIGINS || true)"
if [[ "$CORS_VALUE" == "0" || "$CORS_VALUE" == "False" || "$CORS_VALUE" == "false" || -z "$CORS_VALUE" ]]; then
  ok 'CORS 未全量放开。'
else
  fail "CORS_ALLOW_ALL_ORIGINS 当前为 '$CORS_VALUE'，不建议在服务器上开启。"
fi

DB_ENGINE="$(get_env DB_ENGINE || true)"
if [[ "$DB_ENGINE" == "django.db.backends.postgresql" ]]; then
  ok '数据库使用 PostgreSQL。'
else
  fail "服务器数据库应使用 PostgreSQL，当前 DB_ENGINE='${DB_ENGINE:-未设置}'。"
fi

ALLOWED_HOSTS="$(get_env ALLOWED_HOSTS || true)"
if [[ -n "$ALLOWED_HOSTS" && "$ALLOWED_HOSTS" != "*" ]]; then
  ok "ALLOWED_HOSTS 已限制：$ALLOWED_HOSTS"
else
  warn 'ALLOWED_HOSTS 为空或为 *，建议只写内网 IP、域名、127.0.0.1 和 localhost。'
fi

section 'Django 与依赖'
if [[ ! -x "$PYTHON" ]]; then
  fail "未找到 Python 虚拟环境：$PYTHON"
else
  sudo -u "$APP_USER" "$PYTHON" "$BACKEND_DIR/manage.py" check && ok 'manage.py check 通过。' || fail 'manage.py check 失败。'
  deploy_output="$(mktemp)"
  if sudo -u "$APP_USER" "$PYTHON" "$BACKEND_DIR/manage.py" check --deploy >"$deploy_output" 2>&1; then
    ok 'manage.py check --deploy 无阻断。'
  else
    warn 'manage.py check --deploy 存在提醒；内网 HTTP 测试阶段可暂缓处理，公网前必须复核。'
  fi
  sed 's/^/  /' "$deploy_output"
  rm -f "$deploy_output"
fi

if [[ -x "$PIP" ]]; then
  "$PIP" check && ok 'pip check 通过。' || fail 'pip check 发现依赖冲突。'
  if "$PIP" show pip-audit >/dev/null 2>&1; then
    "$APP_DIR/venv/bin/pip-audit" || warn 'pip-audit 发现需要评估的依赖漏洞。'
  else
    warn '未安装 pip-audit；如需联网漏洞扫描，可后续单独安装执行。'
  fi
else
  fail "未找到 pip：$PIP"
fi

section '监听端口'
if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -ltnp 2>/dev/null || true)"
  printf '%s\n' "$listeners" | grep -E ':(80|443|5432|8000)[[:space:]]' || warn '未看到 80/443/5432/8000 监听信息。'
  if printf '%s\n' "$listeners" | grep -E '0\.0\.0\.0:5432|\[::\]:5432' >/dev/null; then
    fail 'PostgreSQL 正在对外监听 5432，请改为仅监听 127.0.0.1。'
  else
    ok 'PostgreSQL 未发现对外监听 5432。'
  fi
  if printf '%s\n' "$listeners" | grep -E '0\.0\.0\.0:8000|\[::\]:8000' >/dev/null; then
    fail 'Gunicorn 8000 不应直接对外监听，应只绑定 127.0.0.1。'
  else
    ok 'Gunicorn 未发现对外监听 8000。'
  fi
else
  warn '缺少 ss 命令，跳过端口监听检查。'
fi

section '服务与健康检查'
for service in postgresql nginx after-sales-warehouse; do
  if systemctl is-active --quiet "$service" 2>/dev/null; then
    ok "$service 正在运行。"
  else
    fail "$service 未运行。"
  fi
done

if curl --silent --show-error --fail --max-time 5 http://127.0.0.1/health/ >/dev/null; then
  ok '本机 /health/ 返回正常。'
else
  fail '本机 /health/ 检查失败。'
fi

section '项目目录扫描'
if find "$BACKEND_DIR" -name '*.pyc' -o -name '__pycache__' | grep -q .; then
  warn 'backend 中存在 pyc 或 __pycache__，不影响运行，但建议由部署包排除。'
else
  ok 'backend 未发现 pyc 或 __pycache__。'
fi
[[ -f "$BACKEND_DIR/db.sqlite3" ]] && fail '服务器 backend 中不应保留 db.sqlite3。' || ok '服务器 backend 未发现 db.sqlite3。'

section '结果'
if (( ERRORS > 0 )); then
  printf '安全巡检完成：%s 个阻断项，%s 个提醒项。\n' "$ERRORS" "$WARNINGS"
  exit 1
fi
printf '安全巡检完成：无阻断项，%s 个提醒项。\n' "$WARNINGS"
