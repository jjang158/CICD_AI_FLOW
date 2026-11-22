#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gemini_review.py 단위/통합 테스트 (표준 라이브러리 unittest 만 사용).

실행:  python3 ci/scripts/test_gemini_review.py -v

- diff 파서 (추가/수정/삭제/이름변경/바이너리, hunk 라인 번호)
- 코멘트 라인 보정 (범위 밖 -> 첫 추가 라인, 삭제 전용 hunk -> 파일 수준)
- lock/생성물 파일 제외
- Gemini 클라이언트 재시도(429 -> 200) 및 JSON 파싱 재요청 (로컬 HTTP 서버)
- GitHub review 422 -> issue comment fallback (로컬 HTTP 서버)
- GEMINI_FAKE=1 --dry-run end-to-end (임시 git 저장소)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gemini_review as gr  # noqa: E402

SAMPLE_DIFF = """diff --git a/app/backend/src/main/kotlin/Foo.kt b/app/backend/src/main/kotlin/Foo.kt
index 1111111..2222222 100644
--- a/app/backend/src/main/kotlin/Foo.kt
+++ b/app/backend/src/main/kotlin/Foo.kt
@@ -1,5 +1,6 @@
 package demo

-fun a() = 1
+fun a() = 2
+val password = "hunter2"

 fun b() = 3
@@ -20,4 +21,3 @@ fun z() {
 line20
-line21
-line22
 line23
 line24
diff --git a/new.ts b/new.ts
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.ts
@@ -0,0 +1,3 @@
+export const x = 1;
+console.log(x);
+// --- not a header ---
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
index 4444444..0000000
--- a/gone.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-bye
-bye2
diff --git a/old-name.md b/new-name.md
similarity index 90%
rename from old-name.md
rename to new-name.md
index 5555555..6666666 100644
--- a/old-name.md
+++ b/new-name.md
@@ -1 +1 @@
-# old
+# new
diff --git a/logo.png b/logo.png
new file mode 100644
index 0000000..7777777
Binary files /dev/null and b/logo.png differ
diff --git a/app/frontend/package-lock.json b/app/frontend/package-lock.json
index 8888888..9999999 100644
--- a/app/frontend/package-lock.json
+++ b/app/frontend/package-lock.json
@@ -1,3 +1,3 @@
 {
-  "version": "1.0.0",
+  "version": "1.0.1",
 }
"""


class DiffParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.files = gr.parse_unified_diff(SAMPLE_DIFF)
        self.by_path = {f.path: f for f in self.files}

    def test_file_count_and_status(self) -> None:
        self.assertEqual(len(self.files), 6)
        self.assertEqual(self.by_path["app/backend/src/main/kotlin/Foo.kt"].status, "modified")
        self.assertEqual(self.by_path["new.ts"].status, "added")
        self.assertEqual(self.by_path["gone.txt"].status, "deleted")
        self.assertEqual(self.by_path["new-name.md"].status, "renamed")
        self.assertEqual(self.by_path["new-name.md"].old_path, "old-name.md")
        self.assertTrue(self.by_path["logo.png"].is_binary)

    def test_hunk_line_numbers(self) -> None:
        foo = self.by_path["app/backend/src/main/kotlin/Foo.kt"]
        self.assertEqual(len(foo.hunks), 2)
        h1, h2 = foo.hunks
        self.assertEqual((h1.old_start, h1.old_count, h1.new_start, h1.new_count), (1, 5, 1, 6))
        self.assertEqual(h1.added_lines, [3, 4])
        self.assertEqual(h1.right_lines, [1, 2, 3, 4, 5, 6])
        self.assertFalse(h1.is_deletion_only)
        self.assertEqual(h2.section, "fun z() {")
        self.assertTrue(h2.is_deletion_only)
        self.assertEqual(h2.right_lines, [21, 22, 23])

    def test_content_line_looking_like_header_inside_hunk(self) -> None:
        new_ts = self.by_path["new.ts"]
        self.assertEqual(len(new_ts.hunks), 1)
        self.assertEqual(new_ts.hunks[0].added_lines, [1, 2, 3])
        self.assertEqual(new_ts.hunks[0].lines[2], ("+", "// --- not a header ---", 3))
        self.assertEqual(new_ts.path, "new.ts")  # "+++" 헤더로 경로가 덮어써지지 않아야 함

    def test_single_line_hunk_header(self) -> None:
        renamed = self.by_path["new-name.md"]
        self.assertEqual(renamed.hunks[0].new_count, 1)
        self.assertEqual(renamed.hunks[0].added_lines, [1])

    def test_render_truncation(self) -> None:
        h = self.by_path["new.ts"].hunks[0]
        rendered = h.render(max_chars=30)
        self.assertIn("생략됨", rendered)
        self.assertTrue(h.truncated)


class ClassifyTest(unittest.TestCase):
    def test_exclusions(self) -> None:
        files = gr.parse_unified_diff(SAMPLE_DIFF)
        included, skipped, lock_files, unreviewed = gr.classify_files(files, max_files=50)
        self.assertEqual({f.path for f in included}, {"app/backend/src/main/kotlin/Foo.kt", "new.ts", "gone.txt", "new-name.md"})
        self.assertEqual(lock_files, ["app/frontend/package-lock.json"])
        self.assertIn(("logo.png", "바이너리"), skipped)
        self.assertEqual(unreviewed, [])

    def test_max_files(self) -> None:
        files = gr.parse_unified_diff(SAMPLE_DIFF)
        included, _, _, unreviewed = gr.classify_files(files, max_files=2)
        self.assertEqual(len(included), 2)
        self.assertEqual(len(unreviewed), 2)

    def test_skip_dirs(self) -> None:
        f = gr.FileDiff(path="app/frontend/node_modules/x/index.js", old_path="x", hunks=[gr.Hunk(1, 1, 1, 1, 1, lines=[("+", "a", 1)])])
        _, skipped, _, _ = gr.classify_files([f], 50)
        self.assertEqual(skipped[0][1], "생성물/의존성 디렉터리")


class LineAdjustTest(unittest.TestCase):
    def setUp(self) -> None:
        files = gr.parse_unified_diff(SAMPLE_DIFF)
        self.foo = next(f for f in files if f.path.endswith("Foo.kt"))
        self.h1, self.h2 = self.foo.hunks

    def test_valid_line_kept(self) -> None:
        c = gr.normalize_comment({"line": 4, "severity": "CRITICAL", "category": "security", "body": "secret"}, self.foo, self.h1)
        assert c is not None
        self.assertEqual((c.line, c.severity, c.category, c.adjusted), (4, "critical", "security", False))

    def test_context_line_is_valid_anchor(self) -> None:
        c = gr.normalize_comment({"line": 6, "severity": "info", "category": "style", "body": "ctx"}, self.foo, self.h1)
        assert c is not None
        self.assertEqual((c.line, c.adjusted), (6, False))

    def test_out_of_range_line_adjusted_to_first_added(self) -> None:
        c = gr.normalize_comment({"line": 999, "severity": "minor", "category": "bug", "body": "x"}, self.foo, self.h1)
        assert c is not None
        self.assertEqual((c.line, c.original_line, c.adjusted), (3, 999, True))

    def test_missing_or_bad_values_normalized(self) -> None:
        c = gr.normalize_comment({"line": "abc", "severity": "weird", "category": "nope", "body": "x"}, self.foo, self.h1)
        assert c is not None
        self.assertEqual((c.line, c.severity, c.category, c.adjusted), (3, "info", "style", True))
        self.assertIsNone(gr.normalize_comment({"line": 3, "body": "   "}, self.foo, self.h1))

    def test_deletion_only_hunk_becomes_file_level(self) -> None:
        c = gr.normalize_comment({"line": 21, "severity": "major", "category": "bug", "body": "deleted"}, self.foo, self.h2)
        assert c is not None
        self.assertTrue(c.file_level)
        self.assertIsNone(c.line)

    def test_review_files_guarantees_one_comment_per_hunk(self) -> None:
        class EmptyBackend(gr.ReviewBackend):
            def review_hunks(self, file: gr.FileDiff, hunks: Any) -> Dict[int, gr.HunkReview]:
                return {}  # 모델이 아무 것도 안 준 경우

        out = gr.review_files([self.foo], EmptyBackend(), 12000, per_hunk=False)
        self.assertEqual(len(out.comments), 2)
        self.assertEqual({c.hunk_index for c in out.comments}, {1, 2})
        self.assertTrue(any(c.file_level for c in out.comments))

    def test_backend_failure_yields_auto_failed_comment(self) -> None:
        class Boom(gr.ReviewBackend):
            def review_hunks(self, file: gr.FileDiff, hunks: Any) -> Dict[int, gr.HunkReview]:
                raise gr.GeminiError("boom")

        out = gr.review_files([self.foo], Boom(), 12000, per_hunk=True)
        self.assertEqual(len(out.comments), 2)
        self.assertTrue(all(c.auto_failed for c in out.comments))
        self.assertEqual(len(out.failures), 2)

    def test_fake_backend_flags_secret(self) -> None:
        out = gr.review_files([self.foo], gr.FakeBackend(), 12000, per_hunk=False)
        secret = [c for c in out.comments if c.severity == "critical"]
        self.assertEqual(len(secret), 1)
        self.assertEqual(secret[0].line, 4)

    def test_extract_json_with_fence(self) -> None:
        self.assertEqual(gr.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(gr.extract_json('prefix {"a": [1]} suffix'), {"a": [1]})
        with self.assertRaises(ValueError):
            gr.extract_json("no json here")

    def test_parse_repo_from_change_url(self) -> None:
        self.assertEqual(gr.parse_repo_from_change_url("https://github.com/acme/widgets/pull/42"), ("acme/widgets", 42))
        self.assertEqual(gr.parse_repo_from_change_url(""), (None, None))

    def test_inline_overflow_cut_by_severity(self) -> None:
        comments = [gr.Comment(path="a", line=i, severity=sev, category="bug", body="b", hunk_index=1)
                    for i, sev in enumerate(["info"] * 85 + ["critical"] * 5)]
        ordered = sorted(comments, key=gr.comment_sort_key)
        inline, overflow = ordered[:gr.MAX_INLINE_COMMENTS], ordered[gr.MAX_INLINE_COMMENTS:]
        self.assertEqual(len(inline), 80)
        self.assertEqual(sum(1 for c in inline if c.severity == "critical"), 5)
        self.assertEqual(len(overflow), 10)


# --------------------------------------------------------------------------------------
# 로컬 HTTP 서버로 Gemini/GitHub 호출 경로 검증
# --------------------------------------------------------------------------------------
class _Recorder:
    def __init__(self) -> None:
        self.requests: List[Tuple[str, str, Dict[str, Any], Dict[str, str]]] = []
        self.script: List[Tuple[int, Any]] = []  # (status, body) 순서대로 소비


def _make_server(rec: _Recorder) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            body = json.loads(raw) if raw else {}
            rec.requests.append((self.command, self.path, body, {k.lower(): v for k, v in self.headers.items()}))
            status, resp = rec.script.pop(0) if rec.script else (200, {})
            payload = json.dumps(resp).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _handle  # type: ignore[assignment]

        def log_message(self, *a: Any) -> None:  # 조용히
            pass

    return HTTPServer(("127.0.0.1", 0), Handler)


def _gemini_ok(text: str) -> Dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


class LocalServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = _Recorder()
        self.server = _make_server(self.rec)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_gemini_retry_on_429_then_success(self) -> None:
        self.rec.script = [(429, {"error": "rate"}), (503, {"error": "busy"}), (200, _gemini_ok('{"hunks": []}'))]
        sleeps: List[float] = []
        client = gr.GeminiClient("k-test", model="m", api_url=self.base_url, sleep=sleeps.append, backoff=1.0)
        self.assertEqual(client.generate_json("p", "s"), {"hunks": []})
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(len(self.rec.requests), 3)
        method, path, body, headers = self.rec.requests[0]
        self.assertEqual((method, path), ("POST", "/models/m:generateContent"))
        self.assertEqual(headers.get("x-goog-api-key"), "k-test")
        self.assertNotIn("k-test", path)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "s")

    def test_gemini_non_retryable_error(self) -> None:
        self.rec.script = [(400, {"error": "bad"})]
        client = gr.GeminiClient("k", api_url=self.base_url, sleep=lambda s: None)
        with self.assertRaises(gr.GeminiError):
            client.generate_json("p")
        self.assertEqual(len(self.rec.requests), 1)

    def test_gemini_json_parse_retry_once(self) -> None:
        self.rec.script = [(200, _gemini_ok("not json")), (200, _gemini_ok('{"ok": true}'))]
        client = gr.GeminiClient("k", api_url=self.base_url, sleep=lambda s: None)
        self.assertEqual(client.generate_json("p"), {"ok": True})
        self.assertEqual(len(self.rec.requests), 2)
        self.assertIn("유효한 JSON", self.rec.requests[1][2]["contents"][0]["parts"][0]["text"])
        self.rec.script = [(200, _gemini_ok("x")), (200, _gemini_ok("y"))]
        with self.assertRaises(gr.GeminiParseError):
            client.generate_json("p")

    def test_fallback_on_404_switches_model(self) -> None:
        """404(모델 없음/사용 불가) -> 다음 후보 모델로 전환하고 이후 호출도 그 모델을 쓴다."""
        self.rec.script = [(404, {"error": {"message": "no longer available"}}), (200, _gemini_ok('{"ok": 1}'))]
        client = gr.GeminiClient("k", model="m-old", api_url=self.base_url, sleep=lambda s: None, fallbacks=["m-new", "m-3"])
        self.assertEqual(client.generate_json("p"), {"ok": 1})
        self.assertEqual(client.model, "m-new")
        self.assertEqual(client.requested_model, "m-old")
        self.assertEqual([r[1] for r in self.rec.requests], ["/models/m-old:generateContent", "/models/m-new:generateContent"])
        self.assertEqual(client.model_switches[0]["from"], "m-old")
        self.assertEqual(client.model_switches[0]["reason"], "404 model unavailable")
        self.rec.script = [(200, _gemini_ok('{"ok": 2}'))]
        client.generate_json("p2")
        self.assertEqual(self.rec.requests[-1][1], "/models/m-new:generateContent")
        self.assertEqual(client.fallbacks, ["m-3"])

    def test_fallback_on_persistent_429(self) -> None:
        """429 가 재시도(3회) 후에도 지속되면 다음 모델로 전환한다."""
        self.rec.script = [(429, {}), (429, {}), (429, {}), (200, _gemini_ok('{"ok": 1}'))]
        sleeps: List[float] = []
        client = gr.GeminiClient("k", model="m-pro", api_url=self.base_url, sleep=sleeps.append, backoff=1.0, retries=3, fallbacks=["m-flash"])
        self.assertEqual(client.generate_json("p"), {"ok": 1})
        self.assertEqual(client.model, "m-flash")
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(client.model_switches[0]["reason"], "429 quota exceeded (after retries)")
        backend = gr.GeminiBackend(client)
        self.assertEqual(backend.label, "Gemini m-flash")

    def test_all_models_fail_yields_placeholder_comments(self) -> None:
        """모든 후보가 실패하면 hunk 마다 '자동 리뷰 실패' placeholder 코멘트가 남는다."""
        self.rec.script = [(404, {}), (404, {})]
        client = gr.GeminiClient("k", model="m1", api_url=self.base_url, sleep=lambda s: None, fallbacks=["m2"])
        backend = gr.GeminiBackend(client)
        foo = next(f for f in gr.parse_unified_diff(SAMPLE_DIFF) if f.path.endswith("Foo.kt"))
        out = gr.review_files([foo], backend, 12000, per_hunk=False)
        self.assertEqual(len(out.comments), 2)
        self.assertTrue(all(c.auto_failed for c in out.comments))
        self.assertEqual(len(out.failures), 1)
        self.assertEqual(client.model, "m2")
        self.assertEqual(client.fallbacks, [])
        self.assertEqual(len(self.rec.requests), 2)

    def test_empty_text_response_triggers_single_retry(self) -> None:
        """parts 에 text 가 없으면(사고 토큰만) 파싱 실패와 같이 1회 재요청, 또 비면 GeminiParseError."""
        empty = {"candidates": [{"content": {"parts": [{"thought": True, "text": "..."}]}, "finishReason": "MAX_TOKENS"}]}
        self.rec.script = [(200, empty), (200, _gemini_ok('{"ok": true}'))]
        client = gr.GeminiClient("k", api_url=self.base_url, sleep=lambda s: None)
        self.assertEqual(client.generate_json("p"), {"ok": True})
        self.assertEqual(len(self.rec.requests), 2)
        self.assertEqual(self.rec.requests[0][2]["generationConfig"]["maxOutputTokens"], gr.DEFAULT_MAX_OUTPUT_TOKENS_HUNK)
        self.assertIn("유효한 JSON", self.rec.requests[1][2]["contents"][0]["parts"][0]["text"])
        self.rec.script = [(200, empty), (200, empty)]
        with self.assertRaises(gr.GeminiParseError):
            client.generate_json("p")

    def test_summary_uses_larger_token_budget(self) -> None:
        self.rec.script = [(200, _gemini_ok('{"overview": "o", "risk": "low", "recommendation": "approve"}'))]
        client = gr.GeminiClient("k", api_url=self.base_url, sleep=lambda s: None)
        backend = gr.GeminiBackend(client, max_output_tokens_hunk=100, max_output_tokens_summary=100)
        self.assertEqual((backend.max_output_tokens_hunk, backend.max_output_tokens_summary), (2048, 4096))
        ctx = gr.SummaryContext(pr_number=1, pr_title="t", base="b", head="h", files=[], hunk_summaries=[], comments=[],
                                ci_summary="", lock_files=[], skipped_files=[], unreviewed_files=[])
        self.assertEqual(backend.summarize(ctx).recommendation, "approve")
        self.assertEqual(self.rec.requests[-1][2]["generationConfig"]["maxOutputTokens"], 4096)

    def test_github_review_then_fallback(self) -> None:
        gh = gr.GitHubClient("tok", "acme/widgets", api_url=self.base_url)
        c = gr.Comment(path="a.kt", line=3, severity="major", category="bug", body="fix", hunk_index=1)
        # 1) 정상 review 등록
        self.rec.script = [(200, {"html_url": "https://x/review/1", "id": 1})]
        res = gr.post_to_github(gh, 7, "abc123", "summary", [c], "Gemini m")
        self.assertEqual(res["mode"], "review")
        method, path, body, headers = self.rec.requests[-1]
        self.assertEqual((method, path), ("POST", "/repos/acme/widgets/pulls/7/reviews"))
        self.assertEqual(body["commit_id"], "abc123")
        self.assertEqual(body["event"], "COMMENT")
        self.assertEqual(body["comments"][0]["side"], "RIGHT")
        self.assertEqual(body["comments"][0]["line"], 3)
        self.assertEqual(headers.get("authorization"), "Bearer tok")
        # 2) 422 -> issue comment fallback
        self.rec.script = [(422, {"message": "line must be part of the diff"}), (201, {"html_url": "https://x/issue/9", "id": 9})]
        res = gr.post_to_github(gh, 7, "abc123", "summary", [c], "Gemini m")
        self.assertEqual(res["mode"], "issue_comment")
        self.assertEqual(self.rec.requests[-1][1], "/repos/acme/widgets/issues/7/comments")
        self.assertIn("a.kt:3", self.rec.requests[-1][2]["body"])

    def test_main_posts_review_using_api_head_sha(self) -> None:
        """--diff-file + 로컬 GitHub 서버로 main() 의 게시 경로를 검증한다."""
        with tempfile.TemporaryDirectory() as tmp:
            diff_path = os.path.join(tmp, "x.diff")
            with open(diff_path, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE_DIFF)
            out_path = os.path.join(tmp, "review-result.json")
            self.rec.script = [(200, {"head": {"sha": "feedbeef"}, "title": "T"}), (200, {"html_url": "https://x/review/5", "id": 5})]
            env_backup = dict(os.environ)
            os.environ.update({"GEMINI_FAKE": "1", "GITHUB_TOKEN": "tok", "GITHUB_API_URL": self.base_url})
            try:
                rc = gr.main(["--base", "b", "--head", "h", "--pr", "3", "--repo", "acme/w", "--diff-file", diff_path, "--output", out_path])
            finally:
                os.environ.clear()
                os.environ.update(env_backup)
            self.assertEqual(rc, 0)
            review_req = self.rec.requests[-1]
            self.assertEqual(review_req[1], "/repos/acme/w/pulls/3/reviews")
            self.assertEqual(review_req[2]["commit_id"], "feedbeef")
            with open(out_path, encoding="utf-8") as fh:
                result = json.load(fh)
            self.assertEqual(result["posted"]["mode"], "review")
            self.assertEqual(result["head_sha_posted"], "feedbeef")
            self.assertEqual(result["lock_files"], ["app/frontend/package-lock.json"])


# --------------------------------------------------------------------------------------
# 임시 git 저장소 + GEMINI_FAKE=1 --dry-run end-to-end
# --------------------------------------------------------------------------------------
def _git(cwd: str, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"] + list(args),
        cwd=cwd, stderr=subprocess.STDOUT).decode("utf-8", "replace")


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class FakeEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gr-e2e-")
        repo = self.tmp
        _git(repo, "init", "-q", "-b", "main")
        _write(os.path.join(repo, "app/backend/src/main/kotlin/Todo.kt"),
               "package demo\n\nclass TodoService {\n    fun list() = listOf(1, 2)\n}\n")
        _write(os.path.join(repo, "app/frontend/src/App.tsx"), "export const App = () => <div>hi</div>;\n")
        _write(os.path.join(repo, "app/frontend/package-lock.json"), '{"version": "1"}\n')
        _write(os.path.join(repo, "docs/old.md"), "# old\nline\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        self.base = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "checkout", "-q", "-b", "feature/x")
        _write(os.path.join(repo, "app/backend/src/main/kotlin/Todo.kt"),
               "package demo\n\nclass TodoService {\n    val password = \"hunter2\"\n    fun list() = listOf(1, 2, 3)\n    fun find(id: String) = \"SELECT * FROM todo WHERE id=\" + id\n}\n")
        _write(os.path.join(repo, "app/frontend/src/App.tsx"),
               "export const App = () => {\n  console.log('x');\n  return <div>hi</div>;\n};\n")
        _write(os.path.join(repo, "app/frontend/package-lock.json"), '{"version": "2"}\n')
        _write(os.path.join(repo, "app/frontend/src/new.ts"), "export const y = 2;\n")
        os.remove(os.path.join(repo, "docs/old.md"))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feature")
        self.head = _git(repo, "rev-parse", "HEAD").strip()
        _write(os.path.join(repo, "ci-summary.txt"), "backend tests: 3 total, 0 failed\ntrivy: HIGH 0 CRITICAL 0\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_generates_markdown_and_result(self) -> None:
        env = dict(os.environ, GEMINI_FAKE="1", GEMINI_API_KEY="", GITHUB_TOKEN="")
        env.pop("CHANGE_URL", None)
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "gemini_review.py"), "--base", self.base, "--head", self.head,
             "--dry-run", "--summary-file", "ci-summary.txt", "--pr", "1", "--repo", "acme/demo", "--pr-title", "feat: x"],
            cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        stdout = proc.stdout.decode("utf-8", "replace")
        self.assertIn("AI Code Review", stdout)
        self.assertIn("lock 파일 변경 있음", stdout)
        self.assertIn("package-lock.json", stdout)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "review-dryrun.md")))
        with open(os.path.join(self.tmp, "review-result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        self.assertEqual(result["posted"]["mode"], "dry-run")
        self.assertEqual(result["lock_files"], ["app/frontend/package-lock.json"])
        reviewed = {f["path"] for f in result["files"]}
        self.assertEqual(reviewed, {"app/backend/src/main/kotlin/Todo.kt", "app/frontend/src/App.tsx", "app/frontend/src/new.ts", "docs/old.md"})
        # hunk 마다 최소 1개 코멘트
        hunks = {(f["path"], i) for f in result["files"] for i in range(1, f["hunks"] + 1)}
        commented = {(c["path"], c["hunk_index"]) for c in result["comments"]}
        self.assertEqual(hunks, commented)
        # 삭제된 파일은 파일 수준 코멘트
        self.assertTrue(any(c["file_level"] and c["path"] == "docs/old.md" for c in result["comments"]))
        # fake 백엔드가 하드코딩 비밀/SQL 결합/console.log 를 잡아야 함
        sev = result["stats"]["by_severity"]
        self.assertGreaterEqual(sev.get("critical", 0), 1)
        self.assertGreaterEqual(sev.get("major", 0), 1)
        self.assertGreaterEqual(sev.get("minor", 0), 1)
        self.assertEqual(result["summary"]["recommendation"], "request-changes")
        self.assertIn("CI 요약 원문", result["summary_markdown"])

    def test_missing_api_key_exit_code_3(self) -> None:
        env = dict(os.environ, GEMINI_API_KEY="", GITHUB_TOKEN="x")
        env.pop("GEMINI_FAKE", None)
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "gemini_review.py"), "--base", self.base, "--head", self.head, "--pr", "1", "--repo", "a/b"],
            cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("GEMINI_API_KEY", proc.stderr.decode("utf-8", "replace"))

    def test_fake_mode_runs_without_api_key(self) -> None:
        """GEMINI_FAKE=1 이면 GEMINI_API_KEY 가 비어 있어도 exit 3 없이 dry-run 이 완료되어야 한다 (로컬 시뮬레이션 잡)."""
        env = dict(os.environ, GEMINI_FAKE="1", GEMINI_API_KEY="", GITHUB_TOKEN="")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "gemini_review.py"), "--base", self.base, "--head", self.head,
             "--pr", "1", "--repo", "changeme/cicd-ai-flow", "--dry-run", "--output", "fake-nokey.json"],
            cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertNotIn("GEMINI_API_KEY 가 비어", proc.stderr.decode("utf-8", "replace"))
        with open(os.path.join(self.tmp, "fake-nokey.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        self.assertEqual(result["backend"], "fake-gemini")
        self.assertEqual(result["posted"]["mode"], "dry-run")
        self.assertGreater(result["stats"]["comments_total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
