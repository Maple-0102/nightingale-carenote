"""Dependency-free HTTP API and static server for the Care Note prototype."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import logging
import os
import re
import ssl

from carenote.auth import issue_token, verify_token
from carenote.db import Database
from carenote.errors import CareNoteError, ForbiddenError, ValidationError
from carenote.service import CareNoteService


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "static"
DEFAULT_DB = PROJECT_ROOT / "data" / "carenote.db"
LOGGER = logging.getLogger("nightingale.carenote")
DEFAULT_SESSION_SECRET = "demo-only-change-me"
INSECURE_SESSION_SECRETS = {
    "",
    DEFAULT_SESSION_SECRET,
    "local-demo-secret-change-me",
    "replace-with-a-random-secret",
}


def validate_runtime_config():
    demo_mode = os.getenv("DEMO_MODE", "1")
    if demo_mode not in {"0", "1"}:
        raise RuntimeError("DEMO_MODE must be 0 or 1")
    secret = os.getenv("CARENOTE_SESSION_SECRET", DEFAULT_SESSION_SECRET)
    if demo_mode != "1" and secret in INSECURE_SESSION_SECRETS:
        raise RuntimeError("CARENOTE_SESSION_SECRET must be set to a non-default value outside demo mode")


class CareNoteHandler(SimpleHTTPRequestHandler):
    service = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        super().end_headers()

    def _json(self, status, data):
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("Invalid Content-Length")
        if size > 1_000_000:
            raise ValidationError("Request body exceeds 1 MB")
        if size == 0:
            return {}
        try:
            return json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError("Request body must be valid JSON")

    def _actor_id(self):
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise ForbiddenError("A signed Bearer session is required")
        return verify_token(
            authorization[7:].strip(),
            os.getenv("CARENOTE_SESSION_SECRET", DEFAULT_SESSION_SECRET),
        )

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        unauthenticated = {"/api/health", "/api/demo/session"}
        actor_id = self._actor_id() if path.startswith("/api/") and path not in unauthenticated else None

        if method == "GET" and path in {"/health", "/api/health"}:
            return 200, {"status": "ok", "service": "nightingale-carenote"}
        if method == "POST" and path == "/api/demo/session":
            if os.getenv("DEMO_MODE", "1") != "1":
                raise ForbiddenError("Demo role switching is disabled")
            requested = self._body().get("actor_id")
            actor = self.service.get_actor(requested)
            token = issue_token(
                actor["id"],
                os.getenv("CARENOTE_SESSION_SECRET", DEFAULT_SESSION_SECRET),
                ttl_seconds=3600,
            )
            return 201, {"token": token, "actor": actor, "demo_only": True}
        if method == "GET" and path == "/api/care-note":
            return 200, self.service.get_care_note(actor_id, self._required_query(query, "patient_id"))
        if method == "GET" and path == "/api/audit":
            return 200, self.service.audit_for_patient(actor_id, self._required_query(query, "patient_id"))
        if method == "GET" and path == "/api/decay-preview":
            return 200, self.service.decay_preview(actor_id, self._required_query(query, "patient_id"))
        if method == "GET" and path == "/api/care-note/as-of":
            return 200, self.service.care_note_as_of(
                actor_id, self._required_query(query, "patient_id"), self._required_query(query, "at")
            )
        if method == "GET" and path == "/api/previsit-brief":
            return 200, self.service.get_previsit_brief(actor_id, self._required_query(query, "patient_id"))
        if method == "GET" and path == "/api/patient-access-report":
            return 200, self.service.patient_access_report(
                actor_id, self._required_query(query, "patient_id")
            )
        if method == "GET" and path == "/api/audit/verify":
            return 200, self.service.verify_audit_chain(
                actor_id, self._required_query(query, "patient_id")
            )
        if method == "POST" and path == "/api/security/sandbox":
            return 200, self.service.run_security_sandbox(
                actor_id, self._body().get("patient_id")
            )

        match = re.fullmatch(r"/api/entries/([^/]+)", path)
        if match and method == "GET":
            return 200, self.service.get_entry(actor_id, match.group(1))
        if match and method == "PATCH":
            body = self._body()
            return 200, self.service.edit_entry(
                actor_id,
                match.group(1),
                body.get("content"),
                body.get("expected_version"),
                body.get("reason", "edited"),
            )

        match = re.fullmatch(r"/api/entries/([^/]+)/versions", path)
        if match and method == "GET":
            return 200, self.service.list_versions(actor_id, match.group(1))

        match = re.fullmatch(r"/api/entries/([^/]+)/revert", path)
        if match and method == "POST":
            body = self._body()
            return 200, self.service.revert_entry(
                actor_id, match.group(1), body.get("target_version"), body.get("expected_version")
            )

        match = re.fullmatch(r"/api/entries/([^/]+)/verify", path)
        if match and method == "POST":
            body = self._body()
            return 201, self.service.verify_entry(
                actor_id,
                match.group(1),
                body.get("expected_version"),
                body.get("outcome", "confirmed"),
            )

        match = re.fullmatch(r"/api/entries/([^/]+)/teach-back", path)
        if match and method == "POST":
            return 201, self.service.submit_teach_back(
                actor_id, match.group(1), self._body().get("response_text")
            )

        match = re.fullmatch(r"/api/teach-backs/([^/]+)", path)
        if match and method == "GET":
            return 200, self.service.get_teach_back(actor_id, match.group(1))

        match = re.fullmatch(r"/api/teach-backs/([^/]+)/decision", path)
        if match and method == "POST":
            return 200, self.service.decide_teach_back(
                actor_id, match.group(1), self._body().get("decision")
            )

        match = re.fullmatch(r"/api/entries/([^/]+)/comments", path)
        if match and method == "POST":
            body = self._body()
            return 201, self.service.add_comment(
                actor_id, match.group(1), body.get("body"), body.get("assignee_id")
            )

        match = re.fullmatch(r"/api/comments/([^/]+)", path)
        if match and method == "PATCH":
            body = self._body()
            return 200, self.service.resolve_comment(actor_id, match.group(1), body.get("resolved", True))

        match = re.fullmatch(r"/api/patients/([^/]+)/entries", path)
        if match and method == "POST":
            return 201, self.service.create_entry(actor_id, match.group(1), self._body())

        match = re.fullmatch(r"/api/patients/([^/]+)/highlights", path)
        if match and method == "POST":
            body = self._body()
            entry = self.service.get_entry(actor_id, body.get("entry_id"))
            if entry["patient_id"] != match.group(1):
                raise ValidationError("entry_id does not belong to this patient")
            return 201, self.service.create_highlight(
                actor_id,
                body.get("entry_id"),
                body.get("start_offset"),
                body.get("end_offset"),
                body.get("risk_reason") or "Manually highlighted for review",
                body.get("status", "suggested"),
            )

        match = re.fullmatch(r"/api/highlights/([^/]+)/source", path)
        if match and method == "GET":
            return 200, self.service.resolve_highlight(actor_id, match.group(1))

        match = re.fullmatch(r"/api/highlights/([^/]+)/passport", path)
        if match and method == "GET":
            return 200, self.service.get_highlight_passport(actor_id, match.group(1))

        match = re.fullmatch(r"/api/highlights/([^/]+)/decision", path)
        if match and method == "POST":
            body = self._body()
            return 200, self.service.decide_highlight(
                actor_id, match.group(1), body.get("decision"), body.get("evidence_token")
            )

        match = re.fullmatch(r"/api/patients/([^/]+)/conflicts/([^/]+)/passport", path)
        if match and method == "GET":
            return 200, self.service.get_conflict_passport(actor_id, match.group(1), match.group(2))

        match = re.fullmatch(r"/api/patients/([^/]+)/conflicts/([^/]+)/decision", path)
        if match and method == "POST":
            body = self._body()
            return 200, self.service.decide_conflict(
                actor_id,
                match.group(1),
                match.group(2),
                body.get("decision"),
                body.get("evidence_token"),
            )

        match = re.fullmatch(r"/api/tasks/([^/]+)", path)
        if match and method == "PATCH":
            return 200, self.service.update_task_status(
                actor_id, match.group(1), self._body().get("status")
            )

        match = re.fullmatch(r"/api/patients/([^/]+)/scribe", path)
        if match and method == "POST":
            body = self._body()
            return 201, self.service.scribe(
                actor_id, match.group(1), body.get("raw_text") or "", body.get("interaction_type") or "consult"
            )

        match = re.fullmatch(r"/api/patients/([^/]+)/redaction-preview", path)
        if match and method == "POST":
            return 200, self.service.preview_redaction(
                actor_id, match.group(1), self._body().get("raw_text") or ""
            )

        return 404, {"error": {"code": "not_found", "message": "API route not found", "details": {}}}

    @staticmethod
    def _required_query(query, key):
        values = query.get(key) or []
        if not values or not values[0]:
            raise ValidationError("{} is required".format(key))
        return values[0]

    def _api(self, method):
        try:
            status, data = self._dispatch(method)
            self._json(status, data)
        except CareNoteError as exc:
            self._json(
                exc.status_code,
                {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            )
        except Exception:
            # Avoid leaking paths, SQL, or clinical content in production responses.
            LOGGER.exception("Unhandled API error method=%s path=%s", method, urlparse(self.path).path)
            self._json(500, {"error": {"code": "internal_error", "message": "Unexpected server error", "details": {}}})

    def do_GET(self):
        if urlparse(self.path).path.startswith("/api/") or self.path == "/health":
            self._api("GET")
            return
        super().do_GET()

    def do_POST(self):
        self._api("POST")

    def do_PATCH(self):
        self._api("PATCH")

    def log_message(self, fmt, *args):
        # The default log contains only method/path/status. Request bodies are never logged.
        super().log_message(fmt, *args)


def create_server(host="127.0.0.1", port=8000, db_path=None):
    validate_runtime_config()
    db = Database(db_path or os.getenv("CARENOTE_DB", str(DEFAULT_DB)))
    db.initialize()
    db.seed_demo()
    CareNoteHandler.service = CareNoteService(
        db, evidence_secret=os.getenv("CARENOTE_SESSION_SECRET", DEFAULT_SESSION_SECRET)
    )
    server = ThreadingHTTPServer((host, port), CareNoteHandler)
    cert_path = os.getenv("TLS_CERT")
    key_path = os.getenv("TLS_KEY")
    if cert_path or key_path:
        if not cert_path or not key_path:
            raise RuntimeError("TLS_CERT and TLS_KEY must be supplied together")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    server = create_server(host, port)
    scheme = "https" if os.getenv("TLS_CERT") else "http"
    print(f"Nightingale Care Note running at {scheme}://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
