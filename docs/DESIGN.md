# cicd-ai-flow 설계 및 에이전트 간 계약 (CONTRACT)

> 이 문서는 3개의 병렬 작업(환경 구축 / 테스트 앱 / Jenkins 파이프라인)이
> 서로 맞물리도록 하는 **고정 계약**이다. 각 작업 담당자는 아래 값을 임의로 바꾸지 않는다.
> 바꿔야 한다면 이 문서를 먼저 수정하고 다른 담당자에게 알린다.

## 0. 전체 그림

```
GitHub Repo (monorepo: cicd-ai-flow)
   ├─ main 브랜치 변경 ──▶ Jenkins Multibranch "cicd-ai-flow-cicd"  (Jenkinsfile)
   │                           Checkout → Build → Test → Code Scanning(Sonar+Trivy)
   │                           → Quality Gate → Package(Docker) → Deploy(local docker) → Smoke
   └─ Pull Request 생성 ──▶ Jenkins Multibranch "cicd-ai-flow-codereview" (ci/Jenkinsfile.codereview)
                               Checkout(PR merge) → Build & Test → Scan
                               → AI Code Review(Gemini, git diff hunk별 인라인 코멘트 + 총평)
                               → GitHub PR Review 등록

로컬 WSL(Ubuntu-26.04) + Docker Desktop
   ├─ jenkins       : http://localhost:8080   (컨테이너 내부 이름 jenkins:8080)
   ├─ jenkins-agent : label `cicd-agent` (JDK21, Node22, docker cli, sonar-scanner, python3, trivy)
   ├─ sonarqube     : http://localhost:9000   (컨테이너 내부 이름 sonarqube:9000)
   ├─ sonar-db      : postgres 16
   └─ (배포 대상) app-backend:8081, app-frontend:3000  ← Deploy 스테이지가 띄움
```

## 1. 디렉터리 레이아웃 (고정)

```
cicd-ai-flow/
├─ README.md                      # 전체 사용법 (최종 통합 단계에서 작성)
├─ docs/
│   ├─ DESIGN.md                  # 이 문서
│   └─ VERIFICATION.md            # 상호 검증 결과 (검증 단계에서 작성)
├─ infra/                         # [Task 1] 환경 구축
│   ├─ docker-compose.yml         # jenkins, jenkins-agent, sonarqube, sonar-db
│   ├─ .env.example               # GITHUB_OWNER, GITHUB_REPO, GEMINI_API_KEY 등 (실제 .env 는 git 제외)
│   ├─ jenkins/
│   │   ├─ Dockerfile             # jenkins/jenkins:lts-jdk21 + plugins.txt
│   │   ├─ plugins.txt
│   │   └─ casc/jenkins.yaml      # JCasC: 보안, credentials, SonarQube 서버, Job DSL(멀티브랜치 2개)
│   ├─ agent/
│   │   └─ Dockerfile             # jenkins/inbound-agent 또는 jenkins/agent 기반 빌드 에이전트 이미지
│   └─ scripts/
│       ├─ up.sh                  # 전체 기동 + 준비 대기 + 소나 부트스트랩
│       ├─ down.sh
│       ├─ bootstrap-sonar.sh     # 토큰 생성, 프로젝트/웹훅 등록, Jenkins credential 주입
│       └─ healthcheck.sh         # Jenkins/Sonar/Agent 상태 확인 (검증용)
├─ app/                           # [Task 2] 테스트 애플리케이션
│   ├─ backend/                   # Kotlin + Spring Boot 3.x, Gradle Kotlin DSL, JDK 21, gradlew 포함
│   ├─ frontend/                  # React 18 + TypeScript + Vite + Vitest
│   ├─ docker-compose.app.yml     # Deploy 스테이지가 사용 (backend 8081, frontend 3000)
│   └─ vuln-samples/              # 의도적 취약 코드 모음 + apply/revert 스크립트 (기본은 미적용)
├─ sonar-project.properties       # [Task 2] 루트 1개, 프론트+백엔드 동시 분석
├─ Jenkinsfile                    # [Task 3] CI/CD 파이프라인 (main)
└─ ci/
    ├─ Jenkinsfile.codereview     # [Task 3] CodeReview 파이프라인 (PR)
    └─ scripts/
        ├─ gemini_review.py       # git diff → Gemini → GitHub PR Review (인라인 + 총평)
        ├─ trivy_gate.sh          # Trivy 결과 심각도 게이트
        └─ smoke_test.sh          # 배포 후 헬스체크
```

## 2. Jenkins 계약

| 항목 | 값 |
|---|---|
| URL | http://localhost:8080 (컨테이너 간: http://jenkins:8080) |
| 초기 관리자 | `admin` / `admin` (JCasC, 최초 접속 후 변경 권장) |
| 빌드 노드 label | `cicd-agent` |
| 에이전트 포함 도구 | JDK 21, Node 22 LTS + npm, git, docker CLI(호스트 소켓 마운트), docker compose plugin, sonar-scanner CLI, python3(표준 라이브러리만 사용), trivy, jq, curl. 연결 방식 **SSH**(`jenkins/ssh-agent`, 키는 `infra/secrets/`에 자동 생성) |
| Credential `github-token` | kind: **Username with password**, username=GitHub 계정, password=PAT(repo, workflow 권한). 사용자가 직접 입력 |
| Credential `gemini-api-key` | kind: **Secret text**. 사용자가 직접 입력 (JCasC는 `${GEMINI_API_KEY}` 환경변수에서 읽되 비어있어도 기동되어야 함) |
| Credential `sonar-token` | kind: **Secret text**. bootstrap-sonar.sh 가 자동 생성/주입 |
| SonarQube 서버 설정 이름 | `SonarQube` (sonar plugin "SonarQube servers" name) — Jenkinsfile 에서 `withSonarQubeEnv('SonarQube')` |
| Job DSL 로 생성되는 잡 | `cicd-ai-flow-cicd` (브랜치 `main` 만, script path `Jenkinsfile`), `cicd-ai-flow-codereview` (PR 만 discover, script path `ci/Jenkinsfile.codereview`), **`cicd-ai-flow-local`**(GitHub 없이 검증용: `file:///var/jenkins_home/repos/cicd-ai-flow.git`, `infra/scripts/push-local.sh` 로 푸시) |
| GitHub 소스 | `GITHUB_OWNER`/`GITHUB_REPO` 환경변수(.env) → JCasC/Job DSL 에서 참조 |
| 트리거 | 로컬 Jenkins 는 인터넷에서 접근 불가 → 멀티브랜치 **주기적 스캔(1분)** 기본. 웹훅은 선택(ngrok/cloudflared) 문서화 |
| 필수 플러그인 | workflow-aggregator, git, github, github-branch-source, sonar, configuration-as-code, job-dsl, credentials-binding, pipeline-stage-view, junit, jacoco, htmlpublisher, timestamper, ws-cleanup, docker-workflow, pipeline-utility-steps, warnings-ng |

## 3. SonarQube 계약

| 항목 | 값 |
|---|---|
| URL | http://localhost:9000 (컨테이너 간: http://sonarqube:9000) |
| Edition | Community (브랜치/PR 분석 미지원 → PR 파이프라인은 별도 projectKey `cicd-ai-flow-pr` 로 분석) |
| 관리자 | `admin` / 초기 `admin` → bootstrap 에서 `${SONAR_ADMIN_PASSWORD:-Admin1234!Sonar}` 로 변경 (Community Build 26.x 는 12자 이상 요구) |
| Project key / name | `cicd-ai-flow` / `cicd-ai-flow` |
| Webhook | name `jenkins`, url `http://jenkins:8080/sonarqube-webhook/` (waitForQualityGate 용) |
| Quality Gate | 전용 게이트 `cicd-ai-flow-gate`(bootstrap 이 생성·할당: 전체 코드 vulnerabilities=0, bugs=0, security/reliability A, coverage≥80, 중복≤3 + new_violations=0). 기본 `Sonar way` 는 새 코드 기준이라 **첫 분석이 무조건 PASS** 되는 문제가 있어 채택하지 않음 |
| 분석 방식 | 루트 `sonar-project.properties` + sonar-scanner CLI 8.x (에이전트에 설치). main 이외 브랜치(로컬 검증 잡)는 `cicd-ai-flow-pr` 키로 분석해 main 이력을 보존. `sonar.host.url`/`sonar.token` 은 Jenkins 의 withSonarQubeEnv 가 주입 |
| 커버리지 입력 | backend: `app/backend/build/reports/jacoco/test/jacocoTestReport.xml` / frontend: `app/frontend/coverage/lcov.info` |

## 4. 애플리케이션 계약

| 항목 | 값 |
|---|---|
| Backend | Kotlin 2.x + **Spring Boot 4.x**(3.5.x 는 Spring Framework 6.2 의 CRITICAL CVE 미패치로 Trivy 0건 불가 → 4.2.0 채택), Gradle Kotlin DSL + gradlew, JDK 21, port **8081**, `GET /actuator/health`, `GET /api/todos` 등 간단 CRUD(H2 in-memory) |
| Backend 명령 | `./gradlew clean build jacocoTestReport` → 테스트 리포트 `build/test-results/test/*.xml`, JaCoCo XML 위 경로 |
| Frontend | React 18 + TypeScript + Vite + Vitest(+jsdom, @testing-library), 호스트 port **3000** → 컨테이너 **8080**(`nginxinc/nginx-unprivileged`, 비root; `/api` → backend 프록시) |
| Frontend 명령 | `npm ci && npm run lint && npm run test -- --run --coverage && npm run build` → `coverage/lcov.info`, junit 리포트 `coverage/junit.xml` (vitest reporter) |
| Docker | `app/backend/Dockerfile`, `app/frontend/Dockerfile` (멀티스테이지), `app/docker-compose.app.yml` 이미지 태그는 `IMAGE_TAG` env (기본 `latest`) |
| 취약 코드 데모 | `app/vuln-samples/` 에 Kotlin/TS 취약 샘플 + 취약 의존성(pinned) 목록 + `apply.sh`/`revert.sh`. SonarQube Community Build 26.11 실측: 적용 시 **Vulnerability 11건**(kotlin S5542×2, S5547, S4423, S4426, S4790, S2245×2, S6418 / typescript S1523, S2245; 26.11 은 과거 Hotspot 규칙도 Vulnerability 로 통합 보고). S2077(SQL 결합)·S6299(React raw HTML)·S2068 은 이 버전/리터럴에서 미검출 → AI 리뷰 데모용으로만 유지. Trivy HIGH/CRITICAL **7건**(log4j-core 2.14.1 CVE-2021-44228 등 3, lodash 4.17.15 4). 상세 표는 `app/vuln-samples/README.md` |
| 클린 상태 보장 | 기본 소스(main)는 Sonar Quality Gate PASS + Trivy HIGH/CRITICAL 0 이어야 한다 |

## 5. 파이프라인 스테이지 설계 (제안 → 사용자 협의 사항)

사용자 초안: `Code Build → Code Scanning → Code Quality Validate → Code Test → Code Build → Code Deploy`

문제점: (1) Build 가 2회 중복 (2) SonarQube 는 테스트 커버리지 리포트를 입력으로 받으므로 Test 가 Scanning 보다 먼저 와야 함 (3) Quality Gate 판정은 Scanning 직후여야 의미 있음.

**채택안 (CI/CD, main)**

| # | Stage | 내용 | 실패 시 |
|---|---|---|---|
| 1 | Checkout & Prepare | 소스, git sha, 버전 계산, 도구 버전 출력 | 중단 |
| 2 | Build | backend 컴파일 / frontend 의존성+lint+빌드 (parallel) | 중단 |
| 3 | Test | backend 단위테스트+JaCoCo / frontend Vitest+coverage (parallel), JUnit 리포트 publish | 중단 |
| 4 | Code Scanning | SonarQube 분석 / Trivy fs(의존성 CVE, 시크릿, 설정) (parallel), 리포트 archive | 리포트만 (판정은 5) |
| 5 | Quality Gate | `waitForQualityGate` (Sonar) + Trivy HIGH/CRITICAL 0 게이트 | **중단** |
| 6 | Package | backend/frontend Docker 이미지 빌드 (tag=git sha) + Trivy image scan | 중단 |
| 7 | Deploy | `docker compose -f app/docker-compose.app.yml up -d` (로컬 WSL docker) | 중단 |
| 8 | Smoke Test | backend health, frontend 200 확인 | 중단(롤백 안내) |
| post | always: 리포트 archive, 워크스페이스 정리 / success·failure 요약 출력 | |

**채택안 (CodeReview, PR)**

| # | Stage | 내용 |
|---|---|---|
| 1 | Checkout PR | PR head(merge) 체크아웃, `CHANGE_ID`/`CHANGE_TARGET` 확인, base 브랜치 fetch |
| 2 | Build & Test | 컴파일+단위테스트 (리뷰 컨텍스트: 테스트 결과 요약을 총평에 포함) |
| 3 | Static Scan | Trivy fs + (선택) SonarQube `cicd-ai-flow-pr` 프로젝트 분석 |
| 4 | AI Code Review | `git diff <base>...HEAD` 를 파일/hunk 단위로 Gemini 에 전달 → 수정 부위마다 인라인 코멘트 + 총평 → GitHub PR Review(COMMENT) 등록 |
| 5 | Report | PR commit status / 빌드 설명 갱신 |

Gemini: `GEMINI_MODEL` 기본 `gemini-2.5-pro`(env 로 변경 가능), REST `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, 키는 Jenkins credential `gemini-api-key`. 키가 비어있으면 AI 리뷰 스테이지는 **UNSTABLE** 로 표기하고 나머지 스테이지는 정상 진행.
`gemini_review.py` 는 `--dry-run`(GitHub 에 올리지 않고 stdout 출력)과 `GEMINI_FAKE=1`(API 호출 없이 고정 응답) 모드를 지원해 키 없이도 검증 가능해야 한다.

## 6. 상호 검증 체크리스트 (에이전트 간 호출로 확인)

- [ ] `infra/scripts/up.sh` 실행 후 Jenkins/SonarQube/Agent 모두 healthy, `cicd-agent` 노드 online
- [ ] Jenkins 에 `cicd-ai-flow-cicd`, `cicd-ai-flow-codereview` 잡 존재, credential 3종 존재, SonarQube 서버 `SonarQube` 등록
- [ ] 에이전트 컨테이너 안에서 `app/backend` `./gradlew build jacocoTestReport` 와 `app/frontend` `npm ci && npm test -- --run --coverage && npm run build` 성공
- [ ] 루트 `sonar-project.properties` 로 sonar-scanner 실행 → Quality Gate PASS (클린 상태)
- [ ] `vuln-samples/apply.sh` 적용 후 Sonar Vulnerability/Hotspot 검출 + Trivy CVE 검출 → Quality Gate FAIL, `revert.sh` 후 다시 PASS
- [ ] `Jenkinsfile` 이 로컬 git 브랜치 소스로 생성한 멀티브랜치 잡에서 끝까지(Deploy, Smoke) SUCCESS
- [ ] `ci/scripts/gemini_review.py --dry-run` + `GEMINI_FAKE=1` 로 인라인 코멘트/총평 생성 확인
