#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bootstrap SonarQube and inject the analysis token into Jenkins (idempotent).
#   usage: bash infra/scripts/bootstrap-sonar.sh [--force-token]
#
#   - admin password: initial admin/admin -> ${SONAR_ADMIN_PASSWORD} (skip if done)
#   - projects      : cicd-ai-flow, cicd-ai-flow-pr (skip if present)
#   - token         : global analysis token "jenkins" (regenerated when missing,
#                     when SONAR_TOKEN is empty, or with --force-token / FORCE_SONAR_TOKEN=1)
#   - webhook       : "jenkins" -> http://jenkins:8080/sonarqube-webhook/ (skip if present)
#   - Jenkins       : token written to infra/.env + infra/secrets/SONAR_TOKEN,
#                     JCasC reloaded, credential "sonar-token" verified
# ---------------------------------------------------------------------------
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
cd "$INFRA_DIR"
need_cmd curl python3
load_env

FORCE_TOKEN="${FORCE_SONAR_TOKEN:-0}"
[ "${1:-}" = "--force-token" ] && FORCE_TOKEN=1

# SonarQube (Community Build 25+) requires passwords of at least 12 characters.
SONAR_ADMIN_PASSWORD="${SONAR_ADMIN_PASSWORD:-Admin1234!Sonar}"
WEBHOOK_NAME="jenkins"
WEBHOOK_URL="http://jenkins:8080/sonarqube-webhook/"
TOKEN_NAME="jenkins"
PROJECTS=(cicd-ai-flow cicd-ai-flow-pr)

sonar_code() { # sonar_code USER PASSWORD PATH -> HTTP status
  curl -s -o /dev/null -w '%{http_code}' --max-time 20 -u "$1:$2" "${SONAR_URL}$3"
}

log "waiting for SonarQube (${SONAR_URL}) ..."
wait_for "SonarQube" 600 sonar_ready || die "SonarQube is not UP"

# ---------------------------------------------------------------- admin password
if [ "$(sonar_code admin "$SONAR_ADMIN_PASSWORD" /api/user_tokens/search)" = "200" ]; then
  log "SonarQube admin password already set"
elif [ "$(sonar_code admin admin /api/user_tokens/search)" = "200" ]; then
  if [ "${#SONAR_ADMIN_PASSWORD}" -lt 12 ]; then
    die "SONAR_ADMIN_PASSWORD must be at least 12 characters (SonarQube password policy) - edit infra/.env and re-run"
  fi
  resp="$(curl -sS --max-time 30 -u admin:admin -X POST "${SONAR_URL}/api/users/change_password" \
       -d login=admin -d previousPassword=admin --data-urlencode "password=${SONAR_ADMIN_PASSWORD}" -w '\n%{http_code}')"
  code="${resp##*$'\n'}"
  [[ "$code" =~ ^2 ]] || die "SonarQube password change failed (HTTP ${code}): ${resp%$'\n'*}"
  log "SonarQube admin password changed (admin/admin -> SONAR_ADMIN_PASSWORD)"
else
  die "cannot authenticate to SonarQube as admin with SONAR_ADMIN_PASSWORD nor with admin/admin"
fi
sq() { curl -sS --max-time 30 -u "admin:${SONAR_ADMIN_PASSWORD}" "$@"; }

# ---------------------------------------------------------------- projects
for p in "${PROJECTS[@]}"; do
  resp="$(sq "${SONAR_URL}/api/projects/search?projects=${p}")"
  n="$(json_get "$resp" 'len(d["components"])')"
  if [ "$n" = "0" ]; then
    sq -f -X POST "${SONAR_URL}/api/projects/create" -d "project=${p}" -d "name=${p}" -d "mainBranch=main" >/dev/null
    log "project '${p}' created"
  else
    log "project '${p}' already exists"
  fi
done

# ---------------------------------------------------------------- token
resp="$(sq "${SONAR_URL}/api/user_tokens/search")"
token_exists="$(json_get "$resp" "any(t['name']=='${TOKEN_NAME}' for t in d['userTokens'])")"
if [ "$token_exists" = "True" ] && [ -n "${SONAR_TOKEN:-}" ] && [ "$FORCE_TOKEN" != "1" ]; then
  log "token '${TOKEN_NAME}' exists and SONAR_TOKEN is set - keeping it (use --force-token to regenerate)"
else
  if [ "$token_exists" = "True" ]; then
    sq -f -X POST "${SONAR_URL}/api/user_tokens/revoke" -d "name=${TOKEN_NAME}" >/dev/null
    log "revoked existing token '${TOKEN_NAME}'"
  fi
  resp="$(sq -f -X POST "${SONAR_URL}/api/user_tokens/generate" -d "name=${TOKEN_NAME}" -d "type=GLOBAL_ANALYSIS_TOKEN")"
  SONAR_TOKEN="$(json_get "$resp" 'd["token"]')"
  [ -n "$SONAR_TOKEN" ] || die "token generation failed: $resp"
  set_env_value SONAR_TOKEN "$SONAR_TOKEN"
  log "generated global analysis token '${TOKEN_NAME}' -> infra/.env SONAR_TOKEN"
fi
mkdir -p "$SECRETS_DIR"
printf '%s' "$SONAR_TOKEN" > "${SECRETS_DIR}/SONAR_TOKEN"

# ---------------------------------------------------------------- webhook
resp="$(sq "${SONAR_URL}/api/webhooks/list")"
hook_exists="$(json_get "$resp" "any(w['name']=='${WEBHOOK_NAME}' for w in d['webhooks'])")"
if [ "$hook_exists" = "True" ]; then
  log "webhook '${WEBHOOK_NAME}' already exists"
else
  sq -f -X POST "${SONAR_URL}/api/webhooks/create" -d "name=${WEBHOOK_NAME}" --data-urlencode "url=${WEBHOOK_URL}" >/dev/null
  log "webhook '${WEBHOOK_NAME}' -> ${WEBHOOK_URL} created"
fi

# ---------------------------------------------------------------- quality gate
# The built-in "Sonar way" gate only has NEW-code conditions, so the first analysis
# of any project (no baseline yet) passes unconditionally - even with vulnerabilities.
# cicd-ai-flow-pr is re-analysed per branch/PR, so a dedicated gate with OVERALL-code
# conditions is created once and assigned to both projects (idempotent). The default
# gate (Sonar way) is left untouched.
GATE_NAME="cicd-ai-flow-gate"
GATE_CONDITIONS=(            # "<metric> <op> <error threshold>"; GT = fail when actual > threshold, LT = fail when actual < threshold
  "vulnerabilities GT 0"           # overall code
  "bugs GT 0"                      # overall code
  "security_rating GT 1"           # overall code: worse than A
  "reliability_rating GT 1"        # overall code: worse than A
  "coverage LT 80"                 # overall code
  "duplicated_lines_density GT 3"  # overall code
  "new_violations GT 0"            # new code (only evaluated once a baseline exists)
)
gate_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -u "admin:${SONAR_ADMIN_PASSWORD}" "${SONAR_URL}/api/qualitygates/show?name=${GATE_NAME}")"
if [ "$gate_code" = "200" ]; then
  log "quality gate '${GATE_NAME}' already exists (conditions not re-created)"
else
  sq -f -X POST "${SONAR_URL}/api/qualitygates/create" -d "name=${GATE_NAME}" >/dev/null
  log "quality gate '${GATE_NAME}' created"
  metrics="$(sq "${SONAR_URL}/api/metrics/search?ps=500")"
  # SonarQube 25+/26 pre-populates a new gate with its default NEW-code conditions
  # (new_violations, new_coverage, new_duplicated_lines_density, new_security_hotspots_reviewed)
  existing="$(sq "${SONAR_URL}/api/qualitygates/show?name=${GATE_NAME}")"
  for cond in "${GATE_CONDITIONS[@]}"; do
    read -r metric op err <<<"$cond"
    if [ "$(json_get "$metrics" "any(m['key']=='${metric}' for m in d['metrics'])")" != "True" ]; then
      warn "  metric '${metric}' is not available in this SonarQube version - condition skipped"
      continue
    fi
    if [ "$(json_get "$existing" "any(c['metric']=='${metric}' for c in d.get('conditions',[]))")" = "True" ]; then
      log "  condition on '${metric}' already present (added by SonarQube defaults) - kept"
      continue
    fi
    resp="$(sq -X POST -w '\n%{http_code}' "${SONAR_URL}/api/qualitygates/create_condition" \
            -d "gateName=${GATE_NAME}" -d "metric=${metric}" -d "op=${op}" -d "error=${err}")"
    code="${resp##*$'\n'}"
    if [[ "$code" =~ ^2 ]]; then
      log "  condition: ${metric} ${op} ${err}"
    else
      warn "  condition ${metric} ${op} ${err} rejected (HTTP ${code}): ${resp%$'\n'*}"
    fi
  done
fi
for p in "${PROJECTS[@]}"; do
  current="$(json_get "$(sq "${SONAR_URL}/api/qualitygates/get_by_project?project=${p}")" 'd["qualityGate"]["name"]')"
  if [ "$current" = "$GATE_NAME" ]; then
    log "project '${p}' already uses quality gate '${GATE_NAME}'"
  else
    sq -f -X POST "${SONAR_URL}/api/qualitygates/select" -d "gateName=${GATE_NAME}" -d "projectKey=${p}" >/dev/null
    log "project '${p}' -> quality gate '${GATE_NAME}' (was '${current:-default}')"
  fi
done

# ---------------------------------------------------------------- Jenkins credential
log "waiting for Jenkins (${JENKINS_URL}) ..."
wait_for "Jenkins" 300 jenkins_ready || die "Jenkins is not up"
log "reloading JCasC so credential 'sonar-token' picks up infra/secrets/SONAR_TOKEN ..."
jenkins_casc_reload
actual="$(jenkins_script "def c = com.cloudbees.plugins.credentials.SystemCredentialsProvider.getInstance().getCredentials().find { it.id == 'sonar-token' }
print(c == null ? '' : c.secret.plainText)" | tr -d '\r\n')"
if [ "$actual" = "$SONAR_TOKEN" ]; then
  log "Jenkins credential 'sonar-token' now holds the SonarQube token"
else
  die "Jenkins credential 'sonar-token' does not match the generated token (got '${actual:0:6}...')"
fi
log "SonarQube bootstrap complete"
