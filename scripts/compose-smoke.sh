#!/usr/bin/env bash
# Compose smoke test: build and start the whole stack with one `docker compose up`,
# then check that the backend health check passes against Postgres, that pgvector
# is installed, and that the frontend serves the page.
set -euo pipefail

cd "$(dirname "$0")/.."

# Port 0 lets Docker pick free host ports, so the test does not collide with
# anything else on a shared runner. The real ports are read back after `up`.
export BACKEND_PORT="${BACKEND_PORT:-0}"
export FRONTEND_PORT="${FRONTEND_PORT:-0}"
# Use a project of its own so the final `down --volumes` never touches a stack
# (and its database volume) started with a plain `docker compose up`.
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pdf-chatbot-smoke}"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    docker compose ps || true
    docker compose logs --no-color || true
  fi
  docker compose down --volumes --remove-orphans || true
  exit "$status"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

host_port() {
  docker compose port "$1" "$2" | head -n1 | sed 's/.*://'
}

docker compose up --detach --build --wait --wait-timeout 180

BACKEND_PORT=$(host_port backend 8000)
FRONTEND_PORT=$(host_port frontend 80)
echo "backend on :${BACKEND_PORT}, frontend on :${FRONTEND_PORT}"

echo "--- GET /health"
health_file=$(mktemp)
health_code=$(curl -sS -o "$health_file" -w '%{http_code}' \
  "http://localhost:${BACKEND_PORT}/health")
health_body=$(cat "$health_file")
rm -f "$health_file"
echo "$health_code $health_body"
[ "$health_code" = "200" ] || fail "GET /health returned $health_code, want 200"
[ "$health_body" = '{"status":"ok"}' ] || fail "GET /health body was $health_body"

echo "--- pgvector extension"
vector_version=$(docker compose exec -T db \
  psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-chatbot}" -tAc \
  "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
echo "vector ${vector_version}"
[ -n "$vector_version" ] || fail "pgvector extension is not installed"

echo "--- GET / (page)"
page=$(curl -sSf "http://localhost:${FRONTEND_PORT}/") || fail "GET / failed"
echo "$page" | grep -q '<title>LLM PDF Chatbot</title>' || fail "page title missing"
script=$(echo "$page" | grep -o 'src="/assets/[^"]*\.js"' | head -n1 | cut -d'"' -f2)
[ -n "$script" ] || fail "page does not reference a built script"
curl -sSf -o /dev/null "http://localhost:${FRONTEND_PORT}${script}" \
  || fail "GET ${script} failed"
echo "page and ${script} served"

echo "compose smoke test passed"
