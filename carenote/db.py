"""SQLite schema and deterministic synthetic demo data."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3

from .audit_chain import GENESIS_HASH, event_hash


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clinics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    clinic_id TEXT NOT NULL REFERENCES clinics(id),
    display_name TEXT NOT NULL,
    date_of_birth TEXT,
    external_ref TEXT NOT NULL,
    pronouns TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    clinic_id TEXT REFERENCES clinics(id),
    patient_id TEXT REFERENCES patients(id),
    role TEXT NOT NULL CHECK(role IN ('patient','staff','clinician','admin')),
    display_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    clinic_id TEXT NOT NULL REFERENCES clinics(id),
    author_role TEXT NOT NULL CHECK(author_role IN ('patient','staff','clinician','system')),
    author_id TEXT,
    type TEXT NOT NULL,
    section_key TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('patient','internal')),
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    provenance_pointer TEXT,
    risk_level TEXT NOT NULL DEFAULT 'low',
    entities_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_patient_time
ON entries(patient_id, created_at DESC);

CREATE TABLE IF NOT EXISTS entry_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL REFERENCES entries(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    UNIQUE(entry_id, version)
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL REFERENCES entries(id),
    author_id TEXT NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
    assignee_id TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    source_entry_id TEXT REFERENCES entries(id),
    title TEXT NOT NULL,
    assignee_id TEXT REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')),
    completed_by TEXT REFERENCES users(id),
    completed_at TEXT,
    due_at TEXT,
    risk_level TEXT NOT NULL DEFAULT 'medium',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_patient_status_due
ON tasks(patient_id, status, due_at);

CREATE TABLE IF NOT EXISTS highlights (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    entry_version INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    quote TEXT NOT NULL,
    risk_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested' CHECK(status IN ('suggested','accepted','rejected')),
    score REAL NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_by TEXT REFERENCES users(id),
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS entry_verifications (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL REFERENCES entries(id),
    entry_version INTEGER NOT NULL,
    verified_by TEXT NOT NULL REFERENCES users(id),
    outcome TEXT NOT NULL CHECK(outcome IN ('confirmed','needs_review')),
    verified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entry_verifications_entry_time
ON entry_verifications(entry_id, verified_at DESC);

CREATE TABLE IF NOT EXISTS consistency_decisions (
    conflict_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    rule_id TEXT NOT NULL,
    left_entry_id TEXT NOT NULL REFERENCES entries(id),
    left_entry_version INTEGER NOT NULL,
    right_entry_id TEXT NOT NULL REFERENCES entries(id),
    right_entry_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('acknowledged','dismissed')),
    decided_by TEXT NOT NULL REFERENCES users(id),
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS importance_signals (
    clinic_id TEXT NOT NULL REFERENCES clinics(id),
    feature TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(clinic_id, feature)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prev_hash TEXT,
    event_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_clinic_id
ON audit_log(clinic_id, id);

CREATE TABLE IF NOT EXISTS teach_back_attempts (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    instruction_entry_id TEXT NOT NULL REFERENCES entries(id),
    instruction_version INTEGER NOT NULL,
    patient_actor_id TEXT NOT NULL REFERENCES users(id),
    response_text TEXT NOT NULL,
    matched_json TEXT NOT NULL DEFAULT '[]',
    missing_json TEXT NOT NULL DEFAULT '[]',
    coverage REAL NOT NULL,
    screening_result TEXT NOT NULL CHECK(screening_result IN ('ready_for_review','possible_gap')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','needs_clarification')),
    created_at TEXT NOT NULL,
    decided_by TEXT REFERENCES users(id),
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_teach_back_patient_status
ON teach_back_attempts(patient_id, status, created_at DESC);
"""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Database:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def initialize(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(highlights)")}
            if "decided_by" not in columns:
                conn.execute("ALTER TABLE highlights ADD COLUMN decided_by TEXT")
            if "decided_at" not in columns:
                conn.execute("ALTER TABLE highlights ADD COLUMN decided_at TEXT")
            task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "completed_by" not in task_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN completed_by TEXT")
            if "completed_at" not in task_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
            audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")}
            if "prev_hash" not in audit_columns:
                conn.execute("ALTER TABLE audit_log ADD COLUMN prev_hash TEXT")
            if "event_hash" not in audit_columns:
                conn.execute("ALTER TABLE audit_log ADD COLUMN event_hash TEXT")
            self._backfill_audit_hashes(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_patient_status_due "
                "ON tasks(patient_id, status, due_at)"
            )
            conn.execute("PRAGMA optimize")
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)

    @staticmethod
    def _backfill_audit_hashes(conn):
        clinics = [row["clinic_id"] for row in conn.execute("SELECT DISTINCT clinic_id FROM audit_log")]
        for clinic_id in clinics:
            previous = GENESIS_HASH
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE clinic_id=? ORDER BY id", (clinic_id,)
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                if not row.get("prev_hash") and not row.get("event_hash"):
                    digest = event_hash(row, previous)
                    conn.execute(
                        "UPDATE audit_log SET prev_hash=?,event_hash=? WHERE id=?",
                        (previous, digest, row["id"]),
                    )
                    previous = digest
                else:
                    # Existing hashes are never rewritten on startup: corruption must remain detectable.
                    previous = row.get("event_hash") or previous

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query_one(self, sql, params=()):
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def query_all(self, sql, params=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def seed_demo(self):
        now = utc_now()
        with self.transaction() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO clinics(id,name) VALUES(?,?)",
                [("clinic-acacia", "Acacia Family Clinic"), ("clinic-birch", "Birch Medical")],
            )
            conn.executemany(
                """INSERT OR IGNORE INTO patients
                   (id,clinic_id,display_name,date_of_birth,external_ref,pronouns,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                [
                    ("patient-maya", "clinic-acacia", "Maya Tan", "1984-02-14", "NTG-2048", "she/her", now),
                    ("patient-other", "clinic-birch", "Alex Noor", "1978-06-02", "BRH-1082", "they/them", now),
                ],
            )
            conn.executemany(
                """INSERT OR IGNORE INTO users
                   (id,clinic_id,patient_id,role,display_name,email) VALUES(?,?,?,?,?,?)""",
                [
                    ("clinician-lim", "clinic-acacia", None, "clinician", "Dr Adrian Lim", "adrian@example.test"),
                    ("staff-jia", "clinic-acacia", None, "staff", "Jia Chen", "jia@example.test"),
                    ("admin-acacia", "clinic-acacia", None, "admin", "Nora Admin", "admin@example.test"),
                    ("patient-maya-user", "clinic-acacia", "patient-maya", "patient", "Maya Tan", "maya@example.test"),
                    ("staff-birch", "clinic-birch", None, "staff", "Birch Staff", "birch@example.test"),
                ],
            )

            entries = [
                (
                    "entry-ai-session",
                    "patient-maya",
                    "clinic-acacia",
                    "system",
                    None,
                    "ai_patient_session_summary",
                    "patient_session",
                    "internal",
                    "Patient reports a dry cough that is worse at night, now affecting sleep. No fever. She asks whether the new inhaler could be contributing.",
                    1,
                    "session_8821",
                    "high",
                    json.dumps(["chief_complaint:cough", "duration:3_weeks", "medication:inhaler"]),
                    "active",
                    "2026-08-25T08:36:00Z",
                    "2026-08-25T08:36:00Z",
                ),
                (
                    "entry-staff-spirometry",
                    "patient-maya",
                    "clinic-acacia",
                    "staff",
                    "staff-jia",
                    "staff_note",
                    "coordination",
                    "internal",
                    "Spirometry report received from respiratory lab. Assigned to @DrLim for review before today's consult.",
                    2,
                    "lab_document_spirometry_20260825",
                    "medium",
                    json.dumps(["test:spirometry", "task:review"]),
                    "active",
                    "2026-08-25T09:04:00Z",
                    "2026-08-25T09:10:00Z",
                ),
                (
                    "entry-allergy",
                    "patient-maya",
                    "clinic-acacia",
                    "clinician",
                    "clinician-lim",
                    "clinician_note",
                    "allergies",
                    "internal",
                    "Confirmed penicillin allergy: widespread urticaria after amoxicillin in 2019. No airway involvement.",
                    1,
                    "consult_20250415#allergy",
                    "critical",
                    json.dumps(["allergy:penicillin", "reaction:urticaria"]),
                    "active",
                    "2025-04-15T14:22:00Z",
                    "2025-04-15T14:22:00Z",
                ),
                (
                    "entry-patient-instructions",
                    "patient-maya",
                    "clinic-acacia",
                    "clinician",
                    "clinician-lim",
                    "patient_instruction",
                    "instructions",
                    "patient",
                    "Please bring your inhaler to the appointment. Seek urgent care for severe breathlessness, blue lips, or difficulty speaking.",
                    1,
                    "consult_20260825#instructions",
                    "medium",
                    json.dumps(["instruction:bring_inhaler", "safety_net:breathlessness"]),
                    "active",
                    "2026-08-25T09:20:00Z",
                    "2026-08-25T09:20:00Z",
                ),
                (
                    "entry-annual-review",
                    "patient-maya",
                    "clinic-acacia",
                    "clinician",
                    "clinician-lim",
                    "clinician_note",
                    "preventive_care",
                    "internal",
                    "Routine annual review completed. Preventive screening plan discussed; no new concerns recorded.",
                    1,
                    "consult_20260220#preventive",
                    "low",
                    json.dumps(["care_plan:preventive_screening"]),
                    "active",
                    "2026-02-20T10:15:00Z",
                    "2026-02-20T10:15:00Z",
                ),
                (
                    "entry-legacy-referral",
                    "patient-maya",
                    "clinic-acacia",
                    "staff",
                    "staff-jia",
                    "staff_note",
                    "coordination",
                    "internal",
                    "Legacy referral coordination completed and closed after records were received.",
                    1,
                    "referral_20240510#closed",
                    "low",
                    json.dumps(["workflow:referral_closed"]),
                    "active",
                    "2024-05-10T11:30:00Z",
                    "2024-05-10T11:30:00Z",
                ),
                (
                    "entry-external-amoxicillin",
                    "patient-maya",
                    "clinic-acacia",
                    "staff",
                    "staff-jia",
                    "staff_note",
                    "medication_reconciliation",
                    "internal",
                    "External medication reconciliation lists amoxicillin as active; clinician confirmation is required before use.",
                    1,
                    "external_med_rec_20260825",
                    "high",
                    json.dumps(["medication:amoxicillin", "workflow:medication_reconciliation"]),
                    "active",
                    "2026-08-25T09:16:00Z",
                    "2026-08-25T09:16:00Z",
                ),
            ]
            conn.executemany(
                """INSERT OR IGNORE INTO entries
                   (id,patient_id,clinic_id,author_role,author_id,type,section_key,visibility,
                    content,version,provenance_pointer,risk_level,entities_json,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                entries,
            )
            for entry in entries:
                if entry[0] == "entry-staff-spirometry":
                    conn.execute(
                        """INSERT OR IGNORE INTO entry_versions
                           (entry_id,version,content,changed_by,changed_at,change_reason)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            entry[0],
                            1,
                            "Spirometry report received from respiratory lab.",
                            entry[4] or "system",
                            "2026-08-25T09:04:00Z",
                            "created",
                        ),
                    )
                conn.execute(
                    """INSERT OR IGNORE INTO entry_versions
                       (entry_id,version,content,changed_by,changed_at,change_reason)
                       VALUES(?,?,?,?,?,?)""",
                    (entry[0], entry[9], entry[8], entry[4] or "system", entry[15], "seeded synthetic record"),
                )
            conn.execute(
                """INSERT OR IGNORE INTO comments(id,entry_id,author_id,body,status,assignee_id,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    "comment-spirometry",
                    "entry-staff-spirometry",
                    "clinician-lim",
                    "I'll review this with Maya during the visit.",
                    "open",
                    "clinician-lim",
                    "2026-08-25T09:12:00Z",
                ),
            )
            conn.execute(
                """INSERT OR IGNORE INTO tasks(id,patient_id,source_entry_id,title,assignee_id,status,due_at,risk_level,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    "task-spirometry",
                    "patient-maya",
                    "entry-staff-spirometry",
                    "Review spirometry result",
                    "clinician-lim",
                    "open",
                    "2026-08-25T17:00:00Z",
                    "high",
                    "2026-08-25T09:04:00Z",
                ),
            )
            conn.executemany(
                """INSERT OR IGNORE INTO entry_verifications
                   (id,entry_id,entry_version,verified_by,outcome,verified_at)
                   VALUES(?,?,?,?,?,?)""",
                [
                    (
                        "verification-allergy-20260825",
                        "entry-allergy",
                        1,
                        "clinician-lim",
                        "confirmed",
                        "2026-08-25T09:13:00Z",
                    ),
                    (
                        "verification-instructions-20260825",
                        "entry-patient-instructions",
                        1,
                        "clinician-lim",
                        "confirmed",
                        "2026-08-25T09:20:00Z",
                    ),
                ],
            )
            cough = entries[0][8]
            quote = "dry cough that is worse at night"
            start = cough.index(quote)
            conn.execute(
                """INSERT OR IGNORE INTO highlights
                   (id,patient_id,entry_id,entry_version,start_offset,end_offset,quote,risk_reason,status,score,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "highlight-cough",
                    "patient-maya",
                    "entry-ai-session",
                    1,
                    start,
                    start + len(quote),
                    quote,
                    "Persistent symptom affecting sleep; review during today's consult.",
                    "suggested",
                    7.5,
                    "system",
                    "2026-08-25T08:36:02Z",
                ),
            )
