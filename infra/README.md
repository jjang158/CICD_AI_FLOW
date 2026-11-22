# infra/ — 로컬 CI/CD 환경 (Jenkins + 빌드 에이전트 + SonarQube)

`docs/DESIGN.md` 2장/3장 계약을 그대로 구현한 Docker Compose 스택이다.
WSL(Ubuntu) + Docker Desktop(WSL 통합) 위에서 `up.sh` 한 번으로 아래가 전부 준비된다.

| 서비스 | 컨테이너 | 접속 | 비고 |
|---|---|---|---|
| Jenkins 컨트롤러 | `cicd-jenkins` | http://localhost:8080 (컨테이너 간 `http://jenkins:8080`) | `admin` / `JENKINS_ADMIN_PASSWORD`(기본 `admin`), executors 0, JCasC |
| 빌드 에이전트 | `cicd-jenkins-agent` | Jenkins 노드 `cicd-agent` (label `cicd-agent`, executors 2) | JDK 21, Node 22, git, docker CLI+compose, sonar-scanner, trivy, python3, jq |
| SonarQube | `cicd-sonarqube` | http://localhost:9000 (컨테이너 간 `http://sonarqube:9000`) | Community Build, `admin` / `SONAR_ADMIN_PASSWORD`(기본 `Admin1234!Sonar`) |
| PostgreSQL | `cicd-sonar-db` | (내부) | SonarQube DB |

## 1. 사용법

```bash
# WSL(Ubuntu) 셸에서, 프로젝트 루트 기준
bash infra/scripts/up.sh            # 빌드 + 기동 + 준비 대기 + SonarQube 부트스트랩 + 헬스체크
bash infra/scripts/healthcheck.sh   # 상태 재확인 (하나라도 FAIL 이면 exit 1)
bash infra/scripts/down.sh          # 중지 (데이터 유지)
bash infra/scripts/down.sh --volumes  # 중지 + 모든 볼륨 삭제 (Jenkins/Sonar 데이터, 캐시, 로컬 bare repo)
```

`up.sh` 는 멱등이다. 다시 실행하면 변경된 것만 적용된다 (이미지 재빌드는 캐시, `.env` 변경 시 컨테이너 재생성, SonarQube 항목은 존재하면 skip).
옵션: `--no-build`(이미지 빌드 생략), `--skip-check`(헬스체크 생략).

`up.sh` 가 하는 일:

1. `infra/.env` 가 없으면 `.env.example` 복사, `infra/secrets/agent_ssh_key(.pub)` ed25519 키쌍 생성
2. 컨테이너 안에서 보이는 `/var/run/docker.sock` 의 gid 를 감지해 `DOCKER_GID` 로 export (에이전트 이미지 build arg)
3. `docker compose build && docker compose up -d`
4. Jenkins(`/login` 200), SonarQube(`/api/system/status` UP) 대기
5. `bootstrap-sonar.sh` — admin 비밀번호 변경, 프로젝트 `cicd-ai-flow`/`cicd-ai-flow-pr`, 글로벌 분석 토큰 `jenkins`, 웹훅 `jenkins` 등록, 토큰을 `.env`/`secrets/SONAR_TOKEN` 에 기록 후 **JCasC reload** 로 Jenkins credential `sonar-token` 갱신·검증
6. `healthcheck.sh`

## 2. 사용자가 직접 입력해야 하는 값 (`infra/.env`)

| 키 | 용도 | 비어 있을 때 |
|---|---|---|
| `GITHUB_OWNER`, `GITHUB_REPO` | 멀티브랜치 잡 `cicd-ai-flow-cicd`, `cicd-ai-flow-codereview` 의 GitHub 소스 | 잡은 placeholder(`changeme`)로 생성되지만 주기 스캔이 꺼진 상태 |
| `GITHUB_USERNAME`, `GITHUB_TOKEN` | credential `github-token` (Username with password, PAT: `repo`, `workflow`) | `changeme/changeme` |
| `GEMINI_API_KEY` | credential `gemini-api-key` (Secret text) | 빈 값 → AI 리뷰 스테이지 UNSTABLE |
| `JENKINS_ADMIN_PASSWORD` | Jenkins `admin` 비밀번호 | `admin` |
| `SONAR_ADMIN_PASSWORD` | SonarQube `admin` 비밀번호 (부트스트랩이 초기 `admin/admin` 을 이 값으로 변경). SonarQube 정책상 **12자 이상** | `Admin1234!Sonar` |
| `SONAR_TOKEN` | **자동**: bootstrap-sonar.sh 가 채움 | — |
| `DOCKER_GID` | **자동**: up.sh 가 감지 | 1001 |

값을 바꾼 뒤 적용하는 방법 두 가지:

* `bash infra/scripts/up.sh` 재실행 — `.env` 변경으로 jenkins 컨테이너가 재생성되고 JCasC 가 다시 적용된다 (GitHub 잡의 owner/repo 와 주기 스캔도 이때 반영).
* Jenkins UI → Manage Jenkins → Credentials → System → Global 에서 `github-token`, `gemini-api-key` 직접 수정. 단, 다음 JCasC reload/컨테이너 재생성 때 `.env` 값으로 되돌아가므로 `.env` 도 같이 맞춰 두는 것이 좋다.

JCasC 만 다시 적용하려면 (컨테이너 재시작 없이):

```bash
# admin 비밀번호는 .env 의 JENKINS_ADMIN_PASSWORD
CJ=$(mktemp); CRUMB=$(curl -s -u admin:admin -c "$CJ" 'http://localhost:8080/crumbIssuer/api/xml?xpath=concat(//crumbRequestField,":",//crumb)')
curl -s -o /dev/null -w '%{http_code}\n' -u admin:admin -b "$CJ" -H "$CRUMB" -X POST http://localhost:8080/configuration-as-code/reload
```
(또는 Manage Jenkins → Configuration as Code → Reload existing configuration). 환경변수는 컨테이너 생성 시 고정되므로 `.env` 의 GitHub/Gemini 값 변경은 reload 가 아니라 `up.sh` 재실행(컨테이너 재생성)이 필요하다. `SONAR_TOKEN` 만은 파일(`secrets/SONAR_TOKEN`)로 주입되므로 reload 로 갱신된다.

## 3. 구성 상세

### Jenkins (`jenkins/`)
* `Dockerfile`: `jenkins/jenkins:lts-jdk21` + `plugins.txt`(`jenkins-plugin-cli`), setup wizard 비활성화, `hudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true`(로컬 `file://` 소스 허용), HTML Publisher 용 CSP 완화.
* `casc/jenkins.yaml` (이미지 안 `/usr/local/etc/casc/jenkins.yaml`):
  * 보안: local realm `admin`, `loggedInUsersCanDoAnything`, CSRF 기본, agent protocol `JNLP4-connect` 만, 컨트롤러 executors 0
  * credentials: `github-token`(usernamePassword), `gemini-api-key`(string), `sonar-token`(string), `agent-ssh-key`(SSH private key, 시스템 범위)
  * SonarQube 서버: 이름 **`SonarQube`**, URL `http://sonarqube:9000`, credential `sonar-token` → Jenkinsfile 에서 `withSonarQubeEnv('SonarQube')`
  * 노드 `cicd-agent`: SSH launcher (`jenkins-agent:22`, 호스트키 검증 없음), remoteFS `/home/jenkins/agent`
  * Job DSL(script security off): 멀티브랜치 잡 3개
    * `cicd-ai-flow-cicd` — GitHub `GITHUB_OWNER/GITHUB_REPO`, 브랜치 discovery 만, 정규식 필터 `main`, `Jenkinsfile`, 1분 주기 스캔
    * `cicd-ai-flow-codereview` — 같은 소스, **PR discovery 만**(origin PR: merge with target / fork PR: 권한 있는 사용자 신뢰), `ci/Jenkinsfile.codereview`, 1분 주기 스캔
    * `cicd-ai-flow-local` — Git 소스 `file:///var/jenkins_home/repos/cicd-ai-flow.git`, `Jenkinsfile`, 1분 주기 스캔 (GitHub 없이 로컬 검증용)
* 시크릿 주입: JCasC `${VAR}` 는 컨테이너 환경변수(`.env` → compose `environment`) 또는 `$SECRETS`(`/run/casc-secrets`, `infra/secrets/` bind mount) 안의 같은 이름 파일에서 읽는다. `agent_ssh_key`, `SONAR_TOKEN` 은 파일로 주입된다.

### 에이전트 (`agent/`)
* `jenkins/ssh-agent:jdk21`(Debian) 기반. 컨트롤러가 **SSH** 로 접속해 remoting 을 띄우는 정적 노드.
  * 선택 이유: inbound(JNLP) 방식은 에이전트가 컨트롤러에서 발급된 secret 을 알아야 해서 기동 순서/REST 호출 래퍼가 필요하지만, SSH 방식은 up.sh 가 만든 키쌍만 양쪽에 꽂으면 Jenkins 가 알아서 (재)접속한다. 컨테이너 재시작·JCasC reload 후에도 수동 조작 없음.
* 설치 도구: Node 22 LTS(공식 tarball), docker CLI + compose/buildx 플러그인(Docker apt repo), trivy(공식 apt repo), sonar-scanner CLI(`/opt/sonar-scanner`, `/usr/local/bin/sonar-scanner` 심볼릭 링크), git, curl, jq, unzip, python3.
* `ARG DOCKER_GID` 로 docker 그룹을 만들고 `jenkins` 유저를 추가 → 빌드 스텝(jenkins 유저)에서 `/var/run/docker.sock` 사용 가능 (Docker-out-of-Docker).
* `entrypoint.sh`: 공개키 파일(`/run/casc-secrets/agent_ssh_key.pub`)을 `JENKINS_AGENT_SSH_PUBKEY` 로 올리고, named volume 소유권을 `jenkins` 로 맞춘 뒤 원래 entrypoint(`setup-sshd`)로 넘긴다.
* SSH 세션 환경: `/etc/environment` 에 PATH(`/opt/sonar-scanner/bin:/opt/java/openjdk/bin:...`), `JAVA_HOME` 기록 → 파이프라인 `sh` 스텝에서 모든 도구가 PATH 에 있다.
* 볼륨: `/home/jenkins/agent`(workspace), `~/.gradle`, `~/.npm`, `~/.cache`(trivy DB), `~/.sonar`, `/var/jenkins_home/repos`(컨트롤러와 공유).
* `host.docker.internal` 로 호스트에 publish 된 포트(배포된 앱 8081/3000)에 접근할 수 있다.

### SonarQube (`docker-compose.yml` sonarqube/sonar-db)
* `sonarqube:community`(Community Build) + `postgres:16-alpine`. 데이터/확장/로그는 named volume.
* `command: ["-Dsonar.validateWebhooks=false"]` — SonarQube 는 기본적으로 사설망(site-local) 주소로의 웹훅을 거부하므로, `http://jenkins:8080/sonarqube-webhook/` 등록을 위해 검증을 끈다.
* 비밀번호 정책: 12자 이상 (`SONAR_ADMIN_PASSWORD` 기본 `Admin1234!Sonar`).
* `bootstrap-sonar.sh` 가 만드는 토큰은 **Global Analysis Token** `jenkins` 이다. 분석 전용이라 REST 조회에는 못 쓴다(조회는 admin 계정). 값은 `infra/.env` 의 `SONAR_TOKEN` / `infra/secrets/SONAR_TOKEN`.
* 멀티브랜치 PR 파이프라인은 Community Build 에 브랜치/PR 분석이 없으므로 별도 프로젝트 `cicd-ai-flow-pr` 로 분석한다 (DESIGN.md 3장).

### 로컬 검증용 bare repo (`cicd-ai-flow-local`)
GitHub 없이 파이프라인을 끝까지 돌려보기 위한 경로다. 볼륨 `jenkins_repos` 가 컨트롤러·에이전트 양쪽에 `/var/jenkins_home/repos` 로 마운트되어 있어 두 컨테이너 모두 `file:///var/jenkins_home/repos/cicd-ai-flow.git` 을 읽을 수 있다.

```bash
bash infra/scripts/push-local.sh              # 프로젝트 루트 스냅샷 → main 으로 force push + 브랜치 스캔 트리거
bash infra/scripts/push-local.sh feature-x    # 다른 브랜치 이름으로
COMMIT_MESSAGE="try" bash infra/scripts/push-local.sh main /path/to/dir
```
(`.gitignore`, `.git`, `node_modules`, `build`, `dist`, `coverage`, `infra/.env`, `infra/secrets` 는 제외된다.)
직접 다루려면: `docker compose -f infra/docker-compose.yml exec -u jenkins jenkins-agent git -C /var/jenkins_home/repos/cicd-ai-flow.git log --oneline`.

## 4. 트리거: 1분 주기 스캔 vs GitHub 웹훅

로컬 Jenkins 는 인터넷에서 접근할 수 없으므로 GitHub 웹훅이 도달하지 못한다. 그래서 GitHub 멀티브랜치 잡은 **1분 주기 스캔**(`periodicFolderTrigger 1m`)으로 push/PR 을 감지한다. 체감 지연은 최대 1분, 인증된 GitHub API rate limit(5,000/h) 안에서 충분하다.

웹훅을 쓰고 싶다면 터널을 열고 GitHub 저장소 Settings → Webhooks 에 등록한다.

```bash
# ngrok
ngrok http 8080            # https://xxxx.ngrok-free.app
# 또는 cloudflared
cloudflared tunnel --url http://localhost:8080
```
* Payload URL: `https://<터널 주소>/github-webhook/` , Content type `application/json`, 이벤트: `push`, `pull_request`
* `infra/jenkins/casc/jenkins.yaml` 의 `unclassified.location.url` 을 터널 주소로 바꾸면 PR 상태 링크도 외부에서 열린다.
* 웹훅이 오면 즉시 스캔되고, 주기 스캔은 안전망으로 그대로 두면 된다.

## 5. 트러블슈팅

| 증상 | 확인/조치 |
|---|---|
| `up.sh`: `cannot talk to the Docker daemon` | Docker Desktop 실행 + Settings → Resources → WSL integration 에서 이 배포판 활성화. `docker ps` 가 되는 셸에서 실행 |
| 노드 `cicd-agent` offline | `docker compose -f infra/docker-compose.yml logs jenkins-agent` 에 공개키가 기록됐는지(`Authorizing`/`authorized_keys`), `infra/secrets/agent_ssh_key.pub` 존재 확인. Jenkins → Manage Nodes → cicd-agent → Log. 키를 다시 만들려면 `rm infra/secrets/agent_ssh_key*` 후 `up.sh` |
| 에이전트에서 `docker ps` 권한 오류 | `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock alpine stat -c %g /var/run/docker.sock` 값이 `.env` 의 `DOCKER_GID` 와 다르면 `up.sh` 재실행(이미지 재빌드) |
| Jenkins 기동 실패 / JCasC 오류 | `docker compose -f infra/docker-compose.yml logs jenkins \| grep -iE 'error\|exception'`. 플러그인 호환 문제면 `jenkins/plugins.txt` 에 버전 고정 후 `up.sh` |
| SonarQube 가 오래 안 뜸 / 재시작 반복 | 메모리(최소 4GB 여유) 확인, `docker compose logs sonarqube`. `vm.max_map_count` 는 `SONAR_ES_BOOTSTRAP_CHECKS_DISABLE=true` 로 우회되어 있다 |
| SonarQube admin 로그인 안 됨 | 부트스트랩이 이미 비밀번호를 바꿨다. `.env` 의 `SONAR_ADMIN_PASSWORD` 사용. 모르면 `down.sh --volumes` 후 재기동 |
| `sonar-token` 이 안 맞음 (401 during analysis) | `bash infra/scripts/bootstrap-sonar.sh --force-token` → 토큰 재발급 + JCasC reload |
| `waitForQualityGate` 가 끝나지 않음 | SonarQube → Administration → Configuration → Webhooks 에 `jenkins` → `http://jenkins:8080/sonarqube-webhook/` 가 있고 최근 전송이 200 인지 확인 |
| 로컬 잡이 `file://` 체크아웃을 거부 | 이미지의 `JAVA_OPTS` 에 `-Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true` 가 있어야 한다 (jenkins/Dockerfile) |
| 포트 충돌 | 8080/50000/9000 을 쓰는 다른 프로세스 확인. 바꾸려면 `docker-compose.yml` 의 `ports` 와 `casc/jenkins.yaml` 의 `location.url` |
| Windows 에서 편집한 스크립트가 `\r` 오류 | 모든 파일은 LF 여야 한다: `sed -i 's/\r$//' infra/scripts/*.sh` |

기타 유용한 명령:

```bash
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs -f jenkins
docker compose -f infra/docker-compose.yml exec -u jenkins jenkins-agent bash   # 에이전트 셸 (jenkins 유저)
docker compose -f infra/docker-compose.yml exec jenkins-agent bash              # 에이전트 셸 (root)
cat infra/secrets/SONAR_TOKEN                                                     # 현재 Sonar 토큰
```

## 6. 로컬 데모 · PR 리뷰 시뮬레이션 (GitHub 없이)

### 6.1 Quality Gate `cicd-ai-flow-gate` (bootstrap-sonar.sh 가 생성·할당)

기본 게이트 `Sonar way` 는 **새 코드(new code) 조건만** 가진다. 새 코드는 "이전 분석(베이스라인)" 대비로 계산되므로 **어떤 projectKey 든 첫 분석은 조건 없이 PASS** 되고, 브랜치/PR 마다 상태가 리셋되는 `cicd-ai-flow-pr` 에서는 취약 코드가 들어 있어도 PASS 로 나올 수 있다(실측: `demo/vulnerable-code` 분석 → vulnerabilities 11 인데 QG OK). 그래서 전체 코드 기준 조건을 가진 전용 게이트를 만들어 두 프로젝트(`cicd-ai-flow`, `cicd-ai-flow-pr`)에 할당한다. 기본 게이트는 건드리지 않는다.

| 조건 (metric) | 연산 | 임계값 | 기준 |
|---|---|---|---|
| `vulnerabilities` | > | 0 | 전체 코드 |
| `bugs` | > | 0 | 전체 코드 |
| `security_rating` | > | 1 (A 보다 나쁨) | 전체 코드 |
| `reliability_rating` | > | 1 (A 보다 나쁨) | 전체 코드 |
| `coverage` | < | 80 % | 전체 코드 |
| `duplicated_lines_density` | > | 3 % | 전체 코드 |
| `new_violations` | > | 0 | 새 코드 (베이스라인이 있을 때만 평가) |

* 멱등: 게이트가 이미 있으면 조건을 다시 만들지 않고 할당만 보장한다. 26.x 에서 metric 키가 없으면 그 조건만 건너뛰고 경고를 출력한다.
* SonarQube 25+/26 은 새 게이트에 기본 새 코드 조건 4개(`new_violations` > 0, `new_coverage` < 80, `new_duplicated_lines_density` > 3, `new_security_hotspots_reviewed` < 100)를 자동으로 넣는다. bootstrap 은 이미 있는 metric 은 그대로 두고(`already present ... kept`) 위 표의 전체 코드 조건만 추가하므로, 최종 게이트는 전체 코드 6개 + 새 코드 4개 조건을 가진다.
* 실측(Task 2): 클린 상태는 vulnerabilities/bugs 0, coverage 96.8 %, 중복 0 → PASS. 취약 샘플 적용 시 vulnerabilities 11(security_rating E) → FAIL. SonarQube 26.11 은 과거 Hotspot 규칙(S2245/S4790/S1523/S6418)을 Vulnerability 로 보고하고 `security_hotspots` 는 항상 0 이다.
* 확인: `healthcheck.sh` 의 "quality gate 'cicd-ai-flow-gate'" 항목, 또는 SonarQube → Quality Gates.

### 6.2 데모 브랜치 `demo/vulnerable-code` (CI/CD 파이프라인이 Quality Gate 에서 FAIL 하는 것을 보여줌)

`app/vuln-samples/apply.sh` 는 JDK 21 + Node 22 가 필요하므로 에이전트 컨테이너 안에서 실행한다 (호스트에는 없음). 먼저 최신 main 을 bare repo 에 올린 뒤 브랜치를 딴다.

```bash
bash infra/scripts/push-local.sh main          # 최신 작업 트리 → bare repo main
docker compose -f infra/docker-compose.yml exec -u jenkins jenkins-agent bash -lc '
  set -e
  rm -rf /home/jenkins/tmp/vuln && mkdir -p /home/jenkins/tmp
  git clone -q file:///var/jenkins_home/repos/cicd-ai-flow.git /home/jenkins/tmp/vuln
  cd /home/jenkins/tmp/vuln && git checkout -q -b demo/vulnerable-code
  chmod +x app/backend/gradlew app/vuln-samples/*.sh
  bash app/vuln-samples/apply.sh             # 취약 샘플 + log4j-core 2.14.1 + lodash 4.17.15, lockfile 재생성 (약 10초)
  git add -A && git -c user.name=demo -c user.email=demo@local commit -qm "demo: apply vulnerable samples"
  git push -q origin demo/vulnerable-code'
```

* `cicd-ai-flow-local` 이 1분 안에(또는 `POST /job/cicd-ai-flow-local/build` 로 즉시) 브랜치를 발견해 빌드한다. main 이 아니므로 SonarQube 는 `cicd-ai-flow-pr` 로 분석하고 Deploy/Smoke 는 건너뛴다.
* 기대 결과: **Quality Gate 스테이지 FAILED** — Trivy fs 7건(log4j-core CVE-2021-44228/45046 CRITICAL, CVE-2021-45105 HIGH; lodash CVE-2020-8203/2021-23337/2026-4800/NSWG-ECO-516 HIGH) + SonarQube vulnerabilities 11 (전용 게이트 기준 FAIL).
* 되돌리기: 브랜치를 지우면 된다 (`git push origin --delete demo/vulnerable-code` 를 같은 방식으로 실행). main 은 영향 없다.

### 6.3 PR 리뷰 시뮬레이션 잡 `cicd-ai-flow-local-codereview`

GitHub PR 없이 `ci/Jenkinsfile.codereview` 를 끝까지 돌려 보는 파라미터 잡(JCasC Job DSL 로 생성). 멀티브랜치 PR 환경변수(`CHANGE_*`)를 빌드 파라미터로 주입하므로 Jenkinsfile 의 `env.CHANGE_ID` 가드가 통과한다.

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `REVIEW_BRANCH` | `demo/vulnerable-code` | 리뷰할 bare repo 브랜치 (= PR head) |
| `CHANGE_ID` | `1` | 가상 PR 번호 |
| `CHANGE_TARGET` | `main` | 가상 PR 대상 브랜치 (merge-base 계산에 사용) |
| `CHANGE_BRANCH` / `CHANGE_URL` / `CHANGE_AUTHOR` / `CHANGE_TITLE` | demo 값 | 표시용 (`CHANGE_URL` 에서 owner/repo 를 파싱) |
| `GEMINI_FAKE` | `1` | `gemini_review.py` 가 결정적 가짜 응답 사용 (API 호출 없음) |
| `REVIEW_DRY_RUN` | `true` | GitHub 에 올리지 않고 `review-dryrun.md` 생성 (Jenkinsfile 에도 선언된 파라미터, 첫 실행부터 적용되도록 잡에도 정의) |

```bash
# 실행 (기본값 그대로)
CJ=$(mktemp); CRUMB=$(curl -s -u admin:admin -c "$CJ" 'http://localhost:8080/crumbIssuer/api/xml?xpath=concat(//crumbRequestField,":",//crumb)')
curl -s -o /dev/null -w '%{http_code}\n' -u admin:admin -b "$CJ" -H "$CRUMB" -X POST \
  'http://localhost:8080/job/cicd-ai-flow-local-codereview/buildWithParameters?REVIEW_BRANCH=demo%2Fvulnerable-code&CHANGE_ID=1&CHANGE_TARGET=main&GEMINI_FAKE=1&REVIEW_DRY_RUN=true'
# 결과: http://localhost:8080/job/cicd-ai-flow-local-codereview/lastBuild/  (artifact: review-dryrun.md, review-result.json, ci-summary.txt, trivy-fs.json)
```

주의 사항:
* **`gemini-api-key` credential 이 비어 있으면** Jenkinsfile 의 가드가 AI 리뷰 스테이지를 건너뛰고 UNSTABLE 로 끝난다(`GEMINI_FAKE=1` 이어도). 시뮬레이션에는 아무 값이나 넣어 두면 된다: `infra/.env` 에 `GEMINI_API_KEY=fake-for-local` 후 `up.sh`(컨테이너 재생성), 또는 Jenkins UI 에서 credential 수정(다음 JCasC reload/`up.sh` 때 `.env` 값으로 되돌아감).
* Jenkinsfile 의 `parameters {}` 는 첫 실행 때 잡의 파라미터 정의에 **병합**된다(교체가 아님 — pipeline-model-definition 2.2293 에서 확인). JCasC reload 는 이 파일의 정의로 되돌린다.
* 검증 실측(빌드 #1, 브랜치 `demo/vulnerable-code`): Checkout PR(base=merge-base(origin/main) 계산) → Build & Test(backend 26 / frontend 22 tests) → Static Scan(Trivy 7건 report-only) → AI Code Review(GEMINI_FAKE, dry-run, 인라인 코멘트 12건 + 총평 markdown) → Report, 결과 SUCCESS.
