#!/usr/bin/env bash
# Reverts everything apply.sh did, without relying on git:
# removes the copied sample sources, strips the marker block from build.gradle.kts,
# removes lodash from package.json and regenerates both lock files.
#
# Usage: app/vuln-samples/revert.sh [--dry-run] [--skip-lock]
#   --dry-run    print what would be done, change nothing
#   --skip-lock  do not regenerate gradle.lockfile / package-lock.json
#
# Requires: bash, JDK 21 (for ./gradlew), Node 22 + npm (for package-lock.json). No git needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${APP_DIR}/backend"
FRONTEND_DIR="${APP_DIR}/frontend"
BACKEND_VULN_DIR="${BACKEND_DIR}/src/main/kotlin/com/cicdaiflow/todo/vuln"
FRONTEND_VULN_DIR="${FRONTEND_DIR}/src/vuln"
GRADLE_BUILD="${BACKEND_DIR}/build.gradle.kts"
PACKAGE_JSON="${FRONTEND_DIR}/package.json"

MARK_BEGIN="// vuln-samples:begin"
MARK_END="// vuln-samples:end"

DRY_RUN=0
SKIP_LOCK=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    --skip-lock) SKIP_LOCK=1 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: ${arg}" >&2; exit 2 ;;
  esac
done

log() { printf '[vuln-samples:revert] %s\n' "$*"; }
run() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "DRY-RUN: $*"
  else
    "$@"
  fi
}

# 1. Remove copied sample sources (only files that apply.sh copied, then the directory if empty)
for f in "${SCRIPT_DIR}"/backend/*.kt; do
  target="${BACKEND_VULN_DIR}/$(basename "${f}")"
  if [[ -f "${target}" ]]; then
    log "Removing ${target}"
    run rm -f "${target}"
  fi
done
if [[ -d "${BACKEND_VULN_DIR}" ]]; then
  run rmdir --ignore-fail-on-non-empty "${BACKEND_VULN_DIR}"
fi

target="${FRONTEND_VULN_DIR}/UnsafeHtml.tsx"
if [[ -f "${target}" ]]; then
  log "Removing ${target}"
  run rm -f "${target}"
fi
if [[ -d "${FRONTEND_VULN_DIR}" ]]; then
  run rmdir --ignore-fail-on-non-empty "${FRONTEND_VULN_DIR}"
fi

# 2. Strip the marker block from build.gradle.kts
if grep -qF "${MARK_BEGIN}" "${GRADLE_BUILD}"; then
  log "Removing vuln-samples block from build.gradle.kts"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    tmp="$(mktemp)"
    awk -v begin="${MARK_BEGIN}" -v end="${MARK_END}" '
      index($0, begin) { skipping = 1 }
      !skipping { print }
      index($0, end) { skipping = 0 }' "${GRADLE_BUILD}" > "${tmp}"
    cat "${tmp}" > "${GRADLE_BUILD}"
    rm -f "${tmp}"
  fi
else
  log "build.gradle.kts has no vuln-samples block; skipping"
fi

# 3. Remove lodash from package.json
if node -e "process.exit(require('${PACKAGE_JSON}').dependencies?.lodash ? 0 : 1)"; then
  log "Removing lodash from package.json"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    node - "${PACKAGE_JSON}" <<'NODE'
const fs = require('fs');
const [file] = process.argv.slice(2);
const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
if (pkg.dependencies) {
  delete pkg.dependencies.lodash;
}
fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + '\n');
NODE
  fi
else
  log "package.json does not pin lodash; skipping"
fi

# 4. Regenerate lock files back to the clean dependency graph
if [[ "${SKIP_LOCK}" -eq 1 ]]; then
  log "--skip-lock given: lock files NOT regenerated"
else
  log "Regenerating ${BACKEND_DIR}/gradle.lockfile"
  run chmod +x "${BACKEND_DIR}/gradlew"
  run bash -c "cd '${BACKEND_DIR}' && ./gradlew --no-daemon -q dependencies --write-locks > /dev/null"
  log "Regenerating ${FRONTEND_DIR}/package-lock.json"
  run bash -c "cd '${FRONTEND_DIR}' && npm install --package-lock-only --no-audit --no-fund --silent"
fi

log "Done. The application is back to its clean state."
