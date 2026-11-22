// =====================================================================================
// cicd-ai-flow - CI/CD pipeline (Jenkins multibranch job "cicd-ai-flow-cicd", branch main,
// and the local verification job "cicd-ai-flow-local"). See docs/PIPELINE.md.
//
//   Checkout & Prepare -> Build (backend | frontend) -> Test (backend | frontend)
//   -> Code Scanning (SonarQube | Trivy fs) -> Quality Gate -> Package (images + Trivy image)
//   -> Deploy (docker compose) -> Smoke Test
//
// Agent requirements (label cicd-agent): JDK 21, Node 22, git, docker CLI + compose plugin
// (host socket), sonar-scanner, trivy, python3, jq, curl.
// Credentials: SonarQube server "SonarQube" (sonar-token). No GitHub/Gemini credential needed here.
// =====================================================================================
pipeline {
    agent { label 'cicd-agent' }

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 40, unit: 'MINUTES')
        skipDefaultCheckout()
    }

    parameters {
        booleanParam(name: 'SKIP_DEPLOY', defaultValue: false,
            description: 'true 면 Deploy / Smoke Test 스테이지를 건너뛴다 (이미지 빌드·스캔까지만 수행)')
        string(name: 'TRIVY_SEVERITY', defaultValue: 'HIGH,CRITICAL',
            description: 'Trivy fs 스캔 보고/게이트 심각도 (쉼표 구분). Quality Gate 에서 이 심각도가 1건 이상이면 실패')
        string(name: 'TRIVY_IMAGE_SEVERITY', defaultValue: 'CRITICAL',
            description: 'Trivy image 게이트 심각도. 베이스 OS CVE 가 많으므로 기본은 CRITICAL 만 (보고는 TRIVY_SEVERITY 기준)')
        string(name: 'TRIVY_SCANNERS', defaultValue: 'vuln,secret,misconfig',
            description: 'Trivy fs 스캐너 목록 (vuln,secret,misconfig,license)')
        string(name: 'TRIVY_SKIP_DIRS', defaultValue: '**/node_modules,**/.git,app/vuln-samples,app/backend/.gradle,app/backend/build,app/frontend/dist,app/frontend/coverage,infra',
            description: 'Trivy fs 가 건너뛸 디렉터리 (쉼표 구분, glob 허용). infra/ 는 CI 플랫폼 자체라 앱 게이트에서 제외')
        booleanParam(name: 'SONAR_PR_BASELINE', defaultValue: true,
            description: 'main 빌드에서 SonarQube 프로젝트 cicd-ai-flow-pr 에도 클린 baseline 분석을 올린다 (PR/데모 브랜치의 "새 코드" 기준). false 면 main 만 분석')
    }

    environment {
        // SonarQube projectKey: main -> cicd-ai-flow, 그 외 브랜치(데모/검증) -> cicd-ai-flow-pr (Prepare 에서 SONAR_KEY/SONAR_URL 로 확정)
        SONAR_PROJECT_KEY    = 'cicd-ai-flow'
        SONAR_PR_PROJECT_KEY = 'cicd-ai-flow-pr'
        SONAR_BASE_URL       = 'http://localhost:9000'
        MAIN_BRANCH          = 'main'
        IMAGE_REPO           = 'cicd-ai-flow'
        COMPOSE_FILE         = 'app/docker-compose.app.yml'
        COMPOSE_PROJECT      = 'cicd-ai-flow-app'
        GRADLE_OPTS          = '-Dorg.gradle.daemon=false'
        CI                   = 'true'
        TRIVY_SEVERITY       = "${params.TRIVY_SEVERITY ?: 'HIGH,CRITICAL'}"
        TRIVY_IMAGE_SEVERITY = "${params.TRIVY_IMAGE_SEVERITY ?: 'CRITICAL'}"
        TRIVY_SCANNERS       = "${params.TRIVY_SCANNERS ?: 'vuln,secret,misconfig'}"
        TRIVY_SKIP_DIRS      = "${params.TRIVY_SKIP_DIRS ?: '**/node_modules,**/.git,app/vuln-samples,app/backend/.gradle,app/backend/build,app/frontend/dist,app/frontend/coverage,infra'}"
    }

    stages {
        // ---------------------------------------------------------------------------
        stage('Checkout & Prepare') {
            steps {
                checkout scm
                script {
                    env.IMAGE_TAG       = sh(returnStdout: true, script: 'git rev-parse --short=8 HEAD').trim()
                    env.GIT_COMMIT_FULL = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                    env.IS_MAIN         = (env.BRANCH_NAME == env.MAIN_BRANCH) ? 'true' : 'false'
                    env.SONAR_KEY       = (env.IS_MAIN == 'true') ? env.SONAR_PROJECT_KEY : env.SONAR_PR_PROJECT_KEY
                    env.SONAR_URL       = "${env.SONAR_BASE_URL}/dashboard?id=${env.SONAR_KEY}"
                    currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.IMAGE_TAG}" + (env.IS_MAIN == 'true' ? '' : " (${env.BRANCH_NAME})")
                    echo "branch=${env.BRANCH_NAME} main=${env.IS_MAIN} commit=${env.GIT_COMMIT_FULL} IMAGE_TAG=${env.IMAGE_TAG} sonar=${env.SONAR_KEY}"
                    if (env.IS_MAIN != 'true') {
                        echo "NOTE: '${env.BRANCH_NAME}' 는 main 이 아니므로 SonarQube 는 '${env.SONAR_KEY}' 프로젝트로 분석하고 Deploy/Smoke 는 건너뜁니다 (Quality Gate 까지는 동일하게 판정)."
                    }
                }
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    # Windows 에서 커밋되어 실행 비트가 없을 수 있다
                    chmod +x app/backend/gradlew ci/scripts/*.sh
                    chmod +x app/vuln-samples/*.sh 2>/dev/null || true
                    sed -i 's/\\r$//' app/backend/gradlew ci/scripts/*.sh 2>/dev/null || true

                    echo "== tool versions =="
                    java -version 2>&1 | head -n 1
                    echo "node $(node -v) / npm $(npm -v)"
                    docker version --format 'docker server {{.Server.Version}} / client {{.Client.Version}}'
                    docker compose version
                    trivy --version | head -n 1
                    (sonar-scanner --version 2>&1 | grep -iE 'scanner' | head -n 1) || true
                    python3 --version
                    jq --version
                    git --version
                    echo "== workspace =="
                    ls -la
                '''
            }
        }

        // ---------------------------------------------------------------------------
        stage('Build') {
            parallel {
                stage('Backend') {
                    steps {
                        dir('app/backend') {
                            sh './gradlew clean compileKotlin compileTestKotlin --no-daemon'
                        }
                    }
                }
                stage('Frontend') {
                    steps {
                        dir('app/frontend') {
                            sh 'npm ci --no-audit --no-fund'
                            sh 'npm run lint'
                            sh 'npm run build'
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------------------------------
        stage('Test') {
            parallel {
                stage('Backend') {
                    steps {
                        dir('app/backend') {
                            sh './gradlew test jacocoTestReport --no-daemon'
                        }
                    }
                    post {
                        always {
                            junit testResults: 'app/backend/build/test-results/test/*.xml', allowEmptyResults: false
                            jacoco(execPattern: 'app/backend/build/jacoco/*.exec',
                                   classPattern: 'app/backend/build/classes/kotlin/main',
                                   sourcePattern: 'app/backend/src/main/kotlin',
                                   exclusionPattern: '**/*Test*.class,**/TodoApplicationKt.class',
                                   skipCopyOfSrcFiles: false)
                            publishHTML(target: [reportDir: 'app/backend/build/reports/jacoco/test/html', reportFiles: 'index.html',
                                                 reportName: 'JaCoCo (backend)', allowMissing: true, alwaysLinkToLastBuild: true, keepAll: false])
                        }
                    }
                }
                stage('Frontend') {
                    steps {
                        dir('app/frontend') {
                            sh 'npm run test -- --run --coverage'
                        }
                    }
                    post {
                        always {
                            junit testResults: 'app/frontend/coverage/junit.xml', allowEmptyResults: false
                            publishHTML(target: [reportDir: 'app/frontend/coverage/lcov-report', reportFiles: 'index.html',
                                                 reportName: 'Coverage (frontend)', allowMissing: true, alwaysLinkToLastBuild: true, keepAll: false])
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------------------------------
        stage('Code Scanning') {
            parallel {
                stage('SonarQube') {
                    steps {
                        // sonar.host.url / token 은 withSonarQubeEnv 가 환경변수(SONARQUBE_SCANNER_PARAMS 등)로 주입한다.
                        withSonarQubeEnv('SonarQube') {
                            sh '''#!/usr/bin/env bash
                                set -euo pipefail
                                export SONAR_TOKEN="${SONAR_AUTH_TOKEN:-${SONAR_TOKEN:-}}"
                                # main 이 아니면 별도 프로젝트(cicd-ai-flow-pr)로 분석해 main 의 이력/새 코드 기준을 오염시키지 않는다
                                EXTRA=""
                                if [ "${IS_MAIN}" != "true" ]; then EXTRA="-Dsonar.projectName=cicd-ai-flow (PR)"; fi
                                # main 이면 먼저 cicd-ai-flow-pr 에 '클린 baseline' 분석을 올린다 (Community Edition 은 브랜치 분석이 없어
                                # PR/데모 브랜치 분석의 '새 코드' 기준(previous version)이 이 baseline 이 된다). 마지막에 main 을 분석해야
                                # report-task.txt 가 main 을 가리켜 waitForQualityGate 가 main 의 게이트를 기다린다.
                                if [ "${IS_MAIN}" = "true" ] && [ "${SONAR_PR_BASELINE:-true}" = "true" ]; then
                                  echo "sonar baseline refresh -> projectKey=${SONAR_PR_PROJECT_KEY} (version main-${IMAGE_TAG})"
                                  sonar-scanner \
                                    -Dsonar.projectKey="${SONAR_PR_PROJECT_KEY}" \
                                    -Dsonar.projectName="cicd-ai-flow (PR)" \
                                    -Dsonar.projectVersion="main-${IMAGE_TAG}" \
                                    -Dsonar.scm.revision="${GIT_COMMIT_FULL}" \
                                    -Dsonar.links.ci="${BUILD_URL}" > sonar-baseline.log 2>&1 \
                                    || { tail -n 30 sonar-baseline.log; echo "WARN: baseline 분석 실패 (main 게이트에는 영향 없음)"; }
                                  grep -E "ANALYSIS SUCCESSFUL|EXECUTION (SUCCESS|FAILURE)" sonar-baseline.log || true
                                fi
                                echo "sonar projectKey=${SONAR_KEY} (branch ${BRANCH_NAME})"
                                sonar-scanner \
                                  -Dsonar.projectKey="${SONAR_KEY}" \
                                  -Dsonar.projectVersion="${IMAGE_TAG}" \
                                  -Dsonar.scm.revision="${GIT_COMMIT_FULL}" \
                                  -Dsonar.links.ci="${BUILD_URL}" \
                                  ${EXTRA:+"$EXTRA"}
                            '''
                        }
                    }
                }
                stage('Trivy fs') {
                    steps {
                        sh '''#!/usr/bin/env bash
                            set -euo pipefail
                            if [ -f .trivyignore ]; then
                              echo ".trivyignore 적용 ($(grep -cvE '^\\s*(#|$)' .trivyignore || true) 항목)"
                            fi
                            trivy fs --scanners "${TRIVY_SCANNERS}" --severity "${TRIVY_SEVERITY}" --ignore-unfixed \
                                     --skip-dirs "${TRIVY_SKIP_DIRS}" --format json -o trivy-fs.json .
                            echo "== Trivy fs (${TRIVY_SCANNERS}; ${TRIVY_SEVERITY}) =="
                            trivy convert --format table trivy-fs.json || true
                        '''
                    }
                }
            }
        }

        // ---------------------------------------------------------------------------
        stage('Quality Gate') {
            steps {
                script {
                    // 1) Trivy 게이트 (빠름) - 결과는 모아서 한 번에 판정
                    int trivyRc = sh(returnStatus: true, script: 'ci/scripts/trivy_gate.sh trivy-fs.json "${TRIVY_SEVERITY}"')
                    // 2) SonarQube Quality Gate (webhook -> waitForQualityGate)
                    def qg = null
                    timeout(time: 10, unit: 'MINUTES') {
                        qg = waitForQualityGate(abortPipeline: false)
                    }
                    echo "SonarQube Quality Gate: ${qg.status}  (${env.SONAR_URL})"
                    // 3) 판정에 쓰인 게이트 이름 + 조건 원문을 콘솔에 남긴다 (조회 실패는 스테이지 결과에 영향 없음)
                    try {
                        withSonarQubeEnv('SonarQube') {
                            sh(returnStatus: true, script: '''#!/usr/bin/env bash
                                set +e
                                H="${SONAR_HOST_URL%/}"; T="${SONAR_AUTH_TOKEN:-${SONAR_TOKEN:-}}"
                                GATE="$(curl -sS -m 15 -u "${T}:" "${H}/api/qualitygates/get_by_project?project=${SONAR_KEY}" \
                                        | (jq -r '.qualityGate.name + (if .qualityGate.default then " (default)" else "" end)' 2>/dev/null || true))"
                                echo "SonarQube gate used for '${SONAR_KEY}': ${GATE:-unknown (조회 실패)}"
                                curl -sS -m 15 -u "${T}:" "${H}/api/qualitygates/project_status?projectKey=${SONAR_KEY}" \
                                  | (jq -r '.projectStatus as $p | "SonarQube project_status: " + $p.status + " (newCodePeriod " + (($p.period.mode // "none") + " " + ($p.period.parameter // "")) + ")", (($p.conditions // [])[] | "  - " + .status + " " + .metricKey + " actual=" + (.actualValue // "-") + " " + .comparator + " " + (.errorThreshold // ""))' 2>/dev/null || echo "SonarQube project_status 조회 실패")
                                exit 0
                            ''')
                        }
                    } catch (Exception e) {
                        echo "SonarQube 게이트 이름 조회 실패 (무시): ${e}"
                    }
                    def problems = []
                    if (qg.status != 'OK') {
                        problems << "SonarQube Quality Gate = ${qg.status} -> ${env.SONAR_URL}"
                    }
                    if (trivyRc != 0) {
                        problems << "Trivy fs: ${env.TRIVY_SEVERITY} 취약점/시크릿/설정오류 발견 (trivy-fs.json, 위 로그 참고)"
                    }
                    env.QUALITY_GATE = problems ? 'FAILED' : 'PASSED'
                    if (problems) {
                        error("Quality Gate 실패 - Package 이후 스테이지를 실행하지 않습니다:\n - " + problems.join('\n - '))
                    }
                }
            }
        }

        // ---------------------------------------------------------------------------
        stage('Package') {
            parallel {
                stage('Backend image') {
                    steps {
                        sh '''#!/usr/bin/env bash
                            set -euo pipefail
                            docker build -t "${IMAGE_REPO}/backend:${IMAGE_TAG}" -t "${IMAGE_REPO}/backend:latest" app/backend
                            trivy image --severity "${TRIVY_SEVERITY}" --ignore-unfixed --exit-code 0 \
                                        --format json -o trivy-image-backend.json "${IMAGE_REPO}/backend:${IMAGE_TAG}"
                            trivy convert --format table trivy-image-backend.json || true
                            ci/scripts/trivy_gate.sh trivy-image-backend.json "${TRIVY_IMAGE_SEVERITY}"
                        '''
                    }
                }
                stage('Frontend image') {
                    steps {
                        sh '''#!/usr/bin/env bash
                            set -euo pipefail
                            docker build -t "${IMAGE_REPO}/frontend:${IMAGE_TAG}" -t "${IMAGE_REPO}/frontend:latest" app/frontend
                            trivy image --severity "${TRIVY_SEVERITY}" --ignore-unfixed --exit-code 0 \
                                        --format json -o trivy-image-frontend.json "${IMAGE_REPO}/frontend:${IMAGE_TAG}"
                            trivy convert --format table trivy-image-frontend.json || true
                            ci/scripts/trivy_gate.sh trivy-image-frontend.json "${TRIVY_IMAGE_SEVERITY}"
                        '''
                    }
                }
            }
        }

        // ---------------------------------------------------------------------------
        stage('Deploy') {
            when { expression { return env.IS_MAIN == 'true' && !(params.SKIP_DEPLOY ?: false) } }
            steps {
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    export IMAGE_TAG
                    echo "deploying ${IMAGE_REPO}/{backend,frontend}:${IMAGE_TAG} with ${COMPOSE_FILE} (project ${COMPOSE_PROJECT})"
                    docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" up -d --remove-orphans --no-build
                    docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" ps
                '''
            }
        }

        // ---------------------------------------------------------------------------
        stage('Smoke Test') {
            when { expression { return env.IS_MAIN == 'true' && !(params.SKIP_DEPLOY ?: false) } }
            steps {
                sh 'ci/scripts/smoke_test.sh "${COMPOSE_FILE}" "${COMPOSE_PROJECT}"'
            }
        }
    }

    // -------------------------------------------------------------------------------
    post {
        always {
            archiveArtifacts artifacts: 'trivy-*.json, app/backend/build/reports/**, app/frontend/coverage/**',
                             allowEmptyArchive: true, fingerprint: false
        }
        success {
            script {
                def deployed = (env.IS_MAIN != 'true') ? "deploy skipped (branch ${env.BRANCH_NAME} != ${env.MAIN_BRANCH})"
                             : ((params.SKIP_DEPLOY ?: false) ? 'deploy skipped (SKIP_DEPLOY)' : 'deployed http://localhost:3000 (api 8081)')
                currentBuild.description = "OK ${env.IMAGE_TAG ?: 'n/a'} | ${deployed} | Sonar ${env.SONAR_URL}"
                echo """
                ==================== CI/CD SUCCESS ====================
                 commit      : ${env.GIT_COMMIT_FULL ?: 'n/a'} (tag ${env.IMAGE_TAG ?: 'n/a'})
                 images      : ${env.IMAGE_REPO}/backend:${env.IMAGE_TAG}, ${env.IMAGE_REPO}/frontend:${env.IMAGE_TAG}
                 deploy      : ${deployed}
                 sonar       : ${env.SONAR_URL}
                 trivy       : fs gate ${env.TRIVY_SEVERITY} / image gate ${env.TRIVY_IMAGE_SEVERITY} -> trivy-*.json (archived)
                =======================================================
                """.stripIndent()
            }
        }
        failure {
            script {
                currentBuild.description = "FAILED ${env.IMAGE_TAG ?: 'n/a'} | quality gate ${env.QUALITY_GATE ?: 'n/a'} | Sonar ${env.SONAR_URL}"
                echo """
                ==================== CI/CD FAILED =====================
                 commit      : ${env.GIT_COMMIT_FULL ?: 'n/a'} (tag ${env.IMAGE_TAG ?: 'n/a'})
                 quality gate: ${env.QUALITY_GATE ?: 'not reached'}
                 sonar       : ${env.SONAR_URL}
                 trivy       : trivy-fs.json / trivy-image-*.json (archived if produced)
                 rollback    : IMAGE_TAG=<previous sha> docker compose -p ${env.COMPOSE_PROJECT} -f ${env.COMPOSE_FILE} up -d --no-build
                =======================================================
                """.stripIndent()
            }
        }
        unstable {
            script {
                currentBuild.description = "UNSTABLE ${env.IMAGE_TAG ?: 'n/a'} | Sonar ${env.SONAR_URL}"
                echo "CI/CD UNSTABLE: 테스트 실패 또는 리포트 문제. JUnit/JaCoCo 리포트와 trivy-*.json 을 확인하세요."
            }
        }
        cleanup {
            cleanWs(deleteDirs: true, notFailBuild: true)
        }
    }
}
