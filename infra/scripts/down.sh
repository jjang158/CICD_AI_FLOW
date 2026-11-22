#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Stop the cicd-ai-flow stack.
#   usage: bash infra/scripts/down.sh            # stop + remove containers (data kept)
#          bash infra/scripts/down.sh --volumes  # ALSO delete all named volumes
#                                                # (jenkins_home, sonar data, caches, local bare repo)
# Only resources of compose project "cicd-ai-flow" are touched.
# ---------------------------------------------------------------------------
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
cd "$INFRA_DIR"
load_env
export DOCKER_GID="${DOCKER_GID:-1001}"

case "${1:-}" in
  --volumes|-v)
    log "stopping stack and deleting volumes ..."
    compose down --remove-orphans --volumes
    ;;
  "")
    log "stopping stack (volumes are kept; use --volumes to delete them) ..."
    compose down --remove-orphans
    ;;
  *)
    die "usage: down.sh [--volumes]"
    ;;
esac
log "done"
