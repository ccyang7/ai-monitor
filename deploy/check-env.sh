#!/usr/bin/env bash
# 部署前配置自检：把能提前发现的配置错误拦在 docker compose up 之前。
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env.production"
fail=0

error() {
  echo "[未通过] $1" >&2
  fail=1
}

if [ ! -f "$ENV_FILE" ]; then
  error "缺少项目根目录的 $ENV_FILE，先执行：cp deploy/.env.example $ENV_FILE"
else
  value() {
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -1
  }

  password="$(value POSTGRES_PASSWORD)"
  jwt="$(value JWT_SECRET_KEY)"
  debug="$(value DEBUG)"
  app_env="$(value APP_ENV)"

  [ -n "$password" ] || error "POSTGRES_PASSWORD 为空"
  if echo "$password" | grep -q 'CHANGE_ME'; then
    error "POSTGRES_PASSWORD 还是模板占位符"
  fi
  if [ "${#password}" -lt 16 ]; then
    error "POSTGRES_PASSWORD 少于 16 个字符"
  fi

  [ -n "$jwt" ] || error "JWT_SECRET_KEY 为空"
  if echo "$jwt" | grep -q 'CHANGE_ME'; then
    error "JWT_SECRET_KEY 还是模板占位符"
  fi
  if [ "$(printf %s "$jwt" | wc -c)" -lt 32 ]; then
    error "JWT_SECRET_KEY 少于 32 字节"
  fi

  if [ "$app_env" != "production" ]; then
    error "APP_ENV 必须是 production（当前：${app_env:-未设置}）"
  fi

  if [ "$debug" != "false" ]; then
    error "DEBUG 必须是 false（当前：${debug:-未设置}）"
  fi

  if grep -q '^DATABASE_URL=' "$ENV_FILE"; then
    error "生产 $ENV_FILE 不要写 DATABASE_URL，它由 docker-compose.yml 自动生成"
  fi
fi

if [ ! -f ai-monitor-frontend/dist/index.html ]; then
  error "缺少前端产物 ai-monitor-frontend/dist/index.html，现在本地 npm run build 再上传"
fi

if [ "$fail" -ne 0 ]; then
  echo >&2
  echo "配置检查未通过，先修正上面的项再启动。" >&2
  exit 1
fi

echo "配置检查通过，可以执行：./deploy/compose.sh up -d"
