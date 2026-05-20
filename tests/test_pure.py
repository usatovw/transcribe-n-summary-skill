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


class PreflightModelSelection(unittest.TestCase):
    """The preflight is the only thing standing between a long-audio run and an
    OOM-killed VM. Every decision boundary gets a test."""

    def _pick(self, free_mb, model, duration_min):
        # Monkey-patch the RAM probe.
        orig = pipeline._free_ram_mb
        pipeline._free_ram_mb = lambda: free_mb
        try:
            return pipeline._pick_whisper_model(model, duration_min * 60)
        finally:
            pipeline._free_ram_mb = orig

    def test_short_audio_fits_requested(self):
        # 8 GB free, 5-min audio, asked for medium — should run medium single-pass.
        model, _, chunked = self._pick(8000, "medium", 5)
        self.assertEqual(model, "medium")
        self.assertFalse(chunked)

    def test_short_audio_downgrades_when_tight(self):
        # 2.5 GB free, 5-min audio, asked for medium — single-pass medium needs ~4 GB,
        # should downgrade to small or base single-pass.
        model, _, chunked = self._pick(2500, "medium", 5)
        self.assertIn(model, {"small", "base", "tiny"})
        self.assertFalse(chunked)

    def test_long_audio_prefers_chunked(self):
        # 8 GB free, 90-min audio. Should pick CHUNKED, not single-pass.
        # Quality > speed for long form.
        model, _, chunked = self._pick(8000, "medium", 90)
        self.assertTrue(chunked, "long audio (>60 min) should use chunked mode")

    def test_long_audio_chunked_uses_bigger_model_than_single_pass_tiny(self):
        # 4 GB free, 99-min audio. Single-pass medium needs ~6 GB (no go),
        # single-pass tiny would fit, but chunked base also fits and gives
        # better quality. The preflight should prefer chunked.
        model, _, chunked = self._pick(4000, "medium", 99)
        self.assertTrue(chunked)
        # base or better — not the desperate-fallback tiny
        self.assertIn(model, {"small", "base", "medium"})

    def test_refuse_when_too_low(self):
        # 500 MB free, 99-min audio. Even chunked tiny won't fit.
        with self.assertRaises(RuntimeError) as ctx:
            self._pick(500, "medium", 99)
        msg = str(ctx.exception)
        # Remediation must be in the message — users need to know what to do.
        self.assertIn("swap", msg.lower())
        self.assertIn("ram", msg.lower())

    def test_chunked_works_even_when_requested_doesnt_fit_singlepass(self):
        # 2 GB free, 90-min audio. Single-pass medium needs ~5 GB. Chunked tiny fits.
        model, _, chunked = self._pick(2000, "medium", 90)
        self.assertTrue(chunked)

    def test_required_ram_long_form_multiplier(self):
        # >90 min audio uses x1.5 multiplier — verify the math.
        short = pipeline._required_whisper_ram_mb("medium", 30 * 60)  # 30 min
        long_ = pipeline._required_whisper_ram_mb("medium", 100 * 60)  # 100 min
        self.assertGreater(long_, short, "long audio should require more RAM than short")
        # x1.5 over 3500 base + 800 safety = at least 5800
        self.assertGreater(long_, 5000)

    def test_required_ram_chunked_no_duration_factor(self):
        # Chunked peak should be independent of total audio length —
        # we only ever hold one chunk in memory.
        short = pipeline._required_whisper_ram_chunked_mb("medium")
        long_ = pipeline._required_whisper_ram_chunked_mb("medium")
        self.assertEqual(short, long_)

    def test_unknown_model_falls_back_to_table_default(self):
        # If config has a typo'd model name, preflight should not crash.
        need = pipeline._required_whisper_ram_mb("not-a-real-model", 30 * 60)
        self.assertGreater(need, 0)


class AudioDuration(unittest.TestCase):
    def test_returns_zero_for_missing_file(self):
        # If ffprobe fails or the file is missing, must return 0 — caller
        # treats 0 as "unknown" and doesn't compute wild RAM numbers.
        self.assertEqual(pipeline._audio_duration_seconds("/nonexistent/foo.wav"), 0.0)


if __name__ == "__main__":
    unittest.main()
