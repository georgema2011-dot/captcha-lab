# CAPTCHA Lab

A free, self-hosted test bench for CAPTCHA integrations you own or are authorized to test. One dashboard runs official reCAPTCHA, hCaptcha and Turnstile development workflows and reads simple first-party image challenges with local Tesseract.

## Start the lab

### Docker Compose

Install Docker Engine with the Compose plugin, or use a compatible local Docker runtime.

```sh
docker compose up --build --wait
```

Open [http://localhost:8000](http://localhost:8000). Stop the lab with `docker compose down`.

### Native Python

Use Python 3.12 and install Tesseract with English language data. On macOS, `brew install tesseract`. On Debian or Ubuntu, `sudo apt-get install tesseract-ocr tesseract-ocr-eng`.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn lab.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --limit-concurrency 24
```

In Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and ensure `tesseract.exe` is on PATH, or use Docker.

## Run a scenario

| Scenario | Workflow | Expected |
| --- | --- | --- |
| reCAPTCHA v2 | Run the built-in Google test checkbox, then complete its prompt | Accepted using Google's public test keys |
| reCAPTCHA v3 | Run with a dedicated development key pair and localhost | Score evaluated against configured minimum, action and hostname |
| hCaptcha | Run the documented test token on localhost, or run the widget on the hosts alias | Accepted using hCaptcha's publisher test pair |
| Turnstile | Run the passing widget with the selected server-side sandbox secret | Accept, reject, or simulate a spent token |
| Image OCR | Select an authorized still image or click **Try a sample** | Single-line text from the local English OCR engine |

Provider scripts and Siteverify calls require internet access. The dashboard loads a provider's script when you run that widget. OCR and sample generation work offline.

### reCAPTCHA v3

Use Google's reCAPTCHA admin console to create a separate **score-based v3 development** key pair with `localhost` as its allowed hostname. Keep hostname validation enabled. The lab uses the classic Siteverify API and requires the integration's site key and secret key.

Copy `.env.example` to `.env`, fill in the pair, and restart the app. With Compose, run `docker compose up -d --force-recreate`. Use [http://localhost:8000](http://localhost:8000), choose v3, and click **Run test**. Google advises that development scores can differ from production traffic. Use the displayed score and configured minimum to test both sides of your score policy.

### hCaptcha widget

Add `127.0.0.1 captcha-lab.test` to your machine's hosts file and open [http://captcha-lab.test:8000](http://captcha-lab.test:8000). The hosts file is at `/etc/hosts` on macOS/Linux and `C:\Windows\System32\drivers\etc\hosts` on Windows. The local alias lets you exercise the official widget flow. On localhost the dashboard directly submits the documented fixed test token to Siteverify.

## Local API

GET `/health` reports whether the OCR executable is installed. GET `/api/config` returns public site keys and scenario metadata. GET `/api/ocr/sample` returns a generated PNG. POST `/api/ocr` accepts raw PNG, JPEG or WebP bytes capped at 2 MiB and 2 million pixels. POST `/api/verify/{scenario}` accepts JSON with a single `token` string from the corresponding lab flow.

```sh
curl -fsS http://localhost:8000/api/ocr/sample -o sample.png
curl -fsS -H 'Content-Type: image/png' --data-binary @sample.png http://localhost:8000/api/ocr

curl -fsS -H 'Content-Type: application/json' \
  --data '{"token":"10000000-aaaa-bbbb-cccc-000000000001"}' \
  http://localhost:8000/api/verify/hcaptcha
```

For verification, HTTP 200 returns the verdict in `accepted`, alongside `expected`, `errors` and the v3 score when available. HTTP 502 indicates a provider transport or response error. Use a fresh widget token for each run. The Turnstile duplicate scenario uses the documented spent-token secret to simulate that response.

## Scope and security

- Run on a trusted machine with the published port bound to loopback. The local API trusts processes on that machine. Use authorized development credentials and keep the lab separate from production verification.
- The three verification destinations are fixed. Secrets stay on the server. The app permits only local hostnames, rejects cross-origin mutations and applies a browser content security policy.
- Verification tokens and images are transient. OCR accepts still images, limits work to two simultaneous reads and times out each Tesseract process after five seconds. Results stay in the current page.
- The lab uses intentionally permissive provider credentials for integration tests. Treat all lab verdicts as development data.
- OCR is intended for simple, readable single-line text from your own challenges. Results depend on image clarity and font.

## Tests and Codex handoff

```sh
python -m pip install -r requirements-dev.txt
python -m pytest --cov=lab --cov-report=term-missing --cov-fail-under=85
node --check lab/static/lab.js
```

GitHub Actions runs Python tests with installed Tesseract, checks frontend syntax, and builds and exercises the Compose service. Provider tests in CI use mocked Siteverify responses, so CI needs no provider secrets. For a live check, run each dashboard scenario and compare it to the table above. The v3 live check needs your development keys.

Open this repository folder as a project in Codex. [AGENTS.md](AGENTS.md) contains the project map, guardrails and verification commands.

## Provider references

- [Google reCAPTCHA test keys and FAQ](https://developers.google.com/recaptcha/docs/faq)
- [Google reCAPTCHA v3](https://developers.google.com/recaptcha/docs/v3)
- [hCaptcha integration testing and local development](https://docs.hcaptcha.com/#integration-testing-test-keys)
- [Cloudflare Turnstile test credentials](https://developers.cloudflare.com/turnstile/troubleshooting/testing/)
