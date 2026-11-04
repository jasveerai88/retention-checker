#!/usr/bin/env python3
"""Tests for retention-checker. Stdlib unittest only.

Run from the pack root:
    python3 -m unittest discover -s tests -v

Unit tests cover every check's decision logic in both directions without
touching ffmpeg. Integration tests generate real videos with the shipped
examples/make_examples.py (ffmpeg required — they SKIP, never fake-pass,
when ffmpeg is absent) and run the CLI end to end: good passes, bad
fails, and every error path is a clean ERROR with no traceback.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
SRC = PACK_ROOT / "src" / "retention_checker.py"
EXAMPLES = PACK_ROOT / "examples"
sys.path.insert(0, str(PACK_ROOT / "src"))

import retention_checker as rc  # noqa: E402

GOOD_SRT = """1
00:00:00,000 --> 00:00:03,000
Nobody tells you this first step

2
00:00:03,000 --> 00:00:05,000
It decides everything

3
00:00:06,000 --> 00:00:09,000
Here is the simple method

4
00:00:09,000 --> 00:00:12,000
And the part most people miss
"""

LATE_SRT = """1
00:00:05,000 --> 00:00:08,000
The video finally starts talking

2
00:00:08,000 --> 00:00:12,000
Far too late for the hook
"""

FAST_SRT = """1
00:00:00,000 --> 00:00:01,000
This caption races past before you can read it properly

2
00:00:02,000 --> 00:00:03,000
Another one flashing by far too quickly to read
"""


def run_cli(*args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(SRC), *args],
                          capture_output=True, text=True, env=env,
                          timeout=300)


class TestCaptionParsing(unittest.TestCase):
    def test_srt_events(self):
        events = rc.parse_captions_text(GOOD_SRT)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0][0], 0.0)
        self.assertIn("Nobody tells you", events[0][2])

    def test_vtt_events_and_tags_stripped(self):
        vtt = ("WEBVTT\n\ncue-1\n00:00:00.000 --> 00:00:02.000\n"
               "<b>Hello</b> there world\n")
        events = rc.parse_captions_text(vtt)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][2], "Hello there world")

    def test_cps_values(self):
        events = rc.parse_captions_text(GOOD_SRT)
        cps = rc.caption_cps(events)
        self.assertTrue(all(5 < c < 15 for c in cps))

    def test_garbage_parses_to_nothing(self):
        self.assertEqual(rc.parse_captions_text("no timings here"), [])


class TestPacingLogic(unittest.TestCase):
    def test_slow_passes(self):
        status, _ = rc.check_pacing(rc.parse_captions_text(GOOD_SRT),
                                    "good.srt")
        self.assertEqual(status, "PASS")

    def test_fast_holds(self):
        status, note = rc.check_pacing(rc.parse_captions_text(FAST_SRT),
                                       "fast.srt")
        self.assertEqual(status, "HOLD")
        self.assertIn("CPS", note)

    def test_no_sidecar_skips(self):
        status, _ = rc.check_pacing(None, None)
        self.assertEqual(status, "SKIP")

    def test_empty_sidecar_skips(self):
        status, _ = rc.check_pacing([], "empty.srt")
        self.assertEqual(status, "SKIP")

    def test_explicit_empty_captions_warns(self):
        """Mini-fix regression: an explicitly supplied --captions file that
        parses to zero usable events (garbage / empty / zero-duration only)
        must WARN — never hide behind a SKIP and let the video PASS clean."""
        status, note = rc.check_pacing([], "caps.srt", explicit=True)
        self.assertEqual(status, "WARN")
        self.assertIn("captions file supplied but contained no usable "
                      "caption events", note)

    def test_explicit_zero_duration_only_warns(self):
        events = rc.parse_captions_text(
            "1\n00:00:01,000 --> 00:00:01,000\nzero length caption\n")
        self.assertEqual(events, [])
        status, _ = rc.check_pacing(events, "caps.srt", explicit=True)
        self.assertEqual(status, "WARN")


class TestHookLogic(unittest.TestCase):
    def test_captions_early_beats_pass(self):
        status, _ = rc.hook_from_captions(
            rc.parse_captions_text(GOOD_SRT), 12.0)
        self.assertEqual(status, "PASS")

    def test_captions_late_start_holds(self):
        status, note = rc.hook_from_captions(
            rc.parse_captions_text(LATE_SRT), 12.0)
        self.assertEqual(status, "HOLD")
        self.assertIn("5.0s", note)

    def test_captions_no_second_beat_warns(self):
        events = [(0.0, 2.0, "Only one early caption")]
        status, note = rc.hook_from_captions(events, 30.0)
        self.assertEqual(status, "WARN")
        self.assertIn("second caption beat", note)

    def test_title_mismatch_warns_never_holds(self):
        status, note = rc.hook_from_captions(
            rc.parse_captions_text(GOOD_SRT), 12.0,
            title="Quantum Banana Harvest Festival")
        self.assertEqual(status, "WARN")
        self.assertIn("title", note)

    def test_title_match_stays_pass(self):
        status, _ = rc.hook_from_captions(
            rc.parse_captions_text(GOOD_SRT), 12.0,
            title="The First Step Nobody Tells You")
        self.assertEqual(status, "PASS")

    def test_speech_at_zero_passes(self):
        status, _ = rc.hook_from_speech([(0.0, 5.0), (6.0, 12.0)], 12.0)
        self.assertEqual(status, "PASS")

    def test_speech_late_holds(self):
        status, _ = rc.hook_from_speech([(5.0, 12.0)], 12.0)
        self.assertEqual(status, "HOLD")

    def test_speech_lateish_warns(self):
        status, _ = rc.hook_from_speech([(2.0, 12.0)], 12.0)
        self.assertEqual(status, "WARN")

    def test_fully_silent_holds(self):
        status, _ = rc.hook_from_speech([], 12.0)
        self.assertEqual(status, "HOLD")

    def test_empty_events_skip(self):
        status, _ = rc.hook_from_captions([], 12.0)
        self.assertEqual(status, "SKIP")


class TestDeadZoneLogic(unittest.TestCase):
    def test_zone_inside_window_counts(self):
        zones = rc.dead_zones([(9.0, 13.0)], 20.0)
        self.assertEqual(len(zones), 1)

    def test_zone_starting_after_80pct_excluded(self):
        zones = rc.dead_zones([(17.0, 20.0)], 20.0)
        self.assertEqual(zones, [])

    def test_short_silence_ignored(self):
        zones = rc.dead_zones([(5.0, 6.0)], 20.0)
        self.assertEqual(zones, [])

    def test_speech_segments_complement(self):
        segs = rc.speech_segments([(5.0, 6.0)], 12.0)
        self.assertEqual(segs, [(0.0, 5.0), (6.0, 12.0)])

    def test_trailing_silence_segment(self):
        segs = rc.speech_segments([(0.0, 5.0), (9.0, 13.0)], 20.0)
        self.assertEqual(segs, [(5.0, 9.0), (13.0, 20.0)])

    def test_hold_and_warn_verdicts(self):
        info = {"has_audio": True, "duration": 20.0}
        status, _ = rc.check_dead_zones(info, [(0.0, 5.0)])
        self.assertEqual(status, "HOLD")
        status, _ = rc.check_dead_zones(info, [(2.0, 5.0)])
        self.assertEqual(status, "WARN")
        status, _ = rc.check_dead_zones(info, [])
        self.assertEqual(status, "PASS")

    def test_no_audio_skips(self):
        info = {"has_audio": False, "duration": 20.0}
        status, _ = rc.check_dead_zones(info, None)
        self.assertEqual(status, "SKIP")


class TestSpecLogic(unittest.TestCase):
    def test_shorts_good(self):
        status, _ = rc.classify_spec(720, 1280, 12.0, "youtube-shorts")
        self.assertEqual(status, "PASS")

    def test_shorts_landscape_holds(self):
        status, _ = rc.classify_spec(1280, 720, 12.0, "youtube-shorts")
        self.assertEqual(status, "HOLD")

    def test_shorts_overlong_holds(self):
        status, _ = rc.classify_spec(720, 1280, 200.0, "youtube-shorts")
        self.assertEqual(status, "HOLD")

    def test_shorts_lowres_warns(self):
        status, _ = rc.classify_spec(540, 960, 12.0, "youtube-shorts")
        self.assertEqual(status, "WARN")

    def test_tiktok_vertical_passes(self):
        status, _ = rc.classify_spec(720, 1280, 60.0, "tiktok")
        self.assertEqual(status, "PASS")

    def test_long_good(self):
        status, _ = rc.classify_spec(1920, 1080, 600.0, "youtube-long")
        self.assertEqual(status, "PASS")

    def test_long_vertical_warns(self):
        status, note = rc.classify_spec(1080, 1920, 600.0, "youtube-long")
        self.assertEqual(status, "WARN")
        self.assertIn("vertical", note)

    def test_long_tiny_holds(self):
        status, _ = rc.classify_spec(640, 360, 600.0, "youtube-long")
        self.assertEqual(status, "HOLD")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg/ffprobe not on PATH — integration tests "
                     "skipped, never faked")
class TestCliIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rc-tests-")
        gen = subprocess.run(
            [sys.executable, str(EXAMPLES / "make_examples.py"), cls.tmp],
            capture_output=True, text=True, timeout=600)
        if gen.returncode != 0:
            raise unittest.SkipTest(
                f"example generation failed: {gen.stderr[-500:]}")
        cls.good = str(Path(cls.tmp) / "good.mp4")
        cls.bad = str(Path(cls.tmp) / "bad.mp4")
        # a landscape clip and a silent (no-audio) clip
        cls.landscape = str(Path(cls.tmp) / "landscape.mp4")
        cls.silent = str(Path(cls.tmp) / "silent.mp4")
        for out, size in ((cls.landscape, "640x360"),
                          (cls.silent, "720x1280")):
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i",
                   f"testsrc2=size={size}:rate=30:duration=4"]
            if out == cls.landscape:
                cmd += ["-f", "lavfi", "-i",
                        "sine=frequency=440:duration=4",
                        "-map", "0:v", "-map", "1:a", "-c:a", "aac",
                        "-shortest"]
            cmd += ["-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", out]
            subprocess.run(cmd, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_good_video_passes_exit0(self):
        r = run_cli(self.good)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Verdict: PASS", r.stdout)
        self.assertEqual(r.stdout.count("[PASS]"), 5)
        self.assertNotIn("Traceback", r.stderr)

    def test_bad_video_holds_exit2(self):
        r = run_cli(self.bad)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Verdict: HOLD", r.stdout)
        self.assertIn("[HOLD] Hook window", r.stdout)
        self.assertIn("[HOLD] Dead zones", r.stdout)
        self.assertIn("[HOLD] Pacing", r.stdout)
        self.assertIn("[PASS] Loudness", r.stdout)

    def test_no_captions_uses_audio_hook_and_skips_pacing(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "clip.mp4"
            shutil.copy(self.good, clip)
            r = run_cli(str(clip))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[PASS] Hook window: first speech", r.stdout)
        self.assertIn("[SKIP] Pacing", r.stdout)

    def test_unrelated_title_warns_exit1(self):
        r = run_cli(self.good, "--title",
                    "Quantum Banana Harvest Festival")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Verdict: WARN", r.stdout)

    def test_vertical_judged_as_long_warns_exit1(self):
        r = run_cli(self.good, "--platform", "youtube-long")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("[WARN] Platform spec", r.stdout)

    def test_landscape_on_shorts_holds(self):
        r = run_cli(self.landscape)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("[HOLD] Platform spec", r.stdout)

    def test_no_audio_file_refuses_to_pass(self):
        r = run_cli(self.silent)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("[SKIP] Hook window", r.stdout)
        self.assertIn("[SKIP] Loudness", r.stdout)
        self.assertIn("refusing to pass", r.stdout)

    def test_explicit_captions_flag(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "clip.mp4"
            shutil.copy(self.good, clip)
            caps = Path(d) / "caps.srt"
            caps.write_text(GOOD_SRT, encoding="utf-8")
            r = run_cli(str(clip), "--captions", str(caps))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[PASS] Pacing", r.stdout)

    def test_explicit_garbage_captions_warns_never_passes(self):
        """Mini-fix regression (CLI): a good video + an explicitly supplied
        garbage captions file used to SKIP pacing and return a clean PASS.
        It must now WARN overall (exit 1)."""
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "clip.mp4"
            shutil.copy(self.good, clip)
            caps = Path(d) / "caps.srt"
            caps.write_text("this is not a captions file at all\n",
                            encoding="utf-8")
            r = run_cli(str(clip), "--captions", str(caps))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("[WARN] Pacing: captions file supplied but "
                      "contained no usable caption events", r.stdout)
        self.assertIn("Verdict: WARN", r.stdout)
        self.assertNotIn("Verdict: PASS", r.stdout)


class TestCliErrors(unittest.TestCase):
    def test_missing_file(self):
        r = run_cli("/nonexistent/definitely-not-here.mp4")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERROR: file not found", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "corrupt.mp4"
            fake.write_text("this is not a video", encoding="utf-8")
            r = run_cli(str(fake))
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERROR:", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_missing_explicit_captions(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "x.mp4"
            fake.write_bytes(b"\x00" * 64)
            r = run_cli(str(fake), "--captions", "/nonexistent/no.srt")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERROR:", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_no_ffmpeg_on_path(self):
        r = run_cli(__file__, env_extra={"PATH": "/nonexistent-rc-dir"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERROR:", r.stdout)
        self.assertIn("ffmpeg", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)


class TestJsonErrors(unittest.TestCase):
    """v0.2.0 --json error paths: one JSON object, same exit code,
    never plain text and never a traceback."""

    def test_json_missing_file_error_object(self):
        r = run_cli("/nonexistent/definitely-not-here.mp4", "--json")
        self.assertEqual(r.returncode, 2)
        data = json.loads(r.stdout)  # must parse as JSON
        self.assertIn("file not found", data["error"])
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_json_no_ffmpeg_error_object(self):
        r = run_cli(__file__, "--json",
                    env_extra={"PATH": "/nonexistent-rc-dir"})
        self.assertEqual(r.returncode, 2)
        data = json.loads(r.stdout)
        self.assertIn("ffmpeg", data["error"])
        self.assertNotIn("Traceback", r.stdout + r.stderr)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg/ffprobe not on PATH — integration tests "
                     "skipped, never faked")
class TestJsonMode(unittest.TestCase):
    """v0.2.0 --json on the generated examples: parses, carries the
    verdict + every check, and agrees with the human-mode verdict."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rc-json-tests-")
        gen = subprocess.run(
            [sys.executable, str(EXAMPLES / "make_examples.py"), cls.tmp],
            capture_output=True, text=True, timeout=600)
        if gen.returncode != 0:
            raise unittest.SkipTest(
                f"example generation failed: {gen.stderr[-500:]}")
        cls.good = str(Path(cls.tmp) / "good.mp4")
        cls.bad = str(Path(cls.tmp) / "bad.mp4")
        cls.silent = str(Path(cls.tmp) / "silent.mp4")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             "testsrc2=size=720x1280:rate=30:duration=4",
             "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", cls.silent],
            capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_json_good_passes_with_all_checks(self):
        r = run_cli(self.good, "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["tool"], "retention-checker")
        self.assertEqual(data["version"], rc.VERSION)
        self.assertEqual(data["verdict"], "PASS")
        self.assertEqual(len(data["checks"]), 5)
        for c in data["checks"]:
            self.assertEqual(c["status"], "PASS", c)
            self.assertIn("name", c)
            self.assertIn("message", c)
        self.assertEqual(data["measured_checks"], 5)
        self.assertAlmostEqual(data["duration_seconds"], 12.0, delta=0.6)
        self.assertEqual(data["warnings"], [])
        self.assertIsNone(data["note"])

    def test_json_bad_holds_and_agrees_with_human(self):
        r = run_cli(self.bad, "--json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "HOLD")
        by_name = {c["name"]: c["status"] for c in data["checks"]}
        self.assertEqual(by_name["Hook window"], "HOLD")
        self.assertEqual(by_name["Dead zones"], "HOLD")
        self.assertEqual(by_name["Pacing"], "HOLD")
        human = run_cli(self.bad)
        self.assertEqual(human.returncode, 2)
        self.assertIn(f"Verdict: {data['verdict']}", human.stdout)

    def test_json_silent_video_refusal_stays_hold(self):
        r = run_cli(self.silent, "--json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "HOLD")
        self.assertIn("refusing to pass", data["note"])
        self.assertLess(data["measured_checks"], 2)
        statuses = {c["status"] for c in data["checks"]}
        self.assertIn("SKIP", statuses)  # a SKIP stays a SKIP in JSON


if __name__ == "__main__":
    unittest.main()
