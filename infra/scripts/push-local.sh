#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Snapshot a working tree into the local bare repo that the Jenkins job
# "cicd-ai-flow-local" scans (no GitHub needed):
#     file:///var/jenkins_home/repos/cicd-ai-flow.git   (volume jenkins_repos,
#     mounted in both the controller and the agent container)
#
#   usage: bash infra/scripts/push-local.sh [branch] [source-dir]
#          branch      default: main
#          source-dir  default: project root (parent of infra/)
#   env:   COMMIT_MESSAGE  optional commit message
#
# The tree is copied (honouring .gitignore files, excluding .git, node_modules,
# build, .gradle, dist, coverage, infra/.env, infra/secrets), committed on a
# fresh orphan history and force-pushed to <branch>. A branch scan is then
# triggered on the Jenkins job (it also scans every minute by itself).
# ---------------------------------------------------------------------------
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
cd "$INFRA_DIR"
load_env
export DOCKER_GID="${DOCKER_GID:-1001}"

BRANCH="${1:-main}"
SRC="$(cd "${2:-$PROJECT_DIR}" && pwd)"
MSG="${COMMIT_MESSAGE:-snapshot $(date '+%Y-%m-%d %H:%M:%S') from ${SRC##*/}}"

log "pushing ${SRC} -> file:///var/jenkins_home/repos/cicd-ai-flow.git (branch ${BRANCH})"
compose run --rm --no-deps -T --entrypoint bash -u jenkins \
  -v "${SRC}:/src:ro" -e "BRANCH=${BRANCH}" -e "MSG=${MSG}" jenkins-agent -c '
set -euo pipefail
BARE=/var/jenkins_home/repos/cicd-ai-flow.git
if [ ! -d "$BARE" ]; then
  git init -q --bare -b main "$BARE"
  echo "created bare repo $BARE"
fi
TMP="$(mktemp -d)"
tar -C /src --exclude=.git --exclude-vcs-ignores \
    --exclude="./infra/secrets" --exclude="./infra/.env" \
    --exclude="node_modules" --exclude="build" --exclude=".gradle" \
    --exclude="dist" --exclude="coverage" --exclude=".idea" --exclude=".vscode" \
    -cf - . | tar -C "$TMP" -xf -
cd "$TMP"
git init -q -b "$BRANCH"
git -c user.name="cicd-ai-flow local" -c user.email="local@cicd-ai-flow" add -A
git -c user.name="cicd-ai-flow local" -c user.email="local@cicd-ai-flow" commit -q --allow-empty -m "$MSG"
git push -q --force "$BARE" "HEAD:refs/heads/$BRANCH"
echo "pushed $(git rev-parse --short HEAD) ($(git ls-files | wc -l) files) to $BRANCH"
cd / && rm -rf "$TMP"
'

# best effort: trigger "Scan Multibranch Pipeline Now"
if jenkins_ready; then
  code="$(jenkins_post -o /dev/null -w '%{http_code}' "${JENKINS_URL}/job/cicd-ai-flow-local/build?delay=0" 2>/dev/null || echo 000)"
  log "branch scan of job cicd-ai-flow-local triggered (HTTP ${code}); ${JENKINS_URL}/job/cicd-ai-flow-local/"
else
  warn "Jenkins not reachable - the job scans every minute by itself"
fi
