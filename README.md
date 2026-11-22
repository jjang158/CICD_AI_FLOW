# cicd-ai-flow

로컬 WSL + Docker Desktop 위에 **Jenkins + SonarQube + Trivy** 로 구성한 CI/CD 코드 검증 자동화 환경과,
그 환경으로 검증되는 **React + Kotlin/Spring Boot** 샘플 애플리케이션, 그리고 **Gemini 기반 PR 코드 리뷰** 파이프라인이다.

```
GitHub (monorepo)                              로컬 WSL · Docker Desktop
 ├─ main 변경 ──▶ Jenkins "cicd-ai-flow-cicd"        ├─ cicd-jenkins        http://localhost:8080  (admin/admin)
 │               Checkout → Build → Test →           ├─ cicd-jenkins-agent  label cicd-agent (JDK21·Node22·docker·sonar-scanner·trivy)
 │               Code Scanning(Sonar+Trivy) →         ├─ cicd-sonarqube      http://localhost:9000  (admin/Admin1234!Sonar)
 │               Quality Gate → Package → Deploy →    ├─ cicd-sonar-db       postgres 16
 │               Smoke Test                           └─ (배포 결과) cicd-app-backend :8081, cicd-app-frontend :3000
 └─ PR 생성 ───▶ Jenkins "cicd-ai-flow-codereview"
                 Checkout PR → Build&Test → Static Scan →
                 AI Code Review(Gemini: diff hunk 마다 인라인 코멘트 + 총평) → GitHub PR Review 등록
```

## 1. 빠른 시작 (WSL Ubuntu 셸, 프로젝트 루트)

전제: Docker Desktop 이 실행 중이고 WSL 통합이 켜져 있어야 한다 (`docker info` 가 성공해야 함). sudo 는 필요 없다.

```bash
bash infra/scripts/up.sh          # 이미지 빌드 → 4개 컨테이너 기동 → SonarQube 부트스트랩 → healthcheck (최초 5~10분)
bash infra/scripts/healthcheck.sh # 언제든 상태 점검 (36항목)
bash infra/scripts/down.sh        # 중지 (볼륨 유지). --volumes 로 완전 삭제
```

기동 후 접속:

| 서비스 | URL | 계정 |
|---|---|---|
| Jenkins | http://localhost:8080 | `admin` / `admin` (`infra/.env` 의 `JENKINS_ADMIN_PASSWORD`) |
| SonarQube | http://localhost:9000 | `admin` / `Admin1234!Sonar` (`SONAR_ADMIN_PASSWORD`) |
| 배포된 앱 (main 빌드 후) | http://localhost:3000 (프론트), http://localhost:8081/actuator/health (백엔드) | – |

## 2. 사용자가 직접 입력해야 하는 값

`infra/.env` (최초 `up.sh` 가 `.env.example` 에서 복사, git 제외) 를 수정한 뒤 `bash infra/scripts/up.sh` 를 다시 실행하면 Jenkins 에 반영된다.

| 키 | 용도 |
|---|---|
| `GITHUB_OWNER`, `GITHUB_REPO` | 멀티브랜치 잡이 바라볼 GitHub 저장소 (예: `jjang158` / `CICD_AI_FLOW`, **`.git` 접미사 없이**). 비어 있으면 잡은 만들어지되 스캔하지 않음 |
| `GITHUB_USERNAME`, `GITHUB_TOKEN` | Jenkins credential `github-token` (PAT 권한: `repo`; PR 리뷰 코멘트 게시에도 사용) |
| `GEMINI_API_KEY` | Jenkins credential `gemini-api-key` (GCP Gemini API). 비어 있으면 AI 리뷰 스테이지만 UNSTABLE 로 건너뜀. 기본 모델은 `gemini-3.5-flash`(CodeReview 잡 파라미터 `GEMINI_MODEL` 로 변경, 404/429 시 `GEMINI_MODEL_FALLBACKS` 로 자동 전환). 무료 티어 키는 pro 계열이 429(quota) 를 돌려주므로 flash 계열을 권장 |

Jenkins UI 에서 직접 credential 을 수정해도 된다 (Manage Jenkins → Credentials → `github-token`, `gemini-api-key`).

## 3. GitHub 에 연결하기

1. GitHub 에 빈 저장소를 만들고 이 디렉터리를 push 한다 (`main` 브랜치, 취약 코드 데모용 `demo/vulnerable-code` 브랜치도 함께). 현재 연결된 저장소: `jjang158/CICD_AI_FLOW` (PR #1 = 데모 브랜치).
2. `infra/.env` 에 `GITHUB_OWNER`, `GITHUB_USERNAME`, `GITHUB_TOKEN`, `GEMINI_API_KEY` 를 채우고 `bash infra/scripts/up.sh` 재실행.
3. Jenkins 의 `cicd-ai-flow-cicd` 가 1분 주기로 `main` 을 스캔해 변경 시 빌드·배포하고, `cicd-ai-flow-codereview` 가 PR 을 발견하면 AI 리뷰를 PR 에 남긴다.
4. 로컬 Jenkins 는 인터넷에서 접근할 수 없으므로 GitHub 웹훅 대신 **1분 주기 스캔**을 쓴다. 즉시 트리거가 필요하면 ngrok/cloudflared 로 웹훅을 연결한다 (`docs/PIPELINE.md` 3.2).

## 4. 취약점 검출 데모

- `main` 의 앱은 **클린** 상태다: SonarQube 이슈 0건(커버리지 96.8%), Trivy HIGH/CRITICAL 0건 → Quality Gate PASS → 배포까지 진행.
- `demo/vulnerable-code` 브랜치는 `app/vuln-samples/apply.sh` 를 적용한 상태다: SonarQube Vulnerability **11건**(AES/ECB, DES, TLSv1, RSA 1024, MD5, 약한 난수, 하드코딩 시크릿, eval), Trivy **7건**(log4j-core 2.14.1 CVE-2021-44228 등, lodash 4.17.15) → Quality Gate **FAIL** → Package/Deploy 미실행.
- 이 브랜치로 PR 을 만들면 CodeReview 파이프라인이 변경 hunk 마다 인라인 코멘트와 총평(위험도, 머지 권고)을 PR 에 남긴다.
- 상세 규칙 표: `app/vuln-samples/README.md`. 시나리오: `docs/PIPELINE.md` 4장.

## 5. GitHub 없이 로컬에서 검증하기

`up.sh` 가 로컬 bare repo(`/var/jenkins_home/repos/cicd-ai-flow.git`) 를 소스로 하는 잡 두 개를 함께 만든다.

```bash
bash infra/scripts/push-local.sh main                       # 작업 트리를 bare repo 의 main 으로 푸시 → cicd-ai-flow-local 이 1분 내 빌드
# PR 리뷰 시뮬레이션 (Gemini 호출 없이 fake 응답, GitHub 미게시)
curl -u admin:admin -X POST "http://localhost:8080/job/cicd-ai-flow-local-codereview/buildWithParameters"
```

| 잡 | 내용 |
|---|---|
| `cicd-ai-flow-local` | 멀티브랜치, 모든 브랜치 빌드. `main` 만 Deploy/Smoke 실행, 다른 브랜치는 Sonar 프로젝트 `cicd-ai-flow-pr` 로 분석 |
| `cicd-ai-flow-local-codereview` | 파라미터화 파이프라인. `demo/vulnerable-code` → `main` PR 을 흉내 내어 `ci/Jenkinsfile.codereview` 전 스테이지 실행, 결과는 artifact `review-dryrun.md` |

## 6. 디렉터리

```
infra/        Jenkins·에이전트·SonarQube Docker 구성, JCasC, 부트스트랩/헬스체크 스크립트   → infra/README.md
app/backend   Kotlin 2.4 + Spring Boot 4.2 Todo API (Gradle, JaCoCo, 의존성 lock)         → app/backend/README.md
app/frontend  React 18 + TypeScript + Vite + Vitest, nginx(비root) 로 서빙                 → app/frontend/README.md
app/vuln-samples  의도적 취약 코드·취약 의존성 + apply.sh/revert.sh                          → app/vuln-samples/README.md
Jenkinsfile   CI/CD 파이프라인 (main)                                                      → docs/PIPELINE.md
ci/           CodeReview 파이프라인, gemini_review.py, trivy_gate.sh, smoke_test.sh
docs/         DESIGN.md(설계·계약), PIPELINE.md(파이프라인 가이드), VERIFICATION.md(검증 결과)
sonar-project.properties  루트 1개로 백엔드+프론트 동시 분석
```

## 7. 알아 둘 점

- **PC 재부팅 후**: Docker Desktop 은 자동 시작이 꺼져 있다. Docker Desktop 을 켜면 컨테이너가 자동 복구되지만(`restart: unless-stopped`), 안전하게 `bash infra/scripts/up.sh`(멱등) 또는 `healthcheck.sh` 를 한 번 실행한다. 에이전트가 `Exited` 면 `docker restart cicd-jenkins-agent` 로 10초 내 복구되고 Jenkins 노드는 약 30초 내 online 이 된다.
- GitHub PAT 는 `repo` 권한만 있으면 충분하다. 넓은 권한(admin:*, delete_repo 등)의 토큰은 쓰지 않는 것이 좋다.

- SonarQube 는 Community Build 라 브랜치/PR 분석이 없다. 그래서 main 은 `cicd-ai-flow`, 그 외(PR·브랜치)는 `cicd-ai-flow-pr` 프로젝트로 분석한다.
- 기본 Quality Gate `Sonar way` 는 "새 코드" 기준이라 프로젝트의 첫 분석은 무조건 PASS 된다. 이를 피하려고 부트스트랩이 전체 코드 기준 게이트 `cicd-ai-flow-gate`(vulnerabilities=0, bugs=0, 보안/신뢰성 A, 커버리지≥80%, 중복≤3%, 새 이슈 0) 를 만들어 두 프로젝트에 할당한다.
- Spring Boot 3.5 계열은 Spring Framework 6.2 의 CRITICAL CVE 가 패치되지 않아 Trivy 0건을 만들 수 없어 Spring Boot 4.2 를 사용했다.
- 시간이 지나 새 CVE 가 공개되면 클린 main 도 Trivy 게이트에서 실패할 수 있다. 의존성을 올리고 `gradle.lockfile`/`package-lock.json` 을 재생성하거나, 불가피하면 루트 `.trivyignore` 에 사유와 함께 등록한다 (`docs/PIPELINE.md` 5장).
# CICD_AI_FLOW
# CICD_AI_FLOW
