#!/usr/bin/env bash
# Applies the intentionally vulnerable samples to the application so the pipeline can
# demonstrate SonarQube / Trivy failures. Undo with revert.sh.
#
# Usage: app/vuln-samples/apply.sh [--dry-run] [--skip-lock]
#   --dry-run    print what would be done, change nothing
#   --skip-lock  do not regenerate gradle.lockfile / package-lock.json (Trivy needs them, so only for debugging)
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
LOG4J_DEP='implementation("org.apache.logging.log4j:log4j-core:2.14.1")'
LODASH_VERSION="4.17.15"

DRY_RUN=0
SKIP_LOCK=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    --skip-lock) SKIP_LOCK=1 ;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: ${arg}" >&2; exit 2 ;;
  esac
done

log() { printf '[vuln-samples:apply] %s\n' "$*"; }
run() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "DRY-RUN: $*"
  else
    "$@"
  fi
}

# 1. Kotlin samples -> backend main source set
log "Copying Kotlin samples to ${BACKEND_VULN_DIR}"
run mkdir -p "${BACKEND_VULN_DIR}"
for f in "${SCRIPT_DIR}"/backend/*.kt; do
  run cp "${f}" "${BACKEND_VULN_DIR}/"
done

# 2. TSX sample -> frontend src
log "Copying TSX sample to ${FRONTEND_VULN_DIR}"
run mkdir -p "${FRONTEND_VULN_DIR}"
run cp "${SCRIPT_DIR}/frontend/UnsafeHtml.tsx" "${FRONTEND_VULN_DIR}/"

# 3. Vulnerable backend dependency (log4j-core 2.14.1, CVE-2021-44228 "Log4Shell")
if grep -qF "${MARK_BEGIN}" "${GRADLE_BUILD}"; then
  log "build.gradle.kts already contains the vuln-samples block; skipping"
else
  log "Adding ${LOG4J_DEP} to build.gradle.kts"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    tmp="$(mktemp)"
    awk -v begin="${MARK_BEGIN}" -v end="${MARK_END}" -v dep="${LOG4J_DEP}" '
      { print }
      /^dependencies \{/ && !done {
        print "    " begin " (intentionally vulnerable, added by app/vuln-samples/apply.sh)"
        print "    " dep
        print "    " end
        done = 1
      }' "${GRADLE_BUILD}" > "${tmp}"
    cat "${tmp}" > "${GRADLE_BUILD}"
    rm -f "${tmp}"
  fi
fi

# 4. Vulnerable frontend dependency (lodash 4.17.15, CVE-2020-8203 / CVE-2021-23337 ...)
if node -e "process.exit(require('${PACKAGE_JSON}').dependencies?.lodash ? 0 : 1)"; then
  log "package.json already pins lodash; skipping"
else
  log "Adding lodash@${LODASH_VERSION} to package.json"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    node - "${PACKAGE_JSON}" "${LODASH_VERSION}" <<'NODE'
const fs = require('fs');
const [file, version] = process.argv.slice(2);
const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
pkg.dependencies = pkg.dependencies ?? {};
pkg.dependencies.lodash = version;
pkg.dependencies = Object.fromEntries(Object.entries(pkg.dependencies).sort(([a], [b]) => a.localeCompare(b)));
fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + '\n');
NODE
  fi
fi

# 5. Refresh lock files so Trivy sees the vulnerable versions
if [[ "${SKIP_LOCK}" -eq 1 ]]; then
  log "--skip-lock given: lock files NOT regenerated"
else
  log "Regenerating ${BACKEND_DIR}/gradle.lockfile"
  run chmod +x "${BACKEND_DIR}/gradlew"
  run bash -c "cd '${BACKEND_DIR}' && ./gradlew --no-daemon -q dependencies --write-locks > /dev/null"
  log "Regenerating ${FRONTEND_DIR}/package-lock.json"
  run bash -c "cd '${FRONTEND_DIR}' && npm install --package-lock-only --no-audit --no-fund --silent"
fi

log "Done. Expected: SonarQube Quality Gate FAIL (new Vulnerabilities + unreviewed Hotspots) and Trivy HIGH/CRITICAL > 0."
