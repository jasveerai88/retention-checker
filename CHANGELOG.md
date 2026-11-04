# Changelog

## v0.2.0 — 2026-11-03
Agent-ready release (no behaviour change to the existing human output or verdicts):
- `--json` flag: prints one JSON object to stdout capturing the same verdict and checks as the human report (tool, version, file/path, platform, duration, frame size, has_audio, captions sidecar used, verdict, a checks list of {name, status, message} for all five checks, warnings list, measured_checks count, and the refusal text in `note` when fewer than two checks could measure). Exit codes are unchanged in both modes (0 PASS, 1 WARN, 2 HOLD/ERROR).
- Error paths in `--json` mode emit `{"error": "<message>"}` as JSON with the same exit code (2) — missing file, corrupt file, missing ffmpeg/ffprobe, missing explicit captions path, and argparse usage errors — never plain text, never a traceback.
- `SKILL.md` added at repo root: an agent-facing brief (purpose, exact commands incl. `--json`, the full JSON schema, exit codes, honesty rules — a SKIP is not a PASS and the refusal HOLD means "could not be judged").
- README gained a "Use with AI agents" section pointing at `--json` + `SKILL.md`.
- 5 tests added (53 total, all passing): JSON error objects for a missing file and for missing ffmpeg (both exit 2), JSON PASS on the generated good example with all five checks, JSON HOLD on the bad example agreeing with the human-mode verdict, and the silent-video refusal staying a HOLD in JSON with its SKIP checks still visible as SKIP.

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
