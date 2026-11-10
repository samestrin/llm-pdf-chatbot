#!/usr/bin/env bash
# Compose smoke test: build and start the whole stack with one `docker compose up`,
# then check that the backend health check passes against Postgres, that pgvector
# is installed, that the frontend serves the page, and that the frontend's nginx
# proxies /api to the backend. With the fake model of docker-compose.smoke.yml
# (scripts/fake-llm/) in place of a real one, it then uploads the sample PDF, asks a
# question the sample answers and checks the streamed answer cites the sample's page;
# last, it stops the fake, asks again and checks the answer fails as unreachable.
set -euo pipefail

cd "$(dirname "$0")/.."

# Port 0 lets Docker pick free host ports, so the test does not collide with
# anything else on a shared runner. The real ports are read back after `up`.
export BACKEND_PORT="${BACKEND_PORT:-0}"
export FRONTEND_PORT="${FRONTEND_PORT:-0}"
# Use a project of its own so the final `down --volumes` never touches a stack
# (and its database volume) started with a plain `docker compose up`.
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pdf-chatbot-smoke}"
# The real compose file plus the smoke's override (the fake model). Exported, so every
# `docker compose` call below, the cleanup's `down` too, uses the same files.
export COMPOSE_FILE="docker-compose.yml:docker-compose.smoke.yml"

SAMPLE="frontend/public/samples/sample.pdf"
COLLECTION="smoke"
QUESTION="How much did revenue grow in the third quarter?"

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

echo "--- GET /api/health through the frontend (nginx proxy)"
proxied=$(curl -sSf "http://localhost:${FRONTEND_PORT}/api/health") \
  || fail "GET /api/health through the frontend failed"
echo "$proxied"
[ "$proxied" = '{"status":"ok"}' ] || fail "proxied /api/health body was $proxied"

# POST a question to the chat route and print its NDJSON events, one per line.
ask() {
  local body content_type answer_file
  body=$(jq -cn --arg q "$QUESTION" '{question: $q}')
  answer_file=$(mktemp)
  content_type=$(curl -sS -N -o "$answer_file" -w '%{content_type}' \
    -H 'Content-Type: application/json' -d "$body" \
    "http://localhost:${BACKEND_PORT}/collections/${COLLECTION}/chat") \
    || { rm -f "$answer_file"; fail "POST /collections/${COLLECTION}/chat failed"; }
  case "$content_type" in
    application/x-ndjson*) ;;
    *) rm -f "$answer_file"; fail "chat answered as $content_type, want application/x-ndjson" ;;
  esac
  cat "$answer_file"
  rm -f "$answer_file"
}

echo "--- upload ${SAMPLE}"
upload=$(curl -sSf -F "file=@${SAMPLE};type=application/pdf" \
  "http://localhost:${BACKEND_PORT}/collections/${COLLECTION}/documents") \
  || fail "upload of ${SAMPLE} failed"
echo "$upload"
document_id=$(echo "$upload" | jq -r '.id')

echo "--- wait for ingestion (reading and embedding by the fake model)"
state=""
for _ in $(seq 1 90); do
  status=$(curl -sSf "http://localhost:${BACKEND_PORT}/documents/${document_id}/status") \
    || fail "GET /documents/${document_id}/status failed"
  state=$(echo "$status" | jq -r '.state')
  case "$state" in
    ready) break ;;
    failed) fail "ingestion failed: $(echo "$status" | jq -c '.error')" ;;
  esac
  sleep 1
done
[ "$state" = "ready" ] || fail "ingestion is still ${state} after 90 s"
echo "$status" | jq -c '{state, pages_read, chunks_total, chunks_embedded}'

echo "--- ask: ${QUESTION}"
events=$(ask)
echo "$events"
[ "$(echo "$events" | tail -n1 | jq -r '.type')" = "done" ] \
  || fail "the answer did not end in a done event"
cited=$(echo "$events" | jq -r --arg id "$document_id" \
  'select(.type == "claim" and .document.id == $id and .page == 2)
   | "\(.text) [\(.document.filename), page \(.page): \"\(.quote)\"]"')
[ -n "$cited" ] || fail "no claim cites page 2 of the sample"
echo "cited answer: ${cited}"

echo "--- stop the fake model and ask again"
docker compose stop fake-llm
events=$(ask)
echo "$events"
message=$(echo "$events" | jq -r 'select(.type == "error") | .message')
# The stable part of the message only: the backend may add a hint after it.
case "$message" in
  *"the model endpoint is unreachable"*) ;;
  *) fail "with the fake stopped the error was '${message}', want the model endpoint is unreachable" ;;
esac
[ "$(echo "$events" | jq -r 'select(.type == "claim" or .type == "done") | .type')" = "" ] \
  || fail "with the fake stopped the answer still had a claim or a done event"

echo "compose smoke test passed"
