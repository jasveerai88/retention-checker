#!/usr/bin/env python3
"""Generate the example videos the README runs on.

    python3 examples/make_examples.py [output-dir]

Creates, in the output dir (default: this folder):
  good.mp4  12s, 720x1280 — tone ("speech") from 0s with a 1s pause at
            5-6s, loudness-normalised to -14 LUFS. Sidecar good.srt has
            a caption at 0s and a new beat every 3s at ~10 CPS.
            Expected: all five checks PASS, exit 0.
  bad.mp4   20s, 720x1280 — silence 0-5s and 9-13s, loudness-normalised
            (loudness is NOT its problem). Sidecar bad.srt starts at 5s
            and opens with a 40-CPS caption.
            Expected: Hook HOLD, Dead zones HOLD, Pacing HOLD, exit 2.

The tone is a speech *proxy*: silencedetect cannot tell a 440 Hz tone
from a voice, and the hook/dead-zone checks judge timing, not words.
Real narration behaves the same way through every gate here.

Needs ffmpeg on PATH. Stdlib only.
"""
import re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERROR: command failed: {' '.join(cmd[:3])} ...")
        print(r.stderr[-1500:])
        sys.exit(1)


def make(out, duration, mute_expr):
    audio = (f"volume=0:enable='{mute_expr}',"
             f"loudnorm=I=-14:TP=-1.5:LRA=11")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         f"testsrc2=size=720x1280:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-filter_complex", f"[1:a]{audio}[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)])
    print(f"wrote {out}")


def measure_lufs(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i",
                        str(path), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", r.stderr)
    return float(m[-1]) if m else None


def fix_loudness(path):
    """Single-pass loudnorm undershoots when much of the file is silent
    (the integrated measurement is gated). Correct with one measured
    gain pass so both examples really land at -14 LUFS."""
    i = measure_lufs(path)
    if i is None:
        return
    delta = -14.0 - i
    if abs(delta) < 0.3:
        return
    tmp = path.with_suffix(".tmp.mp4")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(path), "-c:v", "copy", "-af", f"volume={delta:.2f}dB",
         str(tmp)])
    tmp.replace(path)


def main():
    if shutil.which("ffmpeg") is None:
        print("ERROR: ffmpeg not found on PATH — cannot generate examples.")
        return 1
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    # good: mute only the 5-6s pause
    make(out_dir / "good.mp4", 12, "between(t,5,6)")
    fix_loudness(out_dir / "good.mp4")
    # bad: mute the 0-5s opening and the 9-13s mid-roll gap
    make(out_dir / "bad.mp4", 20, "between(t,0,5)+between(t,9,13)")
    fix_loudness(out_dir / "bad.mp4")
    for name in ("good.srt", "bad.srt"):
        src = HERE / name
        dst = out_dir / name
        if src.resolve() != dst.resolve():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
