#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.yml"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env}"
REMOTE="${REMOTE:-origin}"
BRANCH="${1:-}"
HTTP_PORT="${HTTP_PORT:-80}"

log() {
  printf '[deploy] %s\n' "$*"
}

die() {
  printf '[deploy] %s\n' "$*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    log "deployment failed; tailing recent logs"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs --tail=120 backend frontend db || true
  fi
  exit "$status"
}
trap cleanup EXIT

need git
need docker
need curl

[[ -d "$ROOT/.git" ]] || die "not a git repository: $ROOT"
[[ -f "$COMPOSE_FILE" ]] || die "missing compose file: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "missing env file: $ENV_FILE (create deploy/.env first)"

if [[ -z "$BRANCH" ]]; then
  BRANCH="$(git -C "$ROOT" branch --show-current)"
  [[ -n "$BRANCH" ]] || BRANCH="main"
fi

if [[ -n "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=no)" ]]; then
  git -C "$ROOT" status --short
  die "working tree has tracked changes; commit or stash before deploying"
fi

log "fetching ${REMOTE}/${BRANCH}"
git -C "$ROOT" fetch --prune "$REMOTE" "$BRANCH"

log "checking out ${BRANCH}"
git -C "$ROOT" checkout "$BRANCH"

log "fast-forwarding to ${REMOTE}/${BRANCH}"
git -C "$ROOT" pull --ff-only "$REMOTE" "$BRANCH"

log "building images"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --pull

log "starting stack"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --remove-orphans

log "waiting for backend health"
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz" >/dev/null && \
     curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz/db" >/dev/null; then
    break
  fi
  sleep 2
done

curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz" >/dev/null || die "health check failed: /healthz"
curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz/db" >/dev/null || die "health check failed: /healthz/db"

log "compose status"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps

log "deployment complete"
