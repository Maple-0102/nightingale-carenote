"""Small HMAC session token used to demonstrate authenticated server-side RBAC."""

import base64
import hashlib
import hmac
import json
import time

from .errors import ForbiddenError


def _b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_token(actor_id, secret, ttl_seconds=3600):
    payload = {"sub": actor_id, "exp": int(time.time()) + int(ttl_seconds)}
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return encoded + "." + signature


def verify_token(token, secret):
    try:
        encoded, supplied = token.split(".", 1)
        expected = _b64encode(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ForbiddenError("Invalid session signature")
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        if int(payload["exp"]) < int(time.time()):
            raise ForbiddenError("Session has expired")
        return payload["sub"]
    except ForbiddenError:
        raise
    except Exception:
        raise ForbiddenError("Invalid session token")


def issue_evidence_token(payload, secret, ttl_seconds=600):
    envelope = {"evidence": payload, "exp": int(time.time()) + int(ttl_seconds)}
    encoded = _b64encode(json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return encoded + "." + signature


def verify_evidence_token(token, secret):
    try:
        encoded, supplied = token.split(".", 1)
        expected = _b64encode(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ForbiddenError("Invalid evidence signature")
        envelope = json.loads(_b64decode(encoded).decode("utf-8"))
        if int(envelope["exp"]) < int(time.time()):
            raise ForbiddenError("Evidence review has expired; reopen the evidence")
        return envelope["evidence"]
    except ForbiddenError:
        raise
    except Exception:
        raise ForbiddenError("Invalid evidence token")
