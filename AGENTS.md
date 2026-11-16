# CAPTCHA Lab handoff

CAPTCHA Lab is a local FastAPI dashboard for authorized provider integration testing and first-party offline OCR. Open this Git repository as a project in Codex. Start with README.md.

- `lab/main.py` serves the dashboard and applies host, origin, body, timeout and concurrency controls.
- `lab/providers.py` holds fixed official Siteverify destinations and sandbox keys. Only reCAPTCHA v3 takes a dedicated localhost development key pair from the environment.
- `lab/ocr.py` normalizes bounded still images and uses Tesseract over stdin/stdout.
- `lab/static/` contains the dependency-free dashboard.
- `tests/test_lab.py` covers the API and real Tesseract when available. `.github/workflows/ci.yml` also exercises Compose.
- Use official provider test/development flows and first-party images. Keep credentials server-side. Keep app and container ports local, and preserve fixed upstream URLs and resource bounds.

Before committing, run the README tests, check the dashboard on desktop and narrow layouts, and verify affected provider scenarios. Report mocked and live results separately. Read the Git status and preserve unrelated work. Keep secrets, generated images and environment files out of Git.
