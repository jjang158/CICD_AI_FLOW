#!/usr/bin/env bash
# trivy_gate.sh - Trivy JSON 결과(fs/image)를 심각도별로 집계하고, 지정 심각도가 1건 이상이면 exit 1.
#
# 사용법: ci/scripts/trivy_gate.sh <trivy-json> [SEVERITIES] [--report-only]
#   SEVERITIES     쉼표 구분, 대소문자 무관 (기본 HIGH,CRITICAL / 환경변수 GATE_SEVERITY 로도 지정 가능)
#   --report-only  집계만 출력하고 항상 exit 0 (PR 파이프라인에서 ci-summary 작성용)
#
# 집계 대상: .Results[].Vulnerabilities[] + .Results[].Secrets[] + .Results[].Misconfigurations[] (Status != PASS)
# jq 가 있으면 jq, 없으면 python3 로 파싱한다. 둘 다 없으면 exit 2.
#
# 종료 코드: 0 통과 / 1 게이트 실패 / 2 사용법·입력 오류
set -euo pipefail

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

REPORT_ONLY=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --report-only) REPORT_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

JSON="${POSITIONAL[0]:-}"
SEVERITIES="$(echo "${POSITIONAL[1]:-${GATE_SEVERITY:-HIGH,CRITICAL}}" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
MAX_LIST="${GATE_MAX_LIST:-50}"

if [ -z "$JSON" ]; then
  usage >&2
  exit 2
fi
if [ ! -s "$JSON" ]; then
  echo "trivy_gate: 결과 파일이 없거나 비어 있음: $JSON" >&2
  exit 2
fi

# --------------------------------------------------------------------------
# 결과 -> TSV (type, severity, id, pkg, installed, fixed, target, title)
# --------------------------------------------------------------------------
extract_with_jq() {
  jq -r '
    [ .Results[]? as $r
      | ( $r.Vulnerabilities[]?
          | ["vuln", (.Severity // "UNKNOWN"), (.VulnerabilityID // "-"), (.PkgName // "-"),
             (.InstalledVersion // "-"), (.FixedVersion // "-"), ($r.Target // "-"), (.Title // "")] ),
        ( $r.Secrets[]?
          | ["secret", (.Severity // "UNKNOWN"), (.RuleID // "-"), (.Category // "-"),
             ("line " + ((.StartLine // 0) | tostring)), "-", ($r.Target // "-"), (.Title // "")] ),
        ( $r.Misconfigurations[]? | select((.Status // "FAIL") != "PASS")
          | ["misconfig", (.Severity // "UNKNOWN"), (.ID // "-"), (.Type // "-"),
             "-", "-", ($r.Target // "-"), (.Title // "")] )
    ] | .[] | map(tostring | gsub("[\t\n\r]"; " ")) | @tsv
  ' "$JSON"
}

extract_with_python() {
  python3 - "$JSON" <<'PY'
import json, sys

def clean(v):
    return str(v if v is not None else "-").replace("\t", " ").replace("\n", " ").replace("\r", " ")

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
for r in data.get("Results") or []:
    target = r.get("Target") or "-"
    for v in r.get("Vulnerabilities") or []:
        print("\t".join(clean(x) for x in ["vuln", v.get("Severity") or "UNKNOWN", v.get("VulnerabilityID"), v.get("PkgName"),
                                           v.get("InstalledVersion"), v.get("FixedVersion") or "-", target, v.get("Title") or ""]))
    for s in r.get("Secrets") or []:
        print("\t".join(clean(x) for x in ["secret", s.get("Severity") or "UNKNOWN", s.get("RuleID"), s.get("Category"),
                                           "line %s" % s.get("StartLine", 0), "-", target, s.get("Title") or ""]))
    for m in r.get("Misconfigurations") or []:
        if (m.get("Status") or "FAIL") == "PASS":
            continue
        print("\t".join(clean(x) for x in ["misconfig", m.get("Severity") or "UNKNOWN", m.get("ID"), m.get("Type"),
                                           "-", "-", target, m.get("Title") or ""]))
PY
}

if command -v jq >/dev/null 2>&1; then
  PARSER=jq
  TSV="$(extract_with_jq)"
elif command -v python3 >/dev/null 2>&1; then
  PARSER=python3
  TSV="$(extract_with_python)"
else
  echo "trivy_gate: jq 또는 python3 가 필요합니다" >&2
  exit 2
fi

# --------------------------------------------------------------------------
# 집계 표 + 게이트 판정 (awk)
# --------------------------------------------------------------------------
echo "== Trivy gate: $JSON (gate: $SEVERITIES, parser: $PARSER) =="
# awk 는 표/목록을 출력하고 마지막 줄에 게이트 대상 건수만 숫자로 출력한다.
REPORT="$(printf '%s\n' "$TSV" | awk -F'\t' -v gate="$SEVERITIES" -v maxlist="$MAX_LIST" '
  BEGIN {
    n = split("CRITICAL HIGH MEDIUM LOW UNKNOWN", order, " ")
    split(gate, g, ",")
    for (i in g) if (g[i] != "") gated[g[i]] = 1
    listed = 0; total_gated = 0
  }
  NF < 2 { next }
  {
    type = $1; sev = toupper($2)
    if (!(sev in known)) { known[sev] = 1; if (sev != "CRITICAL" && sev != "HIGH" && sev != "MEDIUM" && sev != "LOW" && sev != "UNKNOWN") extra[sev] = 1 }
    count[sev, type]++; count[sev, "all"]++; count["TOTAL", type]++; count["TOTAL", "all"]++
    if (sev in gated) {
      total_gated++
      if (listed < maxlist) {
        listed++
        fix = ($6 == "-" || $6 == "") ? "" : (" -> " $6)
        detail = ($5 == "-" || $5 == "") ? "" : (" " $5)
        line = sprintf("  [%s] %s %s %s%s%s (%s)", sev, type, $3, $4, detail, fix, $7)
        if ($8 != "") line = line " " substr($8, 1, 80)
        gated_lines[listed] = line
      }
    }
  }
  END {
    printf "%-9s %7s %8s %11s %7s\n", "Severity", "Vulns", "Secrets", "Misconfigs", "Total"
    for (i = 1; i <= n; i++) {
      s = order[i]
      printf "%-9s %7d %8d %11d %7d\n", s, count[s, "vuln"] + 0, count[s, "secret"] + 0, count[s, "misconfig"] + 0, count[s, "all"] + 0
    }
    for (s in extra) printf "%-9s %7d %8d %11d %7d\n", s, count[s, "vuln"] + 0, count[s, "secret"] + 0, count[s, "misconfig"] + 0, count[s, "all"] + 0
    printf "%-9s %7d %8d %11d %7d\n", "TOTAL", count["TOTAL", "vuln"] + 0, count["TOTAL", "secret"] + 0, count["TOTAL", "misconfig"] + 0, count["TOTAL", "all"] + 0
    printf "\nGated findings (%s): %d\n", gate, total_gated
    for (i = 1; i <= listed; i++) print gated_lines[i]
    if (total_gated > listed) printf "  ... and %d more\n", total_gated - listed
    print total_gated
  }
')"

printf '%s\n' "$REPORT" | sed '$d'
GATED_COUNT="$(printf '%s\n' "$REPORT" | tail -n 1 | tr -dc '0-9')"
GATED_COUNT="${GATED_COUNT:-0}"

if [ "$GATED_COUNT" -gt 0 ]; then
  if [ "$REPORT_ONLY" -eq 1 ]; then
    echo "RESULT: REPORT-ONLY - ${GATED_COUNT} finding(s) at ${SEVERITIES} (게이트 미적용)"
    exit 0
  fi
  echo "RESULT: FAIL - ${GATED_COUNT} finding(s) at ${SEVERITIES}. 수정하거나 근거를 남기고 .trivyignore 에 추가하세요."
  exit 1
fi
echo "RESULT: PASS - no findings at ${SEVERITIES}"
exit 0
