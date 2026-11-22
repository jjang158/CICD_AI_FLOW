# cicd-ai-flow 파이프라인 가이드 (Task 3)

이 문서는 루트 `Jenkinsfile`(CI/CD), `ci/Jenkinsfile.codereview`(PR AI 리뷰), `ci/scripts/*` 의 설계·운영 가이드다.
고정 계약(잡 이름, credential ID, 포트, 경로)은 [`docs/DESIGN.md`](DESIGN.md) 를 따른다.

| 파일 | 역할 | Jenkins 잡 |
|---|---|---|
| `Jenkinsfile` | CI/CD: 빌드 → 테스트 → 스캔 → Quality Gate → 이미지 → 배포 → 스모크 | `cicd-ai-flow-cicd` (GitHub, `main` 만), `cicd-ai-flow-local` (로컬 bare repo, 모든 브랜치) |
| `ci/Jenkinsfile.codereview` | PR: 빌드/테스트 → Trivy → Gemini 리뷰 → GitHub PR Review 게시 | `cicd-ai-flow-codereview` (GitHub PR 만, script path `ci/Jenkinsfile.codereview`) |
| `ci/scripts/gemini_review.py` | `git diff` → hunk 파싱 → Gemini → PR Review(인라인 + 총평) | 위 PR 잡의 *AI Code Review* 스테이지 |
| `ci/scripts/trivy_gate.sh` | Trivy JSON 심각도 집계 + 게이트 (exit 1) | 두 잡 모두 |
| `ci/scripts/smoke_test.sh` | 배포 컨테이너를 docker network 경유로 헬스체크 | CI/CD *Smoke Test* |
| `ci/scripts/test_gemini_review.py` | `gemini_review.py` 단위/통합 테스트 (표준 라이브러리 unittest) | 로컬 검증 |

---

## 1. CI/CD 파이프라인 (`Jenkinsfile`)

### 1.1 스테이지 최종안

```
Checkout & Prepare
   └─ Build ──────────── [Backend ‖ Frontend]
        └─ Test ───────── [Backend ‖ Frontend]      (JUnit / JaCoCo / lcov publish)
             └─ Code Scanning ─ [SonarQube ‖ Trivy fs]   (리포트만)
                  └─ Quality Gate  (Sonar waitForQualityGate + Trivy HIGH/CRITICAL = 0)  ← 여기서 실패하면 이후 전부 중단
                       └─ Package ─ [Backend image ‖ Frontend image]  (docker build + trivy image + CRITICAL 게이트)
                            └─ Deploy  (docker compose -p cicd-ai-flow-app up -d --no-build)
                                 └─ Smoke Test (backend health / frontend / api proxy)
post: always → 리포트 archive · success/failure/unstable → 요약 echo + build description · cleanup → cleanWs
```

| # | Stage | 수행 내용 | 실패 조건 (→ 빌드 FAILURE, 이후 중단) |
|---|---|---|---|
| 1 | **Checkout & Prepare** | `checkout scm`, `IMAGE_TAG`=짧은 git sha(8자) 계산, `chmod +x gradlew ci/scripts/*.sh vuln-samples/*.sh`, CRLF 제거, 도구 버전 출력 | 체크아웃 실패, 필수 도구 없음 |
| 2 | **Build** (parallel) | backend `./gradlew clean compileKotlin compileTestKotlin --no-daemon` / frontend `npm ci && npm run lint && npm run build` | 컴파일 오류, lint 오류(ESLint), 타입 오류(`tsc -b`) |
| 3 | **Test** (parallel) | backend `./gradlew test jacocoTestReport` → `junit`, `jacoco`, HTML publish / frontend `npm run test -- --run --coverage` → `junit coverage/junit.xml`, lcov HTML publish | 테스트 실패(UNSTABLE 이 아니라 gradle/vitest 가 non-zero 로 끝나므로 FAILURE), 리포트 파일 없음 |
| 4 | **Code Scanning** (parallel) | `withSonarQubeEnv('SonarQube') { sonar-scanner -Dsonar.projectKey=$SONAR_KEY -Dsonar.projectVersion=$IMAGE_TAG }` (루트 `sonar-project.properties`; **`main` 이면 `cicd-ai-flow`, 그 외 브랜치(데모 `demo/vulnerable-code`, 로컬 검증 잡)는 `cicd-ai-flow-pr`** 로 분석해 main 의 새 코드 기준을 오염시키지 않음) / `trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --ignore-unfixed --skip-dirs … --format json -o trivy-fs.json .` + `trivy convert --format table` 로그 출력 | 스캐너 실행 자체의 오류(서버 접속 불가, 토큰 오류). **발견된 취약점으로는 실패하지 않음** (판정은 5) |
| 5 | **Quality Gate** | `ci/scripts/trivy_gate.sh trivy-fs.json $TRIVY_SEVERITY` → `waitForQualityGate()` (10분 timeout, SonarQube webhook 필요) → 두 결과를 모아 한 번에 판정 | Sonar Quality Gate ≠ OK **또는** Trivy HIGH/CRITICAL ≥ 1건 **또는** webhook 미도착으로 10분 timeout |
| 6 | **Package** (parallel) | `docker build -t cicd-ai-flow/{backend,frontend}:$IMAGE_TAG -t …:latest app/{backend,frontend}` → `trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 0 --format json` → `trivy_gate.sh … $TRIVY_IMAGE_SEVERITY`(기본 **CRITICAL** 만) | 이미지 빌드 실패, 이미지 CRITICAL CVE ≥ 1건 |
| 7 | **Deploy** | `IMAGE_TAG=… docker compose -p cicd-ai-flow-app -f app/docker-compose.app.yml up -d --remove-orphans --no-build` — **`BRANCH_NAME == main` 일 때만** 실행(그 외 브랜치는 Package 까지만), `SKIP_DEPLOY=true` 면 skip | compose 실패(포트 충돌 8081/3000, 이미지 없음, healthcheck 로 `depends_on` 대기 실패) |
| 8 | **Smoke Test** | `ci/scripts/smoke_test.sh` – 컨테이너 포트를 `docker inspect` 로 자동 탐색(frontend 8080/80, backend 8081), 최대 30회 × 2초 재시도. `main` 에서만 실행 | `backend/actuator/health` ≠ UP, `backend/api/health` 실패, frontend `/` ≠ HTML, `/api/todos` 가 JSON 아님 → compose ps/logs 출력 + **롤백 안내** |

### 1.2 사용자 초안 대비 변경 이유 (DESIGN.md 5장)

사용자 초안: `Code Build → Code Scanning → Code Quality Validate → Code Test → Code Build → Code Deploy`

| 변경 | 이유 |
|---|---|
| Build 2회 → 1회 (Build + Package 로 역할 분리) | 소스 컴파일(Build)과 Docker 이미지 생성(Package)은 다른 일이며, 같은 컴파일을 두 번 할 이유가 없다. 이미지는 Quality Gate 통과 후에만 만든다. |
| Test 를 Scanning **앞**으로 | SonarQube 는 JaCoCo XML(`app/backend/build/reports/jacoco/test/jacocoTestReport.xml`)과 lcov(`app/frontend/coverage/lcov.info`)를 **입력**으로 받아 커버리지/새 코드 조건을 판정한다. 테스트가 뒤에 있으면 커버리지 0% 로 게이트가 항상 실패한다. |
| Quality Gate 를 Scanning **직후** 독립 스테이지로 | `waitForQualityGate` 는 분석 직후 webhook 으로 결과를 받는다. 판정이 별도 스테이지여야 Stage View 에서 "어디서 막혔는지" 한눈에 보이고, Package 이후를 확실히 차단할 수 있다. |
| Scanning 에 Trivy fs 추가, Package 에 Trivy image 추가 | Sonar 는 코드 품질/보안 코드 패턴, Trivy 는 의존성 CVE·시크릿·Dockerfile 설정·베이스 이미지 CVE 를 본다. 둘은 보완 관계다. |
| Smoke Test 추가 | "배포 성공" 의 정의를 컨테이너 기동이 아니라 실제 HTTP 응답으로 둔다. |
| Build/Test/Scanning/Package 를 parallel | backend(Gradle)와 frontend(npm)는 독립적이라 병렬화로 시간을 줄인다. 같은 workspace 를 공유한다(디렉터리가 다르므로 충돌 없음). |

### 1.3 파라미터

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `SKIP_DEPLOY` | `false` | `true` 면 Deploy/Smoke 생략 (이미지 빌드·스캔까지만) |
| `TRIVY_SEVERITY` | `HIGH,CRITICAL` | Trivy fs 보고·게이트 심각도. 이미지 스캔의 **보고** 심각도로도 사용 |
| `TRIVY_IMAGE_SEVERITY` | `CRITICAL` | Trivy image **게이트** 심각도. 베이스 OS(Debian/Alpine) 패키지의 HIGH CVE 가 흔해 기본은 CRITICAL 만 |
| `TRIVY_SCANNERS` | `vuln,secret,misconfig` | Trivy fs 스캐너 |
| `TRIVY_SKIP_DIRS` | `**/node_modules,**/.git,app/vuln-samples,app/backend/.gradle,app/backend/build,app/frontend/dist,app/frontend/coverage,infra` | 스캔 제외. `app/vuln-samples` 는 "미적용 샘플" 이므로 제외, `infra` 는 CI 플랫폼 자체(Jenkins/agent Dockerfile 은 의도적으로 root)라 앱 게이트에서 제외. `app/backend/build` 를 빼지 않으면 app.jar 내부 jar 재스캔 + trivy-java-db 다운로드가 발생 |
| `SONAR_PR_BASELINE` | `true` | main 빌드에서 `cicd-ai-flow-pr` 프로젝트에도 **클린 baseline 분석**을 먼저 올린다(그 다음 main 분석 → `report-task.txt` 가 main 을 가리켜 `waitForQualityGate` 는 main 게이트를 기다림). PR/데모 브랜치 분석의 "새 코드(previous version)" 기준이 된다 |

환경변수(고정): `SONAR_PROJECT_KEY=cicd-ai-flow`, `SONAR_PR_PROJECT_KEY=cicd-ai-flow-pr`, `MAIN_BRANCH=main`, `IMAGE_REPO=cicd-ai-flow`, `COMPOSE_FILE=app/docker-compose.app.yml`, `COMPOSE_PROJECT=cicd-ai-flow-app`, `GRADLE_OPTS=-Dorg.gradle.daemon=false`, `CI=true`. Prepare 스테이지가 `IMAGE_TAG`(짧은 sha), `IS_MAIN`(`BRANCH_NAME == MAIN_BRANCH`), `SONAR_KEY`(main → `cicd-ai-flow`, 그 외 → `cicd-ai-flow-pr`), `SONAR_URL`(대시보드 링크)을 `env` 로 설정한다.

> 멀티브랜치 잡의 **첫 빌드**에서는 파라미터 정의가 아직 저장되기 전이라 `params.X` 가 null 일 수 있다. 그래서 모든 사용처가 `params.X ?: 기본값` 형태다.

### 1.4 Quality Gate 동작 세부

- Trivy 게이트를 먼저(빠름) 실행해 결과 코드를 보관하고, `waitForQualityGate(abortPipeline:false)` 로 Sonar 결과를 받은 뒤 **두 결과를 합쳐** `error()` 로 중단한다. 로그에 두 원인이 모두 남는다.
- `waitForQualityGate` 는 SonarQube → Jenkins **webhook**(`http://jenkins:8080/sonarqube-webhook/`, 이름 `jenkins`) 이 있어야 돌아온다. 없으면 10분 후 timeout 으로 실패한다 (`infra/scripts/bootstrap-sonar.sh` 가 등록).
- `.trivyignore` 가 리포지토리 루트에 있으면 Trivy 가 자동으로 읽는다(CVE ID 한 줄에 하나, `#` 주석). 근거를 주석으로 남겨라.
- Sonar 대시보드: <http://localhost:9000/dashboard?id=cicd-ai-flow> (main) / <http://localhost:9000/dashboard?id=cicd-ai-flow-pr> (그 외 브랜치·PR) — 빌드 description 에도 기록된다.
- **"첫 분석은 무조건 PASS" 주의**: 기본 게이트 `Sonar way` 는 조건이 전부 **새 코드** 기준이라 어떤 projectKey 든 첫 분석에는 조건이 평가되지 않아 OK 가 난다(SonarQube Community Build 26.11 실측). 취약 브랜치가 `cicd-ai-flow-pr` 의 첫 분석이면 Sonar 는 OK, Trivy 게이트(7건)로만 FAIL 한다. 그래서 (1) main 빌드가 `cicd-ai-flow-pr` 에 클린 baseline 을 먼저 올리고(`SONAR_PR_BASELINE`), (2) `infra/scripts/bootstrap-sonar.sh` 가 **전체 코드 기준** 전용 게이트 `cicd-ai-flow-gate`(vulnerabilities=0, bugs=0, security/reliability A, coverage≥80, duplication≤3, new_violations=0)를 두 프로젝트에 할당한다 — 이 게이트가 적용되면 취약 브랜치는 첫 분석부터 ERROR 다. 어느 게이트로 판정됐는지는 `GET /api/qualitygates/project_status?projectKey=<key>` 의 `conditions` 로 확인한다.
- 한 번 취약 코드가 분석된 projectKey 는 이슈가 "기존 이슈"로 남아(닫힘 → 재오픈 시 최초 생성일 유지) 새 코드 기준 게이트로는 다시 잡히지 않는다. 데모 전에 `-pr` 프로젝트를 깨끗이 하려면 `POST /api/projects/delete?project=cicd-ai-flow-pr` 후 `POST /api/projects/create?project=cicd-ai-flow-pr&name=cicd-ai-flow-pr`(웹훅은 전역이라 유지됨).

### 1.5 Smoke Test 의 네트워크 접근 방식

Jenkins 에이전트는 컨테이너(`cicd-jenkins-agent`, 네트워크 `cicd-net`)이므로 호스트 포트 `localhost:8081/3000` 에 바로 닿지 못할 수 있다. `smoke_test.sh` 는

1. `docker compose -p cicd-ai-flow-app ps --services/-q` 로 서비스·컨테이너를 찾고 `docker inspect` 로 네트워크 이름(`cicd-ai-flow-app-net`)을 얻은 뒤
2. `docker run --rm --network <net> curlimages/curl:8.11.1 -fsS http://backend:8081/actuator/health` 식으로 **같은 네트워크 안에서** 검사한다 (`mode=net`).
3. curl 이미지를 못 받으면 `docker exec <backend> curl|wget` (`mode=exec`), 그것도 안 되면 에이전트의 `curl http://host.docker.internal:8081` (`mode=host`, infra compose 가 `extra_hosts: host.docker.internal:host-gateway` 를 넣어 둠) 로 fallback 한다.

포트는 고정하지 않는다: 컨테이너의 `Config.ExposedPorts` / `HostConfig.PortBindings` 를 `docker inspect` 로 읽어 frontend 는 80 → 8080 → 3000, backend 는 8081 → 8080 순으로 후보를 만들고(환경변수 `FRONTEND_PORT`/`BACKEND_PORT` 로 고정 가능) 매 시도마다 후보를 차례로 시험한다. 현재 이미지(`nginxinc/nginx-unprivileged`)는 **8080** 이 탐색된다. host 모드의 publish 포트도 `PortBindings` 에서 읽는다(없으면 8081/3000).

검사 항목: `backend:<port>/actuator/health` 에 `"status":"UP"`, `backend:<port>/api/health` 가 JSON/UP, `frontend:<port>/` 가 HTML, `frontend:<port>/api/todos` 가 JSON(`[`/`{` 로 시작 → nginx `/api` 프록시 동작 확인).

---

## 2. CodeReview 파이프라인 (`ci/Jenkinsfile.codereview`)

### 2.1 스테이지

| # | Stage | 내용 | 실패/UNSTABLE 조건 |
|---|---|---|---|
| 0 | Not a PR | `CHANGE_ID` 가 없으면 모든 스테이지 skip (브랜치 빌드 보호) | – |
| 1 | **Checkout PR** | `checkout scm`(PR discovery strategy 1 = target 과 merge 한 커밋) → `github-token` 으로 `git fetch origin <CHANGE_TARGET>` → `PR_HEAD_SHA = HEAD^2`(merge 커밋이 아니면 HEAD) → `BASE_SHA = git merge-base origin/<target> PR_HEAD_SHA` → `GH_REPO` 를 `CHANGE_URL` 에서 파싱 | 체크아웃 실패. base fetch 실패는 경고 후 `HEAD^1` 로 대체 |
| 2 | **Build & Test** (parallel) | backend `./gradlew build jacocoTestReport`(JaCoCo 80% 검증 포함) / frontend `npm ci && npm run lint && npm run test -- --run --coverage`, `junit` publish, 결과를 `ci-summary.txt` 로 기록 | 실패해도 `catchError` 로 **빌드 UNSTABLE / 스테이지 FAILURE** 표시 후 리뷰 계속 |
| 3 | **Static Scan** | Trivy fs(json + table) → `trivy_gate.sh --report-only` 출력을 `ci-summary.txt` 에 append. `SONAR_PR_SCAN=true` 면 `sonar-scanner -Dsonar.projectKey=cicd-ai-flow-pr` + 게이트 결과도 append | Trivy 실행 오류만 실패. 취약점 발견은 리뷰 총평 입력일 뿐 |
| 4 | **AI Code Review** | `withCredentials(gemini-api-key, github-token)` → `python3 ci/scripts/gemini_review.py --base $BASE_SHA --head $PR_HEAD_SHA --pr $CHANGE_ID --repo owner/repo --summary-file ci-summary.txt …` | `GEMINI_API_KEY` 비어 있음(exit 3) → **UNSTABLE** 후 통과(단 `GEMINI_FAKE=1` 이면 키 없이도 가짜 백엔드로 정상 실행). 그 외 실패(exit 2/4) → 빌드 UNSTABLE, 스테이지 FAILURE |
| 5 | **Report** | `review-result.json` 을 읽어 `currentBuild.description` 에 PR 번호/코멘트 수/권고/게시 모드/URL 기록. GitHub commit status 는 github-branch-source 가 빌드 결과로 자동 갱신 | – |
| post | `review-*.json`, `review-dryrun.md`, `ci-summary.txt`, `trivy-*.json`, 리포트 archive → `cleanWs` | |

### 2.2 파라미터

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `SONAR_PR_SCAN` | `false` | SonarQube CE 는 PR 분석이 없으므로 별도 projectKey `cicd-ai-flow-pr` 로 분석 (대시보드 `?id=cicd-ai-flow-pr`) |
| `GEMINI_MODEL` | `gemini-2.5-pro` | 모델 변경 (예: `gemini-2.5-flash` 로 속도/비용 절감) |
| `REVIEW_LANGUAGE` | `ko` | 코멘트 언어 (`ko`/`en`) |
| `REVIEW_MAX_FILES` | `50` | 초과 파일은 총평에 "미검토" 로 표기 |
| `REVIEW_GRANULARITY` | `file` | `file`: 파일당 Gemini 1회 호출(모든 hunk 에 코멘트 요구, 빠진 hunk 는 개별 재요청) / `hunk`: hunk 마다 1회 (정확하지만 호출 수 ↑, 무료 티어 RPM 주의) |
| `REVIEW_DRY_RUN` | `false` | GitHub 에 올리지 않고 `review-dryrun.md` 만 생성 |
| `GEMINI_FAKE` | `` (빈 값) | `1` 이면 Gemini API 를 호출하지 않고 결정적 가짜 백엔드 사용. `GEMINI_API_KEY` 가 비어 있어도 스테이지가 SUCCESS 로 끝난다(검증/시뮬레이션용) |

### 2.3 로컬 시뮬레이션 잡 `cicd-ai-flow-local-codereview` (GitHub 없이)

Task 1 의 JCasC 가 만드는 파라미터 잡으로, 로컬 bare repo(`file:///var/jenkins_home/repos/cicd-ai-flow.git`)의 `REVIEW_BRANCH` 를 체크아웃하고 멀티브랜치 PR 환경변수(`CHANGE_ID`, `CHANGE_TARGET`, `CHANGE_BRANCH`, `CHANGE_URL`, `CHANGE_AUTHOR`, `CHANGE_TITLE`)를 **빌드 파라미터로 주입**해 `ci/Jenkinsfile.codereview` 를 그대로 실행한다. 기본값 `REVIEW_BRANCH=demo/vulnerable-code`, `GEMINI_FAKE=1`, `REVIEW_DRY_RUN=true` 이면 Gemini/GitHub 를 전혀 호출하지 않으며 **`gemini-api-key` 가 비어 있어도 AI Code Review 스테이지가 SUCCESS** 로 끝난다(결과물: `review-dryrun.md`, `review-result.json`, `ci-summary.txt`, `trivy-fs.json` artifact). 파라미터 표·실행 명령(`buildWithParameters`; 파라미터 잡이라 `POST …/build` 는 400)·주의 사항은 [`infra/README.md` 6.3 절](../infra/README.md#63-pr-리뷰-시뮬레이션-잡-cicd-ai-flow-local-codereview) 참고. 최신 코드를 반영하려면 먼저 `bash infra/scripts/push-local.sh main` 으로 스냅샷을 push 한다. 실제 Gemini 로 돌려 보려면 `GEMINI_FAKE` 를 비우고(키 필요) `REVIEW_DRY_RUN=true` 를 유지하면 GitHub 게시 없이 markdown 만 생성된다.

### 2.4 `gemini_review.py` 동작

```
git diff --unified=3 --find-renames <base>...<head>
 ├─ 파일/hunk 파싱: 새 파일 라인 번호 추적, 추가·수정 라인(+) 과 문맥 라인 구분
 ├─ 제외: lock 파일(package-lock.json, gradle.lockfile, yarn.lock …) → 총평에 "lock 파일 변경 있음" 표기
 │        node_modules/ dist/ build/ coverage/ … , 바이너리(*.jar, *.png …), 내용 변경 없는 rename
 ├─ 파일(또는 hunk) 단위로 Gemini generateContent (responseMimeType=application/json, 재시도 3회: 429/5xx 지수 백오프,
 │   JSON 파싱 실패 시 1회 재요청 → 그래도 실패하면 해당 hunk 는 "자동 리뷰 실패" 코멘트)
 ├─ hunk 마다 최소 1개 코멘트 보장 (문제 없으면 "LGTM: …" info)
 ├─ 라인 검증: line 이 hunk 의 새 파일 범위(문맥+추가) 밖이면 첫 추가 라인으로 보정 (GitHub 422 방지)
 │   삭제 전용 hunk → 파일 수준 코멘트(총평 표)
 ├─ 총평 1회 호출: 변경 요약 · 위험도(low/medium/high) · 머지 권고(approve/request-changes/comment)
 │   · 테스트/스캔 요약(ci-summary.txt 포함) · 심각도/카테고리 통계
 ├─ 코멘트 > 80건이면 severity 순으로 80건만 인라인, 나머지는 총평 표
 ├─ GET /repos/{o}/{r}/pulls/{n} 로 head.sha → POST /pulls/{n}/reviews (event=COMMENT, comments[].side=RIGHT)
 │   실패(422 등) → POST /issues/{n}/comments 로 총평 + 코멘트 목록 fallback
 └─ review-result.json 저장, stdout 요약
```

- 키는 **헤더 `x-goog-api-key`** 로만 전송하고 로그에 출력하지 않는다. 종료 코드: `0` 정상, `2` 인자/git 오류, `3` `GEMINI_API_KEY` 없음, `4` GitHub 게시 실패.
- 검증 모드: `GEMINI_FAKE=1`(API 호출 없이 결정적 응답; 하드코딩 비밀/SQL 결합/`dangerouslySetInnerHTML`/`console.log`/TODO 패턴을 잡아 줌) + `--dry-run`(GitHub 미호출, `review-dryrun.md`).

```bash
# 로컬 검증 (WSL / 에이전트 컨테이너 공통, 표준 라이브러리만 필요)
python3 ci/scripts/test_gemini_review.py -v
GEMINI_FAKE=1 python3 ci/scripts/gemini_review.py --base origin/main --head HEAD --dry-run --summary-file ci-summary.txt
# 실제 Gemini 로 dry-run (GitHub 에는 안 올림)
GEMINI_API_KEY=... python3 ci/scripts/gemini_review.py --base origin/main --head HEAD --dry-run
```

---

## 3. GitHub 설정 가이드

### 3.1 Personal Access Token (`github-token` credential)

| 토큰 종류 | 필요 권한 |
|---|---|
| Classic PAT | `repo` (private repo 읽기 + PR review/comment 게시 + commit status), `workflow` (DESIGN.md 계약), `read:org` (조직 repo 를 Jenkins 가 나열할 때 선택) |
| Fine-grained PAT | Repository permissions: **Contents: Read**, **Pull requests: Read and write**, **Commit statuses: Read and write**, Metadata: Read |

- PR Review 는 토큰 소유자 계정 이름으로 게시된다. 봇 계정을 쓰려면 그 계정의 PAT 를 넣어라. 자기 PR 에 "Request changes" 는 GitHub 이 막지만, 이 파이프라인은 `event=COMMENT` 만 쓰므로 문제없다.
- 입력: `infra/.env` 의 `GITHUB_USERNAME`, `GITHUB_TOKEN`(JCasC 가 `github-token` 으로 주입) 또는 Jenkins UI *Manage Credentials → github-token → Update*.

### 3.2 트리거: 1분 주기 스캔 (기본) vs 웹훅

로컬 Jenkins 는 인터넷에서 접근할 수 없으므로 두 멀티브랜치 잡은 **`periodicFolderTrigger 1m`** 로 GitHub 를 폴링한다(`infra/jenkins/casc/jenkins.yaml`). PR 을 열거나 push 하면 최대 1분 내 빌드가 시작된다. (API rate limit: 인증 토큰 기준 5,000 req/h, 1분 스캔은 충분히 여유)

웹훅으로 즉시 트리거하려면 터널을 열고 GitHub 웹훅을 등록한다.

```bash
# ngrok
ngrok http 8080            # https://xxxx.ngrok-free.app
# 또는 cloudflared (계정 불필요한 quick tunnel)
cloudflared tunnel --url http://localhost:8080
```

GitHub repo → *Settings → Webhooks → Add webhook*: Payload URL `https://<tunnel-host>/github-webhook/`, Content type `application/json`, Events: *Pushes*, *Pull requests*. Jenkins *Manage Jenkins → System → GitHub* 서버 항목에서 "Manage hooks" 를 켜면 Jenkins 가 자동 등록도 한다. 터널 주소가 바뀌면 웹훅 URL 도 갱신해야 하므로 데모에는 주기 스캔이 더 단순하다.

### 3.3 Gemini API 키

1. [Google AI Studio](https://aistudio.google.com/apikey) 에서 키 발급.
2. 입력 방법 (둘 중 하나)
   - **`infra/.env`** 의 `GEMINI_API_KEY=...` 를 채우고 `bash infra/scripts/up.sh` 재실행 (JCasC 가 `gemini-api-key` secret text 로 주입).
   - **Jenkins UI**: *Manage Jenkins → Credentials → System → Global → gemini-api-key → Update → Secret* 입력. (JCasC 재로드 시 `.env` 값으로 다시 덮어쓰이므로 `.env` 도 맞춰 두는 것을 권장)
3. 키가 비어 있으면 *AI Code Review* 스테이지가 `WARNING: GEMINI_API_KEY 가 비어 있습니다` 를 남기고 **UNSTABLE** 로 끝난다. 나머지 스테이지는 정상 진행.
4. 모델 변경: 잡 파라미터 `GEMINI_MODEL`(빌드 시 지정) 또는 `ci/Jenkinsfile.codereview` 의 기본값 수정. 스크립트 단독 실행 시에는 환경변수 `GEMINI_MODEL` 또는 `--model`. REST 엔드포인트는 `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` 이며 `GEMINI_API_URL` 로 바꿀 수 있다(테스트용).
5. 무료 티어는 분당 요청 수 제한이 낮다(모델별 상이). 429 는 3회 지수 백오프로 재시도하지만, 큰 PR 은 `REVIEW_GRANULARITY=file`(기본) 을 유지하고 `gemini-2.5-flash` 를 고려하라.

---

## 4. 취약 코드 데모 시나리오

전제: `infra/scripts/up.sh` 완료, `github-token`/`gemini-api-key` 입력, GitHub 에 `main` push 완료, `cicd-ai-flow-cicd` 첫 빌드 SUCCESS(클린 상태 = Sonar PASS + Trivy 0건).

```bash
git checkout -b demo/vulnerable-code
bash app/vuln-samples/apply.sh          # Kotlin/TS 취약 샘플 + 취약 의존성(log4j-core 2.14.1, lodash 4.17.15 …) 적용
git add -A && git commit -m "demo: intentionally vulnerable code"
git push -u origin demo/vulnerable-code
# GitHub 에서 demo/vulnerable-code -> main PR 생성
```

1. **PR 생성 → `cicd-ai-flow-codereview`** 가 1분 내 시작. Build & Test 는 통과하거나 UNSTABLE, Static Scan 의 Trivy 가 HIGH/CRITICAL 을 `ci-summary.txt` 에 남기고, *AI Code Review* 가 **수정 hunk 마다** 인라인 코멘트(하드코딩 비밀, SQL 문자열 결합, 약한 암호화, `dangerouslySetInnerHTML` …)와 총평(위험도 high, 권고 request-changes, Trivy 요약 포함)을 PR 의 *Files changed / Conversation* 에 게시한다. 빌드 description 에 코멘트 수와 리뷰 URL 이 보인다.
2. **PR 을 main 에 머지 → `cicd-ai-flow-cicd`** 가 실행되어 Build/Test 통과 후 **Quality Gate 스테이지에서 FAILURE**: SonarQube 가 Vulnerability/Security Hotspot 으로 `Sonar way` 게이트를 FAIL 시키고, Trivy 가 CVE 를 보고한다. 로그 마지막에 두 원인이 함께 출력되고 Package/Deploy 는 실행되지 않는다. 기존에 배포된 컨테이너는 그대로 남는다(이전 버전 서비스 유지).
3. **revert**: `git revert <merge-commit> -m 1` 또는 `bash app/vuln-samples/revert.sh && git commit -am "revert demo"` → push → 다음 CI/CD 빌드가 Quality Gate **PASS** → Package → Deploy → Smoke SUCCESS.

GitHub 없이 돌려 보려면 `cicd-ai-flow-local` 잡(로컬 bare repo, `infra/scripts/push-local.sh`)에 같은 브랜치를 push 하면 Jenkinsfile 이 동일하게 실행된다(PR 리뷰 잡은 GitHub PR 이 필요). 이 잡은 **모든 브랜치**를 discover 하므로 `demo/vulnerable-code` 브랜치 빌드는 SonarQube `cicd-ai-flow-pr` 프로젝트로 분석되고(대시보드 `?id=cicd-ai-flow-pr`) Quality Gate 에서 FAIL 로 멈춘다 — main 의 Sonar 이력과 배포(`cicd-app-*`)에는 영향이 없다. main 이 아닌 브랜치는 게이트를 통과하더라도 Package 까지만 수행하고 Deploy/Smoke 는 건너뛴다.

---

## 5. Trivy 게이트와 클린 상태 유지

`trivy_gate.sh <json> [SEVERITIES] [--report-only]` 는 `.Results[].Vulnerabilities[]`, `.Secrets[]`, `.Misconfigurations[]`(Status≠PASS) 를 jq(없으면 python3)로 집계해 심각도 × 종류 표와 게이트 대상 목록(최대 `GATE_MAX_LIST`=50)을 출력하고, 대상이 1건 이상이면 exit 1 이다.

```
Severity    Vulns  Secrets  Misconfigs   Total
CRITICAL        1        1           0       2
HIGH            1        0           1       2
...
Gated findings (HIGH,CRITICAL): 4
  [CRITICAL] vuln CVE-2021-44228 org.apache.logging.log4j:log4j-core 2.14.1 -> 2.15.0 (app/backend/gradle.lockfile) ...
RESULT: FAIL - 4 finding(s) at HIGH,CRITICAL. 수정하거나 근거를 남기고 .trivyignore 에 추가하세요.
```

클린 상태(main)를 PASS 로 유지하려면:

- **의존성 버전을 올린다**가 원칙. Trivy DB 는 매일 갱신되므로 어제 PASS 가 오늘 FAIL 이 될 수 있다(`--ignore-unfixed` 라 수정판이 없는 CVE 는 제외됨).
- 즉시 올릴 수 없으면 루트 `.trivyignore` 에 CVE ID 를 근거 주석과 함께 추가한다. Trivy 는 작업 디렉터리의 `.trivyignore` 를 자동 적용하며, 두 파이프라인 모두 리포지토리 루트에서 실행된다.
- Dockerfile 설정 검사(`misconfig`): 프론트 nginx 이미지에 `USER` 가 없으면 `AVD-DS-0002`(HIGH)가 걸린다. `nginxinc/nginx-unprivileged` 베이스로 바꾸거나 `.trivyignore` 에 `AVD-DS-0002` 를 넣는다. `infra/` 는 기본 `TRIVY_SKIP_DIRS` 로 제외되어 있다.
- 이미지 게이트는 베이스 OS 패키지 CVE 가 수시로 생기므로 기본 CRITICAL 만 본다(`TRIVY_IMAGE_SEVERITY`). HIGH 까지 막고 싶으면 파라미터를 `HIGH,CRITICAL` 로.

---

## 6. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| Quality Gate 에서 10분 timeout (`waitForQualityGate` 가 안 돌아옴) | SonarQube webhook 미등록. *Administration → Configuration → Webhooks* 에 `jenkins` → `http://jenkins:8080/sonarqube-webhook/` 가 있는지 확인(`bootstrap-sonar.sh` 재실행). 프로젝트 단위 webhook 도 가능 |
| `sonar-scanner` 가 401/403 | `sonar-token` credential 이 비었거나 만료. `bootstrap-sonar.sh` 가 생성한 토큰을 Jenkins *SonarQube servers → SonarQube* 에 연결했는지 확인 |
| Sonar 가 커버리지 0% | Test 가 Scanning 보다 먼저 실행됐는지, `jacocoTestReport.xml`/`lcov.info` 경로가 `sonar-project.properties` 와 일치하는지 확인 |
| Trivy `failed to download vulnerability DB` | 에이전트의 외부 네트워크(ghcr.io) 차단. DB 캐시는 `/home/jenkins/.cache/trivy`(volume `agent_cache`) 에 남으므로 한 번만 받으면 됨. `trivy image --download-db-only` 로 선행 가능 |
| Trivy 가 클린 main 에서 FAIL | 5장 참고. 새 CVE 공개 또는 Spring Boot 전이 의존성(tomcat-embed, jackson, spring-webmvc) 패치 필요. 임시로 `.trivyignore` |
| `docker: permission denied` (`/var/run/docker.sock`) | `infra/.env` 의 `DOCKER_GID` 가 호스트 소켓 gid 와 다름(`stat -c %g /var/run/docker.sock`). 에이전트 이미지 재빌드 |
| Deploy 에서 `port is already allocated` (8081/3000) | 다른 프로세스가 포트 사용. `docker compose -p cicd-ai-flow-app down` 또는 점유 프로세스 정리 |
| Smoke Test 가 `mode=host` 로 떨어지고 실패 | curl 이미지 pull 불가 + 백엔드 이미지에 curl/wget 없음(JRE 이미지). `host.docker.internal` 이 에이전트에서 해석되는지(`extra_hosts`) 확인. `CURL_IMAGE` 환경변수로 다른 이미지를 지정할 수 있음 |
| PR 잡이 `CHANGE_ID` 없음으로 전부 skip | 브랜치 빌드로 실행됨. 잡의 branch source 가 PR 만 discover 하는지(JCasC `gitHubPullRequestDiscovery`) 확인 |
| AI Code Review 가 UNSTABLE, 로그에 `GEMINI_API_KEY 가 비어 있습니다` | 3.3 절대로 키 입력 |
| AI Code Review exit 4 / `GitHub HTTP 422` 후 issue comment 로 게시됨 | review API 가 거부한 코멘트(diff 밖 라인, 파일 경로 불일치). 스크립트가 라인을 보정하지만 PR 이 리뷰 도중 갱신되면 head sha 가 달라져 발생. fallback 으로 총평+코멘트 목록은 게시됨. 재빌드하면 됨 |
| AI Code Review `HTTP 429 from Gemini` 반복 | 무료 티어 RPM 초과. `GEMINI_MODEL=gemini-2.5-flash`, `REVIEW_GRANULARITY=file`, PR 을 작게 |
| Gemini 응답이 코드펜스/설명 포함 | 자동으로 JSON 추출·1회 재요청. 그래도 실패하면 해당 hunk 는 "자동 리뷰 실패" 코멘트로 남고 `review-result.json` 의 `failures` 에 기록 |
| `gradlew: Permission denied` / `bad interpreter: ^M` | Windows 체크아웃. Checkout 스테이지가 `chmod +x` + CRLF 제거를 하지만 새 스크립트를 추가했다면 `.gitattributes`(`* text=auto eol=lf`) 를 확인 |
| 멀티브랜치 첫 빌드에서 파라미터가 비어 있음 | 정상(첫 빌드에서 정의가 저장됨). 모든 사용처가 기본값 fallback 을 가짐. 두 번째 빌드부터 *Build with Parameters* 가능 |
| `cleanWs` 가 느림 | `node_modules`, `.gradle` 이 workspace 에 포함되기 때문. Gradle/npm 캐시 자체는 `/home/jenkins/.gradle`, `~/.npm` volume 이라 유지됨 |

---

## 7. 로컬에서 할 수 있는 검증

```bash
# 스크립트 문법
bash -n ci/scripts/trivy_gate.sh ci/scripts/smoke_test.sh
python3 -m py_compile ci/scripts/gemini_review.py

# 단위/통합 테스트 (임시 git repo + 로컬 HTTP 서버로 Gemini/GitHub 경로까지 검증)
python3 ci/scripts/test_gemini_review.py -v

# Trivy 게이트를 기존 JSON 으로
trivy fs --format json -o trivy-fs.json . && ci/scripts/trivy_gate.sh trivy-fs.json HIGH,CRITICAL

# Declarative 문법 검증 (Jenkins 기동 후; crumb 필요)
CRUMB=$(curl -s -u admin:admin -c /tmp/jc http://localhost:8080/crumbIssuer/api/json | jq -r '.crumbRequestField+":"+.crumb')
curl -s -u admin:admin -b /tmp/jc -H "$CRUMB" -X POST -F "jenkinsfile=<Jenkinsfile"               http://localhost:8080/pipeline-model-converter/validate
curl -s -u admin:admin -b /tmp/jc -H "$CRUMB" -X POST -F "jenkinsfile=<ci/Jenkinsfile.codereview" http://localhost:8080/pipeline-model-converter/validate

# 배포 후 스모크 (에이전트 또는 docker 가 되는 셸에서)
ci/scripts/smoke_test.sh app/docker-compose.app.yml cicd-ai-flow-app
```
