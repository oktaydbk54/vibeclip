# Contributing to VibeClip

Thanks for your interest! VibeClip is an open-source, chat-driven AI video editor.
Contributions of all sizes are welcome — bug reports, docs, new caption styles,
pipeline improvements.

## Ground rules

- **Be kind.** See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
- **English only** for code, comments, UI strings and docs. (The chat agent
  intentionally replies in the *user's* language — that's a feature, not a string
  to translate.)
- **No build step.** The frontend is vanilla HTML/CSS/JS served by FastAPI. Don't
  introduce a bundler/framework without discussing it in an issue first.
- **No branded/copyrighted media.** Bundled assets must be CC0 / CC-BY (credited)
  or otherwise clearly licensed for redistribution. Never add branded game footage.
- **Never commit secrets.** Keys live only in `.env` (gitignored). A
  `detect-secrets` pre-commit hook guards this — please install it (below).

## Dev setup

```bash
git clone https://github.com/oktaydbk54/vibeclip.git
cd vibeclip
cp .env.example .env          # add an LLM key (OpenAI or DeepSeek)
uv sync                       # or: pip install -e .
python -m chat.app            # http://127.0.0.1:8765
```

You'll also need **ffmpeg** on your PATH and the DejaVu fonts (for caption
rendering). On first run the Whisper model downloads automatically.

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
```

This runs `ruff` (lint) and `detect-secrets` on every commit.

### Tests

```bash
pip install pytest
pytest
```

The suite is intentionally light: a smoke test that the app imports + serves, and
unit tests for the LLM-key resolution (the riskiest logic). Please add a test when
you touch `pipeline/config.py`'s `llm_settings` or the auth/BYOK paths.

## Pull requests

1. Fork, branch from `main`, keep PRs focused.
2. Match the surrounding code's style — comment density, naming, idiom.
3. Run `pre-commit run --all-files` and `pytest` before pushing.
4. Describe **what** changed and **why**. Screenshots/GIFs for UI changes.
5. Sign the CLA when prompted (see below).

## Contributor License Agreement (CLA)

VibeClip is **AGPL-3.0**. To keep the door open for a future hosted/commercial
edition that funds the project, contributions are accepted under a lightweight CLA
that licenses your contribution to the maintainer while leaving you full rights to
your own code. A bot will ask you to sign on your first PR. If you'd rather not,
that's okay — open an issue and we'll figure out the best path for your change.

## Good first issues

Look for the [`good first issue`](https://github.com/oktaydbk54/vibeclip/labels/good%20first%20issue)
label. A few standing ones:

- Englishify the legacy Turkish onboarding enum keys (with back-compat for stored
  profiles).
- Rename the internal `kesim-*` front-end identifiers to `vibeclip-*`.
- Add new caption/style presets under `assets/styles/`.

## Reporting bugs / security

- Bugs: open an issue with steps to reproduce, your OS, and logs.
- Security vulnerabilities: **do not** open a public issue — see
  [`SECURITY.md`](SECURITY.md).
