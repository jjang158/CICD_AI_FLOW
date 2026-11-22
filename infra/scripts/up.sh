#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bring up the whole cicd-ai-flow stack (idempotent - safe to run repeatedly).
#   usage (from WSL): bash infra/scripts/up.sh [--no-build] [--skip-check]
#
#   1. prepare infra/.env (from .env.example) and infra/secrets/ (agent SSH key)
#   2. detect the gid of /var/run/docker.sock -> DOCKER_GID (build arg)
#   3. docker compose build && docker compose up -d
#   4. wait for Jenkins (/login 200) and SonarQube (/api/system/status UP)
#   5. bootstrap SonarQube (password, projects, token, webhook) + Jenkins credential
#   6. healthcheck
# ---------------------------------------------------------------------------
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
cd "$INFRA_DIR"

DO_BUILD=1
DO_CHECK=1
for arg in "$@"; do
  case "$arg" in
    --no-build)   DO_BUILD=0 ;;
    --skip-check) DO_CHECK=0 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

log "cicd-ai-flow infra directory: ${INFRA_DIR}"
need_cmd docker curl python3
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required (Docker Desktop with WSL integration)"
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon (is Docker Desktop running with WSL integration for this distro?)"

# ---------------------------------------------------------------- 1. .env + secrets
if [ ! -f .env ]; then
  cp .env.example .env
  log "created infra/.env from .env.example (edit GITHUB_*/GEMINI_API_KEY later and re-run)"
fi
# normalise CRLF (file may have been edited on Windows)
if grep -q $'\r' .env; then
  tmp="$(mktemp)"; tr -d '\r' < .env > "$tmp"; cat "$tmp" > .env; rm -f "$tmp"
fi
load_env

mkdir -p "$SECRETS_DIR"
if [ ! -s "$SECRETS_DIR/agent_ssh_key" ] || [ ! -s "$SECRETS_DIR/agent_ssh_key.pub" ]; then
  rm -f "$SECRETS_DIR/agent_ssh_key" "$SECRETS_DIR/agent_ssh_key.pub"
  if command -v ssh-keygen >/dev/null 2>&1; then
    ssh-keygen -q -t ed25519 -N '' -C 'jenkins@cicd-ai-flow' -f "$SECRETS_DIR/agent_ssh_key"
  else
    # fall back to generating the key pair inside a container
    docker run --rm -i jenkins/ssh-agent:jdk21 bash -c \
      'ssh-keygen -q -t ed25519 -N "" -C jenkins@cicd-ai-flow -f /tmp/k && cat /tmp/k && echo "=====PUB=====" && cat /tmp/k.pub' \
      > "$SECRETS_DIR/.keygen.tmp"
    sed -n '1,/^=====PUB=====$/p' "$SECRETS_DIR/.keygen.tmp" | sed '$d' > "$SECRETS_DIR/agent_ssh_key"
    sed -n '/^=====PUB=====$/,$p' "$SECRETS_DIR/.keygen.tmp" | sed '1d' > "$SECRETS_DIR/agent_ssh_key.pub"
    rm -f "$SECRETS_DIR/.keygen.tmp"
  fi
  chmod 600 "$SECRETS_DIR/agent_ssh_key" 2>/dev/null || true
  log "generated ed25519 key pair for the agent: infra/secrets/agent_ssh_key(.pub)"
fi
# JCasC reads SONAR_TOKEN from infra/secrets/SONAR_TOKEN; .env is the source of truth
if [ -n "${SONAR_TOKEN:-}" ]; then
  printf '%s' "$SONAR_TOKEN" > "$SECRETS_DIR/SONAR_TOKEN"
elif [ ! -f "$SECRETS_DIR/SONAR_TOKEN" ]; then
  : > "$SECRETS_DIR/SONAR_TOKEN"
fi

# ---------------------------------------------------------------- 2. DOCKER_GID
detect_docker_gid() {
  local gid=""
  # the gid that matters is the one seen *inside* a container
  gid="$(docker run --rm -v /var/run/docker.sock:/var/run/docker.sock alpine:3.20 \
          stat -c %g /var/run/docker.sock 2>/dev/null | tr -d '[:space:]' || true)"
  if ! [[ "$gid" =~ ^[0-9]+$ ]]; then
    gid="$(stat -c %g /var/run/docker.sock 2>/dev/null || true)"
  fi
  [[ "$gid" =~ ^[0-9]+$ ]] || gid="${DOCKER_GID:-1001}"
  echo "$gid"
}
DOCKER_GID="$(detect_docker_gid)"
export DOCKER_GID
set_env_value DOCKER_GID "$DOCKER_GID"
log "DOCKER_GID=${DOCKER_GID} (gid of /var/run/docker.sock inside containers)"

# ---------------------------------------------------------------- 3. build + up
if [ "$DO_BUILD" = 1 ]; then
  log "building images (first run takes several minutes) ..."
  compose build
fi
log "starting containers ..."
compose up -d --remove-orphans
compose ps

# ---------------------------------------------------------------- 4. wait
log "waiting for Jenkins at ${JENKINS_URL} ..."
wait_jenkins_boot() {
  local start=$SECONDS cid restarts
  cid="$(compose ps -q jenkins)"
  while ! jenkins_ready; do
    # fail fast when Jenkins is crash-looping (e.g. a JCasC/plugin error)
    restarts="$(docker inspect --format '{{.RestartCount}}' "$cid" 2>/dev/null || echo 0)"
    if [ "${restarts:-0}" -gt 0 ] && compose logs jenkins 2>/dev/null | grep -q 'Failed to initialize Jenkins'; then
      echo "---- jenkins log (errors) ----"
      compose logs --tail=400 jenkins 2>/dev/null | grep -E 'SEVERE|ConfiguratorException|Exception|Caused' | head -30
      die "Jenkins boot failure - fix the cause and re-run (full log: docker compose -f infra/docker-compose.yml logs jenkins)"
    fi
    if (( SECONDS - start > 600 )); then
      compose logs --tail=100 jenkins
      die "Jenkins did not come up within 600s"
    fi
    sleep 5
  done
}
wait_jenkins_boot
log "Jenkins is up"
log "waiting for SonarQube at ${SONAR_URL} (first start can take 2-3 minutes) ..."
wait_for "SonarQube" 900 sonar_ready || { compose logs --tail=100 sonarqube; die "SonarQube did not come up"; }
log "SonarQube is up"

# ---------------------------------------------------------------- 5. bootstrap SonarQube
bash "${INFRA_DIR}/scripts/bootstrap-sonar.sh"

# ---------------------------------------------------------------- 6. healthcheck
if [ "$DO_CHECK" = 1 ]; then
  bash "${INFRA_DIR}/scripts/healthcheck.sh"
fi

cat <<EOT

  Jenkins   : ${JENKINS_URL}   (admin / ${JENKINS_ADMIN_PASSWORD:-admin})
  SonarQube : ${SONAR_URL}   (admin / ${SONAR_ADMIN_PASSWORD:-Admin1234!Sonar})
  Agent     : node "cicd-agent" (container cicd-jenkins-agent, workspace /home/jenkins/agent/workspace)

  Next: edit infra/.env (GITHUB_OWNER, GITHUB_USERNAME, GITHUB_TOKEN, GEMINI_API_KEY)
        and run "bash infra/scripts/up.sh" again to apply.
EOT
