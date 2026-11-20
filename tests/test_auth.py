"""api/auth.py 的测试：签发、校验、过期、篡改、模式区分、配置错误。"""

from __future__ import annotations

import base64
import json
import time

import pytest

from api import auth


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        auth.ENV_AUTH_MODE,
        auth.ENV_TOKEN_SECRET,
        auth.ENV_TOKEN_TTL_MINUTES,
    ):
        monkeypatch.delenv(name, raising=False)


def _payload_of(token: str) -> dict:
    payload_b64 = token.split(".")[0]
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


def _craft(payload: dict, secret: str) -> str:
    """手工拼一个 Token，用来模拟"签名对但内容不对"的情况。"""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{payload_b64}.{auth._sign(payload_b64, secret)}"


def test_default_mode_is_demo():
    assert auth.get_auth_mode() == "demo"
    assert auth.status()["secret_configured"] is True


def test_token_roundtrip():
    token = auth.create_token("us_demo_a")
    identity = auth.decode_token(token)

    assert identity.user_id == "us_demo_a"
    assert identity.mode == "demo"
    assert identity.expires_at - identity.issued_at == auth.DEFAULT_TTL_MINUTES * 60


def test_token_contains_required_claims():
    token = auth.create_token("us_demo_a")
    payload = _payload_of(token)

    assert payload["uid"] == "us_demo_a"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert payload["mode"] == "demo"
    assert payload["iss"] == "unipulse"
    # 不能把姓名、企微 ID 之类写进凭据
    assert "name" not in payload
    assert "wecom_userid" not in payload


def test_tampered_signature_rejected():
    token = auth.create_token("us_demo_a")
    payload_b64, signature = token.split(".")
    broken = f"{payload_b64}.{signature[:-2]}xx"

    with pytest.raises(auth.AuthError) as excinfo:
        auth.decode_token(broken)
    assert excinfo.value.reason == "BAD_SIGNATURE"


def test_tampered_payload_rejected():
    """把 uid 改成别人，签名就对不上了。"""
    token = auth.create_token("us_demo_a")
    payload = _payload_of(token)
    payload["uid"] = "us_demo_b"

    # 真实攻击场景：改了 payload 却沿用原签名
    payload_b64, signature = token.split(".")
    forged_payload = base64.urlsafe_b64encode(
        json.dumps({**payload, "uid": "us_demo_b"}, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(auth.AuthError):
        auth.decode_token(f"{forged_payload}.{signature}")


def test_malformed_token_rejected():
    with pytest.raises(auth.AuthError) as excinfo:
        auth.decode_token("not-a-token")
    assert excinfo.value.reason == "MALFORMED_TOKEN"


def test_empty_token_rejected():
    with pytest.raises(auth.AuthError) as excinfo:
        auth.decode_token("")
    assert excinfo.value.reason == "MISSING_TOKEN"


def test_expired_token_rejected():
    token = auth.create_token("us_demo_a", ttl_minutes=-1)

    with pytest.raises(auth.AuthError) as excinfo:
        auth.decode_token(token)
    assert excinfo.value.reason == "TOKEN_EXPIRED"


def test_future_issued_token_rejected():
    token = auth.create_token("us_demo_a", now=time.time() + 3600)

    with pytest.raises(auth.AuthError) as excinfo:
        auth.decode_token(token)
    assert excinfo.value.reason == "NOT_YET_VALID"


def test_ttl_from_env(monkeypatch):
    monkeypatch.setenv(auth.ENV_TOKEN_TTL_MINUTES, "30")
    token = auth.create_token("us_demo_a")
    identity = auth.decode_token(token)
    assert identity.expires_at - identity.issued_at == 30 * 60


def test_wrong_mode_rejected(monkeypatch):
    """演示模式签的 Token，不能用于正式模式。"""
    monkeypatch.setenv(auth.ENV_AUTH_MODE, "production")
    monkeypatch.setenv(auth.ENV_TOKEN_SECRET, "x" * 48)

    demo_token = auth.create_token("us_demo_a", mode="demo")

    with pytest.raises(auth.AuthError):
        auth.decode_token(demo_token, mode="production")


def test_wrong_mode_with_valid_signature(monkeypatch):
    """签名正确但 payload 里的 mode 与服务不一致，也必须拒绝。"""
    production_secret = "y" * 48
    now = int(time.time())
    forged = _craft(
        {
            "uid": "us_demo_a",
            "iat": now,
            "exp": now + 600,
            "mode": "demo",
            "iss": "unipulse",
        },
        production_secret,
    )

    monkeypatch.setenv(auth.ENV_AUTH_MODE, "production")
    monkeypatch.setenv(auth.ENV_TOKEN_SECRET, production_secret)

    with pytest.raises(auth.AuthError) as excinfo:
        auth.decode_token(forged)
    assert excinfo.value.reason == "WRONG_MODE"


def test_production_requires_secret(monkeypatch):
    monkeypatch.setenv(auth.ENV_AUTH_MODE, "production")

    with pytest.raises(auth.AuthConfigError) as excinfo:
        auth.ensure_configured()
    assert "UNIPULSE_TOKEN_SECRET" in str(excinfo.value)


def test_production_rejects_short_secret(monkeypatch):
    monkeypatch.setenv(auth.ENV_AUTH_MODE, "production")
    monkeypatch.setenv(auth.ENV_TOKEN_SECRET, "short")

    with pytest.raises(auth.AuthConfigError):
        auth.ensure_configured()


def test_production_rejects_demo_secret(monkeypatch):
    monkeypatch.setenv(auth.ENV_AUTH_MODE, "production")
    monkeypatch.setenv(auth.ENV_TOKEN_SECRET, auth._DEMO_SECRET)

    with pytest.raises(auth.AuthConfigError):
        auth.ensure_configured()


def test_invalid_auth_mode_rejected(monkeypatch):
    monkeypatch.setenv(auth.ENV_AUTH_MODE, "whatever")

    with pytest.raises(auth.AuthConfigError):
        auth.get_auth_mode()


def test_status_does_not_leak_secret():
    info = auth.status()
    assert "secret" not in json.dumps(info).replace("secret_configured", "")
