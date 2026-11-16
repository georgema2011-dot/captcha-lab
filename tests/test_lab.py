import io
import shutil
import subprocess
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from lab import ocr, providers
from lab.main import app


@pytest.fixture
def client(monkeypatch):
    for name in ("RECAPTCHA_V3_SITE_KEY", "RECAPTCHA_V3_SECRET_KEY", "RECAPTCHA_V3_MIN_SCORE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("lab.main.load_dotenv", lambda _path: None)
    with TestClient(app, base_url="http://localhost") as browser:
        yield browser


def upstream(monkeypatch, handler):
    monkeypatch.setattr(
        app.state, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def test_dashboard_and_security(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Test bench" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/static/lab.js").status_code == 200
    assert client.get("/.env").status_code == 404
    assert client.get("/", headers={"Host": "attacker.test"}).status_code == 400
    assert client.post("/api/ocr", headers={"Origin": "http://attacker.test"}).status_code == 403
    assert client.post("/api/ocr", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/health").json()["status"] == "ok"
    config = client.get("/api/config")
    assert providers.V2_SECRET not in config.text
    assert config.json()["scenarios"]["recaptcha-v3"]["site_key"] == ""


@pytest.mark.parametrize(
    "name,endpoint,errors",
    [
        ("recaptcha-v2", providers.GOOGLE, []),
        ("hcaptcha", providers.HCAPTCHA, []),
        ("turnstile-pass", providers.TURNSTILE, []),
        ("turnstile-reject", providers.TURNSTILE, ["invalid-input-response"]),
        ("turnstile-duplicate", providers.TURNSTILE, ["timeout-or-duplicate"]),
    ],
)
def test_verifies_fixed_sandbox_scenarios(client, monkeypatch, name, endpoint, errors):
    def handler(request):
        assert str(request.url) == endpoint
        fields = parse_qs(request.content.decode())
        assert fields["response"] == ["sandbox-token"]
        assert fields["secret"] == [providers.scenarios()[name].secret]
        if name == "hcaptcha":
            assert fields["sitekey"] == [providers.HC_SITE]
        return httpx.Response(200, json={"success": not errors, "error-codes": errors})

    upstream(monkeypatch, handler)
    result = client.post(
        f"/api/verify/{name}", json={"token": "sandbox-token"}, headers={"Origin": "http://localhost"}
    )
    assert result.status_code == 200
    assert result.json()["accepted"] is (not errors)
    assert result.json()["errors"] == errors
    assert "sandbox-token" not in result.text


def test_rejects_invalid_requests(client):
    assert client.post("/api/verify/missing").status_code == 404
    assert client.post("/api/verify/recaptcha-v3").status_code == 409
    url = "/api/verify/recaptcha-v2"
    assert client.post(url, data={"token": "bad"}).status_code == 415
    assert client.post(url, json={"token": " "}).status_code == 422
    assert client.post(url, json={"token": "test", "url": "https://other.test"}).status_code == 422
    assert client.post(url, json={"token": "x" * 8192}).status_code == 413


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500),
        httpx.Response(200, text="bad"),
        httpx.Response(200, json={"success": "true"}),
        httpx.Response(200, json={"success": True, "error-codes": "bad"}),
    ],
)
def test_invalid_upstream_fails_closed(client, monkeypatch, reply):
    upstream(monkeypatch, lambda request: reply)
    assert client.post("/api/verify/recaptcha-v2", json={"token": "test"}).status_code == 502


@pytest.mark.parametrize(
    "overrides, errors",
    [
        ({}, []),
        ({"action": "unrelated"}, ["unexpected-action"]),
        ({"hostname": "wrong.test"}, ["unexpected-hostname"]),
        ({"score": 0.2}, ["score-below-threshold"]),
        ({"score": True}, ["invalid-score"]),
        ({"error-codes": ["Over free quota."]}, ["Over free quota."]),
    ],
)
def test_v3_checks_dev_context_and_score(client, monkeypatch, overrides, errors):
    configured = providers.Scenario(providers.GOOGLE, "dev-public", "dev-secret", "score evaluated")
    monkeypatch.setitem(app.state.scenarios, "recaptcha-v3", configured)
    payload = {"success": True, "hostname": "localhost", "action": providers.ACTION, "score": 0.8}
    upstream(monkeypatch, lambda request: httpx.Response(200, json=payload | overrides))
    url = "/api/verify/recaptcha-v3"
    assert client.post(url, json={"token": "test"}, headers={"Host": "127.0.0.1"}).status_code == 400
    result = client.post(url, json={"token": "test"}).json()
    assert result["accepted"] is (not errors)
    assert result["errors"] == errors


def test_ocr_sample_and_request_validation(client, monkeypatch):
    sample = client.get("/api/ocr/sample")
    assert Image.open(io.BytesIO(sample.content)).size == (380, 116)
    assert client.post("/api/ocr", content=b"svg").status_code == 415
    headers = {"Content-Type": "image/png"}
    assert client.post("/api/ocr", headers=headers, content=b"x" * (ocr.MAX_BYTES + 1)).status_code == 413
    assert client.post("/api/ocr", headers=headers, content=b"corrupt").status_code == 422
    monkeypatch.setattr(ocr.shutil, "which", lambda _name: None)
    assert client.post("/api/ocr", headers=headers, content=sample.content).status_code == 503


def test_ocr_rejects_oversized_dimensions():
    image = Image.new("1", (2000, 1001))
    out = io.BytesIO()
    image.save(out, "PNG")
    with pytest.raises(ocr.InvalidImage):
        ocr.normalize(out.getvalue())


def test_ocr_subprocess_and_timeout(client, monkeypatch):
    sample = client.get("/api/ocr/sample").content
    monkeypatch.setattr(ocr.shutil, "which", lambda _name: "/test/tesseract")

    def read(args, **kwargs):
        assert args[1:3] == ["stdin", "stdout"]
        assert kwargs["timeout"] == 5
        assert kwargs["input"].startswith(b"\x89PNG")
        return subprocess.CompletedProcess(args, 0, b"LAB27\n")

    monkeypatch.setattr(ocr.subprocess, "run", read)
    headers = {"Content-Type": "image/png"}
    assert client.post("/api/ocr", content=sample, headers=headers).json() == {"text": "LAB27"}

    def time_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(ocr.subprocess, "run", time_out)
    assert client.post("/api/ocr", content=sample, headers=headers).status_code == 503


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_actual_ocr(client):
    image = Image.new("L", (380, 116), "white")
    draw = ImageDraw.Draw(image)
    draw.text((190, 58), "TEST 2468", font=ImageFont.load_default(size=56), fill="black", anchor="mm")
    out = io.BytesIO()
    image.save(out, "PNG")
    response = client.post("/api/ocr", content=out.getvalue(), headers={"Content-Type": "image/png"})
    assert response.json() == {"text": "TEST 2468"}
