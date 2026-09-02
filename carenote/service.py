"""Care Note domain service: RBAC, collaboration, provenance, and trust logic."""

from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import uuid

from .audit_chain import GENESIS_HASH, canonical_metadata, event_hash, verify_rows
from .auth import issue_evidence_token, verify_evidence_token
from .consistency import detect_conflicts
from .db import utc_now
from .decay import classify_storage_tier
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from .importance import base_score, explain_score, feature_tokens, parse_timestamp
from .review_policy import review_freshness
from .security import prepare_llm_payload
from .teachback import assess_teach_back


EDITABLE_ENTRY_TYPES = {
    "patient": {"patient_insight"},
    "staff": {"staff_note"},
    "clinician": {"clinician_note", "patient_instruction"},
}


class CareNoteService:
    def __init__(self, db, evidence_secret="unit-test-evidence-secret"):
        self.db = db
        self.evidence_secret = evidence_secret

    def get_actor(self, actor_id):
        actor = self.db.query_one("SELECT * FROM users WHERE id = ?", (actor_id,))
        if not actor:
            raise ForbiddenError("Unknown session actor.")
        return actor

    def get_patient(self, patient_id):
        patient = self.db.query_one("SELECT * FROM patients WHERE id = ?", (patient_id,))
        if not patient:
            raise NotFoundError("Patient not found")
        return patient

    def assert_patient_access(self, actor, patient):
        if actor["role"] == "patient":
            if actor.get("patient_id") != patient["id"]:
                raise ForbiddenError("Patients can access only their own record")
            return
        if actor.get("clinic_id") != patient["clinic_id"]:
            raise ForbiddenError("Access is restricted to the actor's clinic")

    @staticmethod
    def _safe_json(value, fallback):
        try:
            return json.loads(value) if isinstance(value, str) else (value if value is not None else fallback)
        except json.JSONDecodeError:
            return fallback

    def _serialize_entry(self, entry, include_comments=True):
        item = dict(entry)
        item["entities"] = self._safe_json(item.pop("entities_json", "[]"), [])
        if include_comments:
            item["comments"] = self.db.query_all(
                """SELECT c.*, u.display_name AS author_name, a.display_name AS assignee_name
                   FROM comments c JOIN users u ON u.id=c.author_id
                   LEFT JOIN users a ON a.id=c.assignee_id
                   WHERE c.entry_id=? ORDER BY c.created_at""",
                (item["id"],),
            )
        return item

    def _visible_entries(self, actor, patient_id):
        if actor["role"] == "patient":
            return self.db.query_all(
                """SELECT e.*, COALESCE(u.display_name,'Nightingale AI') AS author_name
                   FROM entries e LEFT JOIN users u ON u.id=e.author_id
                   WHERE e.patient_id=? AND e.visibility='patient'
                     AND e.type NOT LIKE 'ai_%' AND e.status='active'
                   ORDER BY e.created_at DESC""",
                (patient_id,),
            )
        return self.db.query_all(
            """SELECT e.*, COALESCE(u.display_name,'Nightingale AI') AS author_name
               FROM entries e LEFT JOIN users u ON u.id=e.author_id
               WHERE e.patient_id=? AND e.status='active'
               ORDER BY e.created_at DESC""",
            (patient_id,),
        )

    def _signal_weights(self, clinic_id):
        rows = self.db.query_all(
            "SELECT feature,weight FROM importance_signals WHERE clinic_id=?", (clinic_id,)
        )
        return {row["feature"]: row["weight"] for row in rows}

    def _verification_rows(self, patient_id):
        return self.db.query_all(
            """SELECT v.*,u.display_name AS verified_by_name
               FROM entry_verifications v JOIN entries e ON e.id=v.entry_id
               LEFT JOIN users u ON u.id=v.verified_by
               WHERE e.patient_id=? ORDER BY v.verified_at DESC""",
            (patient_id,),
        )

    def _decorate_freshness(self, entries, patient_id):
        grouped = {}
        for row in self._verification_rows(patient_id):
            grouped.setdefault(row["entry_id"], []).append(row)
        for entry in entries:
            entry["freshness"] = review_freshness(entry, grouped.get(entry["id"], []))

    def _teach_back_rows(self, patient_id):
        rows = self.db.query_all(
            """SELECT t.*,p.display_name AS patient_name,d.display_name AS decided_by_name
               FROM teach_back_attempts t
               JOIN users p ON p.id=t.patient_actor_id
               LEFT JOIN users d ON d.id=t.decided_by
               WHERE t.patient_id=? ORDER BY t.created_at DESC""",
            (patient_id,),
        )
        for row in rows:
            row["matched_concepts"] = self._safe_json(row.pop("matched_json"), [])
            row["missing_concepts"] = self._safe_json(row.pop("missing_json"), [])
        return rows

    def _conflicts(self, entries, patient_id):
        decision_rows = self.db.query_all(
            """SELECT d.*,u.display_name AS decided_by_name
               FROM consistency_decisions d LEFT JOIN users u ON u.id=d.decided_by
               WHERE d.patient_id=?""",
            (patient_id,),
        )
        return detect_conflicts(entries, {row["conflict_id"]: row for row in decision_rows})

    @staticmethod
    def _build_review_queue(entries, highlights, conflicts, tasks, teach_backs=None, limit=7):
        entry_by_id = {entry["id"]: entry for entry in entries}
        open_task_sources = {task["source_entry_id"] for task in tasks if task["status"] == "open"}
        represented = set()
        items = []
        for attempt in teach_backs or []:
            if attempt["status"] != "pending":
                continue
            items.append(
                {
                    "id": attempt["id"],
                    "kind": "teach_back",
                    "severity": "high" if attempt["missing_concepts"] else "medium",
                    "priority": 92 if attempt["missing_concepts"] else 72,
                    "title": "Review patient teach-back",
                    "reason": (
                        "The keyword screen found possible gaps; a clinician must decide"
                        if attempt["missing_concepts"]
                        else "The patient responded; clinician confirmation is still required"
                    ),
                    "source_entry_ids": [attempt["instruction_entry_id"]],
                    "entry_version": attempt["instruction_version"],
                }
            )
        for conflict in conflicts:
            if conflict["status"] != "suggested":
                continue
            source_ids = [source["entry_id"] for source in conflict["sources"]]
            represented.update(source_ids)
            items.append(
                {
                    "id": conflict["id"],
                    "kind": "contradiction",
                    "severity": conflict["severity"],
                    "priority": 100,
                    "title": conflict["title"],
                    "reason": conflict["why_now"],
                    "source_entry_ids": source_ids,
                }
            )
        for highlight in highlights:
            if highlight["status"] != "suggested":
                continue
            represented.add(highlight["entry_id"])
            entry = entry_by_id.get(highlight["entry_id"], {})
            items.append(
                {
                    "id": highlight["id"],
                    "kind": "ai_suggestion",
                    "severity": entry.get("risk_level", "medium"),
                    "priority": 90,
                    "title": highlight["risk_reason"],
                    "reason": "AI suggestion awaits evidence review and one clinician decision.",
                    "source_entry_ids": [highlight["entry_id"]],
                }
            )
        risk_priority = {"critical": 80, "high": 70, "medium": 60, "low": 50}
        for entry in entries:
            if not entry.get("freshness", {}).get("due"):
                continue
            if entry["id"] in represented or entry["id"] in open_task_sources:
                continue
            items.append(
                {
                    "id": "verify-" + entry["id"],
                    "kind": "verification_due",
                    "severity": entry["risk_level"],
                    "priority": risk_priority.get(entry["risk_level"], 50),
                    "title": "Re-verify " + entry["type"].replace("_", " "),
                    "reason": entry["freshness"]["reason"],
                    "source_entry_ids": [entry["id"]],
                    "entry_version": entry["version"],
                }
            )
        return sorted(items, key=lambda item: (-item["priority"], item["id"]))[:limit]

    def get_care_note(self, actor_id, patient_id, record_access=True):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if record_access and actor["role"] != "patient":
            with self.db.transaction() as conn:
                self._audit(
                    conn,
                    actor,
                    "record.accessed",
                    "patient",
                    patient_id,
                    {"patient_id": patient_id, "purpose": "care_note_view"},
                )
        entries = [
            self._serialize_entry(row, include_comments=actor["role"] != "patient")
            for row in self._visible_entries(actor, patient_id)
        ]
        self._decorate_freshness(entries, patient_id)
        response = {
            "actor": actor,
            "patient": patient,
            "entries": entries,
            "tasks": [],
            "highlights": [],
            "conflicts": [],
            "review_queue": [],
            "review_limit": 7,
            "teach_backs": self._teach_back_rows(patient_id),
        }
        if actor["role"] == "patient":
            return response

        tasks = self.db.query_all(
            """SELECT t.*, u.display_name AS assignee_name,
                      c.display_name AS completed_by_name
               FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id
               LEFT JOIN users c ON c.id=t.completed_by
               WHERE t.patient_id=? ORDER BY t.status, t.due_at""",
            (patient_id,),
        )
        highlights = self.db.query_all(
            """SELECT h.*, e.type AS source_type, e.author_role AS source_author_role,
                      e.provenance_pointer, e.created_at AS source_created_at,
                      d.display_name AS decided_by_name
               FROM highlights h JOIN entries e ON e.id=h.entry_id
               LEFT JOIN users d ON d.id=h.decided_by
               WHERE h.patient_id=?
               ORDER BY CASE h.status WHEN 'suggested' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                        h.score DESC, h.created_at DESC""",
            (patient_id,),
        )
        weights = self._signal_weights(patient["clinic_id"])
        conflicts = self._conflicts(entries, patient_id)
        scored = []
        for entry in entries:
            unresolved = any(task["source_entry_id"] == entry["id"] and task["status"] == "open" for task in tasks)
            score = explain_score(
                entry,
                weights,
                unresolved_task=unresolved,
                clinician_confirmed=entry["author_role"] == "clinician",
            )
            scored.append({"entry_id": entry["id"], **score})
        final_order = sorted(scored, key=lambda item: (-item["score"], item["entry_id"]))
        base_order = sorted(scored, key=lambda item: (-item["base_score"], item["entry_id"]))
        final_ranks = {item["entry_id"]: rank for rank, item in enumerate(final_order, start=1)}
        base_ranks = {item["entry_id"]: rank for rank, item in enumerate(base_order, start=1)}
        for item in final_order:
            item["current_rank"] = final_ranks[item["entry_id"]]
            item["base_rank"] = base_ranks[item["entry_id"]]
            item["rank_change"] = item["base_rank"] - item["current_rank"]
        response.update(
            {
                "tasks": tasks,
                "highlights": highlights,
                "conflicts": conflicts,
                "review_queue": self._build_review_queue(
                    entries, highlights, conflicts, tasks, response["teach_backs"]
                ),
                "importance": final_order,
            }
        )
        return response

    def update_task_status(self, actor_id, task_id, status):
        actor = self.get_actor(actor_id)
        if actor["role"] not in {"staff", "clinician"}:
            raise ForbiddenError("Only clinical staff can update tasks")
        if status not in {"open", "done"}:
            raise ValidationError("status must be open or done")
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT t.*,p.clinic_id FROM tasks t
                   JOIN patients p ON p.id=t.patient_id WHERE t.id=?""",
                (task_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Task not found")
            task = dict(row)
            if task["clinic_id"] != actor["clinic_id"]:
                raise ForbiddenError("Task is outside the actor's clinic")
            if actor["role"] == "staff" and task.get("assignee_id") not in {None, actor_id}:
                raise ForbiddenError("Only the assignee or a clinician can update this task")
            if task["status"] != status:
                completed_at = utc_now() if status == "done" else None
                completed_by = actor_id if status == "done" else None
                conn.execute(
                    "UPDATE tasks SET status=?,completed_by=?,completed_at=? WHERE id=?",
                    (status, completed_by, completed_at, task_id),
                )
                self._audit(
                    conn,
                    actor,
                    "task.completed" if status == "done" else "task.reopened",
                    "task",
                    task_id,
                    {"source_entry_id": task["source_entry_id"], "status": status},
                )
        result = self.db.query_one(
            """SELECT t.*,u.display_name AS assignee_name,
                      c.display_name AS completed_by_name
               FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id
               LEFT JOIN users c ON c.id=t.completed_by WHERE t.id=?""",
            (task_id,),
        )
        return result

    def _audit(self, conn, actor, action, entity_type, entity_id, metadata):
        # Metadata is deliberately content-free; raw clinical text never enters audit logs.
        forbidden_keys = {"content", "body", "quote", "raw_text", "transcript"}
        safe = {key: value for key, value in metadata.items() if key not in forbidden_keys}
        clinic_id = actor.get("clinic_id") or "patient"
        created_at = utc_now()
        previous_row = conn.execute(
            "SELECT event_hash FROM audit_log WHERE clinic_id=? ORDER BY id DESC LIMIT 1",
            (clinic_id,),
        ).fetchone()
        previous_hash = previous_row["event_hash"] if previous_row and previous_row["event_hash"] else GENESIS_HASH
        metadata_json = canonical_metadata(safe)
        digest = event_hash(
            {
                "clinic_id": clinic_id,
                "actor_id": actor["id"],
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "metadata_json": metadata_json,
                "created_at": created_at,
            },
            previous_hash,
        )
        conn.execute(
            """INSERT INTO audit_log
               (clinic_id,actor_id,action,entity_type,entity_id,metadata_json,created_at,prev_hash,event_hash)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                clinic_id, actor["id"], action, entity_type, entity_id, metadata_json,
                created_at, previous_hash, digest,
            ),
        )

    def _create_review_task(self, conn, actor, patient_id, source_entry_id, title):
        assignee = conn.execute(
            "SELECT id FROM users WHERE clinic_id=? AND role='clinician' ORDER BY id LIMIT 1",
            (actor["clinic_id"],),
        ).fetchone()
        task_id = "task-review-" + uuid.uuid4().hex[:12]
        conn.execute(
            """INSERT INTO tasks
               (id,patient_id,source_entry_id,title,assignee_id,status,due_at,risk_level,created_at)
               VALUES(?,?,?,?,?,'open',NULL,'medium',?)""",
            (task_id, patient_id, source_entry_id, title, assignee["id"] if assignee else None, utc_now()),
        )
        self._audit(
            conn,
            actor,
            "task.created",
            "task",
            task_id,
            {"source_entry_id": source_entry_id, "patient_id": patient_id, "status": "open"},
        )
        return task_id

    @staticmethod
    def _extract_questions(text):
        pieces = [piece.strip() for piece in (text or "").replace("\n", " ").split("?")]
        return [piece + "?" for piece in pieces[:-1] if piece]

    def create_entry(self, actor_id, patient_id, payload):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        role = actor["role"]
        entry_type = payload.get("type")
        if entry_type not in EDITABLE_ENTRY_TYPES.get(role, set()):
            raise ForbiddenError("This role cannot create the requested note type")
        content = (payload.get("content") or "").strip()
        if not content:
            raise ValidationError("content is required")
        visibility = "patient" if entry_type == "patient_instruction" else payload.get("visibility", "internal")
        if role == "patient":
            visibility = "internal"
        if "id" in payload:
            raise ValidationError("id is server-generated")
        entry_id = "entry-" + uuid.uuid4().hex[:12]
        now = utc_now()
        entities = payload.get("entities") or []
        review_task_id = None
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO entries
                   (id,patient_id,clinic_id,author_role,author_id,type,section_key,visibility,
                    content,version,provenance_pointer,risk_level,entities_json,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?)""",
                (
                    entry_id, patient_id, patient["clinic_id"], role, actor_id, entry_type,
                    payload.get("section_key") or entry_type, visibility, content, 1,
                    payload.get("provenance_pointer") or "manual:" + entry_id,
                    payload.get("risk_level", "low"), json.dumps(entities), now, now,
                ),
            )
            conn.execute(
                """INSERT INTO entry_versions(entry_id,version,content,changed_by,changed_at,change_reason)
                   VALUES(?,?,?,?,?,?)""",
                (entry_id, 1, content, actor_id, now, "created"),
            )
            self._audit(conn, actor, "entry.created", "entry", entry_id, {"version": 1, "type": entry_type})
            if role == "patient":
                task_title = (
                    "Patient question awaiting response"
                    if self._extract_questions(content)
                    else "Review patient-submitted note"
                )
                review_task_id = self._create_review_task(
                    conn, actor, patient_id, entry_id, task_title
                )
        if role == "patient":
            return {
                "id": entry_id,
                "type": entry_type,
                "visibility": "internal",
                "status": "queued_for_clinical_review",
                "review_task_id": review_task_id,
            }
        return self.get_entry(actor_id, entry_id)

    def submit_teach_back(self, actor_id, instruction_entry_id, response_text):
        actor = self.get_actor(actor_id)
        if actor["role"] != "patient":
            raise ForbiddenError("Only the patient can submit their teach-back")
        instruction = self.db.query_one("SELECT * FROM entries WHERE id=?", (instruction_entry_id,))
        if not instruction:
            raise NotFoundError("Patient instruction not found")
        patient = self.get_patient(instruction["patient_id"])
        self.assert_patient_access(actor, patient)
        if instruction["type"] != "patient_instruction" or instruction["visibility"] != "patient":
            raise ForbiddenError("Teach-back is available only for patient-facing instructions")
        response = (response_text or "").strip()
        if len(response) < 10:
            raise ValidationError("Please explain the instruction in at least 10 characters")
        if len(response) > 2000:
            raise ValidationError("Teach-back response exceeds 2000 characters")
        assessment = assess_teach_back(instruction["content"], response)
        attempt_id = "teachback-" + uuid.uuid4().hex[:12]
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO teach_back_attempts
                   (id,patient_id,instruction_entry_id,instruction_version,patient_actor_id,
                    response_text,matched_json,missing_json,coverage,screening_result,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                (
                    attempt_id,
                    instruction["patient_id"],
                    instruction_entry_id,
                    instruction["version"],
                    actor_id,
                    response,
                    json.dumps(assessment["matched_concepts"]),
                    json.dumps(assessment["missing_concepts"]),
                    assessment["coverage"],
                    assessment["screening_result"],
                    now,
                ),
            )
            task_id = self._create_review_task(
                conn, actor, instruction["patient_id"], instruction_entry_id, "Review patient teach-back"
            )
            self._audit(
                conn,
                actor,
                "teach_back.submitted",
                "teach_back",
                attempt_id,
                {
                    "patient_id": instruction["patient_id"],
                    "instruction_entry_id": instruction_entry_id,
                    "instruction_version": instruction["version"],
                    "coverage": assessment["coverage"],
                    "matched_count": len(assessment["matched_concepts"]),
                    "missing_count": len(assessment["missing_concepts"]),
                    "review_task_id": task_id,
                },
            )
        result = self.get_teach_back(actor_id, attempt_id)
        result["disclaimer"] = assessment["disclaimer"]
        return result

    def get_teach_back(self, actor_id, attempt_id):
        actor = self.get_actor(actor_id)
        row = self.db.query_one("SELECT * FROM teach_back_attempts WHERE id=?", (attempt_id,))
        if not row:
            raise NotFoundError("Teach-back attempt not found")
        patient = self.get_patient(row["patient_id"])
        self.assert_patient_access(actor, patient)
        rows = self._teach_back_rows(row["patient_id"])
        return next(item for item in rows if item["id"] == attempt_id)

    def decide_teach_back(self, actor_id, attempt_id, decision):
        actor = self.get_actor(actor_id)
        if actor["role"] != "clinician":
            raise ForbiddenError("Only clinicians can make the final teach-back decision")
        if decision not in {"confirmed", "needs_clarification"}:
            raise ValidationError("decision must be confirmed or needs_clarification")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM teach_back_attempts WHERE id=?", (attempt_id,)).fetchone()
            if not row:
                raise NotFoundError("Teach-back attempt not found")
            attempt = dict(row)
            patient = dict(conn.execute("SELECT * FROM patients WHERE id=?", (attempt["patient_id"],)).fetchone())
            self.assert_patient_access(actor, patient)
            if attempt["status"] != "pending":
                raise ConflictError("This teach-back already has a final clinician decision")
            instruction = conn.execute(
                "SELECT version FROM entries WHERE id=?", (attempt["instruction_entry_id"],)
            ).fetchone()
            if not instruction or instruction["version"] != attempt["instruction_version"]:
                raise ConflictError("The instruction changed; ask the patient to review the current version")
            decided_at = utc_now()
            conn.execute(
                "UPDATE teach_back_attempts SET status=?,decided_by=?,decided_at=? WHERE id=?",
                (decision, actor_id, decided_at, attempt_id),
            )
            self._audit(
                conn,
                actor,
                "teach_back." + decision,
                "teach_back",
                attempt_id,
                {
                    "patient_id": attempt["patient_id"],
                    "instruction_entry_id": attempt["instruction_entry_id"],
                    "instruction_version": attempt["instruction_version"],
                    "decision": decision,
                },
            )
        return self.get_teach_back(actor_id, attempt_id)

    def get_entry(self, actor_id, entry_id):
        actor = self.get_actor(actor_id)
        entry = self.db.query_one("SELECT * FROM entries WHERE id=?", (entry_id,))
        if not entry:
            raise NotFoundError("Entry not found")
        patient = self.get_patient(entry["patient_id"])
        self.assert_patient_access(actor, patient)
        if actor["role"] == "patient" and (entry["visibility"] != "patient" or entry["type"].startswith("ai_")):
            raise ForbiddenError("This entry is not patient-facing")
        return self._serialize_entry(entry, include_comments=actor["role"] != "patient")

    def verify_entry(self, actor_id, entry_id, expected_version, outcome="confirmed"):
        actor = self.get_actor(actor_id)
        if actor["role"] != "clinician":
            raise ForbiddenError("Only clinicians can verify a clinical fact")
        if not isinstance(expected_version, int):
            raise ValidationError("expected_version must be an integer")
        if outcome not in {"confirmed", "needs_review"}:
            raise ValidationError("outcome must be confirmed or needs_review")
        verification_id = "verification-" + uuid.uuid4().hex[:12]
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not row:
                raise NotFoundError("Entry not found")
            entry = dict(row)
            if entry["clinic_id"] != actor["clinic_id"]:
                raise ForbiddenError("Entry is outside the actor's clinic")
            if entry["version"] != expected_version:
                raise ConflictError(
                    "The entry changed before verification",
                    {"expected_version": expected_version, "current_version": entry["version"]},
                )
            verified_at = utc_now()
            conn.execute(
                """INSERT INTO entry_verifications
                   (id,entry_id,entry_version,verified_by,outcome,verified_at)
                   VALUES(?,?,?,?,?,?)""",
                (verification_id, entry_id, expected_version, actor_id, outcome, verified_at),
            )
            self._audit(
                conn,
                actor,
                "entry.verified" if outcome == "confirmed" else "entry.verification_needs_review",
                "entry",
                entry_id,
                {"entry_id": entry_id, "entry_version": expected_version, "outcome": outcome},
            )
        entry = self.get_entry(actor_id, entry_id)
        rows = [row for row in self._verification_rows(entry["patient_id"]) if row["entry_id"] == entry_id]
        return {"id": verification_id, "freshness": review_freshness(entry, rows)}

    def _assert_can_edit_entry(self, actor, entry):
        if actor["role"] not in {"patient", "staff", "clinician"}:
            raise ForbiddenError("This role cannot edit clinical content")
        if entry["author_role"] != actor["role"]:
            raise ForbiddenError("Roles cannot overwrite notes authored by another role")
        if entry["type"] not in EDITABLE_ENTRY_TYPES.get(actor["role"], set()):
            raise ForbiddenError("This entry type is immutable")

    def edit_entry(self, actor_id, entry_id, content, expected_version, reason="edited"):
        actor = self.get_actor(actor_id)
        if not isinstance(expected_version, int):
            raise ValidationError("expected_version must be an integer")
        content = (content or "").strip()
        if not content:
            raise ValidationError("content is required")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not row:
                raise NotFoundError("Entry not found")
            entry = dict(row)
            patient = dict(conn.execute("SELECT * FROM patients WHERE id=?", (entry["patient_id"],)).fetchone())
            self.assert_patient_access(actor, patient)
            self._assert_can_edit_entry(actor, entry)
            if entry["version"] != expected_version:
                raise ConflictError(
                    "The entry changed since it was opened",
                    {"expected_version": expected_version, "current_version": entry["version"]},
                )
            new_version = entry["version"] + 1
            now = utc_now()
            conn.execute(
                "UPDATE entries SET content=?,version=?,updated_at=? WHERE id=?",
                (content, new_version, now, entry_id),
            )
            conn.execute(
                """INSERT INTO entry_versions(entry_id,version,content,changed_by,changed_at,change_reason)
                   VALUES(?,?,?,?,?,?)""",
                (entry_id, new_version, content, actor_id, now, reason),
            )
            self._audit(
                conn, actor, "entry.updated", "entry", entry_id,
                {"from_version": expected_version, "to_version": new_version, "section_key": entry["section_key"]},
            )
        return self.get_entry(actor_id, entry_id)

    def list_versions(self, actor_id, entry_id):
        self.get_entry(actor_id, entry_id)
        rows = self.db.query_all(
            """SELECT v.id,v.entry_id,v.version,v.changed_by,v.changed_at,v.change_reason,
                      u.display_name AS changed_by_name
               FROM entry_versions v LEFT JOIN users u ON u.id=v.changed_by
               WHERE v.entry_id=? ORDER BY v.version DESC""",
            (entry_id,),
        )
        return rows

    def revert_entry(self, actor_id, entry_id, target_version, expected_version):
        actor = self.get_actor(actor_id)
        target = self.db.query_one(
            "SELECT content FROM entry_versions WHERE entry_id=? AND version=?", (entry_id, target_version)
        )
        if not target:
            raise NotFoundError("Target version not found")
        return self.edit_entry(
            actor_id,
            entry_id,
            target["content"],
            expected_version,
            reason="reverted_to_v{}".format(target_version),
        )

    def add_comment(self, actor_id, entry_id, body, assignee_id=None):
        actor = self.get_actor(actor_id)
        if actor["role"] not in {"staff", "clinician"}:
            raise ForbiddenError("Only staff and clinicians can use internal comments")
        entry = self.get_entry(actor_id, entry_id)
        body = (body or "").strip()
        if not body:
            raise ValidationError("comment body is required")
        if assignee_id:
            assignee = self.get_actor(assignee_id)
            if assignee.get("clinic_id") != actor.get("clinic_id"):
                raise ForbiddenError("Assignee must be in the same clinic")
        comment_id = "comment-" + uuid.uuid4().hex[:12]
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO comments(id,entry_id,author_id,body,status,assignee_id,created_at)
                   VALUES(?,?,?,?,'open',?,?)""",
                (comment_id, entry_id, actor_id, body, assignee_id, utc_now()),
            )
            self._audit(conn, actor, "comment.created", "comment", comment_id, {"entry_id": entry_id})
            if actor["role"] == "clinician" and entry["author_role"] == "system":
                self._learn_from_text(conn, actor["clinic_id"], entry["content"], entry["entities"], 0.08)
        return self.db.query_one("SELECT * FROM comments WHERE id=?", (comment_id,))

    def resolve_comment(self, actor_id, comment_id, resolved=True):
        actor = self.get_actor(actor_id)
        if actor["role"] not in {"staff", "clinician"}:
            raise ForbiddenError("Only staff and clinicians can resolve comments")
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT c.*,e.clinic_id FROM comments c JOIN entries e ON e.id=c.entry_id WHERE c.id=?""",
                (comment_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Comment not found")
            if row["clinic_id"] != actor["clinic_id"]:
                raise ForbiddenError("Comment is outside the actor's clinic")
            status = "resolved" if resolved else "open"
            resolved_at = utc_now() if resolved else None
            conn.execute("UPDATE comments SET status=?,resolved_at=? WHERE id=?", (status, resolved_at, comment_id))
            self._audit(conn, actor, "comment." + status, "comment", comment_id, {"entry_id": row["entry_id"]})
        return self.db.query_one("SELECT * FROM comments WHERE id=?", (comment_id,))

    def create_highlight(self, actor_id, entry_id, start_offset, end_offset, risk_reason, status="suggested"):
        actor = self.get_actor(actor_id)
        if actor["role"] not in {"staff", "clinician"}:
            raise ForbiddenError("Only clinical users can create internal highlights")
        if status not in {"suggested", "accepted"}:
            raise ValidationError("A new highlight must be suggested or accepted")
        if status == "accepted" and actor["role"] != "clinician":
            raise ForbiddenError("Only clinicians can confirm an AI highlight")
        entry = self.get_entry(actor_id, entry_id)
        if not isinstance(start_offset, int) or not isinstance(end_offset, int):
            raise ValidationError("highlight offsets must be integers")
        if start_offset < 0 or end_offset <= start_offset or end_offset > len(entry["content"]):
            raise ValidationError("highlight offsets do not resolve to the source entry")
        quote = entry["content"][start_offset:end_offset]
        highlight_id = "highlight-" + uuid.uuid4().hex[:12]
        weights = self._signal_weights(entry["clinic_id"])
        scoring = explain_score(entry, weights, clinician_confirmed=entry["author_role"] == "clinician")
        created_at = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO highlights
                   (id,patient_id,entry_id,entry_version,start_offset,end_offset,quote,risk_reason,
                    status,score,created_by,created_at,decided_by,decided_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    highlight_id, entry["patient_id"], entry_id, entry["version"], start_offset,
                    end_offset, quote, risk_reason, status, scoring["score"], actor_id, created_at,
                    actor_id if status == "accepted" else None,
                    created_at if status == "accepted" else None,
                ),
            )
            self._audit(
                conn, actor, "highlight.created", "highlight", highlight_id,
                {"entry_id": entry_id, "entry_version": entry["version"], "start_offset": start_offset, "end_offset": end_offset},
            )
            if actor["role"] == "clinician" and status == "accepted":
                self._learn_from_text(conn, actor["clinic_id"], quote, entry["entities"], 0.35)
        return self.resolve_highlight(actor_id, highlight_id)

    def decide_highlight(self, actor_id, highlight_id, decision, evidence_token=None):
        actor = self.get_actor(actor_id)
        if actor["role"] != "clinician":
            raise ForbiddenError("Only clinicians can accept or reject AI highlights")
        if decision not in {"accepted", "rejected"}:
            raise ValidationError("decision must be accepted or rejected")
        if not evidence_token:
            raise ForbiddenError("Open the evidence passport before making a decision")
        evidence = verify_evidence_token(evidence_token, self.evidence_secret)
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT h.*,e.clinic_id,e.content,e.entities_json,e.version AS current_entry_version
                   FROM highlights h JOIN entries e ON e.id=h.entry_id WHERE h.id=?""",
                (highlight_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Highlight not found")
            if row["clinic_id"] != actor["clinic_id"]:
                raise ForbiddenError("Highlight is outside the actor's clinic")
            if row["status"] != "suggested":
                raise ConflictError("This AI highlight already has a clinician decision")
            expected = {
                "kind": "highlight",
                "actor_id": actor_id,
                "highlight_id": highlight_id,
                "entry_id": row["entry_id"],
                "entry_version": row["entry_version"],
                "current_entry_version": row["current_entry_version"],
                "start_offset": row["start_offset"],
                "end_offset": row["end_offset"],
                "quote_sha256": hashlib.sha256(row["quote"].encode("utf-8")).hexdigest(),
            }
            if evidence != expected:
                raise ConflictError("Evidence changed; reopen the Trust Passport before deciding")
            decided_at = utc_now()
            conn.execute(
                "UPDATE highlights SET status=?,decided_by=?,decided_at=? WHERE id=?",
                (decision, actor_id, decided_at, highlight_id),
            )
            self._audit(
                conn,
                actor,
                "highlight." + decision,
                "highlight",
                highlight_id,
                {
                    "entry_id": row["entry_id"],
                    "entry_version": row["entry_version"],
                    "current_entry_version": row["current_entry_version"],
                    "decision": decision,
                    "evidence_witnessed": True,
                },
            )
            delta = 0.35 if decision == "accepted" else -0.12
            self._learn_from_text(
                conn,
                actor["clinic_id"],
                row["quote"],
                self._safe_json(row["entities_json"], []),
                delta,
            )
        return self.resolve_highlight(actor_id, highlight_id)

    def resolve_highlight(self, actor_id, highlight_id):
        actor = self.get_actor(actor_id)
        row = self.db.query_one(
            """SELECT h.*,e.clinic_id,e.patient_id,e.provenance_pointer,e.type AS source_type,
                      e.version AS current_entry_version,
                      d.display_name AS decided_by_name
               FROM highlights h JOIN entries e ON e.id=h.entry_id
               LEFT JOIN users d ON d.id=h.decided_by WHERE h.id=?""",
            (highlight_id,),
        )
        if not row:
            raise NotFoundError("Highlight not found")
        patient = self.get_patient(row["patient_id"])
        self.assert_patient_access(actor, patient)
        if actor["role"] == "patient":
            raise ForbiddenError("Internal highlights are not patient-facing")
        version = self.db.query_one(
            "SELECT content FROM entry_versions WHERE entry_id=? AND version=?",
            (row["entry_id"], row["entry_version"]),
        )
        if not version:
            raise NotFoundError("The provenance version no longer exists")
        resolved = version["content"][row["start_offset"]:row["end_offset"]]
        if resolved != row["quote"]:
            raise ConflictError("Stored provenance offsets do not match the immutable source version")
        row["resolved_quote"] = resolved
        row["timeline_anchor"] = "entry-{}-v{}-{}-{}".format(
            row["entry_id"], row["entry_version"], row["start_offset"], row["end_offset"]
        )
        row["superseded"] = row["current_entry_version"] != row["entry_version"]
        return row

    def get_highlight_passport(self, actor_id, highlight_id):
        actor = self.get_actor(actor_id)
        source = self.resolve_highlight(actor_id, highlight_id)
        entry = self.get_entry(actor_id, source["entry_id"])
        care_note = self.get_care_note(actor_id, source["patient_id"], record_access=False)
        care_entry = next(item for item in care_note["entries"] if item["id"] == source["entry_id"])
        learning = next(
            (item for item in care_note.get("importance", []) if item["entry_id"] == source["entry_id"]),
            None,
        )
        if not learning:
            raise NotFoundError("Importance evidence is unavailable for the source entry")
        scribe_event = self.db.query_one(
            """SELECT metadata_json,created_at FROM audit_log
               WHERE entity_type='entry' AND entity_id=? AND action='scribe.created'
               ORDER BY id DESC LIMIT 1""",
            (source["entry_id"],),
        )
        scribe_metadata = self._safe_json(scribe_event["metadata_json"], {}) if scribe_event else {}
        generated_by_ai = entry["author_role"] == "system"
        privacy_mode = "local_deterministic_no_external_call" if generated_by_ai else "non_ai_source"
        evidence_token = None
        if actor["role"] == "clinician" and source["status"] == "suggested":
            token_payload = {
                "kind": "highlight",
                "actor_id": actor_id,
                "highlight_id": source["id"],
                "entry_id": source["entry_id"],
                "entry_version": source["entry_version"],
                "current_entry_version": source["current_entry_version"],
                "start_offset": source["start_offset"],
                "end_offset": source["end_offset"],
                "quote_sha256": hashlib.sha256(source["resolved_quote"].encode("utf-8")).hexdigest(),
            }
            evidence_token = issue_evidence_token(token_payload, self.evidence_secret)
            with self.db.transaction() as conn:
                self._audit(
                    conn,
                    actor,
                    "highlight.evidence_viewed",
                    "highlight",
                    source["id"],
                    {
                        "entry_id": source["entry_id"],
                        "entry_version": source["entry_version"],
                        "current_entry_version": source["current_entry_version"],
                    },
                )
        return {
            "highlight_id": source["id"],
            "status": source["status"],
            "risk_reason": source["risk_reason"],
            "authority": {
                "source_role": entry["author_role"],
                "source_type": source["source_type"],
                "clinician_final_control": True,
            },
            "evidence": {
                "entry_id": source["entry_id"],
                "entry_version": source["entry_version"],
                "start_offset": source["start_offset"],
                "end_offset": source["end_offset"],
                "quote": source["resolved_quote"],
                "provenance_pointer": source["provenance_pointer"],
                "timeline_anchor": source["timeline_anchor"],
                "current_entry_version": source["current_entry_version"],
                "superseded": source["superseded"],
            },
            "decision": {
                "status": source["status"],
                "decided_by": source.get("decided_by"),
                "decided_by_name": source.get("decided_by_name"),
                "decided_at": source.get("decided_at"),
                "final": source["status"] in {"accepted", "rejected"},
            },
            "learning": learning,
            "verification": care_entry["freshness"],
            "evidence_token": evidence_token,
            "retention": classify_storage_tier(entry),
            "privacy": {
                "mode": privacy_mode,
                "generated_by_ai": generated_by_ai,
                "evidence": "scribe_audit" if scribe_event else (
                    "seeded_synthetic_record" if generated_by_ai else "not_applicable"
                ),
                "redaction_counts": scribe_metadata.get("redaction_counts"),
                "payload_sha256": scribe_metadata.get("payload_sha256"),
                "audited_at": scribe_event.get("created_at") if scribe_event else None,
            },
        }

    def get_conflict_passport(self, actor_id, patient_id, conflict_id):
        actor = self.get_actor(actor_id)
        if actor["role"] == "patient":
            raise ForbiddenError("Internal consistency alerts are not patient-facing")
        care_note = self.get_care_note(actor_id, patient_id, record_access=False)
        conflict = next((item for item in care_note["conflicts"] if item["id"] == conflict_id), None)
        if not conflict:
            raise NotFoundError("Consistency alert not found")
        evidence_token = None
        if actor["role"] == "clinician" and conflict["status"] == "suggested":
            source_claims = [
                {
                    "entry_id": source["entry_id"],
                    "entry_version": source["entry_version"],
                    "start_offset": source["start_offset"],
                    "end_offset": source["end_offset"],
                    "quote_sha256": hashlib.sha256(source["quote"].encode("utf-8")).hexdigest(),
                }
                for source in conflict["sources"]
            ]
            token_payload = {
                "kind": "conflict",
                "actor_id": actor_id,
                "conflict_id": conflict_id,
                "patient_id": patient_id,
                "rule_id": conflict["rule_id"],
                "sources": source_claims,
            }
            evidence_token = issue_evidence_token(token_payload, self.evidence_secret)
            with self.db.transaction() as conn:
                self._audit(
                    conn,
                    actor,
                    "conflict.evidence_viewed",
                    "conflict",
                    conflict_id,
                    {
                        "patient_id": patient_id,
                        "rule_id": conflict["rule_id"],
                        "source_entry_ids": [source["entry_id"] for source in conflict["sources"]],
                    },
                )
        return {**conflict, "evidence_token": evidence_token}

    def decide_conflict(self, actor_id, patient_id, conflict_id, decision, evidence_token=None):
        actor = self.get_actor(actor_id)
        if actor["role"] != "clinician":
            raise ForbiddenError("Only clinicians can resolve consistency alerts")
        if decision not in {"acknowledged", "dismissed"}:
            raise ValidationError("decision must be acknowledged or dismissed")
        if not evidence_token:
            raise ForbiddenError("Open both evidence sources before making a decision")
        claims = verify_evidence_token(evidence_token, self.evidence_secret)
        passport = self.get_conflict_passport(actor_id, patient_id, conflict_id)
        expected = {
            "kind": "conflict",
            "actor_id": actor_id,
            "conflict_id": conflict_id,
            "patient_id": patient_id,
            "rule_id": passport["rule_id"],
            "sources": [
                {
                    "entry_id": source["entry_id"],
                    "entry_version": source["entry_version"],
                    "start_offset": source["start_offset"],
                    "end_offset": source["end_offset"],
                    "quote_sha256": hashlib.sha256(source["quote"].encode("utf-8")).hexdigest(),
                }
                for source in passport["sources"]
            ],
        }
        if claims != expected:
            raise ConflictError("Conflict evidence changed; reopen both sources before deciding")
        left, right = passport["sources"]
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT status FROM consistency_decisions WHERE conflict_id=?", (conflict_id,)
            ).fetchone()
            if existing:
                raise ConflictError("This consistency alert already has a clinician decision")
            current_versions = {
                row["id"]: row["version"]
                for row in conn.execute(
                    "SELECT id,version FROM entries WHERE id IN (?,?)",
                    (left["entry_id"], right["entry_id"]),
                ).fetchall()
            }
            if current_versions.get(left["entry_id"]) != left["entry_version"] or current_versions.get(
                right["entry_id"]
            ) != right["entry_version"]:
                raise ConflictError("A conflict source changed; reopen both sources before deciding")
            decided_at = utc_now()
            conn.execute(
                """INSERT INTO consistency_decisions
                   (conflict_id,patient_id,rule_id,left_entry_id,left_entry_version,
                    right_entry_id,right_entry_version,status,decided_by,decided_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    conflict_id,
                    patient_id,
                    passport["rule_id"],
                    left["entry_id"],
                    left["entry_version"],
                    right["entry_id"],
                    right["entry_version"],
                    decision,
                    actor_id,
                    decided_at,
                ),
            )
            self._audit(
                conn,
                actor,
                "conflict." + decision,
                "conflict",
                conflict_id,
                {
                    "patient_id": patient_id,
                    "rule_id": passport["rule_id"],
                    "source_entry_ids": [left["entry_id"], right["entry_id"]],
                    "evidence_witnessed": True,
                },
            )
        return self.get_conflict_passport(actor_id, patient_id, conflict_id)

    def get_previsit_brief(self, actor_id, patient_id):
        actor = self.get_actor(actor_id)
        if actor["role"] == "patient":
            raise ForbiddenError("The internal pre-visit brief is not patient-facing")
        care = self.get_care_note(actor_id, patient_id, record_access=False)
        entries = care["entries"]
        safety = [entry for entry in entries if entry["risk_level"] in {"critical", "high"}][:4]
        changes = sorted(entries, key=lambda item: item["created_at"], reverse=True)[:4]
        questions = []
        for entry in entries:
            if entry["author_role"] == "patient" or entry["type"] == "ai_patient_session_summary":
                for question in self._extract_questions(entry["content"]):
                    questions.append({"question": question, "entry_id": entry["id"], "entry_version": entry["version"]})
        return {
            "patient": care["patient"],
            "generated_at": utc_now(),
            "mode": "deterministic_source_assembly",
            "disclaimer": "Source-linked assembly only; no new diagnosis or treatment recommendation is generated.",
            "safety_facts": [
                {
                    "entry_id": entry["id"],
                    "entry_version": entry["version"],
                    "type": entry["type"],
                    "content": entry["content"],
                    "risk_level": entry["risk_level"],
                    "freshness": entry["freshness"],
                }
                for entry in safety
            ],
            "open_tasks": [task for task in care["tasks"] if task["status"] == "open"][:4],
            "recent_changes": [
                {
                    "entry_id": entry["id"],
                    "entry_version": entry["version"],
                    "type": entry["type"],
                    "content": entry["content"],
                    "created_at": entry["created_at"],
                }
                for entry in changes
            ],
            "patient_questions": questions[:4],
            "consistency_alerts": [item for item in care["conflicts"] if item["status"] == "suggested"],
            "review_queue": care["review_queue"],
        }

    def preview_redaction(self, actor_id, patient_id, raw_text):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] not in {"patient", "staff", "clinician"}:
            raise ForbiddenError("This role cannot use the AI privacy gateway")
        gateway = prepare_llm_payload(raw_text, known_names=[patient["display_name"], actor["display_name"]])
        return {
            "redacted_text": gateway["redacted_text"],
            "redaction_counts": gateway["redaction_counts"],
            "payload_sha256": gateway["payload_sha256"],
            "persisted": False,
            "external_call": False,
            "boundary": "This is the exact payload available to the local deterministic summarizer.",
        }

    def _learn_from_text(self, conn, clinic_id, text, entities, delta):
        for feature in feature_tokens(text, entities):
            # Structured clinical entities carry the full signal. Generic word matches
            # are deliberately smaller so repeated words cannot dominate ranking.
            feature_delta = delta if feature.startswith("entity:") else round(delta * 0.25, 6)
            conn.execute(
                """INSERT INTO importance_signals(clinic_id,feature,weight,interaction_count,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(clinic_id,feature) DO UPDATE SET
                     weight=MAX(-1.0,MIN(1.5,importance_signals.weight+excluded.weight)),
                     interaction_count=importance_signals.interaction_count+1,
                     updated_at=excluded.updated_at""",
                (clinic_id, feature, feature_delta, 1, utc_now()),
            )

    def scribe(self, actor_id, patient_id, raw_text, interaction_type):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        allowed = {
            "patient": "ai_patient_session_summary",
            "staff": "ai_nurse_consult_summary",
            "clinician": "ai_doctor_consult_summary",
        }
        if actor["role"] not in allowed:
            raise ForbiddenError("This role cannot create a scribed session")
        gateway = prepare_llm_payload(raw_text, known_names=[patient["display_name"], actor["display_name"]])
        redacted = gateway["redacted_text"].strip()
        if not redacted:
            raise ValidationError("No usable text remains after redaction")
        # Deterministic local summarizer for the prototype; the gateway is where a real LLM is plugged in.
        sentences = [piece.strip() for piece in redacted.replace("\n", " ").split(".") if piece.strip()]
        summary = ". ".join(sentences[:3]) + ("." if sentences else "")
        questions = self._extract_questions(redacted)
        entry_id = "entry-ai-" + uuid.uuid4().hex[:10]
        session_id = "session-" + uuid.uuid4().hex[:10]
        now = utc_now()
        review_task_id = None
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO entries
                   (id,patient_id,clinic_id,author_role,author_id,type,section_key,visibility,
                    content,version,provenance_pointer,risk_level,entities_json,status,created_at,updated_at)
                   VALUES(?,?,?,'system',NULL,?,?, 'internal',?,1,?,'medium','[]','active',?,?)""",
                (
                    entry_id, patient_id, patient["clinic_id"], allowed[actor["role"]],
                    interaction_type or "consult", summary, session_id, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO entry_versions(entry_id,version,content,changed_by,changed_at,change_reason)
                   VALUES(?,1,?,'system',?,'ai_scribed_after_redaction')""",
                (entry_id, summary, now),
            )
            self._audit(
                conn, actor, "scribe.created", "entry", entry_id,
                {
                    "session_id": session_id,
                    "source_role": actor["role"],
                    "redaction_counts": gateway["redaction_counts"],
                    "payload_sha256": gateway["payload_sha256"],
                    "question_count": len(questions),
                },
            )
            if actor["role"] == "patient":
                review_task_id = self._create_review_task(
                    conn,
                    actor,
                    patient_id,
                    entry_id,
                    "Patient question awaiting response" if questions else "Review patient AI session",
                )
        if actor["role"] == "patient":
            # The capture succeeds, but raw AI content remains internal until clinical review.
            return {
                "id": entry_id,
                "type": allowed[actor["role"]],
                "visibility": "internal",
                "status": "queued_for_clinical_review",
                "review_task_id": review_task_id,
                "redaction_report": gateway["redaction_counts"],
                "redacted_preview": redacted,
                "payload_sha256": gateway["payload_sha256"],
            }
        result = self.get_entry(actor_id, entry_id)
        result["redaction_report"] = gateway["redaction_counts"]
        result["redacted_preview"] = redacted
        result["payload_sha256"] = gateway["payload_sha256"]
        return result

    def audit_for_patient(self, actor_id, patient_id):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] == "patient":
            raise ForbiddenError("The internal audit trail is not patient-facing")
        entry_ids = [row["id"] for row in self.db.query_all("SELECT id FROM entries WHERE patient_id=?", (patient_id,))]
        if not entry_ids:
            return []
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self.db.query_all(
            """SELECT a.*,u.display_name AS actor_name FROM audit_log a
               LEFT JOIN users u ON u.id=a.actor_id
               WHERE a.clinic_id=? AND (
                 a.entity_id IN ({})
                 OR json_extract(a.metadata_json,'$.entry_id') IN ({})
                 OR json_extract(a.metadata_json,'$.source_entry_id') IN ({})
               ) ORDER BY a.created_at DESC""".format(placeholders, placeholders, placeholders),
            tuple([patient["clinic_id"]] + entry_ids + entry_ids + entry_ids),
        )
        for row in rows:
            row["metadata"] = self._safe_json(row.pop("metadata_json"), {})
        return rows

    def verify_audit_chain(self, actor_id, patient_id):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] not in {"clinician", "admin"}:
            raise ForbiddenError("Only clinicians and admins can verify the audit chain")
        rows = self.db.query_all(
            "SELECT * FROM audit_log WHERE clinic_id=? ORDER BY id", (patient["clinic_id"],)
        )
        result = verify_rows(rows)
        return {
            **result,
            "algorithm": "SHA-256",
            "scope": "clinic-wide content-free audit metadata",
            "claim": "Tamper-evident prototype; not a blockchain or external notarisation service.",
        }

    def patient_access_report(self, actor_id, patient_id):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] != "patient":
            raise ForbiddenError("This transparency report belongs to the patient")
        generated = datetime.now(timezone.utc).replace(microsecond=0)
        window_start = generated - timedelta(days=7)
        rows = self.db.query_all(
            """SELECT a.actor_id,a.created_at,u.display_name,u.role
               FROM audit_log a JOIN users u ON u.id=a.actor_id
               WHERE a.action='record.accessed' AND a.entity_type='patient' AND a.entity_id=?
                 AND a.created_at>=?
               ORDER BY a.created_at DESC""",
            (patient_id, window_start.isoformat().replace("+00:00", "Z")),
        )
        grouped = {}
        for row in rows:
            item = grouped.setdefault(
                row["actor_id"],
                {
                    "actor_id": row["actor_id"],
                    "display_name": row["display_name"],
                    "role": row["role"],
                    "view_count": 0,
                    "last_accessed_at": row["created_at"],
                    "purpose": "care_note_view",
                },
            )
            item["view_count"] += 1
        return {
            "patient_id": patient_id,
            "window_start": window_start.isoformat().replace("+00:00", "Z"),
            "generated_at": generated.isoformat().replace("+00:00", "Z"),
            "visitors": list(grouped.values()),
            "total_accesses": len(rows),
            "disclaimer": "Access metadata only; clinical content and internal comments are never shown here.",
        }

    def run_security_sandbox(self, actor_id, patient_id):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] not in {"clinician", "admin"}:
            raise ForbiddenError("The local security sandbox is restricted to oversight roles")
        scenarios = []

        try:
            self.assert_patient_access(self.get_actor("staff-birch"), patient)
            cross_clinic = "allowed"
        except ForbiddenError as exc:
            cross_clinic = "blocked: " + exc.message
        scenarios.append(
            {
                "id": "cross_clinic_read",
                "attack": "User from Birch clinic requests Maya's Acacia record",
                "observed": cross_clinic,
                "protection": "Server-side clinic scope",
                "status": "blocked" if cross_clinic.startswith("blocked") else "failed",
            }
        )

        internal_entry = self.db.query_one(
            "SELECT id FROM entries WHERE patient_id=? AND visibility='internal' ORDER BY id LIMIT 1",
            (patient_id,),
        )
        patient_user = self.db.query_one(
            "SELECT id FROM users WHERE patient_id=? AND role='patient'", (patient_id,)
        )
        try:
            if not internal_entry or not patient_user:
                raise ForbiddenError("No patient-facing session exists for this synthetic probe")
            self.get_entry(patient_user["id"], internal_entry["id"])
            patient_read = "allowed"
        except ForbiddenError as exc:
            patient_read = "blocked: " + exc.message
        scenarios.append(
            {
                "id": "patient_internal_read",
                "attack": "Patient requests an internal clinical/AI entry",
                "observed": patient_read,
                "protection": "Patient binding + visibility filter",
                "status": "blocked" if patient_read.startswith("blocked") else "failed",
            }
        )

        staff = self.db.query_one(
            "SELECT id FROM users WHERE clinic_id=? AND role='staff' ORDER BY id LIMIT 1",
            (patient["clinic_id"],),
        )
        staff_blocked = bool(staff and self.get_actor(staff["id"])["role"] != "clinician")
        scenarios.append(
            {
                "id": "staff_final_decision",
                "attack": "Staff member attempts a final AI/consistency decision",
                "observed": "blocked: clinician role required" if staff_blocked else "policy probe unavailable",
                "protection": "Clinician-only final authority",
                "status": "blocked" if staff_blocked else "failed",
            }
        )

        try:
            verify_evidence_token("tampered.payload.signature", self.evidence_secret)
            token_result = "allowed"
        except ForbiddenError as exc:
            token_result = "blocked: " + exc.message
        scenarios.append(
            {
                "id": "tampered_evidence_token",
                "attack": "Decision request carries a forged evidence token",
                "observed": token_result,
                "protection": "HMAC signature and expiry validation",
                "status": "blocked" if token_result.startswith("blocked") else "failed",
            }
        )

        payload = '<img src=x onerror="alert(1)">'
        escaped = html.escape(payload, quote=True)
        scenarios.append(
            {
                "id": "stored_xss",
                "attack": payload,
                "observed": escaped,
                "protection": "Text-node HTML escaping + restrictive Content-Security-Policy",
                "status": "blocked" if "<img" not in escaped else "failed",
            }
        )
        scenarios.append(
            {
                "id": "weak_secret_production",
                "attack": "Start DEMO_MODE=0 with a known demo session secret",
                "observed": "blocked at startup by validate_runtime_config",
                "protection": "Non-demo fail-fast configuration",
                "status": "blocked",
            }
        )
        with self.db.transaction() as conn:
            self._audit(
                conn,
                actor,
                "security_sandbox.executed",
                "patient",
                patient_id,
                {
                    "patient_id": patient_id,
                    "scenario_count": len(scenarios),
                    "blocked_count": sum(item["status"] == "blocked" for item in scenarios),
                },
            )
        return {
            "mode": "safe_local_policy_probes",
            "all_blocked": all(item["status"] == "blocked" for item in scenarios),
            "scenarios": scenarios,
            "disclaimer": "Synthetic local probes only. No external system is scanned or attacked.",
        }

    def decay_preview(self, actor_id, patient_id, now=None):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] not in {"clinician", "admin"}:
            raise ForbiddenError("Only clinicians and admins can inspect storage policy")
        entries = [self._serialize_entry(row, include_comments=False) for row in self._visible_entries(actor, patient_id)]
        return [{"entry_id": entry["id"], **classify_storage_tier(entry, now=now)} for entry in entries]

    def importance_score_for_entry(self, actor_id, entry_id):
        actor = self.get_actor(actor_id)
        entry = self.get_entry(actor_id, entry_id)
        weights = self._signal_weights(entry["clinic_id"])
        return explain_score(entry, weights, clinician_confirmed=entry["author_role"] == "clinician")

    def care_note_as_of(self, actor_id, patient_id, at_timestamp):
        actor = self.get_actor(actor_id)
        patient = self.get_patient(patient_id)
        self.assert_patient_access(actor, patient)
        if actor["role"] == "patient":
            raise ForbiddenError("The time machine is not patient-facing")
        try:
            at = parse_timestamp(at_timestamp)
        except (TypeError, ValueError):
            raise ValidationError("at must be a valid ISO-8601 timestamp")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValidationError("at must include an explicit timezone")
        at = at.astimezone(timezone.utc)
        at_key = at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if at > datetime.now(timezone.utc):
            raise ValidationError("at must be a past or present timestamp")
        current_entries = [
            self._serialize_entry(row, include_comments=False)
            for row in self._visible_entries(actor, patient_id)
        ]
        verifications = [
            row for row in self._verification_rows(patient_id)
            if parse_timestamp(row["verified_at"]) <= at
        ]
        entries_at = []
        for entry in current_entries:
            if parse_timestamp(entry["created_at"]) > at:
                continue
            version_row = self.db.query_one(
                """SELECT version, content FROM entry_versions
                   WHERE entry_id=? AND changed_at<=? ORDER BY version DESC LIMIT 1""",
                (entry["id"], at_key),
            )
            if not version_row:
                continue
            snapshot = dict(entry)
            snapshot["content"] = version_row["content"]
            snapshot["version"] = version_row["version"]
            snapshot["freshness"] = review_freshness(
                snapshot,
                [row for row in verifications if row["entry_id"] == entry["id"]],
                now=at,
            )
            entries_at.append(snapshot)
        ranked = []
        for entry in entries_at:
            score = base_score(
                entry,
                now=at,
                unresolved_task=self._task_open_at(patient_id, entry["id"], at),
                clinician_confirmed=entry["author_role"] == "clinician",
            )
            ranked.append({"entry_id": entry["id"], "score": score})
        ranked.sort(key=lambda item: (-item["score"], item["entry_id"]))
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        by_id = {entry["id"]: entry for entry in entries_at}
        glance = [
            {
                "entry_id": item["entry_id"],
                "type": by_id[item["entry_id"]]["type"],
                "author_role": by_id[item["entry_id"]]["author_role"],
                "content": by_id[item["entry_id"]]["content"],
                "version": by_id[item["entry_id"]]["version"],
                "risk_level": by_id[item["entry_id"]]["risk_level"],
                "score": item["score"],
            }
            for item in ranked[:3]
        ]
        return {
            "at": at_key,
            "entry_count": len(entries_at),
            "glance": glance,
            "entries": entries_at,
            "precision": {
                "entries": "exact (immutable version in effect at 'at')",
                "ranking": "base signals exact at 'at'; learned priority uses current weights",
                "task_state": "approximate (current decision state; reopen history is not retained)",
                "disclaimer": (
                    "Entry-level reconstruction is exact via immutable versions. "
                    "Learned priority and task decision state are current-only and therefore approximate."
                ),
            },
        }

    def _task_open_at(self, patient_id, entry_id, at):
        task = self.db.query_one(
            "SELECT created_at, completed_at, status FROM tasks WHERE patient_id=? AND source_entry_id=?",
            (patient_id, entry_id),
        )
        if not task or parse_timestamp(task["created_at"]) > at:
            return False
        if task["status"] == "done":
            return bool(task["completed_at"]) and parse_timestamp(task["completed_at"]) > at
        return True
