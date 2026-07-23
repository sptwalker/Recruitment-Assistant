#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$ROOT/deploy/.env}"

cat > "$TARGET" <<'EOF'
APP_ENV=production
DATABASE_URL=
PLAYWRIGHT_HEADLESS=false
CRAWLER_MIN_INTERVAL_SECONDS=8
CRAWLER_MAX_INTERVAL_SECONDS=30
CRAWLER_MAX_RESUMES_PER_TASK=50
EXPORT_DIR=data/exports
ATTACHMENT_DIR=data/attachments
BROWSER_STATE_DIR=data/browser_state
SNAPSHOT_DIR=data/snapshots
LOG_LEVEL=INFO
FRONTEND_ORIGIN=http://localhost
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_REDIRECT_URI=http://localhost/auth/feishu/callback
JWT_SECRET=change-me-to-a-random-32+-char-secret
EOF

echo "Wrote template: $TARGET"
