# ---------------------------------------------------------------------------
# cicd-ai-flow static build agent
#   base : jenkins/ssh-agent:jdk21  (Debian, OpenJDK 21, sshd, user "jenkins" uid 1000)
#   adds : Node 22 LTS + npm, git, curl, jq, unzip, python3, docker CLI + compose/buildx
#          plugins, sonar-scanner CLI, trivy
#   The Jenkins controller connects over SSH (label: cicd-agent).
# ---------------------------------------------------------------------------
FROM jenkins/ssh-agent:jdk21

ARG DOCKER_GID=1001
ARG NODE_VERSION=22.23.5
ARG SONAR_SCANNER_VERSION=8.5.0.16571

USER root
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    SONAR_SCANNER_HOME=/opt/sonar-scanner

# --- base tools ------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git jq unzip zip xz-utils tar \
        python3 procps openssh-client less vim-tiny rsync; \
    rm -rf /var/lib/apt/lists/*

# --- Node.js 22 LTS (official tarball) --------------------------------------
RUN set -eux; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner; \
    rm -f /tmp/node.tar.xz; \
    node -v; npm -v

# --- Docker CLI + compose + buildx plugins (Docker-out-of-Docker via socket) --
RUN set -eux; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc; \
    chmod a+r /etc/apt/keyrings/docker.asc; \
    . /etc/os-release; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin docker-buildx-plugin; \
    rm -rf /var/lib/apt/lists/*; \
    docker --version; docker compose version

# --- Trivy (official apt repo) ----------------------------------------------
RUN set -eux; \
    curl -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key -o /etc/apt/keyrings/trivy.asc; \
    chmod a+r /etc/apt/keyrings/trivy.asc; \
    echo "deb [signed-by=/etc/apt/keyrings/trivy.asc] https://aquasecurity.github.io/trivy-repo/deb generic main" \
        > /etc/apt/sources.list.d/trivy.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends trivy; \
    rm -rf /var/lib/apt/lists/*; \
    trivy --version

# --- SonarScanner CLI --------------------------------------------------------
RUN set -eux; \
    curl -fsSL "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}-linux-x64.zip" -o /tmp/sonar-scanner.zip; \
    unzip -q /tmp/sonar-scanner.zip -d /opt; \
    mv /opt/sonar-scanner-* "${SONAR_SCANNER_HOME}"; \
    rm -f /tmp/sonar-scanner.zip; \
    chmod +x "${SONAR_SCANNER_HOME}/bin/sonar-scanner"; \
    ln -sf "${SONAR_SCANNER_HOME}/bin/sonar-scanner" /usr/local/bin/sonar-scanner

# --- docker group so that the "jenkins" user can use /var/run/docker.sock ----
RUN set -eux; \
    if ! getent group "${DOCKER_GID}" >/dev/null; then groupadd -g "${DOCKER_GID}" docker; fi; \
    usermod -aG "$(getent group "${DOCKER_GID}" | cut -d: -f1)" jenkins; \
    id jenkins

# --- directories backed by named volumes / shared with the controller ------
RUN set -eux; \
    mkdir -p /home/jenkins/agent /home/jenkins/.gradle /home/jenkins/.npm \
             /home/jenkins/.cache /home/jenkins/.sonar /var/jenkins_home/repos; \
    chown -R jenkins:jenkins /home/jenkins /var/jenkins_home

# --- environment for non-interactive SSH sessions (pam_env reads this) ------
ENV PATH="${SONAR_SCANNER_HOME}/bin:/opt/java/openjdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
RUN set -eux; \
    JAVA_HOME_DETECTED="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"; \
    { \
      echo "PATH=\"${SONAR_SCANNER_HOME}/bin:${JAVA_HOME_DETECTED}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\""; \
      echo "JAVA_HOME=\"${JAVA_HOME_DETECTED}\""; \
      echo "SONAR_SCANNER_HOME=\"${SONAR_SCANNER_HOME}\""; \
      echo "LANG=C.UTF-8"; \
    } > /etc/environment; \
    ln -sf "${JAVA_HOME_DETECTED}/bin/java" /usr/local/bin/java; \
    ln -sf "${JAVA_HOME_DETECTED}/bin/javac" /usr/local/bin/javac; \
    cat /etc/environment

COPY entrypoint.sh /usr/local/bin/agent-entrypoint.sh
RUN chmod +x /usr/local/bin/agent-entrypoint.sh

# sshd must run as root; build steps run as "jenkins" through the SSH session.
EXPOSE 22
ENTRYPOINT ["/usr/local/bin/agent-entrypoint.sh"]
