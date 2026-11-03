# Changelog

## v0.1.0 — 2026-11-03

First release.

- Five pre-publish checks for faceless / AI-narrated video, each reported
  as PASS / WARN / HOLD / SKIP with the reason — a SKIP is never dressed
  up as a pass:
  1. **Hook window** — first caption (or speech) content must land within
     3s, with a second beat by 8.5s. Optional `--title` echo check
     (WARN only): the title's key words should appear in the first 8.5s
     of captions.
  2. **Dead zones** — silences > 2.5s starting in the first 80% of
     runtime, listed with timestamps (HOLD > 4s).
  3. **Pacing** — caption CPS from the .srt/.vtt sidecar (WARN median
     > 17, HOLD median > 20 or any caption > 30 — the Faceless QC gates).
  4. **Loudness** — integrated LUFS vs −14 ±2 via ffmpeg ebur128.
  5. **Platform spec** — resolution / orientation / duration for
     youtube-shorts (default), youtube-long, or tiktok.
- Honest-failure design: missing ffmpeg/ffprobe, missing files, corrupt
  files, and a captions path that does not exist all produce a clean
  ERROR (exit 2), never a traceback. If fewer than two checks could
  measure a file, the verdict is HOLD — the tool refuses to pass a
  video it could not actually judge.
- Exit codes: 0 PASS, 1 WARN, 2 HOLD / ERROR — script-friendly.
- Pre-release fix (delta re-audit, before first publish): an explicitly
  supplied `--captions` file that parses to zero usable events (garbage,
  empty, or only zero-duration captions) now produces a Pacing WARN —
  "captions file supplied but contained no usable caption events" — so
  the overall verdict is at least WARN, never a clean PASS. With no
  `--captions` at all, pacing still SKIPs as before.
- `examples/make_examples.py` generates the two demo videos the README
  runs on (a good one that passes all five checks; a bad one with a 5s
  silent opening, a 4s dead zone and a 46-CPS caption that HOLDs).
- 45 stdlib-unittest tests: every check in both directions, plus CLI
  end-to-end runs on the generated videos and every error path.
- Pure Python standard library + ffmpeg/ffprobe; local only; MIT.
