"""
Google Firebase Authentication verification and HMAC session manager for NyayaAssist.
Pure domain module (Architectural Rule 4.1).
Enforces INV-10 (Zero PII / Document Text in Logs) and DPDPA consent validation.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from app import config
from app.models import AuthUser, FirebasePublicConfig

logger = logging.getLogger("nyayaassist.auth")

_CACHED_SECRET: Optional[bytes] = None


def _get_signing_secret() -> bytes:
    """Returns the HMAC signing secret from config or persistent local secret file."""
    global _CACHED_SECRET
    if config.SESSION_SECRET_KEY:
        return config.SESSION_SECRET_KEY.encode("utf-8")
    if _CACHED_SECRET is not None:
        return _CACHED_SECRET

    secret_file = config.DATA_DIR / ".session_secret"
    if secret_file.exists():
        raw = secret_file.read_text(encoding="utf-8").strip()
        if len(raw) >= 32:
            _CACHED_SECRET = raw.encode("utf-8")
            return _CACHED_SECRET

    generated = secrets.token_hex(32)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(generated, encoding="utf-8")
    _CACHED_SECRET = generated.encode("utf-8")
    return _CACHED_SECRET


def is_firebase_configured() -> bool:
    """Returns True if minimum Firebase Web App credentials are present."""
    return bool(
        config.FIREBASE_API_KEY
        and config.FIREBASE_AUTH_DOMAIN
        and config.FIREBASE_PROJECT_ID
    )


def get_public_firebase_config() -> FirebasePublicConfig:
    """Returns public Firebase Web SDK initialization config (safe for browser)."""
    configured = is_firebase_configured()
    return FirebasePublicConfig(
        configured=configured,
        apiKey=config.FIREBASE_API_KEY if configured else "",
        authDomain=config.FIREBASE_AUTH_DOMAIN if configured else "",
        projectId=config.FIREBASE_PROJECT_ID if configured else "",
        storageBucket=config.FIREBASE_STORAGE_BUCKET if configured else "",
        messagingSenderId=config.FIREBASE_MESSAGING_SENDER_ID if configured else "",
        appId=config.FIREBASE_APP_ID if configured else "",
    )


def hash_uid(uid: str) -> str:
    """Returns a non-reversible SHA-256 hash of a Firebase UID (INV-10)."""
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()


def mask_email(email: Optional[str]) -> Optional[str]:
    """Masks email address for UI display (e.g., h***@gmail.com) to reduce PII exposure."""
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def _decode_jwt_payload(id_token: str) -> Dict[str, Any]:
    """Safely decodes the payload segment of a JWT without external JWT dependencies."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid Firebase ID token format.")
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        data = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Malformed Firebase ID token payload.") from exc
    if not isinstance(data, dict):
        raise ValueError("Firebase ID token payload must be a JSON object.")
    return data


def verify_firebase_id_token(id_token: str) -> Dict[str, Any]:
    """
    Verifies a Firebase Auth ID token via JWT claim validation and Google's
    official Identity Toolkit `accounts:lookup` verification endpoint.
    Never logs raw tokens, emails, or display names (INV-10).
    """
    if not is_firebase_configured():
        raise RuntimeError("Google Firebase is not configured on the server.")

    claims = _decode_jwt_payload(id_token)
    now = int(time.time())
    exp = int(claims.get("exp", 0))
    if exp <= now:
        raise ValueError("Firebase ID token has expired.")

    if claims.get("aud") != config.FIREBASE_PROJECT_ID:
        raise ValueError("Firebase ID token audience does not match configured project.")

    expected_iss = f"https://securetoken.google.com/{config.FIREBASE_PROJECT_ID}"
    if claims.get("iss") != expected_iss:
        raise ValueError("Firebase ID token issuer mismatch.")

    url = f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={config.FIREBASE_API_KEY}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={"idToken": id_token})
            if resp.status_code != 200:
                raise ValueError("Firebase rejected the provided ID token.")
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise ValueError("Could not reach Firebase authentication verification service.") from exc

    users = payload.get("users", [])
    if not users:
        raise ValueError("No verified Firebase user found for the provided token.")

    user_info = users[0]
    uid = user_info.get("localId")
    if not uid:
        raise ValueError("Verified Firebase response missing localId.")

    if user_info.get("disabled"):
        raise ValueError("This user account has been disabled.")

    providers = [p.get("providerId") for p in user_info.get("providerUserInfo", []) if p.get("providerId")]
    provider_label = "google" if "google.com" in providers else "firebase"

    return {
        "uid_hash": hash_uid(uid),
        "display_name": user_info.get("displayName") or None,
        "email_masked": mask_email(user_info.get("email")),
        "photo_url": user_info.get("photoUrl") or None,
        "provider": provider_label,
    }


def _sign_session_payload(session_id: str, expires_at: int) -> str:
    message = f"{session_id}.{expires_at}".encode("utf-8")
    sig = hmac.new(_get_signing_secret(), message, hashlib.sha256).hexdigest()
    return f"{session_id}.{expires_at}.{sig}"


def create_session_token(
    verified_user: Dict[str, Any],
    consent_version: str = "v1.0-2026",
) -> Tuple[str, AuthUser]:
    """Creates an HMAC-signed session cookie value and stores session metadata locally."""
    session_id = uuid.uuid4().hex
    now = int(time.time())
    expires_at = now + config.SESSION_TTL_SECONDS

    auth_user = AuthUser(
        uid_hash=verified_user["uid_hash"],
        display_name=verified_user.get("display_name"),
        email_masked=verified_user.get("email_masked"),
        photo_url=verified_user.get("photo_url"),
        provider=verified_user.get("provider", "firebase"),
        consent_version=consent_version,
        expires_at=expires_at,
    )

    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_path = config.SESSIONS_DIR / f"{session_id}.json"
    with open(session_path, "w", encoding="utf-8") as f:
        f.write(auth_user.model_dump_json(indent=2))

    cookie_value = _sign_session_payload(session_id, expires_at)
    logger.info("Authenticated session issued (provider=%s, consent=%s).", auth_user.provider, consent_version)
    return cookie_value, auth_user


def _parse_and_verify_cookie(cookie_val: Optional[str]) -> Optional[Tuple[str, int]]:
    if not cookie_val:
        return None
    parts = cookie_val.split(".")
    if len(parts) != 3:
        return None
    session_id, expires_str, provided_sig = parts
    if not session_id.isalnum() or len(session_id) != 32:
        return None
    try:
        expires_at = int(expires_str)
    except ValueError:
        return None

    expected_token = _sign_session_payload(session_id, expires_at)
    expected_sig = expected_token.split(".")[2]
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None

    if expires_at <= int(time.time()):
        # Cleanup expired session file
        session_path = config.SESSIONS_DIR / f"{session_id}.json"
        if session_path.exists():
            session_path.unlink(missing_ok=True)
        return None

    return session_id, expires_at


def verify_session_cookie(cookie_val: Optional[str]) -> Optional[AuthUser]:
    """Validates the signed session cookie and returns the AuthUser if active."""
    parsed = _parse_and_verify_cookie(cookie_val)
    if not parsed:
        return None
    session_id, _ = parsed
    session_path = config.SESSIONS_DIR / f"{session_id}.json"
    if not session_path.exists():
        return None
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AuthUser(**data)
    except Exception:
        return None


def revoke_session_cookie(cookie_val: Optional[str]) -> bool:
    """Revokes and deletes the server-side session file if present."""
    parsed = _parse_and_verify_cookie(cookie_val)
    if not parsed:
        return False
    session_id, _ = parsed
    session_path = config.SESSIONS_DIR / f"{session_id}.json"
    if session_path.exists():
        session_path.unlink(missing_ok=True)
        logger.info("Authenticated session revoked.")
        return True
    return False
