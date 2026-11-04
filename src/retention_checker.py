#!/usr/bin/env python3
"""retention-checker — pre-publish retention & platform-spec QC for
faceless / AI-narrated video. Local only, no uploads, no accounts.

Five checks, each reported as PASS / WARN / HOLD / SKIP (with the reason —
a SKIP is never dressed up as a pass):

  1. Hook window  — is anything actually said/shown in the first 3s, and is
                    there a second "promise" beat by ~8s? Judged from the
                    captions sidecar when one exists, otherwise from the
                    audio speech/silence map. With --title, the title's key
                    words are also checked against the first 8s of captions
                    (WARN only — a promise the hook never echoes).
  2. Dead zones   — silences > 2.5s starting in the first 80% of runtime,
                    listed with timestamps (WARN > 2.5s, HOLD > 4s).
  3. Pacing       — caption reading speed (chars/sec) from the sidecar:
                    WARN median > 17 CPS, HOLD median > 20 or any > 30 CPS
                    (the same gates Faceless QC Lite ships). SKIP if there
                    is no sidecar.
  4. Loudness     — integrated LUFS vs the -14 LUFS streaming target (+/-2),
                    measured with ffmpeg's ebur128. SKIP if there is no
                    audio stream or it cannot be measured.
  5. Platform spec — resolution / orientation / duration against the chosen
                    platform's rules (youtube-shorts, youtube-long, tiktok).

Needs: Python 3.10+ and ffmpeg + ffprobe on PATH. If ffmpeg/ffprobe are
missing the tool prints a clean ERROR and exits 2 — it never guesses a
result it could not measure.

Overall verdict = the worst measured check. If fewer than two checks could
produce a real measurement, the verdict is HOLD ("not enough measurable
signal") — this tool refuses to pass a video it could not actually judge.

Exit codes: 0 PASS, 1 WARN, 2 HOLD or ERROR.
"""
import argparse, json, re, shutil, statistics, subprocess, sys
from pathlib import Path

VERSION = "0.2.0"

HOOK_FIRST_WINDOW = 3.0      # seconds: something must happen by here
HOOK_LATE_START = 1.5        # seconds: speech starting after this is "late"
HOOK_PROMISE_BY = 8.5        # seconds: a second beat must land by here
DEAD_ZONE_WARN = 2.5         # seconds of silence
DEAD_ZONE_HOLD = 4.0         # seconds of silence
SILENCE_FLOOR_DB = "-35dB"   # same floor Faceless QC Lite uses
CPS_WARN_MEDIAN = 17.0
CPS_HOLD_MEDIAN = 20.0
CPS_HOLD_MAX = 30.0
LUFS_TARGET = -14.0
LUFS_TOLERANCE = 2.0
FFMPEG_TIMEOUT = 600         # seconds per ffmpeg/ffprobe call

SEVERITY = {"PASS": 0, "WARN": 1, "HOLD": 2}
PLATFORMS = ("youtube-shorts", "youtube-long", "tiktok")


class ToolError(Exception):
    """A clean, user-facing failure (printed as ERROR, exit 2)."""


# --------------------------------------------------------------------------
# subprocess helpers — list argv only, never a shell, always time-bounded
# --------------------------------------------------------------------------

def _run(cmd, timeout=FFMPEG_TIMEOUT):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_ffmpeg(args):
    return _run(["ffmpeg", "-hide_banner", "-nostats", *args])


def probe_video(path):
    """Return dict(duration, width, height, has_audio) or raise ToolError."""
    r = _run(["ffprobe", "-v", "error", "-show_entries",
              "stream=codec_type,width,height",
              "-show_entries", "format=duration",
              "-of", "json", str(path)], timeout=60)
    if r is None or r.returncode != 0:
        raise ToolError(f"ffprobe could not read {path.name} — "
                        "the file may be corrupt or not a video")
    try:
        data = json.loads(r.stdout)
    except (ValueError, TypeError):
        raise ToolError(f"ffprobe returned unreadable data for {path.name} — "
                        "the file may be corrupt or not a video")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ToolError(f"no video stream found in {path.name} — "
                        "not a video file this tool can check")
    try:
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, ValueError, TypeError):
        raise ToolError(f"could not read frame size from {path.name}")
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (ValueError, TypeError):
        duration = None
    if not duration or duration <= 0:
        raise ToolError(f"could not read a positive duration from "
                        f"{path.name} — the file may be corrupt")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {"duration": duration, "width": width, "height": height,
            "has_audio": has_audio}


# --------------------------------------------------------------------------
# captions — one parser feeding both the hook and pacing checks
# --------------------------------------------------------------------------

def _ts_to_sec(ts):
    ts = ts.replace(",", ".")
    parts = [float(x) for x in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


_TIMING_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})")


def parse_captions_text(text):
    """Parse SRT/VTT text into [(start, end, body)] sorted by start."""
    events = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        m = _TIMING_RE.search(block)
        if not m:
            continue
        start, end = _ts_to_sec(m.group(1)), _ts_to_sec(m.group(2))
        body_lines = []
        for line in block[m.end():].splitlines():
            line = line.strip()
            if not line or "-->" in line or line == "WEBVTT":
                continue
            body_lines.append(re.sub(r"<[^>]+>", "", line))
        body = " ".join(body_lines).strip()
        if body and end > start:
            events.append((start, end, body))
    events.sort(key=lambda e: e[0])
    return events


def caption_cps(events):
    """Chars-per-second per caption event (spaces included, tags stripped)."""
    return [len(body) / (end - start) for start, end, body in events
            if end > start and body]


def resolve_captions(video, captions_arg):
    """Return (Path|None, error|None).

    An explicitly passed --captions path that does not exist is a user
    error (reported, never silently ignored). Without --captions, a
    sidecar beside the video is auto-detected; absence is normal.
    """
    if captions_arg:
        cap = Path(captions_arg)
        if not cap.is_file():
            return None, f"captions file not found: {captions_arg}"
        return cap, None
    for ext in (".srt", ".vtt"):
        side = video.with_suffix(ext)
        if side.is_file():
            return side, None
    return None, None


def load_caption_events(video, captions_arg):
    """Return (events|None, cap_name|None, error|None)."""
    cap, err = resolve_captions(video, captions_arg)
    if err:
        return None, None, err
    if cap is None:
        return None, None, None
    try:
        text = cap.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None, f"could not read captions file: {cap}"
    return parse_captions_text(text), cap.name, None


# --------------------------------------------------------------------------
# silence / speech map (ffmpeg silencedetect)
# --------------------------------------------------------------------------

def detect_silences(path, duration):
    """Return [(start, end)] silences, or None if analysis failed."""
    r = run_ffmpeg(["-i", str(path), "-af",
                    f"silencedetect=noise={SILENCE_FLOOR_DB}:d=0.3",
                    "-f", "null", "-"])
    if r is None:
        return None
    starts = [float(x) for x in
              re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in
            re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration  # trailing silence
        silences.append((s, max(e, s)))
    return silences


def speech_segments(silences, duration):
    """Complement of the silences inside [0, duration]: [(start, end)]."""
    segs, cursor = [], 0.0
    for s, e in sorted(silences):
        if s > cursor:
            segs.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        segs.append((cursor, duration))
    return [(s, e) for s, e in segs if e - s > 0.05]


# --------------------------------------------------------------------------
# check 1 — hook window
# --------------------------------------------------------------------------

def _first_beat_verdict(first_start, source):
    if first_start is None:
        return ("HOLD", f"no {source} content at all in the runtime")
    if first_start > HOOK_FIRST_WINDOW:
        return ("HOLD", f"first {source} content starts at "
                        f"{first_start:.1f}s — nothing lands in the first "
                        f"{HOOK_FIRST_WINDOW:.0f}s hook window")
    if first_start > HOOK_LATE_START:
        return ("WARN", f"first {source} content starts late, at "
                        f"{first_start:.1f}s (hook window is "
                        f"{HOOK_FIRST_WINDOW:.0f}s)")
    return ("PASS", f"first {source} content at {first_start:.1f}s")


def _title_words(title):
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 4}


def hook_from_captions(events, duration, title=None):
    """Judge the hook from caption events. Returns (status, note)."""
    if not events:
        return ("SKIP", "captions file held no caption events — "
                        "hook not judged from captions")
    status, note = _first_beat_verdict(events[0][0], "caption")
    if status == "HOLD":
        return status, note
    # promise beat: caption activity in (first window, promise window]
    if duration > HOOK_PROMISE_BY:
        activity = any(s <= HOOK_PROMISE_BY and e > HOOK_FIRST_WINDOW
                       for s, e, _ in events[1:] or events)
        # a single long opening caption spanning the window also counts
        if not activity:
            activity = any(s < HOOK_FIRST_WINDOW and e >= HOOK_PROMISE_BY
                           for s, e, _ in events)
        if not activity:
            status = "WARN" if status == "PASS" else status
            note += (f"; no second caption beat by "
                     f"{HOOK_PROMISE_BY:g}s — the hook promise is thin")
    else:
        note += " (short video — second-beat window is the whole runtime)"
    # optional title-vs-hook echo: WARN leg only, never HOLD
    if title:
        hook_text = " ".join(b for s, e, b in events
                             if s < HOOK_PROMISE_BY).lower()
        words = _title_words(title)
        if words and not (words & set(re.findall(r"[a-z0-9]+", hook_text))):
            status = "WARN" if status == "PASS" else status
            note += ("; title words do not appear in the first "
                     f"{HOOK_PROMISE_BY:g}s of captions — the hook does "
                     "not echo the title's promise")
    return status, note


def hook_from_speech(segments, duration):
    """Judge the hook from the audio speech map. Returns (status, note)."""
    if not segments:
        return ("HOLD", "audio is silent for the whole runtime — "
                        "nothing lands in the hook window")
    status, note = _first_beat_verdict(segments[0][0], "speech")
    if status == "HOLD":
        return status, note
    if duration > HOOK_PROMISE_BY:
        in_window = sum(max(0.0, min(e, HOOK_PROMISE_BY) - max(s, 0.0))
                        for s, e in segments)
        continues = any(e > HOOK_FIRST_WINDOW and s < HOOK_PROMISE_BY
                        for s, e in segments)
        if not continues or in_window < 1.0:
            status = "WARN" if status == "PASS" else status
            note += (f"; speech does not continue into the "
                     f"{HOOK_PROMISE_BY:g}s promise window")
    else:
        note += " (short video — second-beat window is the whole runtime)"
    return status, note


def check_hook(info, events, silences):
    if events:
        return hook_from_captions(events, info["duration"])
    if not info["has_audio"]:
        return ("SKIP", "no captions sidecar and no audio stream — "
                        "hook not judged, not passed")
    if silences is None:
        return ("SKIP", "no captions sidecar and silence analysis "
                        "failed — hook not judged, not passed")
    return hook_from_speech(speech_segments(silences, info["duration"]),
                            info["duration"])


# --------------------------------------------------------------------------
# check 2 — dead zones
# --------------------------------------------------------------------------

def dead_zones(silences, duration):
    """Silences > WARN threshold that START in the first 80% of runtime."""
    limit = 0.8 * duration
    return [(s, e) for s, e in silences
            if e - s > DEAD_ZONE_WARN and s < limit]


def check_dead_zones(info, silences):
    if not info["has_audio"]:
        return ("SKIP", "no audio stream — dead zones not measurable")
    if silences is None:
        return ("SKIP", "silence analysis failed — dead zones not measured")
    zones = dead_zones(silences, info["duration"])
    if not zones:
        return ("PASS", f"no silence > {DEAD_ZONE_WARN}s starting in the "
                        "first 80% of runtime")
    listing = ", ".join(f"{s:.1f}s-{e:.1f}s ({e - s:.1f}s)"
                        for s, e in zones)
    worst = max(e - s for s, e in zones)
    if worst > DEAD_ZONE_HOLD:
        return ("HOLD", f"{len(zones)} dead zone(s) > {DEAD_ZONE_WARN}s in "
                        f"the first 80%: {listing} (HOLD > "
                        f"{DEAD_ZONE_HOLD}s)")
    return ("WARN", f"{len(zones)} dead zone(s) > {DEAD_ZONE_WARN}s in "
                    f"the first 80%: {listing}")


# --------------------------------------------------------------------------
# check 3 — pacing (caption CPS)
# --------------------------------------------------------------------------

def check_pacing(events, cap_name, explicit=False):
    if events is None:
        return ("SKIP", "no captions sidecar (.srt/.vtt) — "
                        "pacing not measured, not passed")
    if not events:
        if explicit:
            # the user handed us this file on purpose; an unusable one
            # must never hide behind a SKIP and let the video PASS clean
            return ("WARN", "captions file supplied but contained no "
                            "usable caption events")
        return ("SKIP", f"no caption events parsed from {cap_name} — "
                        "pacing not measured")
    cps = caption_cps(events)
    med, mx = statistics.median(cps), max(cps)
    detail = (f"caption speed median {med:.1f} CPS, max {mx:.1f} "
              f"({len(cps)} captions from {cap_name})")
    if med > CPS_HOLD_MEDIAN or mx > CPS_HOLD_MAX:
        return ("HOLD", detail + f" (HOLD: median > {CPS_HOLD_MEDIAN:.0f} "
                                 f"or max > {CPS_HOLD_MAX:.0f})")
    if med > CPS_WARN_MEDIAN:
        return ("WARN", detail + f" (warn: median > {CPS_WARN_MEDIAN:.0f})")
    return ("PASS", detail)


# --------------------------------------------------------------------------
# check 4 — loudness
# --------------------------------------------------------------------------

def check_loudness(path, info):
    if not info["has_audio"]:
        return ("SKIP", "no audio stream — loudness not measurable")
    r = run_ffmpeg(["-i", str(path), "-af", "ebur128=peak=true",
                    "-f", "null", "-"])
    if r is None:
        return ("SKIP", "loudness analysis failed — not measured")
    m = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", r.stderr)
    if not m:
        return ("SKIP", "could not measure loudness from the audio stream")
    i = float(m[-1])
    lo, hi = LUFS_TARGET - LUFS_TOLERANCE, LUFS_TARGET + LUFS_TOLERANCE
    if lo <= i <= hi:
        return ("PASS", f"integrated loudness {i:.1f} LUFS "
                        f"(target {LUFS_TARGET:.0f} +/-{LUFS_TOLERANCE:.0f})")
    return ("HOLD", f"integrated loudness {i:.1f} LUFS — outside the "
                    f"{lo:.0f} to {hi:.0f} LUFS window "
                    f"(target {LUFS_TARGET:.0f} +/-{LUFS_TOLERANCE:.0f})")


# --------------------------------------------------------------------------
# check 5 — platform spec
# --------------------------------------------------------------------------

def classify_spec(width, height, duration, platform):
    """Returns (status, note) for the platform's spec rules."""
    vertical = height > width
    orient = ("vertical" if vertical else
              "landscape" if width > height else "square")
    short_side = min(width, height)
    size = f"{width}x{height} {orient}"
    if platform in ("youtube-shorts", "tiktok"):
        max_duration = 180.0 if platform == "youtube-shorts" else 600.0
        if not vertical:
            return ("HOLD", f"{size} — {platform} is a vertical format; "
                            "a non-vertical video is pillarboxed or cropped")
        if duration > max_duration:
            return ("HOLD", f"{size}, {duration:.0f}s — over the "
                            f"{max_duration:.0f}s {platform} limit")
        if short_side < 720:
            return ("WARN", f"{size}, {duration:.0f}s — vertical and in "
                            "length, but the short side is below 720p")
        return ("PASS", f"{size}, {duration:.0f}s — vertical, within the "
                        f"{max_duration:.0f}s {platform} limit, "
                        "short side >= 720p")
    # youtube-long
    if short_side < 480 or max(width, height) < 854:
        return ("HOLD", f"{size} — well below YouTube long-form spec "
                        "(want at least 1280x720)")
    if short_side < 720 or max(width, height) < 1280:
        return ("WARN", f"{size} — below the 1280x720 long-form standard")
    if vertical:
        return ("WARN", f"{size} — full resolution, but vertical in a "
                        "landscape format (it will be pillarboxed)")
    return ("PASS", f"{size}, {duration:.0f}s — meets the 1280x720 "
                    "long-form standard")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

class _JsonAwareParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as JSON in --json mode
    ({"error": ...} on stdout, same exit code 2) — an agent driving
    this tool with --json never gets plain text or a traceback."""

    json_mode = False

    def error(self, message):
        if self.json_mode:
            print(json.dumps({"error": message}))
            self.exit(2)
        super().error(message)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = _JsonAwareParser(
        description="retention-checker — pre-publish retention & spec QC "
                    "for faceless / AI-narrated video")
    ap.json_mode = "--json" in argv
    ap.add_argument("video", help="video file to check")
    ap.add_argument("--captions",
                    help="captions sidecar (.srt/.vtt); auto-detected "
                         "beside the video when omitted")
    ap.add_argument("--title",
                    help="the planned title; its key words are checked "
                         "against the first 8s of captions (WARN only)")
    ap.add_argument("--platform", choices=PLATFORMS,
                    default="youtube-shorts",
                    help="platform spec to judge against "
                         "(default: youtube-shorts)")
    ap.add_argument("--json", action="store_true",
                    help="Print one JSON object (verdict, checks, "
                         "warnings) instead of the human-readable report")
    a = ap.parse_args(argv)

    def fail(msg):
        # The same clean error in both modes: plain "ERROR: ..." for a
        # human, {"error": ...} for an agent — exit 2 either way.
        if a.json:
            print(json.dumps({"error": msg}))
        else:
            print(f"ERROR: {msg}")
        return 2

    p = Path(a.video)
    if not p.is_file():
        return fail(f"file not found: {p}")
    missing = [tool for tool in ("ffmpeg", "ffprobe")
               if shutil.which(tool) is None]
    if missing:
        return fail(f"{' and '.join(missing)} not found on PATH — "
                    "retention-checker needs FFmpeg (ffmpeg + ffprobe) to "
                    "measure a video. Install FFmpeg and re-run; nothing "
                    "was measured and nothing was passed.")
    try:
        info = probe_video(p)
    except ToolError as e:
        return fail(str(e))

    events, cap_name, cap_err = load_caption_events(p, a.captions)
    if cap_err:
        return fail(cap_err)

    silences = (detect_silences(p, info["duration"])
                if info["has_audio"] else None)

    if events:
        hook = hook_from_captions(events, info["duration"],
                                  title=a.title)
    else:
        hook = check_hook(info, events, silences)

    checks = [
        ("Hook window", hook),
        ("Dead zones", check_dead_zones(info, silences)),
        ("Pacing", check_pacing(events, cap_name,
                                 explicit=a.captions is not None)),
        ("Loudness", check_loudness(p, info)),
        ("Platform spec", classify_spec(info["width"], info["height"],
                                        info["duration"], a.platform)),
    ]

    measured = 0
    worst = 0
    for _name, (status, _note) in checks:
        if status != "SKIP":
            measured += 1
            worst = max(worst, SEVERITY[status])
    refusal = None
    if measured < 2:
        verdict = "HOLD"
        refusal = (f"only {measured} of 5 checks could measure this "
                   "file — refusing to pass on so little signal")
        exit_code = 2
    else:
        verdict = {0: "PASS", 1: "WARN", 2: "HOLD"}[worst]
        exit_code = worst

    if a.json:
        payload = {
            "tool": "retention-checker",
            "version": VERSION,
            "file": p.name,
            "path": str(p),
            "platform": a.platform,
            "duration_seconds": info["duration"],
            "width": info["width"],
            "height": info["height"],
            "has_audio": info["has_audio"],
            "captions": cap_name,
            "verdict": verdict,
            "checks": [
                {"name": name, "status": status, "message": note}
                for name, (status, note) in checks
            ],
            "warnings": [note for _n, (s, note) in checks if s == "WARN"],
            "measured_checks": measured,
            "note": refusal,
        }
        print(json.dumps(payload, indent=2))
        return exit_code

    print(f"retention-checker v{VERSION} — {p.name}")
    print(f"Platform: {a.platform} · Duration: {info['duration']:.1f}s · "
          f"{info['width']}x{info['height']}")
    for name, (status, note) in checks:
        print(f"  [{status}] {name}: {note}")
    if refusal is not None:
        print(f"Verdict: HOLD ({refusal})")
        return 2
    print(f"Verdict: {verdict}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
