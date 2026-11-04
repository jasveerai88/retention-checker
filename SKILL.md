---
name: retention-checker
description: Pre-publish retention and platform-spec QC for faceless / AI-narrated video — hook window, dead zones, caption pacing, loudness, platform spec; use on a finished video file before uploading it.
---

# retention-checker — agent skill

## Purpose

Runs the finishing-layer checks an editor does not: is anything
actually happening in the first 3 seconds, where are the dead zones,
are the captions readable, is it loud enough, and is it the right
shape/length for the platform. Every check reports PASS / WARN / HOLD /
SKIP **with the reason**. It runs locally (Python stdlib + ffmpeg) —
the unreleased video never leaves the machine.

## When to use / not use

Use when:
- A video file is finished and about to be uploaded; you need a
  pre-publish verdict and the specific moments that fail.
- You need hook timing, silence/dead-zone timestamps, caption CPS, or
  integrated LUFS as data.

Do not use when:
- You need transcription or a judgement of the words themselves —
  there is none. The hook is judged from the captions sidecar or from
  speech *timing* in the audio, never from word quality.
- ffmpeg/ffprobe are not installed — the tool then ERRORs (exit 2) by
  design; it will not guess.

## Commands

```bash
# human-readable report (defaults: youtube-shorts, sidecar auto-detected)
python3 src/retention_checker.py video.mp4

# machine-readable (agents): one JSON object on stdout
python3 src/retention_checker.py video.mp4 --json

# explicit captions, planned title, different platform
python3 src/retention_checker.py video.mp4 \
    --captions video.srt --title "The Planned Title" \
    --platform youtube-long --json
```

Arguments: positional video path; `--captions` (.srt/.vtt sidecar;
auto-detected beside the video when omitted — an explicitly passed
path that does not exist is an ERROR), `--title` (key words checked
against the first 8.5s of captions, WARN only), `--platform`
(`youtube-shorts` default · `youtube-long` · `tiktok`), `--json`.

## Output JSON schema

On a completed check, stdout is exactly one JSON object:

- `tool` — `"retention-checker"`
- `version` — tool version string
- `file` — the video's filename; `path` — the path as given
- `platform` — the platform the spec check judged against
- `duration_seconds` — measured duration (float)
- `width`, `height` — measured frame size
- `has_audio` — bool
- `captions` — the sidecar filename used, or null
- `verdict` — `"PASS"` · `"WARN"` · `"HOLD"` (worst measured check)
- `checks` — list of `{name, status, message}` for: Hook window,
  Dead zones, Pacing, Loudness, Platform spec. Status is `"PASS"` ·
  `"WARN"` · `"HOLD"` · `"SKIP"`, and `message` carries the reason and
  the measured numbers (timestamps, CPS, LUFS).
- `warnings` — the messages of all WARN checks
- `measured_checks` — how many of the 5 checks produced a real
  measurement (did not SKIP)
- `note` — null, or the refusal text when `measured_checks` < 2

On error, stdout is one JSON object `{"error": "<message>"}` with
exit code 2 — missing file, corrupt file, missing captions path, or
ffmpeg/ffprobe absent. Never plain text, never a traceback.

## Exit codes

- `0` — verdict PASS
- `1` — verdict WARN
- `2` — verdict HOLD, the refusal HOLD, or any ERROR

## Limits & honesty rules

- **A SKIP is not a PASS.** A check that could not measure reports
  SKIP with its reason, in human and JSON output alike. Never report
  a skipped check as passed, and never average it away: the verdict
  is the worst *measured* check, and SKIPs stay visible in `checks`.
- **The refusal rule:** if fewer than two checks could measure the
  file, the verdict is HOLD with `note` explaining the refusal —
  "refusing to pass on so little signal". Report that as "could not
  be judged", not as a content failure and never as a pass.
- Silence is measured at a −35 dB floor; very quiet speech under it
  reads as silence. Loudness is integrated LUFS only (no true-peak /
  LRA gate).
- Dead zones count silences *starting* in the first 80% of runtime;
  the last 20% (outro) is deliberately out of scope.
- The `--title` echo check is WARN at most — a title mismatch alone
  never HOLDs a video.
- An explicitly supplied captions file that parses to zero usable
  events makes Pacing WARN, not SKIP — an unusable sidecar the caller
  asked for must never hide behind a skip.
