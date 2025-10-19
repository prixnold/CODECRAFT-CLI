# CodeCraft CLI (Local scaffold & automation)

This repository provides a local, ready-to-run CodeCraft CLI scaffold that assembles multi-language projects from prewritten snippet modules.

What this deliverable includes:
- A Python package skeleton `codecraft/` with:
  - `cli.py` — Click-based CLI entrypoint (`codecraft`)
  - `assembler.py` — selection and assembly logic
  - `modules/` — bundled example snippets (frontend/backend/mobile/cli)
- An automation script `build_publish.py` that builds the wheel, creates an EXE, and drafts Winget/Chocolatey artifacts.
- `pyproject.toml` to build a distributable wheel.

Important:
- I cannot publish packages or create GitHub releases for you; you must run repository uploads from your environment and provide any required tokens.
- The automation script will prompt before any network action.

Quick start (local):
1. Create a virtualenv and activate it:
   python -m venv .venv
   source .venv/bin/activate  # macOS / Linux
   .venv\Scripts\activate     # Windows

2. Install build tools:
   python -m pip install --upgrade build twine pyinstaller click

3. Build the package:
   python -m build

4. Install locally for testing:
   pip install dist/codecraft_cli-0.1.0-py3-none-any.whl

5. Try the CLI:
   codecraft "Build a workout tracker app #mobile_friendly"

6. Use the automation script `build_publish.py` to build EXE and prepare manifests. It will prompt before uploads.

Security:
- The automation will ask for tokens at runtime. Do not share tokens publicly.
