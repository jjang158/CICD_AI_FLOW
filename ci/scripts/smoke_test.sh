#!/usr/bin/env bash
# smoke_test.sh - Deploy 스테이지 이후 배포된 컨테이너를 **docker network 경유**로 검사한다.
#
# Jenkins 에이전트는 컨테이너라서 호스트 포트(localhost:8081/3000)에 직접 닿지 못할 수 있다.
# 그래서 compose 프로젝트의 네트워크를 찾아 그 네트워크에 붙인 curl 컨테이너로
#   http://<backend>:8081/actuator/health, http://<frontend>:80/, http://<frontend>:80/api/todos
# 를 확인한다. curl 이미지를 못 쓰면 docker exec / host.docker.internal 로 fallback 한다.
#
# 사용법: ci/scripts/smoke_test.sh [compose-file] [project]
# 환경변수:
#   COMPOSE_FILE (app/docker-compose.app.yml)  COMPOSE_PROJECT (cicd-ai-flow-app)
#   BACKEND_SERVICE (backend)  FRONTEND_SERVICE (frontend)   - compose 서비스명. 없으면 이름에 backend/frontend 가 들어간 서비스를 자동 선택
#   BACKEND_PORT (8081)  FRONTEND_PORT (80)                 - 컨테이너 내부 포트
#   HOST_ADDR (host.docker.internal)  HOST_BACKEND_PORT (8081)  HOST_FRONTEND_PORT (3000)  - host fallback 용
#   RETRIES (30)  SLEEP (2)  CURL_IMAGE (curlimages/curl:8.11.1)
# 종료 코드: 0 모두 통과 / 1 실패 (compose ps + logs --tail=100 출력, 롤백 안내)
set -uo pipefail

COMPOSE_FILE="${1:-${COMPOSE_FILE:-app/docker-compose.app.yml}}"
PROJECT="${2:-${COMPOSE_PROJECT:-cicd-ai-flow-app}}"
BACKEND_SERVICE="${BACKEND_SERVICE:-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-frontend}"
# 컨테이너 내부 포트: 비워 두면 docker inspect(ExposedPorts/PortBindings)로 자동 탐색 (frontend 80 -> 8080 -> 3000, backend 8081 -> 8080)
BACKEND_PORT="${BACKEND_PORT:-}"
FRONTEND_PORT="${FRONTEND_PORT:-}"
HOST_ADDR="${HOST_ADDR:-host.docker.internal}"
# 호스트 publish 포트: 비워 두면 PortBindings 에서 탐색, 없으면 8081 / 3000
HOST_BACKEND_PORT="${HOST_BACKEND_PORT:-}"
HOST_FRONTEND_PORT="${HOST_FRONTEND_PORT:-}"
RETRIES="${RETRIES:-30}"
SLEEP="${SLEEP:-2}"
CURL_IMAGE="${CURL_IMAGE:-curlimages/curl:8.11.1}"

log()  { echo "[smoke] $*"; }
fail() { echo "[smoke] FAIL: $*" >&2; }

# COMPOSE_FILE 은 docker compose 와 같이 ':' 로 여러 파일을 이어 줄 수 있다 (base:override)
COMPOSE_ARGS=(-p "$PROJECT")
IFS=':' read -r -a _files <<< "$COMPOSE_FILE"
for f in "${_files[@]}"; do
  if [ ! -f "$f" ]; then
    fail "compose 파일 없음: $f"
    exit 1
  fi
  COMPOSE_ARGS+=(-f "$f")
done
compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }

# --------------------------------------------------------------------------
# 1. 서비스/컨테이너/네트워크 탐색
# --------------------------------------------------------------------------
SERVICES="$(compose ps --services 2>/dev/null || true)"
log "project=$PROJECT compose=$COMPOSE_FILE services: $(echo "$SERVICES" | tr '\n' ' ')"
compose ps 2>/dev/null || true

pick_service() {  # $1 원하는 이름, $2 패턴
  if echo "$SERVICES" | grep -qx "$1"; then echo "$1"; return; fi
  echo "$SERVICES" | grep -i "$2" | head -n 1
}
BACKEND_SERVICE="$(pick_service "$BACKEND_SERVICE" backend)"
FRONTEND_SERVICE="$(pick_service "$FRONTEND_SERVICE" frontend)"
if [ -z "$BACKEND_SERVICE" ] || [ -z "$FRONTEND_SERVICE" ]; then
  fail "backend/frontend 서비스를 찾지 못함 (services: $SERVICES)"
  exit 1
fi

BACKEND_CID="$(compose ps -q "$BACKEND_SERVICE" 2>/dev/null | head -n 1)"
FRONTEND_CID="$(compose ps -q "$FRONTEND_SERVICE" 2>/dev/null | head -n 1)"
if [ -z "$BACKEND_CID" ] || [ -z "$FRONTEND_CID" ]; then
  fail "컨테이너가 떠 있지 않음 (backend=$BACKEND_CID frontend=$FRONTEND_CID)"
  compose logs --tail=100 2>/dev/null || true
  exit 1
fi

NET="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' "$BACKEND_CID" 2>/dev/null | head -n 1)"
BACKEND_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$BACKEND_CID" 2>/dev/null || true)"
FRONTEND_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$FRONTEND_CID" 2>/dev/null || true)"
log "backend=$BACKEND_SERVICE(${BACKEND_CID:0:12} ip=$BACKEND_IP) frontend=$FRONTEND_SERVICE(${FRONTEND_CID:0:12} ip=$FRONTEND_IP) network=$NET"

# --------------------------------------------------------------------------
# 1b. 포트 탐색 (이미지 EXPOSE / compose publish 가 바뀌어도 동작하도록)
# --------------------------------------------------------------------------
exposed_ports() {  # $1 cid -> 컨테이너가 EXPOSE 하거나 publish 한 tcp 포트 목록 (중복 제거)
  {
    docker inspect -f '{{range $p, $_ := .Config.ExposedPorts}}{{$p}}{{"\n"}}{{end}}' "$1" 2>/dev/null
    docker inspect -f '{{range $p, $_ := .HostConfig.PortBindings}}{{$p}}{{"\n"}}{{end}}' "$1" 2>/dev/null
  } | grep '/tcp$' | cut -d/ -f1 | awk 'NF && !seen[$0]++'
}
published_port() {  # $1 cid, $2 container port -> 호스트 publish 포트 (없으면 빈 문자열)
  docker inspect -f "{{range (index .HostConfig.PortBindings \"$2/tcp\")}}{{.HostPort}}{{\"\\n\"}}{{end}}" "$1" 2>/dev/null | head -n 1
}
# port_candidates <cid> <explicit> <candidates...> -> 시도할 포트 목록(공백 구분): 명시값 > 탐색된 포트 중 후보 순 > 나머지 탐색 포트 > 후보
port_candidates() {
  local cid="$1" explicit="$2"; shift 2
  if [ -n "$explicit" ]; then echo "$explicit"; return; fi
  local found out="" c p
  found="$(exposed_ports "$cid")"
  for c in "$@"; do echo "$found" | grep -qx "$c" && out="$out $c"; done
  for p in $found; do echo "$out" | grep -qw "$p" || out="$out $p"; done
  for c in "$@"; do echo "$out" | grep -qw "$c" || out="$out $c"; done
  echo "$out"
}
BACKEND_PORTS="$(port_candidates "$BACKEND_CID" "$BACKEND_PORT" 8081 8080)"
FRONTEND_PORTS="$(port_candidates "$FRONTEND_CID" "$FRONTEND_PORT" 80 8080 3000)"
set -- $BACKEND_PORTS;  BACKEND_PORT="$1"
set -- $FRONTEND_PORTS; FRONTEND_PORT="$1"
[ -n "$HOST_BACKEND_PORT" ]  || HOST_BACKEND_PORT="$(published_port "$BACKEND_CID" "$BACKEND_PORT")"
[ -n "$HOST_FRONTEND_PORT" ] || HOST_FRONTEND_PORT="$(published_port "$FRONTEND_CID" "$FRONTEND_PORT")"
HOST_BACKEND_PORT="${HOST_BACKEND_PORT:-8081}"
HOST_FRONTEND_PORT="${HOST_FRONTEND_PORT:-3000}"
log "ports: backend container [${BACKEND_PORTS# }] host ${HOST_BACKEND_PORT} / frontend container [${FRONTEND_PORTS# }] host ${HOST_FRONTEND_PORT}"

# --------------------------------------------------------------------------
# 2. 접근 방식 결정: net(curl 컨테이너) -> exec(컨테이너 안 curl/wget) -> host(host.docker.internal)
# --------------------------------------------------------------------------
MODE=""
if [ -n "$NET" ] && docker run --rm --network "$NET" "$CURL_IMAGE" --version >/dev/null 2>&1; then
  MODE=net
elif docker exec "$BACKEND_CID" sh -c 'command -v curl || command -v wget' >/dev/null 2>&1; then
  MODE=exec
else
  MODE=host
fi
log "access mode: $MODE"

# fetch <net-url> <host-url>  -> 본문을 stdout 으로, 실패 시 non-zero
fetch() {
  local net_url="$1" host_url="$2"
  case "$MODE" in
    net)  docker run --rm --network "$NET" "$CURL_IMAGE" -fsS -m 5 "$net_url" ;;
    exec) docker exec "$BACKEND_CID" sh -c "if command -v curl >/dev/null; then curl -fsS -m 5 '$net_url'; else wget -qO- -T 5 '$net_url'; fi" ;;
    host) curl -fsS -m 5 "$host_url" ;;
  esac
}

# check <name> <net-urls(space separated, 순서대로 시도)> <host-url> [expected-regex]
check() {
  local name="$1" net_urls="$2" host_url="$3" expect="${4:-}" i body net_url
  for i in $(seq 1 "$RETRIES"); do
    for net_url in $net_urls; do
      if body="$(fetch "$net_url" "$host_url" 2>/dev/null)"; then
        if [ -z "$expect" ] || echo "$body" | grep -Eq "$expect"; then
          log "OK  $name ($([ "$MODE" = host ] && echo "$host_url" || echo "$net_url")) -> $(echo "$body" | head -c 160 | tr '\n' ' ')"
          return 0
        fi
        log "..  $name 응답은 왔지만 기대값($expect) 불일치 (시도 $i/$RETRIES, $net_url): $(echo "$body" | head -c 120 | tr '\n' ' ')"
      fi
      [ "$MODE" = host ] && break   # host 모드는 host_url 하나만 의미 있음
    done
    log "..  $name 대기 중 (시도 $i/$RETRIES)"
    sleep "$SLEEP"
  done
  fail "$name ($net_urls / $host_url) ${RETRIES}회 시도 후 실패"
  return 1
}

# --------------------------------------------------------------------------
# 3. 검사
# --------------------------------------------------------------------------
urls() {  # $1 service, $2 ports(space separated), $3 path -> "http://svc:p1/path http://svc:p2/path ..."
  local out="" p
  for p in $2; do out="$out http://$1:$p$3"; done
  echo "${out# }"
}
BACKEND_HOST="http://${HOST_ADDR}:${HOST_BACKEND_PORT}"
FRONTEND_HOST="http://${HOST_ADDR}:${HOST_FRONTEND_PORT}"

STATUS=0
check "backend health"      "$(urls "$BACKEND_SERVICE"  "$BACKEND_PORTS"  /actuator/health)" "${BACKEND_HOST}/actuator/health" '"status"[[:space:]]*:[[:space:]]*"UP"' || STATUS=1
check "backend api health"  "$(urls "$BACKEND_SERVICE"  "$BACKEND_PORTS"  /api/health)"      "${BACKEND_HOST}/api/health"      '^[[:space:]]*[\[{]|UP|ok' || STATUS=1
check "frontend index"      "$(urls "$FRONTEND_SERVICE" "$FRONTEND_PORTS" /)"                "${FRONTEND_HOST}/"                '<html|<!doctype|<!DOCTYPE|<div' || STATUS=1
check "frontend api proxy"  "$(urls "$FRONTEND_SERVICE" "$FRONTEND_PORTS" /api/todos)"       "${FRONTEND_HOST}/api/todos"       '^[[:space:]]*[\[{]' || STATUS=1

if [ "$STATUS" -ne 0 ]; then
  echo "----- docker compose ps -----"
  compose ps 2>/dev/null || true
  echo "----- docker compose logs --tail=100 -----"
  compose logs --tail=100 2>/dev/null || true
  cat >&2 <<EOF
[smoke] 스모크 테스트 실패. 롤백 안내:
  - 이전 태그로 되돌리기 : IMAGE_TAG=<이전 sha> docker compose -p $PROJECT -f $COMPOSE_FILE up -d --no-build
  - 전체 내리기         : docker compose -p $PROJECT -f $COMPOSE_FILE down
EOF
  exit 1
fi
log "ALL PASSED (mode=$MODE)"
exit 0
