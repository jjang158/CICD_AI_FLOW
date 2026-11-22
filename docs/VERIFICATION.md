# 검증 결과 (VERIFICATION)

작성일 2026-11-23. 세 작업(환경 / 앱 / 파이프라인)을 각각 병렬로 구축한 뒤, 서로의 산출물을 실제 환경에서 교차 검증한 결과다.
원문 로그는 Jenkins 빌드 콘솔과 SonarQube 대시보드에 남아 있다.

## 1. 환경 (infra/)

| 항목 | 결과 |
|---|---|
| `bash infra/scripts/up.sh` | 멱등 확인(3회 실행, 모두 exit 0). 최초 기동 시 이미지 빌드 포함 약 8분 |
| `healthcheck.sh` | **36 / 36 PASS** (Jenkins 잡 3종·credential 4종·SonarQube 서버 설정, 에이전트 도구 10종, SonarQube 프로젝트·웹훅·토큰·전용 Quality Gate, 컨테이너 4개 healthy) |
| 에이전트 연결 | SSH(`jenkins/ssh-agent`), `up.sh` 가 키쌍을 자동 생성. 컨테이너 재시작 후 10초 내 healthy, Jenkins 노드 25초 내 online |
| PC 재부팅 | Docker Desktop 은 자동 시작이 아님. 기동 후 컨테이너는 자동 복구되나 에이전트가 1회 `Exited(127)` 로 남은 사례 있음 → `up.sh`/`docker restart` 로 복구 |
| SonarQube | Community Build 26.11. 기본 QG `Sonar way` 는 새 코드 기준이라 첫 분석이 무조건 PASS → 전체 코드 기준 `cicd-ai-flow-gate` 생성·할당 |

## 2. 애플리케이션 (app/)

| 항목 | 결과 |
|---|---|
| Backend 빌드/테스트 | Spring Boot 4.2.0 / Kotlin 2.4.21 / Gradle 8.14.8, `./gradlew clean build jacocoTestReport` BUILD SUCCESSFUL, 테스트 26개 PASS, 라인 커버리지 100% (85/85), 명령 커버리지 97.3% |
| Frontend 빌드/테스트 | React 18.3 / Vite 8.3 / Vitest 5.1, lint 0, 테스트 22개 PASS, 커버리지 Stmts 97.75% |
| SonarQube 클린 상태 | 프로젝트 `cicd-ai-flow-verify` clean-1: bugs 0, vulnerabilities 0, code smells 0, hotspots 0, coverage 96.8%, 중복 0% → QG OK |
| SonarQube 취약 샘플 적용 | vuln-1: **VULNERABILITY 11건**(kotlin S5542×2, S5547, S4423, S4426, S4790, S2245×2, S6418 / typescript S1523, S2245), security rating E → QG **ERROR** |
| SonarQube 원복 | clean-3: 이슈 0 → QG OK 복귀 |
| Trivy 클린 상태 | fs(HIGH,CRITICAL, 의존성·시크릿·설정) 0건, 이미지 backend/frontend CRITICAL 0 · HIGH 0 |
| Trivy 취약 샘플 적용 | **7건**: log4j-core 2.14.1 (CVE-2021-44228, CVE-2021-45046 CRITICAL, CVE-2021-45105 HIGH), lodash 4.17.15 (CVE-2020-8203, CVE-2021-23337, CVE-2026-4800, NSWG-ECO-516 HIGH) |
| apply.sh / revert.sh | 적용 후에도 빌드·테스트 통과, 원복 후 원본과 바이트 동일 |
| 미검출(버전 특성) | kotlin S2077(SQL 결합), typescript S6299(React raw HTML)는 26.11 에 규칙 없음, S2068 은 해당 리터럴에 미발화 — AI 리뷰 데모용으로만 유지 |

## 3. 파이프라인 (Jenkinsfile, ci/)

### 3.1 로컬 bare repo 잡 (`cicd-ai-flow-local`, GitHub 없이)

| 빌드 | 결과 |
|---|---|
| `main` #1 | Smoke Test 만 실패(프론트 포트 80 가정 ↔ 실제 8080) → 포트 동적 탐색으로 수정 |
| `main` #2~#4 | **SUCCESS** (워밍 후 약 1분). Sonar 분석 → 웹훅 2초 내 수신 → QG OK, Trivy 0, 이미지 CRITICAL 0, 배포, 스모크 4항목 통과 |
| `demo/vulnerable-code` #1 | Quality Gate 에서 중단: Trivy 7건. (Sonar 는 첫 분석이라 OK → 전용 게이트 도입 계기) |
| `demo/vulnerable-code` #2 | 전용 게이트 적용 후: **SonarQube ERROR**(vulnerabilities 11, security_rating 5) + Trivy 7건 → Package/Deploy 미실행 |
| `cicd-ai-flow-local-codereview` #1 | PR 시뮬레이션(파라미터로 CHANGE_* 주입, `GEMINI_FAKE=1`, dry-run): 전 스테이지 SUCCESS, 인라인 코멘트 12건 + 총평 생성 |

### 3.2 GitHub 잡 (`jjang158/CICD_AI_FLOW`, 1분 주기 스캔)

| 빌드 | 결과 |
|---|---|
| `cicd-ai-flow-cicd » main` #1 | 검증 중 에이전트 컨테이너를 재시작해 Package 에서 중단(환경 테스트의 부작용) |
| `cicd-ai-flow-cicd » main` #2 | **SUCCESS** 2분 33초: Sonar QG OK, Trivy 0, 이미지 CRITICAL 0, `cicd-app-*` 배포, 스모크 ALL PASSED |
| `cicd-ai-flow-codereview » PR-1` #1 | PR 발견 후 1분 내 빌드, **SUCCESS**: Build&Test(26/22 PASS) → Trivy 7건 보고 → GitHub review 게시(inline 6 + 총평) → commit status `continuous-integration/jenkins/pr-merge` success. 단 Gemini 가 전부 404(`gemini-2.5-pro` 는 신규 사용자 사용 불가) → placeholder 코멘트 |
| `cicd-ai-flow-codereview » PR-2` #1 (모델 교체 PR, 클린 변경) | **SUCCESS** 2분 3초: `gemini-3.5-flash` 로 9개 파일·22 hunk 리뷰, inline **22건**(info 21, minor 1) + 총평(위험도 low, **approve**, 테스트/Trivy 결과 포함) GitHub 게시, 모델 전환 없음, JSON 파싱 재요청 1회 |
| `cicd-ai-flow-codereview » PR-1` #2 (취약 데모, `GEMINI_MODEL=gemini-3.5-flash` 파라미터) | **SUCCESS**: inline 9건 — `InsecureRandomToken.kt`(CRITICAL, `SecureRandom` 권고), `UnsafeHtml.tsx` eval(CRITICAL)·XSS(CRITICAL)·하드코딩 시크릿(MAJOR) 등 실제 코멘트 5건 + 총평 **request-changes / high**. 단 PR-2 직후라 무료 티어 quota(429)로 hunk 4개·총평은 placeholder. 이 빌드는 PR #2 머지 전 스크립트(폴백 없음) 기준 |

### 3.3 Gemini 모델 (2026-11-23 실측)

| 모델 | 결과 |
|---|---|
| `gemini-2.5-pro`, `gemini-2.5-flash` | 404 "no longer available to new users" |
| `gemini-3.1-pro-preview` 등 pro 계열 | 429 quota exceeded (무료 티어) |
| `gemini-3.5-flash` (GA), `gemini-3.5-flash-lite`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite` | 200 OK |

→ 기본 모델 `gemini-3.5-flash`, fallback `gemini-3-flash-preview,gemini-3.1-flash-lite` 로 변경(PR #2). 라이브 dry-run(데모 diff): 7회 호출 모두 200, 코멘트 9건(critical 8, major 1), 총평 request-changes/high, 전부 한국어·수정 예시 포함.
무료 티어는 분당/일일 호출 한도가 작아 PR 두 개를 연달아 리뷰하면 429 가 난다. PR #2 머지 후에는 429 지속 시 폴백 모델로 자동 전환되며, 큰 PR 은 `REVIEW_GRANULARITY=file`(기본) 유지를 권장.

## 4. 남은 항목 / 권고

- **PR #2 머지는 사용자 작업**: 자동화 권한 정책상 main 머지는 수행하지 않았다. 머지하면 `cicd-ai-flow-cicd » main` 이 1분 내 빌드·배포되고, PR-1 도 target 변경으로 재빌드되어 폴백 체인이 적용된 리뷰가 다시 게시된다.

- 사용자 판단: SonarQube `cicd-ai-flow-pr` 프로젝트에 검증용 취약 분석 이력이 남아 있음(전용 게이트는 전체 코드 기준이라 동작에 영향 없음). 깨끗이 하려면 프로젝트 삭제 후 `bootstrap-sonar.sh` 재실행. 로컬 bare repo 의 `verify/vulnerable-code` 브랜치도 삭제 가능.
- PR #1 에는 모델 교체 전 placeholder 리뷰 1건이 남아 있다(삭제하지 않음).
- GitHub PAT 는 `repo` 범위면 충분하다. 현재 토큰은 범위가 매우 넓어 교체를 권장.
- 시간이 지나 새 CVE 가 나오면 클린 main 도 Trivy 게이트에서 실패할 수 있다 → 의존성 갱신 후 lock 재생성.
