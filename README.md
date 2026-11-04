# retention-checker

**A pre-publish retention check for faceless and AI-narrated video — run it before you upload, not after the comments tell you.**

Your editor tells you nothing about whether a finished video will hold
attention. Cloud "hook graders" want you to upload your unreleased video
and cap your free uses. `retention-checker` runs the finishing-layer
checks locally, on your machine, in seconds: is anything actually
happening in the first 3 seconds? Where are the dead zones? Are the
captions readable? Is it loud enough, and the right shape for the
platform?

- Pure Python 3 standard library + ffmpeg — no dependencies, no install step
- Local only — your unreleased video never leaves your machine
- Honest by design: every check prints PASS / WARN / HOLD / **SKIP with
  the reason** — a check that could not measure never pretends it passed
- Script-friendly exit codes: `0` = PASS, `1` = WARN, `2` = HOLD / ERROR

## The five checks

| # | Check | PASS | WARN | HOLD |
|---|---|---|---|---|
| 1 | Hook window | first caption/speech content ≤ 1.5s, second beat by 8.5s | content lands 1.5–3s, no second beat by 8.5s, or `--title` words missing from the hook captions | nothing lands in the first 3s |
| 2 | Dead zones | no silence > 2.5s starting in the first 80% | a silence of 2.5–4s | a silence > 4s |
| 3 | Pacing (caption speed) | median ≤ 17 CPS and max ≤ 30 | median > 17 CPS | median > 20 CPS or any caption > 30 CPS |
| 4 | Loudness | −16 to −12 LUFS | — | outside −14 LUFS ±2 |
| 5 | Platform spec | right orientation, duration and resolution for the platform | vertical in a long-form check, or below the resolution standard | wrong orientation, or over the platform duration limit |

Notes on the gates:

- **Hook** is judged from the captions sidecar when one exists (it knows
  what is *said*, not just that sound exists), otherwise from the audio
  speech/silence map. With `--title`, the title's key words are checked
  against the first 8.5s of captions — WARN only; a title is a promise,
  and a hook that never echoes it is a retention leak.
- **Pacing** uses the same caption-speed gates as
  [Faceless QC Lite](https://github.com/jasveerai88/faceless-qc-lite)
  and [caption-toolkit](https://github.com/jasveerai88/caption-toolkit).
- **Platform rules** (the tool's working rules): youtube-shorts =
  vertical, ≤ 180s; youtube-long = 1280x720 standard; tiktok = vertical,
  ≤ 600s standard in-app window.
- **The refusal rule:** if fewer than two checks could produce a real
  measurement (say, a silent video with no captions), the verdict is
  HOLD — *"refusing to pass on so little signal."* A silent file is a
  legitimate format, but this tool will not certify retention it
  cannot see.

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe` on PATH (used to probe, measure loudness and
  map silence). If they are missing, the tool prints a clean ERROR and
  exits 2 — it never guesses.

## Usage

```bash
# basic check (defaults: youtube-shorts, sidecar auto-detected)
python3 src/retention_checker.py video.mp4

# explicit captions, planned title, different platform
python3 src/retention_checker.py video.mp4 \
    --captions video.srt \
    --title "The First Step Nobody Tells You" \
    --platform youtube-shorts
```

`--platform` accepts `youtube-shorts` (default), `youtube-long`, `tiktok`.
`--captions` points at an .srt/.vtt sidecar; when omitted, a sidecar with
the video's name beside it is auto-detected. An explicitly passed
captions path that does not exist is an ERROR — a typo'd path is never
silently ignored.

## Use with AI agents

Driving this from a coding agent (Claude Code, Codex, …)? Add `--json`:
stdout becomes one JSON object with the verdict, all five checks as
`{name, status, message}` (SKIP stays SKIP — it is never converted to a
pass), the measured numbers, and the refusal note when a file could not
be judged. Errors in `--json` mode are JSON too — `{"error": "..."}`
with exit code 2. Full brief for agents, including the JSON schema and
exit codes: [SKILL.md](SKILL.md).

## Real runs (verbatim, v0.2.0)

The example videos are generated, not committed (they are tone-based
speech proxies — the checks judge timing, and a 440 Hz tone times exactly
like narration through every gate here):

```bash
python3 examples/make_examples.py   # writes examples/good.mp4 + bad.mp4
```

**The good one** — voice from 0s, a 1s pause at 5–6s, captions at ~10 CPS,
−14 LUFS:

```text
$ python3 src/retention_checker.py examples/good.mp4
retention-checker v0.2.0 — good.mp4
Platform: youtube-shorts · Duration: 12.0s · 720x1280
  [PASS] Hook window: first caption content at 0.0s
  [PASS] Dead zones: no silence > 2.5s starting in the first 80% of runtime
  [PASS] Pacing: caption speed median 10.1 CPS, max 10.7 (4 captions from good.srt)
  [PASS] Loudness: integrated loudness -14.0 LUFS (target -14 +/-2)
  [PASS] Platform spec: 720x1280 vertical, 12s — vertical, within the 180s youtube-shorts limit, short side >= 720p
Verdict: PASS
(exit code: 0)
```

**The bad one** — 5s of silence before anything happens, a 4s dead zone
at 9–13s, and an opening caption at 46 CPS. Its loudness is fine (−13.9
LUFS): loudness was never its problem.

```text
$ python3 src/retention_checker.py examples/bad.mp4
retention-checker v0.1.0 — bad.mp4
Platform: youtube-shorts · Duration: 20.0s · 720x1280
  [HOLD] Hook window: first caption content starts at 5.0s — nothing lands in the first 3s hook window
  [HOLD] Dead zones: 2 dead zone(s) > 2.5s in the first 80%: 0.0s-5.0s (5.0s), 9.0s-13.0s (4.0s) (HOLD > 4.0s)
  [HOLD] Pacing: caption speed median 6.7 CPS, max 46.0 (4 captions from bad.srt) (HOLD: median > 20 or max > 30)
  [PASS] Loudness: integrated loudness -13.9 LUFS (target -14 +/-2)
  [PASS] Platform spec: 720x1280 vertical, 20s — vertical, within the 180s youtube-shorts limit, short side >= 720p
Verdict: HOLD
(exit code: 2)
```

**Refusing to fake it** — a video with no audio and no captions gives the
spec check and nothing else measurable, so:

```text
  [SKIP] Hook window: no captions sidecar and no audio stream — hook not judged, not passed
  [SKIP] Dead zones: no audio stream — dead zones not measurable
  [SKIP] Pacing: no captions sidecar (.srt/.vtt) — pacing not measured, not passed
  [SKIP] Loudness: no audio stream — loudness not measurable
  [PASS] Platform spec: 720x1280 vertical, 4s — vertical, within the 180s youtube-shorts limit, short side >= 720p
Verdict: HOLD (only 1 of 5 checks could measure this file — refusing to pass on so little signal)
(exit code: 2)
```

## Known limits — stated plainly

- **No transcription.** The hook is judged from your captions sidecar or
  from speech *timing* in the audio. The tool cannot judge whether the
  hook's words are good — only that something lands, when it lands, and
  whether the title's words show up.
- **Silence is measured at a −35 dB floor** (ffmpeg `silencedetect`, the
  same floor Faceless QC Lite uses). Very quiet speech under that floor
  reads as silence.
- **Loudness is integrated LUFS only** (ebur128) — no true-peak or
  dynamics/LRA gate in v0.1.0.
- **Dead-zone scope** is silences *starting* in the first 80% of
  runtime; an outro fade in the last 20% is deliberately not a defect.
- **The title check needs captions** and is a WARN at most — never a HOLD.
- Caption events with zero/negative duration are ignored by the parser
  (they carry no reading time), so a sidecar made only of those reads as
  "no caption events" and pacing SKIPs rather than passing.

## Privacy

- Reads only the video file and captions sidecar you point it at.
- Stores nothing; writes nothing except what you redirect yourself.
- Sends nothing anywhere — no network calls, no telemetry, no accounts.
- Works fully offline; your unreleased video stays on your machine.
- The only external programs it runs are your local ffmpeg/ffprobe.

## Who runs it

retention-checker is the pre-upload gate for three faceless channels run
by its author (140+ Shorts published) — every upload passes these five
checks first, and the gates come from measured failures on those
channels, not from theory.

## Sponsor

GitHub Sponsors is LIVE: **[github.com/sponsors/jasveerai88](https://github.com/sponsors/jasveerai88)**
— sponsorships keep these tools free and fund the next ones.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

53 tests: every check's logic in both directions (unit), plus CLI
end-to-end runs on the generated example videos — good PASSes, bad
HOLDs, a title mismatch WARNs — and every error path (missing file,
corrupt file, missing captions path, no ffmpeg on PATH) asserted to be
a clean ERROR with no traceback. The `--json` mode is covered the same
way in both directions: JSON verdicts on the good/bad examples, the
refusal HOLD staying a HOLD in JSON, and JSON error objects on the
error paths. Integration tests SKIP, never fake-pass, when ffmpeg is
absent.

## Licence

MIT — see [LICENSE](LICENSE).
