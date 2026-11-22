#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gemini_review.py - git diff -> Gemini -> GitHub Pull Request Review.

ci/Jenkinsfile.codereview 의 "AI Code Review" 스테이지에서 호출된다.
파이썬 표준 라이브러리만 사용한다 (urllib, json, subprocess, argparse, re).

동작 개요
  1. ``git diff --unified=3 <base>...<head>`` 를 파일/hunk 단위로 파싱한다.
     바이너리, lock 파일, node_modules/dist/build 등은 리뷰 대상에서 제외한다.
  2. 파일 단위(기본) 또는 hunk 단위로 Gemini 에 리뷰를 요청하고, hunk 마다 최소 1개의
     코멘트(문제가 없으면 ``LGTM`` info 코멘트)를 확보한다.
  3. 전체 변경에 대한 총평(요약, 위험도, 머지 권고, 테스트/스캔 요약, 통계)을 1회 요청한다.
  4. GitHub ``POST /repos/{repo}/pulls/{pr}/reviews`` 로 총평(body) + 인라인 코멘트를 한 번에
     등록한다. 실패(422 등) 시 issue comment 로 fallback 한다.
  5. ``review-result.json`` 에 전체 결과를 저장하고 stdout 에 요약을 출력한다.

환경 변수
  GEMINI_API_KEY   Gemini API 키 (헤더 ``x-goog-api-key`` 로 전송, 로그 출력 금지)
  GEMINI_MODEL     기본 ``gemini-2.5-pro``
  GEMINI_API_URL   기본 ``https://generativelanguage.googleapis.com/v1beta`` (테스트용 override)
  GEMINI_FAKE      ``1`` 이면 API 호출 없이 결정적 가짜 응답 사용 (검증용)
  GITHUB_TOKEN     GitHub PAT (repo 권한)
  GITHUB_API_URL   기본 ``https://api.github.com``
  REVIEW_LANGUAGE  기본 ``ko``
  CHANGE_URL       ``--repo`` 가 없을 때 owner/repo 파싱에 사용 (Jenkins 멀티브랜치 PR 환경변수)

종료 코드
  0 정상 (fallback 게시 포함)   2 인자/git 오류   3 GEMINI_API_KEY 없음   4 GitHub 게시 실패
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__version__ = "1.0.0"

# 기본 모델: 무료 티어에서 pro 계열은 429(quota), gemini-2.5-* 는 404(신규 사용자 불가) 이므로 GA flash 를 쓴다.
DEFAULT_MODEL = "gemini-3.5-flash"
# 404(모델 없음/사용 불가) 또는 429 가 재시도 후에도 지속되면 순서대로 전환한다 (env GEMINI_MODEL_FALLBACKS).
DEFAULT_MODEL_FALLBACKS = "gemini-3-flash-preview,gemini-3.1-flash-lite"
# thinking 모델은 출력 토큰에 사고 토큰이 포함되므로 넉넉히 (hunk >= 2048, 총평 >= 4096 보장)
DEFAULT_MAX_OUTPUT_TOKENS_HUNK = 8192
DEFAULT_MAX_OUTPUT_TOKENS_SUMMARY = 8192
DEFAULT_GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GITHUB_API_URL = "https://api.github.com"
USER_AGENT = "cicd-ai-flow-gemini-review/" + __version__

SEVERITIES: Tuple[str, ...] = ("critical", "major", "minor", "info")
SEVERITY_RANK: Dict[str, int] = {s: i for i, s in enumerate(SEVERITIES)}
SEVERITY_ICON: Dict[str, str] = {"critical": "🔴", "major": "🟠", "minor": "🟡", "info": "🔵"}
CATEGORIES: Tuple[str, ...] = ("bug", "security", "performance", "style", "test", "docs")
RECOMMENDATIONS: Tuple[str, ...] = ("approve", "request-changes", "comment")
RISKS: Tuple[str, ...] = ("low", "medium", "high")

MAX_INLINE_COMMENTS = 80
GITHUB_BODY_LIMIT = 65000
RETRY_STATUSES = {429, 500, 502, 503, 504}

LOCK_FILE_RE = re.compile(
    r"(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|gradle\.lockfile|"
    r"[^/]+\.lockfile|Cargo\.lock|poetry\.lock|Pipfile\.lock|composer\.lock|Gemfile\.lock)$"
)
SKIP_DIR_RE = re.compile(r"(^|/)(node_modules|dist|build|\.gradle|coverage|target|\.git)/")
BINARY_EXT_RE = re.compile(
    r"\.(jar|war|ear|class|png|jpe?g|gif|ico|pdf|zip|gz|tgz|bz2|xz|7z|woff2?|ttf|eot|otf|"
    r"mp[34]|mov|exe|dll|so|dylib|bin)$",
    re.IGNORECASE,
)
DIFF_HEADER_RE = re.compile(r'^diff --git "?a/(.*?)"? "?b/(.*?)"?$')
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
CHANGE_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/pull/(\d+)")

LANG_BY_EXT: Dict[str, str] = {
    ".kt": "Kotlin", ".kts": "Kotlin (Gradle DSL)", ".java": "Java", ".ts": "TypeScript",
    ".tsx": "TypeScript (React)", ".js": "JavaScript", ".jsx": "JavaScript (React)",
    ".py": "Python", ".sh": "Shell", ".yml": "YAML", ".yaml": "YAML", ".json": "JSON",
    ".md": "Markdown", ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".properties": "Properties",
    ".xml": "XML", ".toml": "TOML", ".groovy": "Groovy", ".gradle": "Gradle",
}


# --------------------------------------------------------------------------------------
# 로깅
# --------------------------------------------------------------------------------------
def log(msg: str) -> None:
    """stderr 로 로그 한 줄을 출력한다 (API 키 등 비밀은 절대 포함하지 않는다)."""
    sys.stderr.write("[gemini-review] " + msg + "\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------------------
# 데이터 모델
# --------------------------------------------------------------------------------------
@dataclass
class Hunk:
    """unified diff 의 hunk 하나. ``lines`` 는 (prefix, text, new_line_no) 튜플 목록."""

    index: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str = ""
    lines: List[Tuple[str, str, Optional[int]]] = field(default_factory=list)
    truncated: bool = False

    @property
    def added_lines(self) -> List[int]:
        """새 파일 기준으로 추가/수정된 라인 번호 목록."""
        return [n for p, _, n in self.lines if p == "+" and n is not None]

    @property
    def right_lines(self) -> List[int]:
        """GitHub RIGHT side 에 코멘트를 달 수 있는 라인(문맥 + 추가) 목록."""
        return [n for p, _, n in self.lines if p in (" ", "+") and n is not None]

    @property
    def is_deletion_only(self) -> bool:
        """추가 라인이 하나도 없는(삭제 전용) hunk 인지."""
        return not self.added_lines

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_count, 1) - 1

    def render(self, max_chars: int) -> str:
        """프롬프트용으로 라인 번호를 붙여 렌더링한다. 길면 잘라낸다."""
        out: List[str] = []
        total = 0
        for i, (prefix, text, new_no) in enumerate(self.lines):
            no = f"{new_no:>5}" if new_no is not None else "     "
            row = f"{no} | {prefix}{text}"
            total += len(row) + 1
            if total > max_chars:
                remaining = len(self.lines) - i
                out.append(f"      | ... (hunk 이 길어 {remaining}줄 생략됨)")
                self.truncated = True
                break
            out.append(row)
        return "\n".join(out)

    def raw(self) -> str:
        """원본 hunk 텍스트(헤더 포함)."""
        head = f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@ {self.section}".rstrip()
        return "\n".join([head] + [p + t for p, t, _ in self.lines])


@dataclass
class FileDiff:
    """변경 파일 하나."""

    path: str
    old_path: str
    status: str = "modified"  # added | modified | deleted | renamed
    is_binary: bool = False
    hunks: List[Hunk] = field(default_factory=list)
    excluded_reason: Optional[str] = None

    @property
    def language(self) -> str:
        _, ext = os.path.splitext(self.path)
        return LANG_BY_EXT.get(ext.lower(), "unknown")

    @property
    def added_line_count(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def removed_line_count(self) -> int:
        return sum(1 for h in self.hunks for p, _, _ in h.lines if p == "-")


@dataclass
class Comment:
    """정규화된 리뷰 코멘트."""

    path: str
    line: Optional[int]
    severity: str
    category: str
    body: str
    hunk_index: int
    file_level: bool = False
    original_line: Optional[int] = None
    adjusted: bool = False
    auto_failed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "line": self.line, "severity": self.severity, "category": self.category,
            "body": self.body, "hunk_index": self.hunk_index, "file_level": self.file_level,
            "original_line": self.original_line, "adjusted": self.adjusted, "auto_failed": self.auto_failed,
        }

    def rendered_body(self, model_label: str) -> str:
        badge = f"{SEVERITY_ICON.get(self.severity, '')} **[{self.severity.upper()}][{self.category}]**"
        return f"{badge} {self.body}\n\n<sub>🤖 AI review · {model_label}</sub>"


@dataclass
class HunkReview:
    """백엔드(Gemini/Fake)가 돌려준 hunk 하나의 리뷰 원본."""

    index: int
    summary: str = ""
    comments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SummaryResult:
    """총평 응답."""

    overview: str
    risk: str = "medium"
    risk_reason: str = ""
    recommendation: str = "comment"
    highlights: List[str] = field(default_factory=list)
    test_and_scan: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overview": self.overview, "risk": self.risk, "risk_reason": self.risk_reason,
            "recommendation": self.recommendation, "highlights": self.highlights,
            "test_and_scan": self.test_and_scan,
        }


@dataclass
class SummaryContext:
    """총평 프롬프트 입력."""

    pr_number: Optional[int]
    pr_title: str
    base: str
    head: str
    files: List[FileDiff]
    hunk_summaries: List[Tuple[str, int, str]]  # (path, hunk index, summary)
    comments: List[Comment]
    ci_summary: str
    lock_files: List[str]
    skipped_files: List[Tuple[str, str]]  # (path, reason)
    unreviewed_files: List[str]


# --------------------------------------------------------------------------------------
# git / diff 파싱
# --------------------------------------------------------------------------------------
class GitError(RuntimeError):
    pass


def run_git(args: Sequence[str], cwd: Optional[str] = None) -> str:
    """git 명령을 실행하고 stdout 을 돌려준다."""
    cmd = ["git"] + list(args)
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)} 실패 (exit {proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def get_diff(base: str, head: str, cwd: Optional[str] = None) -> str:
    """``git diff --unified=3 base...head`` 결과를 돌려준다."""
    return run_git(["diff", "--unified=3", "--no-color", "--no-ext-diff", "--find-renames", f"{base}...{head}"], cwd)


def resolve_sha(rev: str, cwd: Optional[str] = None) -> str:
    return run_git(["rev-parse", rev], cwd).strip()


def _split_header_path(line: str, prefix: str) -> Optional[str]:
    """``--- a/x`` / ``+++ b/x`` 헤더에서 경로를 추출한다. /dev/null 이면 None."""
    rest = line[4:].strip()
    if rest.startswith('"') and rest.endswith('"'):
        rest = rest[1:-1]
    if rest == "/dev/null":
        return None
    if rest.startswith(prefix):
        rest = rest[len(prefix):]
    return rest.split("\t")[0]


def parse_unified_diff(text: str) -> List[FileDiff]:
    """unified diff 텍스트를 FileDiff 목록으로 파싱한다.

    hunk 내부의 ``---``/``+++`` 로 시작하는 콘텐츠 라인과 파일 헤더를 구분하기 위해
    hunk 헤더의 old/new 카운트를 추적한다.
    """
    files: List[FileDiff] = []
    current: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    remaining_old = remaining_new = 0
    new_line = 0

    for raw in text.splitlines():
        m = DIFF_HEADER_RE.match(raw)
        if m:
            current = FileDiff(path=m.group(2), old_path=m.group(1))
            files.append(current)
            hunk = None
            remaining_old = remaining_new = 0
            continue
        if current is None:
            continue

        in_hunk = hunk is not None and (remaining_old > 0 or remaining_new > 0)
        if not in_hunk:
            hm = HUNK_HEADER_RE.match(raw)
            if hm:
                old_start, old_count = int(hm.group(1)), int(hm.group(2) if hm.group(2) is not None else 1)
                new_start, new_count = int(hm.group(3)), int(hm.group(4) if hm.group(4) is not None else 1)
                hunk = Hunk(index=len(current.hunks) + 1, old_start=old_start, old_count=old_count,
                            new_start=new_start, new_count=new_count, section=hm.group(5).strip())
                current.hunks.append(hunk)
                remaining_old, remaining_new = old_count, new_count
                new_line = new_start
                continue
            if raw.startswith("new file mode"):
                current.status = "added"
            elif raw.startswith("deleted file mode"):
                current.status = "deleted"
            elif raw.startswith("rename from "):
                current.status = "renamed"
                current.old_path = raw[len("rename from "):]
            elif raw.startswith("rename to "):
                current.path = raw[len("rename to "):]
            elif raw.startswith("Binary files") or raw.startswith("GIT binary patch"):
                current.is_binary = True
            elif raw.startswith("--- "):
                p = _split_header_path(raw, "a/")
                if p is not None:
                    current.old_path = p
            elif raw.startswith("+++ "):
                p = _split_header_path(raw, "b/")
                if p is not None:
                    current.path = p
            continue

        # hunk 본문
        assert hunk is not None
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        prefix, text_part = (raw[0], raw[1:]) if raw else (" ", "")
        if prefix == "+":
            hunk.lines.append(("+", text_part, new_line))
            new_line += 1
            remaining_new -= 1
        elif prefix == "-":
            hunk.lines.append(("-", text_part, None))
            remaining_old -= 1
        else:
            hunk.lines.append((" ", text_part, new_line))
            new_line += 1
            remaining_new -= 1
            remaining_old -= 1
    return files


def classify_files(files: List[FileDiff], max_files: int) -> Tuple[List[FileDiff], List[Tuple[str, str]], List[str], List[str]]:
    """리뷰 대상 / 제외(사유) / lock 파일 / max-files 초과 파일로 분류한다."""
    included: List[FileDiff] = []
    skipped: List[Tuple[str, str]] = []
    lock_files: List[str] = []
    unreviewed: List[str] = []
    for f in files:
        reason: Optional[str] = None
        if LOCK_FILE_RE.search(f.path):
            reason = "lock 파일"
            lock_files.append(f.path)
        elif SKIP_DIR_RE.search(f.path):
            reason = "생성물/의존성 디렉터리"
        elif f.is_binary or BINARY_EXT_RE.search(f.path):
            reason = "바이너리"
        elif not f.hunks:
            reason = "내용 변경 없음(모드/이름 변경)"
        if reason:
            f.excluded_reason = reason
            skipped.append((f.path, reason))
            continue
        if len(included) >= max_files:
            unreviewed.append(f.path)
            continue
        included.append(f)
    return included, skipped, lock_files, unreviewed


# --------------------------------------------------------------------------------------
# Gemini 클라이언트
# --------------------------------------------------------------------------------------
class GeminiError(RuntimeError):
    pass


class GeminiParseError(GeminiError):
    pass


def extract_json(text: str) -> Any:
    """모델 응답에서 JSON 객체를 꺼낸다 (코드펜스/앞뒤 설명 허용)."""
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


class GeminiModelUnavailable(GeminiError):
    """HTTP 404: 모델이 없거나 이 키로는 사용할 수 없음 -> 다음 후보 모델로 전환."""


class GeminiQuotaExceeded(GeminiError):
    """HTTP 429 가 재시도 후에도 지속됨 -> 다음 후보 모델로 전환."""


class GeminiEmptyResponse(GeminiError):
    """candidates[].content.parts 에 text 가 없음(사고 토큰 소진 등) -> 파싱 실패와 같이 1회 재요청."""


def parse_model_list(value: Optional[str]) -> List[str]:
    """쉼표 구분 모델 목록 문자열 -> 공백 제거/중복 제거된 리스트."""
    out: List[str] = []
    for m in (value or "").split(","):
        m = m.strip()
        if m and m not in out:
            out.append(m)
    return out


class GeminiClient:
    """generativelanguage ``generateContent`` REST 호출 (재시도/백오프 + 모델 fallback 포함).

    - 429/5xx/네트워크 오류: 지수 백오프로 ``retries`` 회 재시도
    - 404(모델 없음/사용 불가) 또는 429 가 재시도 후에도 지속: ``fallbacks`` 의 다음 모델로 전환하고
      이후 모든 호출에 그 모델을 사용 (``model_switches`` 에 기록, ``model`` 이 실제 사용 모델)
    - 빈 텍스트 응답은 ``GeminiEmptyResponse`` -> ``generate_json`` 이 파싱 실패와 같이 1회 재요청
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, api_url: str = DEFAULT_GEMINI_API_URL,
                 timeout: float = 180.0, retries: int = 3, backoff: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep, temperature: float = 0.2,
                 fallbacks: Optional[Sequence[str]] = None) -> None:
        self.api_key = api_key
        self.model = model
        self.requested_model = model
        self.fallbacks: List[str] = [m for m in (fallbacks or []) if m and m != model]
        self.model_switches: List[Dict[str, str]] = []
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep
        self.temperature = temperature
        self.calls = 0

    # ---- 단일 모델 호출 (재시도 포함) -------------------------------------------------
    def _request_with_model(self, prompt: str, system: Optional[str], max_output_tokens: int) -> str:
        url = f"{self.api_url}/models/{self.model}:generateContent"
        body: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "maxOutputTokens": int(max_output_tokens),
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key, "User-Agent": USER_AGENT}
        last_err: Optional[GeminiError] = None
        last_code = 0
        for attempt in range(self.retries):
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return self._extract_text(payload)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                e.close()
                last_code = e.code
                if e.code == 404:
                    raise GeminiModelUnavailable(f"HTTP 404 from Gemini (model {self.model}): {detail}")
                last_err = GeminiError(f"HTTP {e.code} from Gemini (model {self.model}): {detail}")
                if e.code not in RETRY_STATUSES:
                    raise last_err
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_code = 0
                last_err = GeminiError(f"Gemini 네트워크 오류 (model {self.model}): {e}")
            wait = self.backoff * (2 ** attempt)
            log(f"Gemini 호출 실패 (시도 {attempt + 1}/{self.retries}): {last_err}; {wait:.0f}s 후 재시도")
            self.sleep(wait)
        if last_code == 429:
            raise GeminiQuotaExceeded(f"HTTP 429 가 {self.retries}회 재시도 후에도 지속 (model {self.model}): {last_err}")
        raise last_err if last_err else GeminiError("Gemini 호출 실패")

    # ---- 모델 fallback -----------------------------------------------------------------
    def _request(self, prompt: str, system: Optional[str], max_output_tokens: int) -> str:
        while True:
            try:
                return self._request_with_model(prompt, system, max_output_tokens)
            except (GeminiModelUnavailable, GeminiQuotaExceeded) as e:
                if not self.fallbacks:
                    raise
                nxt = self.fallbacks.pop(0)
                reason = "404 model unavailable" if isinstance(e, GeminiModelUnavailable) else "429 quota exceeded (after retries)"
                log(f"모델 전환: {self.model} -> {nxt} ({reason})")
                self.model_switches.append({"from": self.model, "to": nxt, "reason": reason, "detail": str(e)[:200]})
                self.model = nxt

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        if "promptFeedback" in payload and payload["promptFeedback"].get("blockReason"):
            raise GeminiError(f"프롬프트가 차단됨: {payload['promptFeedback'].get('blockReason')}")
        candidates = payload.get("candidates") or []
        if not candidates:
            raise GeminiError(f"응답에 candidates 없음: {json.dumps(payload)[:300]}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise GeminiEmptyResponse(f"빈 텍스트 응답 (finishReason={candidates[0].get('finishReason')}, parts={len(parts)})")
        return text

    def generate_json(self, prompt: str, system: Optional[str] = None,
                      max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS_HUNK) -> Any:
        """JSON 응답을 요청하고 파싱한다. 파싱 실패/빈 텍스트면 1회 재요청 후 포기."""
        retry_prompt = prompt + "\n\n직전 응답이 유효한 JSON 이 아니었습니다. 설명이나 코드펜스 없이 JSON 객체 하나만 출력하세요."
        try:
            text = self._request(prompt, system, max_output_tokens)
        except GeminiEmptyResponse as e:
            log(f"{e} - 1회 재요청")
            text = ""
        if text:
            try:
                return extract_json(text)
            except (json.JSONDecodeError, ValueError):
                log("응답 JSON 파싱 실패 - JSON 만 출력하도록 1회 재요청")
        try:
            text = self._request(retry_prompt, system, max_output_tokens)
            return extract_json(text)
        except GeminiEmptyResponse as e:
            raise GeminiParseError(f"재요청도 빈 텍스트: {e}")
        except (json.JSONDecodeError, ValueError) as e:
            raise GeminiParseError(f"JSON 파싱 실패: {e}")


# --------------------------------------------------------------------------------------
# 리뷰 백엔드 (Gemini / Fake)
# --------------------------------------------------------------------------------------
SYSTEM_PROMPT = {
    "ko": (
        "당신은 꼼꼼하고 실용적인 시니어 소프트웨어 엔지니어이자 코드 리뷰어입니다. "
        "Kotlin/Spring Boot 백엔드, React/TypeScript 프론트엔드, Gradle/npm 설정, Docker/Jenkins/셸 구성 파일을 리뷰합니다. "
        "버그, 보안 취약점(인젝션, 하드코딩된 비밀, 약한 암호화, XSS, 안전하지 않은 역직렬화, 취약한 의존성), "
        "성능 문제, 테스트 누락, 가독성과 유지보수성을 중점적으로 봅니다. "
        "근거 없는 추측은 피하고, 지적할 때는 구체적인 수정 방향이나 예시 코드를 제시합니다. "
        "모든 설명은 한국어로 작성하며, 반드시 요청된 JSON 형식으로만 응답합니다."
    ),
    "en": (
        "You are a meticulous, pragmatic senior software engineer acting as a code reviewer for a Kotlin/Spring Boot "
        "backend, a React/TypeScript frontend and Docker/Jenkins/shell configuration. Focus on bugs, security "
        "vulnerabilities, performance, missing tests, readability and maintainability. Avoid speculation; give "
        "concrete fixes. Write in English and respond only in the requested JSON format."
    ),
}


def build_hunk_prompt(file: FileDiff, hunks: Sequence[Hunk], language: str, max_hunk_chars: int) -> str:
    """파일 하나(의 일부 hunk 들)에 대한 리뷰 프롬프트를 만든다."""
    lang_note = "한국어" if language == "ko" else "English"
    lines = [
        f"파일: `{file.path}` (상태: {file.status}, 언어 추정: {file.language})",
        "" if file.status != "renamed" else f"(이전 경로: `{file.old_path}`)",
        f"아래는 이 파일의 변경 hunk {len(hunks)}개입니다. 각 줄 앞의 숫자는 **새 파일 기준 라인 번호**이며, "
        "`+` 는 추가/수정된 줄, `-` 는 삭제된 줄(새 파일에는 없음), 공백은 문맥 줄입니다.",
        "",
    ]
    for h in hunks:
        lines.append(f"### Hunk {h.index} (새 파일 라인 {h.new_start}-{h.new_end}) {h.section}".rstrip())
        lines.append("```")
        lines.append(h.render(max_hunk_chars))
        lines.append("```")
        lines.append("")
    schema = ('{"hunks":[{"index":1,"hunk_summary":"이 hunk 의 변경 요약 한 줄",'
              '"comments":[{"line":12,"severity":"major","category":"bug","body":"지적 내용과 수정 제안"}]}]}')
    lines += [
        "요구 사항:",
        "1. 위의 **모든 hunk 각각**에 대해 최소 1개의 코멘트를 작성합니다. 문제가 없으면 severity \"info\" 로 "
        "\"LGTM: <한 줄 요약>\" 코멘트를 남깁니다.",
        "2. 코멘트의 line 은 반드시 해당 hunk 에 표시된 라인 번호 중 하나여야 하며 `+` 줄을 우선합니다. "
        "삭제된 줄에 대한 의견은 가장 가까운 `+` 줄 또는 문맥 줄에 답니다.",
        "3. severity: critical(즉시 수정해야 하는 버그/보안 결함) | major(머지 전 수정 권장) | minor(개선 제안) | info(참고/칭찬/LGTM)",
        "4. category: bug | security | performance | style | test | docs",
        f"5. body 는 {lang_note} 마크다운으로 2~6문장, 가능하면 수정 예시 코드를 포함합니다. 같은 지적은 한 번만 합니다.",
        "6. 출력은 아래 스키마의 JSON 객체 하나만 출력합니다 (설명·코드펜스 금지):",
        schema,
    ]
    return "\n".join(lines)


def build_summary_prompt(ctx: SummaryContext, language: str) -> str:
    """총평 프롬프트."""
    lang_note = "한국어" if language == "ko" else "English"
    file_rows = [f"- `{f.path}` ({f.status}, +{f.added_line_count}/-{f.removed_line_count}, hunk {len(f.hunks)})" for f in ctx.files]
    hunk_rows = [f"- `{p}` hunk {i}: {s}" for p, i, s in ctx.hunk_summaries if s]
    sev = count_by(ctx.comments, lambda c: c.severity)
    cat = count_by(ctx.comments, lambda c: c.category)
    top = sorted((c for c in ctx.comments if c.severity in ("critical", "major")), key=comment_sort_key)[:15]
    top_rows = [f"- [{c.severity}/{c.category}] `{c.path}`:{c.line if c.line else 'file'} - {c.body[:200]}" for c in top]
    schema = ('{"overview":"변경 요약 (마크다운, 3~8문장)","risk":"low|medium|high","risk_reason":"위험도 근거 1~2문장",'
              '"recommendation":"approve|request-changes|comment","highlights":["가장 중요한 지적 (최대 5개)"],'
              '"test_and_scan":"테스트/스캔 결과 해석 2~4문장"}')
    parts = [
        f"PR #{ctx.pr_number if ctx.pr_number else '?'}: {ctx.pr_title or '(제목 없음)'}",
        f"비교 범위: {ctx.base[:12]}..{ctx.head[:12]}",
        "",
        "## 변경 파일", *file_rows,
        "", "## hunk 별 요약", *(hunk_rows or ["(없음)"]),
        "", "## 리뷰 코멘트 통계",
        f"- 심각도: {json.dumps(sev, ensure_ascii=False)}",
        f"- 카테고리: {json.dumps(cat, ensure_ascii=False)}",
        "", "## 주요 지적 (critical/major)", *(top_rows or ["(없음)"]),
        "", "## CI 결과 (빌드/테스트/스캔 요약)", "```", (ctx.ci_summary.strip() or "(CI 요약 없음)"), "```",
    ]
    if ctx.lock_files:
        parts += ["", f"## 참고: lock 파일 변경 있음 - {', '.join(ctx.lock_files)} (의존성 변경 여부를 언급하세요)"]
    if ctx.unreviewed_files:
        parts += ["", f"## 참고: max-files 초과로 미검토 파일 {len(ctx.unreviewed_files)}개 - {', '.join(ctx.unreviewed_files[:20])}"]
    parts += [
        "", "위 정보를 바탕으로 PR 총평을 작성하세요. 머지 권고는 critical 이 있으면 request-changes, "
        "major 만 있으면 상황에 따라 request-changes 또는 comment, minor/info 뿐이고 CI 가 정상이면 approve 를 권합니다.",
        f"모든 문장은 {lang_note} 로 작성하고, 아래 스키마의 JSON 객체 하나만 출력하세요:", schema,
    ]
    return "\n".join(parts)


class ReviewBackend:
    """리뷰 백엔드 인터페이스."""

    label = "backend"

    def review_hunks(self, file: FileDiff, hunks: Sequence[Hunk]) -> Dict[int, HunkReview]:
        raise NotImplementedError

    def summarize(self, ctx: SummaryContext) -> SummaryResult:
        raise NotImplementedError


class GeminiBackend(ReviewBackend):
    """Gemini 호출 백엔드."""

    def __init__(self, client: GeminiClient, language: str = "ko", max_hunk_chars: int = 12000,
                 max_output_tokens_hunk: int = DEFAULT_MAX_OUTPUT_TOKENS_HUNK,
                 max_output_tokens_summary: int = DEFAULT_MAX_OUTPUT_TOKENS_SUMMARY) -> None:
        self.client = client
        self.language = language if language in SYSTEM_PROMPT else "ko"
        self.max_hunk_chars = max_hunk_chars
        self.max_output_tokens_hunk = max(2048, int(max_output_tokens_hunk))
        self.max_output_tokens_summary = max(4096, int(max_output_tokens_summary))

    @property
    def label(self) -> str:  # type: ignore[override]
        """실제 사용 중인 모델(fallback 전환 반영)."""
        return f"Gemini {self.client.model}"

    def review_hunks(self, file: FileDiff, hunks: Sequence[Hunk]) -> Dict[int, HunkReview]:
        prompt = build_hunk_prompt(file, hunks, self.language, self.max_hunk_chars)
        data = self.client.generate_json(prompt, SYSTEM_PROMPT[self.language], max_output_tokens=self.max_output_tokens_hunk)
        return parse_hunk_reviews(data)

    def summarize(self, ctx: SummaryContext) -> SummaryResult:
        prompt = build_summary_prompt(ctx, self.language)
        data = self.client.generate_json(prompt, SYSTEM_PROMPT[self.language], max_output_tokens=self.max_output_tokens_summary)
        return parse_summary(data)


FAKE_PATTERNS: List[Tuple["re.Pattern[str]", str, str, str]] = [
    (re.compile(r"(password|passwd|secret|api[_-]?key)\s*[:=]\s*[\"'][^\"']+[\"']", re.I), "critical", "security",
     "하드코딩된 비밀 값으로 보입니다. 환경 변수나 시크릿 저장소에서 읽도록 변경하세요."),
    (re.compile(r"\b(eval|exec)\s*\(", re.I), "major", "security",
     "동적 코드 실행은 인젝션 위험이 있습니다. 입력을 코드로 평가하지 않는 방식으로 바꾸세요."),
    (re.compile(r"\"SELECT .*\"\s*\+", re.I), "major", "security",
     "문자열 결합으로 SQL 을 조립하고 있습니다. PreparedStatement/파라미터 바인딩을 사용하세요."),
    (re.compile(r"dangerouslySetInnerHTML|innerHTML\s*="), "major", "security",
     "신뢰할 수 없는 HTML 을 직접 삽입하면 XSS 위험이 있습니다. sanitize 하거나 텍스트로 렌더링하세요."),
    (re.compile(r"\bTODO\b|\bFIXME\b"), "minor", "docs", "TODO/FIXME 가 남아 있습니다. 이슈로 추적하거나 머지 전에 해결하세요."),
    (re.compile(r"console\.log\(|println\("), "minor", "style", "디버그 출력이 남아 있습니다. 로거를 사용하거나 제거하세요."),
]


class FakeBackend(ReviewBackend):
    """``GEMINI_FAKE=1`` 용 결정적 가짜 백엔드 (API 호출 없음)."""

    label = "fake-gemini"

    def review_hunks(self, file: FileDiff, hunks: Sequence[Hunk]) -> Dict[int, HunkReview]:
        out: Dict[int, HunkReview] = {}
        for h in hunks:
            comments: List[Dict[str, Any]] = []
            for prefix, text, no in h.lines:
                if prefix != "+" or no is None:
                    continue
                for pat, sev, cat, msg in FAKE_PATTERNS:
                    if pat.search(text):
                        comments.append({"line": no, "severity": sev, "category": cat,
                                         "body": f"{msg} (`{text.strip()[:60]}`)"})
                        break
            if not comments:
                anchor = h.added_lines[0] if h.added_lines else (h.right_lines[0] if h.right_lines else None)
                comments.append({"line": anchor, "severity": "info", "category": "style",
                                 "body": f"LGTM: {len(h.added_lines)}줄 추가/수정 구간에서 특이사항을 찾지 못했습니다 (fake)."})
            out[h.index] = HunkReview(index=h.index, summary=f"{file.path} hunk {h.index}: +{len(h.added_lines)} 줄 변경 (fake)", comments=comments)
        return out

    def summarize(self, ctx: SummaryContext) -> SummaryResult:
        sev = count_by(ctx.comments, lambda c: c.severity)
        risk = "high" if sev.get("critical") else ("medium" if sev.get("major") else "low")
        rec = "request-changes" if sev.get("critical") else ("comment" if sev.get("major") else "approve")
        return SummaryResult(
            overview=f"(fake) {len(ctx.files)}개 파일, {sum(len(f.hunks) for f in ctx.files)}개 hunk 가 변경되었습니다. "
                     f"코멘트 {len(ctx.comments)}건을 생성했습니다.",
            risk=risk, risk_reason="(fake) 코멘트 심각도 분포에 따른 자동 판정", recommendation=rec,
            highlights=[f"{c.path}:{c.line} {c.body[:80]}" for c in sorted(ctx.comments, key=comment_sort_key)[:3]],
            test_and_scan="(fake) CI 요약: " + (ctx.ci_summary.strip().splitlines()[0] if ctx.ci_summary.strip() else "없음"),
        )


def parse_hunk_reviews(data: Any) -> Dict[int, HunkReview]:
    """모델 JSON 응답 -> {hunk index: HunkReview}. 관대하게 파싱한다."""
    out: Dict[int, HunkReview] = {}
    if isinstance(data, list):
        data = {"hunks": data}
    if not isinstance(data, dict):
        return out
    hunks = data.get("hunks")
    if hunks is None and "comments" in data:  # 단일 hunk 응답 형태
        hunks = [{"index": 1, "hunk_summary": data.get("hunk_summary", ""), "comments": data.get("comments", [])}]
    for item in hunks or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        comments = item.get("comments") or []
        if not isinstance(comments, list):
            comments = []
        out[idx] = HunkReview(index=idx, summary=str(item.get("hunk_summary") or item.get("summary") or ""),
                              comments=[c for c in comments if isinstance(c, dict)])
    return out


def parse_summary(data: Any) -> SummaryResult:
    if not isinstance(data, dict):
        raise GeminiParseError("총평 응답이 객체가 아님")
    risk = str(data.get("risk", "medium")).lower()
    rec = str(data.get("recommendation", "comment")).lower().replace("_", "-")
    highlights = data.get("highlights") or []
    if not isinstance(highlights, list):
        highlights = [str(highlights)]
    return SummaryResult(
        overview=str(data.get("overview") or data.get("summary") or "").strip(),
        risk=risk if risk in RISKS else "medium", risk_reason=str(data.get("risk_reason") or ""),
        recommendation=rec if rec in RECOMMENDATIONS else "comment",
        highlights=[str(h) for h in highlights][:5], test_and_scan=str(data.get("test_and_scan") or ""),
    )


# --------------------------------------------------------------------------------------
# 코멘트 정규화 / 라인 보정
# --------------------------------------------------------------------------------------
def count_by(items: Iterable[Any], key: Callable[[Any], str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        k = key(it)
        out[k] = out.get(k, 0) + 1
    return out


def comment_sort_key(c: Comment) -> Tuple[int, str, int]:
    return (SEVERITY_RANK.get(c.severity, 99), c.path, c.line or 0)


def normalize_comment(raw: Dict[str, Any], file: FileDiff, hunk: Hunk) -> Optional[Comment]:
    """모델이 준 코멘트를 검증/보정한다.

    - severity/category 를 허용 값으로 정규화
    - line 이 hunk 의 새 파일 범위(문맥+추가 라인) 밖이면 첫 추가 라인으로 보정
    - 삭제 전용 hunk 는 파일 수준 코멘트(line=None)로 전환
    """
    body = str(raw.get("body") or raw.get("comment") or raw.get("message") or "").strip()
    if not body:
        return None
    severity = str(raw.get("severity", "info")).lower().strip()
    if severity not in SEVERITY_RANK:
        severity = "info"
    category = str(raw.get("category", "style")).lower().strip()
    if category not in CATEGORIES:
        category = "style"
    original: Optional[int]
    try:
        original = int(raw.get("line")) if raw.get("line") is not None else None
    except (TypeError, ValueError):
        original = None

    if hunk.is_deletion_only:
        return Comment(path=file.path, line=None, severity=severity, category=category, body=body,
                       hunk_index=hunk.index, file_level=True, original_line=original, adjusted=original is not None)

    valid = set(hunk.right_lines)
    line = original
    adjusted = False
    if line is None or line not in valid:
        line = hunk.added_lines[0]
        adjusted = True
    return Comment(path=file.path, line=line, severity=severity, category=category, body=body,
                   hunk_index=hunk.index, original_line=original, adjusted=adjusted)


def fallback_comment(file: FileDiff, hunk: Hunk, reason: str, auto_failed: bool = False) -> Comment:
    """코멘트가 없거나 리뷰가 실패한 hunk 에 넣을 대체 코멘트."""
    if hunk.is_deletion_only:
        return Comment(path=file.path, line=None, severity="info", category="docs", body=reason,
                       hunk_index=hunk.index, file_level=True, auto_failed=auto_failed)
    return Comment(path=file.path, line=hunk.added_lines[0], severity="info", category="docs", body=reason,
                   hunk_index=hunk.index, auto_failed=auto_failed)


def chunk_hunks(hunks: Sequence[Hunk], max_request_chars: int, per_hunk: bool) -> List[List[Hunk]]:
    """요청 크기를 제한하기 위해 hunk 를 묶는다."""
    if per_hunk:
        return [[h] for h in hunks]
    batches: List[List[Hunk]] = []
    cur: List[Hunk] = []
    size = 0
    for h in hunks:
        hsize = len(h.raw())
        if cur and size + hsize > max_request_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append(h)
        size += hsize
    if cur:
        batches.append(cur)
    return batches


# --------------------------------------------------------------------------------------
# 리뷰 실행
# --------------------------------------------------------------------------------------
@dataclass
class ReviewOutput:
    comments: List[Comment]
    hunk_summaries: List[Tuple[str, int, str]]
    failures: List[Tuple[str, str]]  # (path, error)


def review_files(files: Sequence[FileDiff], backend: ReviewBackend, max_hunk_chars: int, per_hunk: bool) -> ReviewOutput:
    """리뷰 대상 파일을 백엔드로 보내 hunk 마다 최소 1개의 코멘트를 확보한다."""
    comments: List[Comment] = []
    summaries: List[Tuple[str, int, str]] = []
    failures: List[Tuple[str, str]] = []
    max_request_chars = max_hunk_chars * 3
    for f in files:
        for batch in chunk_hunks(f.hunks, max_request_chars, per_hunk):
            try:
                reviews = backend.review_hunks(f, batch)
            except Exception as e:  # noqa: BLE001 - 어떤 실패든 hunk 는 '자동 리뷰 실패' 로 남긴다
                log(f"{f.path} hunk {[h.index for h in batch]} 리뷰 실패: {e}")
                failures.append((f.path, str(e)[:300]))
                for h in batch:
                    comments.append(fallback_comment(
                        f, h, f"자동 리뷰 실패: 이 변경 구간은 AI 리뷰를 받지 못했습니다 ({str(e)[:120]}). 수동 검토가 필요합니다.",
                        auto_failed=True))
                continue
            missing = [h for h in batch if h.index not in reviews]
            if missing and len(batch) > 1:
                # 파일 단위 응답에서 빠진 hunk 는 개별 재요청
                for h in missing:
                    try:
                        reviews.update(backend.review_hunks(f, [h]))
                    except Exception as e:  # noqa: BLE001
                        log(f"{f.path} hunk {h.index} 개별 재요청 실패: {e}")
            for h in batch:
                rv = reviews.get(h.index)
                got: List[Comment] = []
                if rv is not None:
                    summaries.append((f.path, h.index, rv.summary))
                    for raw in rv.comments:
                        c = normalize_comment(raw, f, h)
                        if c is not None:
                            got.append(c)
                if not got:
                    got.append(fallback_comment(f, h, "LGTM: 이 변경 구간에서 특이사항을 찾지 못했습니다 (자동 생성)."))
                comments.extend(got)
    return ReviewOutput(comments=comments, hunk_summaries=summaries, failures=failures)


def local_summary(ctx: SummaryContext, reason: str) -> SummaryResult:
    """총평 호출이 실패했을 때 로컬에서 만드는 대체 총평."""
    sev = count_by(ctx.comments, lambda c: c.severity)
    risk = "high" if sev.get("critical") else ("medium" if sev.get("major") else "low")
    rec = "request-changes" if sev.get("critical") else "comment"
    return SummaryResult(
        overview=f"AI 총평 생성에 실패해 자동 요약으로 대체합니다 ({reason}). 변경 파일 {len(ctx.files)}개, "
                 f"hunk {sum(len(f.hunks) for f in ctx.files)}개, 코멘트 {len(ctx.comments)}건.",
        risk=risk, risk_reason="코멘트 심각도 분포 기준 자동 판정", recommendation=rec,
        highlights=[f"`{c.path}`:{c.line or 'file'} {c.body[:100]}" for c in sorted(ctx.comments, key=comment_sort_key)[:5]],
        test_and_scan=ctx.ci_summary.strip()[:500] or "CI 요약 없음",
    )


# --------------------------------------------------------------------------------------
# 마크다운 생성
# --------------------------------------------------------------------------------------
def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build_summary_markdown(ctx: SummaryContext, summary: SummaryResult, inline: Sequence[Comment],
                           overflow: Sequence[Comment], file_level: Sequence[Comment], model_label: str,
                           failures: Sequence[Tuple[str, str]]) -> str:
    """GitHub review body 용 총평 마크다운."""
    risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(summary.risk, "⚪")
    rec_label = {"approve": "✅ approve", "request-changes": "🛑 request-changes", "comment": "💬 comment"}.get(summary.recommendation, summary.recommendation)
    sev = count_by(ctx.comments, lambda c: c.severity)
    cat = count_by(ctx.comments, lambda c: c.category)
    parts: List[str] = [f"## 🤖 AI Code Review ({model_label})", "", summary.overview or "(요약 없음)", ""]
    parts.append(f"**위험도**: {risk_icon} {summary.risk}" + (f" - {summary.risk_reason}" if summary.risk_reason else ""))
    parts.append(f"**머지 권고**: {rec_label}")
    if summary.highlights:
        parts += ["", "### 주요 지적 사항"] + [f"- {h}" for h in summary.highlights]
    parts += ["", "### 테스트 · 스캔 결과"]
    if summary.test_and_scan:
        parts.append(summary.test_and_scan)
    if ctx.ci_summary.strip():
        parts += ["", "<details><summary>CI 요약 원문 (ci-summary.txt)</summary>", "", "```text", ctx.ci_summary.strip()[:6000], "```", "", "</details>"]
    parts += ["", "### 코멘트 통계",
              md_table(["심각도"] + list(SEVERITIES), [["건수"] + [str(sev.get(s, 0)) for s in SEVERITIES]]), "",
              md_table(["카테고리"] + list(CATEGORIES), [["건수"] + [str(cat.get(c, 0)) for c in CATEGORIES]])]
    parts.append(f"\n인라인 코멘트 {len(inline)}건" + (f", 아래 표로만 제공 {len(overflow)}건" if overflow else "") + (f", 파일 수준 {len(file_level)}건" if file_level else ""))
    if file_level:
        parts += ["", "### 파일 수준 코멘트 (삭제 전용 hunk)",
                  md_table(["파일", "hunk", "심각도", "내용"], [[f"`{c.path}`", str(c.hunk_index), c.severity, c.body[:400]] for c in file_level])]
    if overflow:
        parts += ["", f"### 인라인에 싣지 못한 코멘트 ({MAX_INLINE_COMMENTS}건 초과분)",
                  md_table(["파일:라인", "심각도", "카테고리", "내용"], [[f"`{c.path}:{c.line}`", c.severity, c.category, c.body[:300]] for c in overflow])]
    scope = [f"- 리뷰 파일 {len(ctx.files)}개, hunk {sum(len(f.hunks) for f in ctx.files)}개 (비교 범위 `{ctx.base[:10]}..{ctx.head[:10]}`)"]
    if ctx.lock_files:
        scope.append(f"- ⚠️ lock 파일 변경 있음 (리뷰 제외): {', '.join('`' + p + '`' for p in ctx.lock_files)}")
    other_skips = [(p, r) for p, r in ctx.skipped_files if p not in ctx.lock_files]
    if other_skips:
        scope.append("- 제외 파일: " + ", ".join(f"`{p}` ({r})" for p, r in other_skips[:20]))
    if ctx.unreviewed_files:
        scope.append(f"- ⚠️ max-files 초과로 미검토: {', '.join('`' + p + '`' for p in ctx.unreviewed_files[:20])}")
    if failures:
        scope.append(f"- ⚠️ 자동 리뷰 실패 {len(failures)}건: " + ", ".join(f"`{p}`" for p, _ in failures[:10]))
    parts += ["", "### 리뷰 범위", *scope, "", f"<sub>generated by ci/scripts/gemini_review.py v{__version__}</sub>"]
    body = "\n".join(parts)
    if len(body) > GITHUB_BODY_LIMIT:
        body = body[:GITHUB_BODY_LIMIT - 60] + "\n\n... (본문이 길어 잘렸습니다)"
    return body


def build_dryrun_markdown(body: str, inline: Sequence[Comment], model_label: str) -> str:
    parts = [body, "", "---", "", f"## 인라인 코멘트 ({len(inline)}건)", ""]
    for c in inline:
        parts += [f"#### `{c.path}` L{c.line}" + (" (라인 보정됨)" if c.adjusted else ""), "", c.rendered_body(model_label), ""]
    return "\n".join(parts)


# --------------------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------------------
class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub HTTP {status}: {message}")
        self.status = status


class GitHubClient:
    """GitHub REST v3 최소 클라이언트."""

    def __init__(self, token: str, repo: str, api_url: str = DEFAULT_GITHUB_API_URL, timeout: float = 60.0) -> None:
        self.token = token
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.api_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            e.close()
            raise GitHubError(e.code, detail)
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError(0, str(e))

    def get_pull(self, pr: int) -> Dict[str, Any]:
        return self._call("GET", f"/repos/{self.repo}/pulls/{pr}")

    def create_review(self, pr: int, commit_id: str, body: str, comments: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"commit_id": commit_id, "body": body, "event": "COMMENT", "comments": list(comments)}
        return self._call("POST", f"/repos/{self.repo}/pulls/{pr}/reviews", payload)

    def create_issue_comment(self, pr: int, body: str) -> Dict[str, Any]:
        return self._call("POST", f"/repos/{self.repo}/issues/{pr}/comments", {"body": body})


def post_to_github(gh: GitHubClient, pr: int, head_sha: str, body: str, inline: Sequence[Comment],
                   model_label: str) -> Dict[str, Any]:
    """PR review 를 등록하고, 실패하면 issue comment 로 fallback 한다."""
    review_comments = [{"path": c.path, "line": c.line, "side": "RIGHT", "body": c.rendered_body(model_label)}
                       for c in inline if c.line is not None]
    try:
        res = gh.create_review(pr, head_sha, body, review_comments)
        return {"mode": "review", "url": res.get("html_url"), "id": res.get("id"), "inline": len(review_comments)}
    except GitHubError as e:
        log(f"PR review 등록 실패 ({e}); issue comment 로 fallback")
    rows = [[f"`{c.path}:{c.line}`", c.severity, c.category, c.body[:300]] for c in inline]
    fb_body = body + "\n\n---\n\n### 인라인 코멘트 (review 등록 실패로 목록으로 게시)\n\n" + md_table(["파일:라인", "심각도", "카테고리", "내용"], rows)
    if len(fb_body) > GITHUB_BODY_LIMIT:
        fb_body = fb_body[:GITHUB_BODY_LIMIT - 60] + "\n\n... (본문이 길어 잘렸습니다)"
    res = gh.create_issue_comment(pr, fb_body)
    return {"mode": "issue_comment", "url": res.get("html_url"), "id": res.get("id"), "inline": 0}


def parse_repo_from_change_url(url: str) -> Tuple[Optional[str], Optional[int]]:
    """``https://github.com/owner/repo/pull/12`` -> ("owner/repo", 12)."""
    m = CHANGE_URL_RE.search(url or "")
    if not m:
        return None, None
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="git diff -> Gemini 코드 리뷰 -> GitHub PR Review")
    p.add_argument("--base", required=True, help="비교 기준 커밋(merge-base) sha/ref")
    p.add_argument("--head", default="HEAD", help="PR head sha/ref (기본 HEAD)")
    p.add_argument("--pr", type=int, help="PR 번호 (없으면 CHANGE_URL 에서 파싱)")
    p.add_argument("--repo", help="owner/name (없으면 CHANGE_URL 에서 파싱)")
    p.add_argument("--summary-file", help="CI 결과 요약 텍스트 파일 (총평에 포함)")
    p.add_argument("--dry-run", action="store_true", help="GitHub 에 올리지 않고 markdown 출력")
    p.add_argument("--max-files", type=int, default=50)
    p.add_argument("--max-hunk-chars", type=int, default=12000)
    p.add_argument("--granularity", choices=("file", "hunk"), default="file", help="Gemini 호출 단위 (기본 file)")
    p.add_argument("--diff-file", help="git 대신 이 unified diff 파일을 입력으로 사용 (디버그용)")
    p.add_argument("--repo-dir", default=".", help="git 저장소 경로")
    p.add_argument("--output", default="review-result.json")
    p.add_argument("--dryrun-output", default="review-dryrun.md")
    p.add_argument("--pr-title", default=os.environ.get("CHANGE_TITLE", ""))
    p.add_argument("--language", default=os.environ.get("REVIEW_LANGUAGE", "ko"))
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
                   help=f"Gemini 모델 (env GEMINI_MODEL, 기본 {DEFAULT_MODEL})")
    p.add_argument("--fallbacks", default=os.environ.get("GEMINI_MODEL_FALLBACKS", DEFAULT_MODEL_FALLBACKS),
                   help=f"404/지속 429 시 순서대로 전환할 모델 (쉼표 구분, env GEMINI_MODEL_FALLBACKS, 기본 {DEFAULT_MODEL_FALLBACKS})")
    p.add_argument("--max-output-tokens", type=int, default=int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS_HUNK)),
                   help="hunk 리뷰 호출의 maxOutputTokens (총평은 이 값과 4096 중 큰 쪽)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fake = os.environ.get("GEMINI_FAKE", "").strip() in ("1", "true", "yes")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()

    repo, pr = args.repo, args.pr
    if not repo or not pr:
        url_repo, url_pr = parse_repo_from_change_url(os.environ.get("CHANGE_URL", ""))
        repo = repo or url_repo
        pr = pr or url_pr
    if not args.dry_run and (not repo or not pr):
        log("--repo/--pr 가 없고 CHANGE_URL 에서도 파싱할 수 없습니다")
        return 2
    if not fake and not api_key:
        log("GEMINI_API_KEY 가 비어 있습니다 - AI 리뷰를 건너뜁니다 (Jenkins credential 'gemini-api-key' 를 입력하세요)")
        return 3
    if not args.dry_run and not github_token:
        log("GITHUB_TOKEN 이 비어 있습니다 (credential 'github-token')")
        return 2

    # 1. diff
    try:
        if args.diff_file:
            with open(args.diff_file, "r", encoding="utf-8", errors="replace") as fh:
                diff_text = fh.read()
            base_sha, head_sha_local = args.base, args.head
        else:
            base_sha = resolve_sha(args.base, args.repo_dir)
            head_sha_local = resolve_sha(args.head, args.repo_dir)
            diff_text = get_diff(base_sha, head_sha_local, args.repo_dir)
    except GitError as e:
        log(str(e))
        return 2
    all_files = parse_unified_diff(diff_text)
    files, skipped, lock_files, unreviewed = classify_files(all_files, args.max_files)
    log(f"diff 파일 {len(all_files)}개 중 리뷰 대상 {len(files)}개, 제외 {len(skipped)}개, 미검토 {len(unreviewed)}개, hunk {sum(len(f.hunks) for f in files)}개")

    ci_summary = ""
    if args.summary_file and os.path.exists(args.summary_file):
        with open(args.summary_file, "r", encoding="utf-8", errors="replace") as fh:
            ci_summary = fh.read()

    # 2. 백엔드
    backend: ReviewBackend
    client: Optional[GeminiClient] = None
    if fake:
        backend = FakeBackend()
        log("GEMINI_FAKE=1: 가짜 백엔드 사용 (API 호출 없음)")
    else:
        fallbacks = parse_model_list(args.fallbacks)
        client = GeminiClient(api_key, model=args.model, api_url=os.environ.get("GEMINI_API_URL", DEFAULT_GEMINI_API_URL),
                              fallbacks=fallbacks)
        backend = GeminiBackend(client, language=args.language, max_hunk_chars=args.max_hunk_chars,
                                max_output_tokens_hunk=args.max_output_tokens,
                                max_output_tokens_summary=max(args.max_output_tokens, DEFAULT_MAX_OUTPUT_TOKENS_SUMMARY))
        log(f"모델 {args.model} (fallback: {', '.join(fallbacks) or '없음'}), 언어 {args.language}, maxOutputTokens hunk={backend.max_output_tokens_hunk} summary={backend.max_output_tokens_summary}")

    # 3. hunk 리뷰
    out = review_files(files, backend, args.max_hunk_chars, per_hunk=(args.granularity == "hunk"))
    ctx = SummaryContext(pr_number=pr, pr_title=args.pr_title, base=base_sha, head=head_sha_local, files=list(files),
                         hunk_summaries=out.hunk_summaries, comments=out.comments, ci_summary=ci_summary,
                         lock_files=lock_files, skipped_files=skipped, unreviewed_files=unreviewed)

    # 4. 총평
    if files:
        try:
            summary = backend.summarize(ctx)
        except Exception as e:  # noqa: BLE001
            log(f"총평 생성 실패: {e}")
            summary = local_summary(ctx, str(e)[:120])
    else:
        summary = SummaryResult(overview="리뷰 대상 코드 변경이 없습니다 (lock/바이너리/생성물만 변경되었거나 diff 가 비어 있음).",
                                risk="low", recommendation="comment")

    # 5. 인라인 선택 (>80 이면 severity 순으로 자름)
    line_comments = sorted((c for c in out.comments if not c.file_level and c.line is not None), key=comment_sort_key)
    file_level = [c for c in out.comments if c.file_level]
    inline, overflow = line_comments[:MAX_INLINE_COMMENTS], line_comments[MAX_INLINE_COMMENTS:]
    inline = sorted(inline, key=lambda c: (c.path, c.line or 0))
    body = build_summary_markdown(ctx, summary, inline, overflow, file_level, backend.label, out.failures)

    # 6. 게시 / dry-run
    posted: Dict[str, Any] = {"mode": "dry-run"}
    head_sha_remote = head_sha_local
    exit_code = 0
    if args.dry_run:
        md = build_dryrun_markdown(body, inline, backend.label)
        with open(args.dryrun_output, "w", encoding="utf-8") as fh:
            fh.write(md)
        sys.stdout.write(md + "\n")
        log(f"dry-run: {args.dryrun_output} 저장")
    else:
        assert repo and pr
        gh = GitHubClient(github_token, repo, os.environ.get("GITHUB_API_URL", DEFAULT_GITHUB_API_URL))
        try:
            pull = gh.get_pull(pr)
            head_sha_remote = str((pull.get("head") or {}).get("sha") or head_sha_local)
            if head_sha_remote != head_sha_local and not args.diff_file:
                log(f"경고: 로컬 head {head_sha_local[:10]} 와 GitHub PR head {head_sha_remote[:10]} 가 다릅니다 (PR 이 갱신되었을 수 있음)")
            if not args.pr_title and pull.get("title"):
                ctx.pr_title = str(pull["title"])
            posted = post_to_github(gh, pr, head_sha_remote, body, inline, backend.label)
            log(f"GitHub 게시 완료: mode={posted['mode']} url={posted.get('url')}")
        except GitHubError as e:
            log(f"GitHub 게시 실패: {e}")
            posted = {"mode": "failed", "error": str(e)}
            exit_code = 4

    # 7. 결과 저장
    stats = {
        "files_total": len(all_files), "files_reviewed": len(files), "files_skipped": len(skipped),
        "files_unreviewed": len(unreviewed), "hunks": sum(len(f.hunks) for f in files),
        "comments_total": len(out.comments), "comments_inline": len(inline), "comments_overflow": len(overflow),
        "comments_file_level": len(file_level), "by_severity": count_by(out.comments, lambda c: c.severity),
        "by_category": count_by(out.comments, lambda c: c.category),
        "adjusted_lines": sum(1 for c in out.comments if c.adjusted), "auto_failed": sum(1 for c in out.comments if c.auto_failed),
        "backend_failures": len(out.failures),
    }
    result = {
        "version": __version__, "repo": repo, "pr": pr, "base": base_sha, "head": head_sha_local, "head_sha_posted": head_sha_remote,
        "model": (client.model if client else "fake"), "model_requested": (client.requested_model if client else "fake"),
        "model_switches": (client.model_switches if client else []), "gemini_calls": (client.calls if client else 0),
        "backend": backend.label, "posted": posted,
        "summary": summary.to_dict(), "summary_markdown": body, "stats": stats,
        "files": [{"path": f.path, "status": f.status, "hunks": len(f.hunks), "added": f.added_line_count, "removed": f.removed_line_count} for f in files],
        "skipped_files": [{"path": p, "reason": r} for p, r in skipped], "lock_files": lock_files, "unreviewed_files": unreviewed,
        "comments": [c.to_dict() for c in out.comments], "hunk_summaries": [{"path": p, "hunk": i, "summary": s} for p, i, s in out.hunk_summaries],
        "failures": [{"path": p, "error": e} for p, e in out.failures],
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print("\n=== AI Code Review 요약 ===")
    print(f"PR #{pr} ({repo}) backend={backend.label} base={base_sha[:10]} head={head_sha_local[:10]}")
    if client and client.model_switches:
        print("모델 전환: " + " -> ".join([client.requested_model] + [s["to"] for s in client.model_switches])
              + f" (사유: {'; '.join(s['reason'] for s in client.model_switches)})")
    print(f"파일 {stats['files_reviewed']}/{stats['files_total']} 리뷰, hunk {stats['hunks']}, 코멘트 {stats['comments_total']} "
          f"(inline {stats['comments_inline']}, overflow {stats['comments_overflow']}, file-level {stats['comments_file_level']})")
    print(f"심각도 {json.dumps(stats['by_severity'], ensure_ascii=False)} / 권고 {summary.recommendation} / 위험도 {summary.risk}")
    print(f"게시: {posted}")
    print(f"결과 파일: {args.output}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
