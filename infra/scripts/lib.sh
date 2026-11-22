#!/usr/bin/env bash
# Shared helpers for infra/scripts/*.sh - source this file, do not execute it.
# Requires: bash 4+, docker (compose v2), curl, python3 (JSON parsing).

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$(cd "${INFRA_DIR}/.." && pwd)"
ENV_FILE="${INFRA_DIR}/.env"
SECRETS_DIR="${INFRA_DIR}/secrets"
JENKINS_URL="${JENKINS_URL:-http://localhost:8080}"
SONAR_URL="${SONAR_URL:-http://localhost:9000}"
export INFRA_DIR PROJECT_DIR ENV_FILE SECRETS_DIR JENKINS_URL SONAR_URL

# --- logging ---------------------------------------------------------------
log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[%s] WARN:\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '\033[1;31m[%s] ERROR:\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

need_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command not found: $c"
  done
}

# --- .env handling -----------------------------------------------------------
# load_env: export KEY=VALUE pairs from infra/.env (supports "quoted" values, CRLF)
load_env() {
  [ -f "$ENV_FILE" ] || return 0
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$val" =~ ^\"(.*)\"$ ]]; then
      val="${BASH_REMATCH[1]}"
    elif [[ "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi
    export "$key=$val"
  done < "$ENV_FILE"
}

# set_env_value KEY VALUE : insert or replace KEY in infra/.env (keeps file order)
set_env_value() {
  local key="$1" val="$2" line tmp
  line="${key}=${val}"
  if [[ "$val" =~ [[:space:]\#\'\"\$] ]]; then
    line="${key}=\"${val}\""
  fi
  tmp="$(mktemp)"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v l="$line" '
      BEGIN { done = 0 }
      { if (!done && index($0, k "=") == 1) { print l; done = 1 } else { print } }
    ' "$ENV_FILE" > "$tmp"
  else
    [ -f "$ENV_FILE" ] && cat "$ENV_FILE" > "$tmp"
    printf '%s\n' "$line" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"   # write in place (keeps inode)
  rm -f "$tmp"
}

# --- JSON ------------------------------------------------------------------
# json_get '<json>' '<python expression over d>'   e.g. json_get "$r" 'd["token"]'
json_get() {
  python3 -I -c 'import sys, json
d = json.loads(sys.argv[1])
print(eval(sys.argv[2]))' "$1" "$2" 2>/dev/null
}

# --- docker compose ----------------------------------------------------------
compose() { (cd "$INFRA_DIR" && docker compose "$@"); }

# --- waiting -------------------------------------------------------------------
# wait_for "<description>" <timeout-seconds> <command...>
wait_for() {
  local desc="$1" timeout="$2"; shift 2
  local start=$SECONDS
  while ! "$@" >/dev/null 2>&1; do
    if (( SECONDS - start > timeout )); then
      warn "timeout after ${timeout}s waiting for ${desc}"
      return 1
    fi
    sleep 5
  done
  return 0
}

jenkins_ready() {
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${JENKINS_URL}/login")" = "200" ]
}

sonar_ready() {
  curl -s --max-time 10 "${SONAR_URL}/api/system/status" | grep -q '"UP"'
}

# --- Jenkins REST helpers ------------------------------------------------------
jenkins_curl() {
  # -g: no URL globbing (API paths contain [brackets])
  curl -sS -g --max-time 60 -u "admin:${JENKINS_ADMIN_PASSWORD:-admin}" "$@"
}

# jenkins_crumb_init: fetch a CSRF crumb bound to a session cookie
jenkins_crumb_init() {
  JENKINS_COOKIE_JAR="$(mktemp)"
  JENKINS_CRUMB_HEADER="$(jenkins_curl -c "$JENKINS_COOKIE_JAR" \
      "${JENKINS_URL}/crumbIssuer/api/xml?xpath=concat(//crumbRequestField,%22:%22,//crumb)")"
  [[ "$JENKINS_CRUMB_HEADER" == Jenkins-Crumb:* ]] || die "could not obtain a Jenkins crumb (is JENKINS_ADMIN_PASSWORD correct?)"
  export JENKINS_COOKIE_JAR JENKINS_CRUMB_HEADER
}

jenkins_post() {
  [ -n "${JENKINS_CRUMB_HEADER:-}" ] || jenkins_crumb_init
  jenkins_curl -b "$JENKINS_COOKIE_JAR" -H "$JENKINS_CRUMB_HEADER" -X POST "$@"
}

# jenkins_script '<groovy>' : run on the script console, print its output
jenkins_script() {
  jenkins_post --data-urlencode "script=$1" "${JENKINS_URL}/scriptText"
}

# jenkins_casc_reload : re-apply JCasC (re-reads env + $SECRETS files)
jenkins_casc_reload() {
  local code
  jenkins_crumb_init
  code="$(jenkins_post -o /dev/null -w '%{http_code}' "${JENKINS_URL}/configuration-as-code/reload")"
  [[ "$code" =~ ^(200|302)$ ]] || die "JCasC reload failed (HTTP ${code})"
  jenkins_crumb_init
}
