# ---------------------------------------------------------------------------
# cicd-ai-flow Jenkins controller
#   base    : jenkins/jenkins:lts-jdk21
#   plugins : plugins.txt (installed at build time with jenkins-plugin-cli)
#   config  : JCasC file baked into the image at /usr/local/etc/casc/jenkins.yaml
#             (re-apply with "POST /configuration-as-code/reload" or rebuild)
#   secrets : JCasC resolves ${name} from files in $SECRETS (/run/casc-secrets),
#             which docker-compose bind-mounts from infra/secrets/
# ---------------------------------------------------------------------------
FROM jenkins/jenkins:lts-jdk21

# - no setup wizard (JCasC configures everything)
# - allow file:// git remotes (local verification job cicd-ai-flow-local)
# - relaxed CSP so HTML Publisher reports (coverage) render with CSS/JS
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true -Dhudson.model.DirectoryBrowserSupport.CSP= -Djava.awt.headless=true"
ENV CASC_JENKINS_CONFIG=/usr/local/etc/casc/jenkins.yaml
ENV SECRETS=/run/casc-secrets

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --verbose --plugin-file /usr/share/jenkins/ref/plugins.txt

COPY casc/ /usr/local/etc/casc/
