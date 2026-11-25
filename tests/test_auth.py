"""
Tests for Google Firebase Authentication, Session Signing, and Login/Logout Pages.
Verifies INV-07 (statutory disclaimer), INV-10 (zero PII/tokens in logs), and DPDPA §6 consent gate.
Synchronous execution for pure determinism and zero threading latency.
"""

import base64
import json
import logging
import time
from pathlib import Path
import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app import auth, config
from app.main import (
    serve_login,
    serve_logout,
    get_auth_config,
    get_current_user,
    create_firebase_session,
    logout_session,
)
from app.models import FirebaseSessionRequest


def _make_request(cookies: dict | None = None, scheme: str = "http") -> Request:
    headers = []
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", cookie_str.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/",
        "headers": headers,
    }
    return Request(scope)


def _make_fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.fakesignature1234567890"


def test_login_and_logout_pages_served():
    login_resp = serve_login()
    login_path = Path(login_resp.path)
    assert login_path.exists()
    login_html = login_path.read_text(encoding="utf-8")
    assert "firebase-auth.js" in login_html
    assert "Continue with Google" in login_html
    assert "DPDPA §6 Consent" in login_html

    logout_resp = serve_logout()
    logout_path = Path(logout_resp.path)
    assert logout_path.exists()
    logout_html = logout_path.read_text(encoding="utf-8")
    assert "signOut" in logout_html
    assert "/api/auth/logout" in logout_html


def test_auth_config_unconfigured_vs_configured(monkeypatch):
    monkeypatch.setattr(config, "FIREBASE_API_KEY", "")
    monkeypatch.setattr(config, "FIREBASE_AUTH_DOMAIN", "")
    monkeypatch.setattr(config, "FIREBASE_PROJECT_ID", "")
    cfg_off = get_auth_config()
    assert cfg_off.configured is False

    monkeypatch.setattr(config, "FIREBASE_API_KEY", "test-api-key")
    monkeypatch.setattr(config, "FIREBASE_AUTH_DOMAIN", "nyaya-demo.firebaseapp.com")
    monkeypatch.setattr(config, "FIREBASE_PROJECT_ID", "nyaya-demo")
    cfg_on = get_auth_config()
    assert cfg_on.configured is True
    assert cfg_on.projectId == "nyaya-demo"


def test_session_requires_dpdpa_consent():
    token = _make_fake_jwt({
        "aud": "nyaya-demo",
        "iss": "https://securetoken.google.com/nyaya-demo",
        "exp": int(time.time()) + 300,
    })
    req = FirebaseSessionRequest(id_token=token, consent_accepted=False)
    with pytest.raises(HTTPException) as exc_info:
        create_firebase_session(req, _make_request(), Response())
    assert exc_info.value.status_code == 400
    assert "consent" in exc_info.value.detail.lower()


def test_firebase_session_lifecycle_and_log_privacy(monkeypatch, caplog):
    monkeypatch.setattr(config, "FIREBASE_API_KEY", "test-api-key")
    monkeypatch.setattr(config, "FIREBASE_AUTH_DOMAIN", "nyaya-demo.firebaseapp.com")
    monkeypatch.setattr(config, "FIREBASE_PROJECT_ID", "nyaya-demo")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "users": [
                    {
                        "localId": "firebase_uid_secret_123",
                        "email": "aarav.sharma@example.com",
                        "displayName": "Aarav Sharma",
                        "emailVerified": True,
                        "providerUserInfo": [{"providerId": "google.com"}],
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json=None):
            assert "accounts:lookup" in url
            return FakeResponse()

    monkeypatch.setattr(auth.httpx, "Client", FakeClient)

    fake_token = _make_fake_jwt({
        "aud": "nyaya-demo",
        "iss": "https://securetoken.google.com/nyaya-demo",
        "exp": int(time.time()) + 3600,
    })

    with caplog.at_level(logging.INFO):
        # 1. Create session
        resp = Response()
        session_req = FirebaseSessionRequest(
            id_token=fake_token,
            consent_accepted=True,
            consent_version="v1.0-2026",
        )
        body = create_firebase_session(session_req, _make_request(), resp)
        assert body.authenticated is True
        assert body.disclaimer == config.DISCLAIMER
        assert body.user is not None
        assert body.user.provider == "google"
        assert body.user.email_masked == "aa***@example.com"
        assert body.user.uid_hash != "firebase_uid_secret_123"

        set_cookie_header = resp.headers.get("set-cookie", "")
        assert f"{config.SESSION_COOKIE_NAME}=" in set_cookie_header
        cookie_val = set_cookie_header.split(f"{config.SESSION_COOKIE_NAME}=", 1)[1].split(";", 1)[0]

        # 2. Check /api/auth/me with cookie
        me_data = get_current_user(_make_request({config.SESSION_COOKIE_NAME: cookie_val}))
        assert me_data.authenticated is True
        assert me_data.user.display_name == "Aarav Sharma"

        # 3. Logout revokes session and clears cookie
        logout_resp_obj = Response()
        logout_body = logout_session(
            _make_request({config.SESSION_COOKIE_NAME: cookie_val}),
            logout_resp_obj,
        )
        assert logout_body["status"] == "logged_out"
        assert logout_body["session_revoked"] is True
        assert logout_body["disclaimer"] == config.DISCLAIMER
        assert "Max-Age=0" in logout_resp_obj.headers.get("set-cookie", "")

        # 4. Subsequent /api/auth/me is unauthenticated
        me_after = get_current_user(_make_request({config.SESSION_COOKIE_NAME: cookie_val}))
        assert me_after.authenticated is False

    # INV-10 check: zero raw email, raw UID, or ID token in logs
    log_text = caplog.text
    assert "aarav.sharma@example.com" not in log_text
    assert "firebase_uid_secret_123" not in log_text
    assert fake_token not in log_text


def test_tampered_session_cookie_rejected():
    tampered = "0" * 32 + ".9999999999.tamperedsig"
    resp = get_current_user(_make_request({config.SESSION_COOKIE_NAME: tampered}))
    assert resp.authenticated is False


def test_unauthenticated_product_access_rejected():
    from app.main import require_authenticated_user
    with pytest.raises(HTTPException) as exc_info:
        require_authenticated_user(_make_request())
    assert exc_info.value.status_code == 401
    assert "sign in" in exc_info.value.detail.lower()


def test_document_owner_isolation_blocks_other_user(monkeypatch):
    from app.main import (
        load_sample_document,
        get_document_analysis,
        delete_document,
    )
    from app.models import AuthUser

    monkeypatch.setattr(config, "LLM_PROVIDER", "none")
    user_a = AuthUser(uid_hash="owner_a_hash_111", provider="google")
    user_b = AuthUser(uid_hash="owner_b_hash_222", provider="google")

    doc = load_sample_document("gold_01_nia_138", current_user=user_a)
    try:
        # Owner A can access
        fetched = get_document_analysis(doc.doc_id, current_user=user_a)
        assert fetched.doc_id == doc.doc_id

        # Another authenticated user B is blocked with 403
        with pytest.raises(HTTPException) as exc_get:
            get_document_analysis(doc.doc_id, current_user=user_b)
        assert exc_get.value.status_code == 403

        with pytest.raises(HTTPException) as exc_del:
            delete_document(doc.doc_id, current_user=user_b)
        assert exc_del.value.status_code == 403
    finally:
        delete_document(doc.doc_id, current_user=user_a)
