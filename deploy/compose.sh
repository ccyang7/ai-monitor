#!/usr/bin/env bash
# 所有 compose 命令统一走这里：直接敲 docker compose 会漏掉 --env-file，
# POSTGRES_PASSWORD 插值为空，compose 会拿着空密码建库。
set -euo pipefail

cd "$(dirname "$0")/.."
exec docker compose --env-file .env.production "$@"
