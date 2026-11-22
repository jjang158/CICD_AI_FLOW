#!/usr/bin/env bash
# Wrapper around the jenkins/ssh-agent entrypoint (setup-sshd).
#  1. loads the agent's SSH public key from the mounted file when the
#     JENKINS_AGENT_SSH_PUBKEY env var is not set
#  2. fixes ownership of named volumes mounted into the jenkins home
set -eu

PUBKEY_FILE="${JENKINS_AGENT_SSH_PUBKEY_FILE:-/run/casc-secrets/agent_ssh_key.pub}"
if [ -z "${JENKINS_AGENT_SSH_PUBKEY:-}" ] && [ -f "${PUBKEY_FILE}" ]; then
  JENKINS_AGENT_SSH_PUBKEY="$(tr -d '\r' < "${PUBKEY_FILE}")"
  export JENKINS_AGENT_SSH_PUBKEY
fi
if [ -z "${JENKINS_AGENT_SSH_PUBKEY:-}" ]; then
  echo "[agent-entrypoint] WARNING: no SSH public key (JENKINS_AGENT_SSH_PUBKEY or ${PUBKEY_FILE}); the controller will not be able to connect." >&2
fi

# Named volumes are created root-owned when the mount point did not exist in
# the image. We run as root here, so make them writable for the jenkins user.
for d in /home/jenkins/agent /home/jenkins/.gradle /home/jenkins/.npm \
         /home/jenkins/.cache /home/jenkins/.sonar /var/jenkins_home/repos; do
  if [ -d "$d" ]; then
    chown jenkins:jenkins "$d" 2>/dev/null || true
  fi
done

exec /usr/local/bin/setup-sshd "$@"
