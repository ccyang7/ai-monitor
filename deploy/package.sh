#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$(dirname "$PROJECT_ROOT")/ai-monitor-deploy.tar.gz"
SKIP_BUILD=0
FRESH=0
WITH_BACKUP=0

usage() {
  cat <<'EOF'
用法: ./deploy/package.sh [选项]

选项:
  --skip-build    跳过前端构建，直接使用现有 dist
  --fresh         强制 npm ci 后构建前端
  --with-backup   同时打包 deploy/backup.sh
  -h, --help      显示帮助
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --fresh) FRESH=1 ;;
    --with-backup) WITH_BACKUP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

if [ "$SKIP_BUILD" -eq 1 ] && [ "$FRESH" -eq 1 ]; then
  echo "错误：--skip-build 和 --fresh 不能同时使用" >&2
  exit 1
fi

cd "$PROJECT_ROOT"

if [ "$SKIP_BUILD" -eq 0 ]; then
  command -v npm >/dev/null 2>&1 || {
    echo "错误：未找到 npm，请先安装 Node.js" >&2
    exit 1
  }

  if [ "$FRESH" -eq 1 ] || [ ! -d ai-monitor-frontend/node_modules ]; then
    (cd ai-monitor-frontend && npm ci)
  fi
  (cd ai-monitor-frontend && npm run build)
fi

if [ ! -f ai-monitor-frontend/dist/index.html ]; then
  echo "错误：缺少 ai-monitor-frontend/dist/index.html，先构建前端" >&2
  exit 1
fi

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/ai-monitor-package.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/deploy"
mkdir -p "$STAGE/ai-monitor-backend/scripts"
mkdir -p "$STAGE/ai-monitor-backend/sql"
mkdir -p "$STAGE/ai-monitor-frontend"

cp .dockerignore docker-compose.yml "$STAGE/"
cp deploy/backend.Dockerfile \
   deploy/nginx.conf \
   deploy/compose.sh \
   deploy/check-env.sh \
   deploy/.env.example \
   "$STAGE/deploy/"

cp -a ai-monitor-backend/app "$STAGE/ai-monitor-backend/"
cp ai-monitor-backend/main.py \
   ai-monitor-backend/pyproject.toml \
   ai-monitor-backend/uv.lock \
   "$STAGE/ai-monitor-backend/"
cp ai-monitor-backend/scripts/init_models.py \
   ai-monitor-backend/scripts/init_admin.py \
   "$STAGE/ai-monitor-backend/scripts/"
cp ai-monitor-backend/sql/schema.sql "$STAGE/ai-monitor-backend/sql/"
cp -a ai-monitor-backend/sql/migrations "$STAGE/ai-monitor-backend/sql/"
cp -a ai-monitor-frontend/dist "$STAGE/ai-monitor-frontend/"

if [ "$WITH_BACKUP" -eq 1 ]; then
  cp deploy/backup.sh "$STAGE/deploy/"
fi

find "$STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} \;
find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

required=(
  .dockerignore
  docker-compose.yml
  deploy/.env.example
  deploy/backend.Dockerfile
  deploy/nginx.conf
  deploy/compose.sh
  deploy/check-env.sh
  ai-monitor-backend/main.py
  ai-monitor-backend/pyproject.toml
  ai-monitor-backend/uv.lock
  ai-monitor-backend/scripts/init_models.py
  ai-monitor-backend/scripts/init_admin.py
  ai-monitor-backend/sql/schema.sql
  ai-monitor-backend/sql/migrations/001_init.sql
  ai-monitor-frontend/dist/index.html
)

for path in "${required[@]}"; do
  if [ ! -f "$STAGE/$path" ]; then
    echo "错误：缺少 $path" >&2
    exit 1
  fi
done

if [ "$WITH_BACKUP" -eq 1 ] && [ ! -f "$STAGE/deploy/backup.sh" ]; then
  echo "错误：缺少 deploy/backup.sh" >&2
  exit 1
fi

tar -czf "$OUTPUT" -C "$STAGE" .

echo "打包完成：$OUTPUT"
du -h "$OUTPUT"
