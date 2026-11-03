# Contributing

Contributions are welcome — bug reports, fixes and small focused features.

## Ground rules

- **Standard library + ffmpeg only.** retention-checker's promise is
  "clone and run" — no third-party Python dependencies, ever.
- **Local only.** No network calls, no telemetry, no uploads of user files.
- **A SKIP never becomes a PASS.** If a check cannot measure, it says so.
  PRs that soften an honest SKIP into a pass will not be merged.
- **Errors are clean.** Missing/corrupt input, bad flags, and missing
  ffmpeg produce a one-line ERROR and exit 2 — never a traceback.
- **Both directions tested.** A gate change needs a test where good input
  passes and bad input fails — not just one side.
- **Gates move on evidence, not taste.** Threshold changes (CPS, LUFS,
  silence lengths, hook windows) need measured data or a linked issue
  discussion, not a preference.

## Getting started

```bash
git clone https://github.com/jasveerai88/retention-checker
cd retention-checker
python3 examples/make_examples.py
python3 -m unittest discover -s tests -v
python3 src/retention_checker.py examples/good.mp4
```

## Pull requests

1. Fork, branch, and keep the change to one topic.
2. Add or update tests under `tests/`; the full suite must pass.
3. Note the change in `CHANGELOG.md` under an "Unreleased" heading.
4. Describe the behaviour change with a real before/after run if the CLI
   output changes.
