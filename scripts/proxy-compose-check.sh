#!/usr/bin/env bash
# Checks docker-compose.proxy.yml without starting anything: `docker compose config`
# passes with a placeholder .env.proxy, and the stack keeps the shape a deploy behind a
# reverse proxy needs (docs/deploy/reverse-proxy.md): no published host port, no model server,
# no source bind-mount, `restart: unless-stopped`, the external `proxy` network, and the
# `db` service with its `db-data` volume that deploy tooling backs up.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE=docker-compose.proxy.yml
ENV_FILE=.env.proxy

# The real .env.proxy exists only on the host. Write a placeholder for the check and
# remove it after, but never touch one that is already here.
created_env=
if [ ! -e "$ENV_FILE" ]; then
  cat >"$ENV_FILE" <<'EOF'
POSTGRES_USER=placeholder
POSTGRES_PASSWORD=placeholder
POSTGRES_DB=placeholder
DATABASE_URL=postgresql+psycopg://placeholder:placeholder@llm-pdf-chatbot-db:5432/placeholder
LLM_BASE_URL=http://placeholder:4000/v1
LLM_API_KEY=placeholder
LLM_FAST_MODEL=placeholder
LLM_REASONING_MODEL=placeholder
LLM_EMBEDDING_MODEL=placeholder
OCR_URL=http://placeholder:5000
EOF
  created_env=1
fi
config_file=$(mktemp)
cleanup() {
  rm -f "$config_file"
  [ -z "$created_env" ] || rm -f "$ENV_FILE"
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" config --format json >"$config_file"
echo "docker compose config: ok"

python3 - "$config_file" <<'EOF'
import json
import sys

with open(sys.argv[1]) as f:
    config = json.load(f)
services = config["services"]
problems = []

if config.get("name") != "llm-pdf-chatbot":
    problems.append(f"project name is {config.get('name')!r}, want 'llm-pdf-chatbot'")
if set(services) != {"db", "backend", "frontend"}:
    problems.append(f"services are {sorted(services)}, want backend, db, frontend")
if not config.get("networks", {}).get("proxy", {}).get("external"):
    problems.append("network proxy is not external")

for name, service in services.items():
    if service.get("ports"):
        problems.append(f"{name} publishes a host port")
    if service.get("restart") != "unless-stopped":
        problems.append(f"{name} restart is {service.get('restart')!r}")
    text = json.dumps(service).lower()
    if "ollama" in text or "vllm" in text:
        problems.append(f"{name} looks like a model server")
    if "healthcheck" not in service:
        problems.append(f"{name} has no healthcheck")
    for volume in service.get("volumes", []):
        if volume.get("type") != "volume":
            problems.append(f"{name} mounts {volume.get('source')} from the host")
    if "proxy" in service.get("networks", {}) and name == "db":
        problems.append("db is on the proxy network")
    if name in ("backend", "frontend"):
        if "image" in service and service["image"] != f"llm-pdf-chatbot-{name}":
            problems.append(f"{name} sets image {service['image']!r}; deploy tooling tags llm-pdf-chatbot-{name}")
        if "proxy" not in service.get("networks", {}):
            problems.append(f"{name} is not on the proxy network")
        # `config` folds env_file into environment.
        if name == "backend" and "LLM_BASE_URL" not in service.get("environment", {}):
            problems.append("backend does not read its settings from .env.proxy")

db_volumes = [v.get("source") for v in services.get("db", {}).get("volumes", [])]
if "db-data" not in db_volumes:
    problems.append("db does not keep its data in the db-data volume")
if services.get("backend", {}).get("container_name") != "llm-pdf-chatbot-backend":
    problems.append("backend container is not llm-pdf-chatbot-backend (frontend/nginx.conf forwards /api there)")

for problem in problems:
    print(f"FAIL: {problem}", file=sys.stderr)
if problems:
    sys.exit(1)
print("proxy compose shape: ok (no host port, no model server, no bind-mount)")
EOF
