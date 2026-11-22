#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Verify the cicd-ai-flow stack. Prints PASS/FAIL per item, exit 1 on any FAIL.
#   usage: bash infra/scripts/healthcheck.sh [--no-tools]
#     --no-tools  skip the "docker compose exec" checks inside the agent container
# ---------------------------------------------------------------------------
set -uo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
cd "$INFRA_DIR"
need_cmd curl python3
load_env

CHECK_TOOLS=1
[ "${1:-}" = "--no-tools" ] && CHECK_TOOLS=0
SONAR_ADMIN_PASSWORD="${SONAR_ADMIN_PASSWORD:-Admin1234!Sonar}"
PASS=0; FAIL=0; SKIP=0

check() { # check "<name>" <command...>
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf 'PASS  %s\n' "$name"; PASS=$((PASS + 1))
  else
    printf 'FAIL  %s\n' "$name"; FAIL=$((FAIL + 1))
  fi
}
skip() { printf 'SKIP  %s\n' "$1"; SKIP=$((SKIP + 1)); }
json_true() { [ "$(json_get "$1" "$2")" = "True" ]; }

echo "== Jenkins (${JENKINS_URL})"
check "Jenkins /login returns 200" jenkins_ready

jobs_json="$(jenkins_curl "${JENKINS_URL}/api/json?tree=jobs[name]" 2>/dev/null || echo '{"jobs":[]}')"
for j in cicd-ai-flow-cicd cicd-ai-flow-codereview cicd-ai-flow-local; do
  check "Jenkins job '${j}' exists" json_true "$jobs_json" "any(x['name']=='${j}' for x in d['jobs'])"
done

agent_online() {
  jenkins_curl "${JENKINS_URL}/computer/cicd-agent/api/json?tree=offline" | grep -q '"offline":false'
}
check "Jenkins node 'cicd-agent' is online (waits up to 180s)" wait_for "agent" 180 agent_online

creds_json="$(jenkins_curl "${JENKINS_URL}/credentials/store/system/domain/_/api/json?tree=credentials[id]" 2>/dev/null || echo '{"credentials":[]}')"
for c in github-token gemini-api-key sonar-token agent-ssh-key; do
  check "Jenkins credential '${c}' exists" json_true "$creds_json" "any(x['id']=='${c}' for x in d['credentials'])"
done

sonar_server_url="$(jenkins_script "def d = jenkins.model.Jenkins.get().getDescriptorByType(hudson.plugins.sonar.SonarGlobalConfiguration)
def i = d.installations.find { it.name == 'SonarQube' }
print(i == null ? '' : (i.serverUrl + ' cred=' + i.credentialsId))" 2>/dev/null | tr -d '\r\n')"
check "Jenkins SonarQube server 'SonarQube' -> http://sonarqube:9000 (credential sonar-token)" \
  [ "$sonar_server_url" = "http://sonarqube:9000 cred=sonar-token" ]

if [ -n "${SONAR_TOKEN:-}" ]; then
  cred_token="$(jenkins_script "def c = com.cloudbees.plugins.credentials.SystemCredentialsProvider.getInstance().getCredentials().find { it.id == 'sonar-token' }
print(c == null ? '' : c.secret.plainText)" 2>/dev/null | tr -d '\r\n')"
  check "Jenkins credential 'sonar-token' equals SONAR_TOKEN from infra/.env" [ "$cred_token" = "$SONAR_TOKEN" ]
else
  skip "Jenkins credential 'sonar-token' value (SONAR_TOKEN not set in infra/.env yet)"
fi

# run a command on the agent through the Jenkins remoting channel = real build environment
agent_env_out="$(jenkins_script "def node = jenkins.model.Jenkins.get().getNode('cicd-agent')
def out = new ByteArrayOutputStream()
node.createLauncher(hudson.model.TaskListener.NULL).launch()
    .cmds('bash', '-c', 'id -un; java -version 2>&1 | head -1; node -v; npm -v; git --version; docker ps >/dev/null && echo DOCKER_OK; docker compose version; sonar-scanner --version 2>&1 | grep -i scanner | head -1; trivy --version | head -1; python3 --version; jq --version')
    .stdout(out).stderr(out).join()
print(out.toString())" 2>/dev/null | tr -d '\r')"
check "Agent (via Jenkins channel) runs as user jenkins" grep -q '^jenkins$' <<<"$agent_env_out"
check "Agent (via Jenkins channel) java 21" grep -qE 'openjdk version "21' <<<"$agent_env_out"
check "Agent (via Jenkins channel) node 22 + npm" grep -qE '^v22\.' <<<"$agent_env_out"
check "Agent (via Jenkins channel) git" grep -q 'git version' <<<"$agent_env_out"
check "Agent (via Jenkins channel) docker ps via host socket" grep -q 'DOCKER_OK' <<<"$agent_env_out"
check "Agent (via Jenkins channel) docker compose plugin" grep -q 'Docker Compose version' <<<"$agent_env_out"
check "Agent (via Jenkins channel) sonar-scanner" grep -qi 'scanner' <<<"$agent_env_out"
check "Agent (via Jenkins channel) trivy" grep -qi 'version' <<<"$agent_env_out"
check "Agent (via Jenkins channel) python3" grep -q 'Python 3' <<<"$agent_env_out"
check "Agent (via Jenkins channel) jq" grep -q 'jq-' <<<"$agent_env_out"

echo "== SonarQube (${SONAR_URL})"
check "SonarQube /api/system/status is UP" sonar_ready
sq() { curl -sS --max-time 30 -u "admin:${SONAR_ADMIN_PASSWORD}" "$@"; }
check "SonarQube admin login with SONAR_ADMIN_PASSWORD" \
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -u "admin:${SONAR_ADMIN_PASSWORD}" "${SONAR_URL}/api/user_tokens/search")" = "200" ]
for p in cicd-ai-flow cicd-ai-flow-pr; do
  resp="$(sq "${SONAR_URL}/api/projects/search?projects=${p}" 2>/dev/null || echo '{"components":[]}')"
  check "SonarQube project '${p}' exists" json_true "$resp" "any(c['key']=='${p}' for c in d['components'])"
done
resp="$(sq "${SONAR_URL}/api/webhooks/list" 2>/dev/null || echo '{"webhooks":[]}')"
check "SonarQube webhook 'jenkins' -> http://jenkins:8080/sonarqube-webhook/" \
  json_true "$resp" "any(w['name']=='jenkins' and w['url']=='http://jenkins:8080/sonarqube-webhook/' for w in d['webhooks'])"
resp="$(sq "${SONAR_URL}/api/user_tokens/search" 2>/dev/null || echo '{"userTokens":[]}')"
check "SonarQube global analysis token 'jenkins' exists" json_true "$resp" "any(t['name']=='jenkins' for t in d['userTokens'])"
resp="$(sq "${SONAR_URL}/api/qualitygates/show?name=cicd-ai-flow-gate" 2>/dev/null || echo '{}')"
check "SonarQube quality gate 'cicd-ai-flow-gate' exists with overall-code conditions (vulnerabilities, bugs)" \
  json_true "$resp" "{c['metric'] for c in d.get('conditions',[])} >= {'vulnerabilities','bugs'}"
for p in cicd-ai-flow cicd-ai-flow-pr; do
  resp="$(sq "${SONAR_URL}/api/qualitygates/get_by_project?project=${p}" 2>/dev/null || echo '{}')"
  check "SonarQube project '${p}' uses quality gate 'cicd-ai-flow-gate'" \
    json_true "$resp" "d.get('qualityGate',{}).get('name')=='cicd-ai-flow-gate'"
done

echo "== Containers"
if [ "$CHECK_TOOLS" = 1 ] && command -v docker >/dev/null 2>&1; then
  for s in jenkins jenkins-agent sonarqube sonar-db; do
    check "container '${s}' is healthy" \
      bash -c "docker inspect --format '{{.State.Health.Status}}' \"\$(cd '$INFRA_DIR' && docker compose ps -q $s)\" | grep -q '^healthy\$'"
  done
  check "docker compose exec -u jenkins: 'docker ps' works for user jenkins" \
    compose exec -T -u jenkins jenkins-agent docker ps
  check "docker compose exec -u jenkins: bare repo dir /var/jenkins_home/repos writable" \
    compose exec -T -u jenkins jenkins-agent bash -c 'test -w /var/jenkins_home/repos'
else
  skip "container checks (docker not available or --no-tools)"
fi

echo
echo "RESULT: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped"
[ "$FAIL" -eq 0 ]
