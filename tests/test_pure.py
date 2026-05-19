"""Tests for the pure functions in pipeline.py and source_router.py.

Run from the project root:
    python -m pytest tests/
    # or, without pytest:
    python -m unittest discover tests/

These tests intentionally do NOT exercise the LLM pipeline, Whisper, or any
network I/O — those need integration tests with credentials. The pure helpers
are where logic bugs hide silently, so they're tested first.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

# Make the project importable without installing it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Stub anthropic so pipeline.py imports without the dep installed.
if "anthropic" not in sys.modules:
    _anthropic = types.ModuleType("anthropic")
    _anthropic.Anthropic = type("Anthropic", (), {})
    _anthropic.APIError = type("APIError", (Exception,), {})
    _anthropic.APIStatusError = type("APIStatusError", (Exception,), {})
    _anthropic.APITimeoutError = type("APITimeoutError", (Exception,), {})
    sys.modules["anthropic"] = _anthropic

import pipeline  # noqa: E402
import source_router  # noqa: E402


class ExtractJsonPayload(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(pipeline._extract_json_payload('{"x":1}'), '{"x":1}')

    def test_fenced(self):
        wrapped = "```json\n{\"x\":1}\n```"
        out = pipeline._extract_json_payload(wrapped)
        self.assertEqual(json.loads(out), {"x": 1})

    def test_prose_wrapped(self):
        wrapped = 'Here you go: {"x":1} hope this helps'
        self.assertTrue(pipeline._extract_json_payload(wrapped).strip().startswith("{"))
        self.assertEqual(json.loads(pipeline._extract_json_payload(wrapped)), {"x": 1})

    def test_array(self):
        self.assertEqual(json.loads(pipeline._extract_json_payload('[1,2,3]')), [1, 2, 3])


class ParseSrt(unittest.TestCase):
    def test_basic(self):
        srt = (
            "1\n00:00:00,000 --> 00:00:02,500\nHello world\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\nSecond line\n"
        )
        segs = pipeline._parse_srt(srt)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["text"], "Hello world")
        self.assertAlmostEqual(segs[0]["start"], 0.0)
        self.assertAlmostEqual(segs[0]["end"], 2.5)

    def test_strips_html_tags(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\n<c>foo</c> <i>bar</i>\n"
        segs = pipeline._parse_srt(srt)
        self.assertEqual(segs[0]["text"], "foo bar")

    def test_drops_empty_after_strip(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\n<c></c>\n"
        segs = pipeline._parse_srt(srt)
        self.assertEqual(segs, [])

    def test_skips_malformed_blocks(self):
        srt = "1\nnot-a-timestamp\nfoo\n"
        self.assertEqual(pipeline._parse_srt(srt), [])


class WordDiffRatio(unittest.TestCase):
    def test_identical(self):
        s = "the quick brown fox"
        self.assertEqual(pipeline._word_diff_ratio(s, s), 0.0)

    def test_disjoint(self):
        a = "alpha beta gamma delta"
        b = "one two three four"
        r = pipeline._word_diff_ratio(a, b)
        self.assertGreater(r, 0.9)

    def test_empty_first(self):
        self.assertEqual(pipeline._word_diff_ratio("", "anything"), 1.0)


class FmtTs(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(pipeline._fmt_ts(0.0), "00:00:00")

    def test_hour_minute_second(self):
        self.assertEqual(pipeline._fmt_ts(3661.5), "01:01:01")

    def test_srt(self):
        self.assertEqual(pipeline._fmt_srt_ts(0), "00:00:00,000")
        self.assertEqual(pipeline._fmt_srt_ts(3661.25), "01:01:01,250")


class SanitizeVideoId(unittest.TestCase):
    def test_normal_id_passthrough(self):
        self.assertEqual(source_router._sanitize_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_strips_path_traversal(self):
        out = source_router._sanitize_video_id("../etc/passwd")
        self.assertNotIn("/", out)
        self.assertFalse(out.startswith("."))

    def test_dotdot_falls_back_to_hash(self):
        out = source_router._sanitize_video_id("..")
        # After lstrip("."), an empty string falls back to _hash_id.
        self.assertEqual(len(out), 12)
        self.assertTrue(out.isalnum())

    def test_empty_falls_back_to_hash(self):
        out = source_router._sanitize_video_id("")
        self.assertEqual(len(out), 12)

    def test_truncates_long(self):
        out = source_router._sanitize_video_id("a" * 200)
        self.assertEqual(len(out), 80)


class ShouldRun(unittest.TestCase):
    def test_no_resume_always_runs(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "01_acquire.json").write_text('{"ok":true}')
            self.assertTrue(pipeline._should_run(s, "01_acquire", 1, 0, no_resume=True))

    def test_skip_when_checkpoint_valid(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "01_acquire.json").write_text('{"ok":true}')
            self.assertFalse(pipeline._should_run(s, "01_acquire", 1, 0, no_resume=False))

    def test_rerun_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            self.assertTrue(pipeline._should_run(s, "01_acquire", 1, 0, no_resume=False))

    def test_rerun_when_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "01_acquire.json").write_text("{not json")
            self.assertTrue(pipeline._should_run(s, "01_acquire", 1, 0, no_resume=False))

    def test_from_step_forces_rerun(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "01_acquire.json").write_text('{"ok":true}')
            (Path(td) / "02_chunk.json").write_text('{"ok":true}')
            self.assertTrue(pipeline._should_run(s, "01_acquire", 1, 1, no_resume=False))
            self.assertTrue(pipeline._should_run(s, "02_chunk", 2, 1, no_resume=False))


class InvalidateDownstream(unittest.TestCase):
    def test_keeps_upstream_clears_downstream(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            # All steps "done"
            for name, _ in pipeline.STEP_ORDER:
                (Path(td) / f"{name}.json").write_text('{"ok":true}')
            (Path(td) / "08_verify_iter1.json").write_text('{"ok":true}')
            (Path(td) / "09_gap_iter2.json").write_text('{"ok":true}')

            pipeline._invalidate_downstream(s, from_step=5)

            # Steps before 5 still there.
            self.assertTrue((Path(td) / "04_ledger.json").exists())
            # Steps from 5 onwards gone.
            for missing in ("05_plan.json", "06_tensions.json", "07_compose.json", "10_edit_final.json"):
                self.assertFalse((Path(td) / missing).exists(), f"{missing} should be removed")
            # Edit-loop iter files gone too.
            self.assertFalse((Path(td) / "08_verify_iter1.json").exists())
            self.assertFalse((Path(td) / "09_gap_iter2.json").exists())


class FinalIterJson(unittest.TestCase):
    def test_picks_highest_iteration(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "08_verify_iter1.json").write_text('{"verdict":"FAIL"}')
            (Path(td) / "08_verify_iter2.json").write_text('{"verdict":"FAIL"}')
            (Path(td) / "08_verify_iter3.json").write_text('{"verdict":"PASS"}')
            out = pipeline._final_iter_json(s, "08_verify")
            self.assertEqual(out, {"verdict": "PASS"})

    def test_returns_empty_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            self.assertEqual(pipeline._final_iter_json(s, "08_verify"), {})


class StateHas(unittest.TestCase):
    def test_validate_rejects_empty(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "01_acquire.json").write_text("")
            self.assertTrue(s.has("01_acquire", validate=False))
            self.assertFalse(s.has("01_acquire", validate=True))

    def test_validate_rejects_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "01_acquire.json").write_text("{nope")
            self.assertFalse(s.has("01_acquire", validate=True))

    def test_accepts_md(self):
        with tempfile.TemporaryDirectory() as td:
            s = pipeline.State(video_id="x", state_dir=Path(td))
            (Path(td) / "07_compose.md").write_text("essay content")
            self.assertTrue(s.has("07_compose", validate=True))


class RenderTakeaways(unittest.TestCase):
    def test_ranks_by_centrality(self):
        ledger = {"ledger": [
            {"id": "C001", "text": "Low priority", "speaker": "guest", "ts": "00:01:00", "centrality": 0.1},
            {"id": "C002", "text": "High priority", "speaker": "host", "ts": "00:02:00", "centrality": 0.9},
        ]}
        out = pipeline._render_takeaways(ledger, top_n=2)
        i_high = out.index("High priority")
        i_low = out.index("Low priority")
        self.assertLess(i_high, i_low)

    def test_handles_missing_fields(self):
        # A claim with missing text/speaker/ts shouldn't crash the renderer.
        ledger = {"ledger": [{"id": "C001", "centrality": 0.5}]}
        out = pipeline._render_takeaways(ledger)
        self.assertIn("C001", out)


class HashId(unittest.TestCase):
    def test_stable(self):
        self.assertEqual(source_router._hash_id("foo"), source_router._hash_id("foo"))

    def test_length_12(self):
        self.assertEqual(len(source_router._hash_id("anything")), 12)


if __name__ == "__main__":
    unittest.main()
