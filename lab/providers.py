"""Official sandbox credentials and fixed Siteverify destinations."""

import math
import os
from dataclasses import dataclass, field

import httpx

GOOGLE = "https://www.google.com/recaptcha/api/siteverify"
HCAPTCHA = "https://api.hcaptcha.com/siteverify"
TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
ACTION = "captcha_lab"

V2_SITE = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
V2_SECRET = "6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe"
HC_SITE = "10000000-ffff-ffff-ffff-000000000001"
HC_SECRET = "0x0000000000000000000000000000000000000000"
TS_SITE = "1x" + "0" * 20 + "AA"


@dataclass(frozen=True)
class Scenario:
    endpoint: str
    site_key: str
    secret: str = field(repr=False)
    expected: str


def scenarios():
    return {
        "recaptcha-v2": Scenario(GOOGLE, V2_SITE, V2_SECRET, "accepted"),
        "recaptcha-v3": Scenario(
            GOOGLE, os.getenv("RECAPTCHA_V3_SITE_KEY", "").strip(),
            os.getenv("RECAPTCHA_V3_SECRET_KEY", "").strip(), "score evaluated",
        ),
        "hcaptcha": Scenario(HCAPTCHA, HC_SITE, HC_SECRET, "accepted"),
        "turnstile-pass": Scenario(TURNSTILE, TS_SITE, "1x" + "0" * 31 + "AA", "accepted"),
        "turnstile-reject": Scenario(TURNSTILE, TS_SITE, "2x" + "0" * 31 + "AA", "rejected"),
        "turnstile-duplicate": Scenario(TURNSTILE, TS_SITE, "3x" + "0" * 31 + "AA", "timeout-or-duplicate"),
    }


async def verify(client: httpx.AsyncClient, name: str, scenario: Scenario, token: str, minimum: float):
    form = {"secret": scenario.secret, "response": token}
    if name == "hcaptcha":
        form["sitekey"] = scenario.site_key
    response = await client.post(scenario.endpoint, data=form)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or type(payload.get("success")) is not bool:
        raise ValueError("Invalid Siteverify response")
    errors = payload.get("error-codes", [])
    if not isinstance(errors, list) or any(not isinstance(code, str) for code in errors):
        raise ValueError("Invalid Siteverify error codes")

    accepted = payload["success"] and not errors
    result = {"accepted": accepted, "expected": scenario.expected, "errors": errors[:10]}

    if name == "recaptcha-v3" and accepted:
        score = payload.get("score")
        if payload.get("hostname") != "localhost":
            result["errors"].append("unexpected-hostname")
        if payload.get("action") != ACTION:
            result["errors"].append("unexpected-action")
        if type(score) not in (int, float) or not 0 <= score <= 1 or not math.isfinite(score):
            result["errors"].append("invalid-score")
        else:
            result["score"] = score
            if score < minimum:
                result["errors"].append("score-below-threshold")
        result["minimum_score"] = minimum
        result["accepted"] = not result["errors"]
    return result
