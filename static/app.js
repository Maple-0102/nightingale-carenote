const state = {
  actorId: "clinician-lim",
  patientId: "patient-maya",
  care: null,
  focus: null,
  token: null,
  returnContext: null,
};

const toast = document.querySelector("#toast");
const roleButton = document.querySelector("#role-button");
const roleMenu = document.querySelector("#role-menu");
const dialog = document.querySelector("#workspace-dialog");

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function notify(message, isError = false) {
  toast.textContent = message;
  toast.style.background = isError ? "#82362f" : "#14251f";
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || "Request failed");
    error.status = response.status;
    error.details = payload.error?.details || {};
    throw error;
  }
  return payload;
}

async function startSession(actorId) {
  const response = await fetch("/api/demo/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor_id: actorId }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || "Unable to start demo session");
  state.actorId = actorId;
  state.token = payload.token;
}

function formatDate(value, includeTime = true) {
  const date = new Date(value);
  const options = includeTime
    ? { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat("en-SG", options).format(date);
}

function typeLabel(type) {
  return {
    ai_patient_session_summary: "AI patient session summary",
    ai_doctor_consult_summary: "AI doctor consult summary",
    ai_nurse_consult_summary: "AI nurse consult summary",
    clinician_note: "Clinician note",
    staff_note: "Staff note",
    patient_insight: "Patient-provided insight",
    patient_instruction: "Patient instruction",
  }[type] || type.replaceAll("_", " ");
}

function roleLabel(role) {
  return { clinician: "Clinician", staff: "Staff", patient: "Patient", admin: "Admin" }[role] || role;
}

function freshnessLabel(freshness = {}) {
  if (freshness.state === "fresh") return `Verified ${freshness.age_days}d ago`;
  if (freshness.state === "overdue") return `Verification overdue · ${freshness.age_days}d`;
  if (freshness.state === "never_verified") return "Never explicitly verified";
  return "Not yet due for review";
}

function highlightedContent(content, entryId) {
  if (state.focus?.entryId !== entryId) return escapeHtml(content);
  const start = state.focus.startOffset;
  const end = state.focus.endOffset;
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > content.length) {
    return escapeHtml(content);
  }
  return `${escapeHtml(content.slice(0, start))}<mark>${escapeHtml(content.slice(start, end))}</mark>${escapeHtml(content.slice(end))}`;
}

function showDialog(title, eyebrow, content) {
  document.querySelector("#dialog-title").textContent = title;
  document.querySelector("#dialog-eyebrow").textContent = eyebrow;
  document.querySelector("#dialog-content").innerHTML = content;
  if (!dialog.open) dialog.showModal();
}

function updateReturnContext() {
  const button = document.querySelector("#return-context-button");
  button.hidden = state.returnContext?.kind !== "conflict";
  if (!button.hidden) button.dataset.conflictId = state.returnContext.id;
}

function clearReturnContext() {
  state.returnContext = null;
  updateReturnContext();
  renderTimeline();
}

function renderHeader() {
  const { actor, patient, entries } = state.care;
  document.querySelector("#patient-name").innerHTML = `${escapeHtml(patient.display_name)} <span class="patient-id">${escapeHtml(patient.external_ref)}</span>`;
  document.querySelector(".patient-meta").textContent = `${patient.date_of_birth ? "Born " + formatDate(patient.date_of_birth, false) : ""} · ${patient.pronouns || ""} · Acacia Family Clinic`;
  roleButton.innerHTML = `${roleLabel(actor.role)} <span aria-hidden="true">⌄</span>`;
  document.body.classList.toggle("patient-view", actor.role === "patient");
  document.querySelector("#add-note-button").hidden = actor.role === "admin";
  document.querySelector("#scribe-button").hidden = actor.role === "admin";
  document.querySelector("#audit-button").hidden = actor.role === "patient";
  document.querySelector("#security-button").hidden = !["clinician", "admin"].includes(actor.role);
  document.querySelector("#storage-card").hidden = !["clinician", "admin"].includes(actor.role);
  document.querySelector("#review-count").textContent = String((state.care.review_queue || []).length);
  const latestClinician = entries
    .filter((entry) => entry.author_role === "clinician")
    .sort((left, right) => new Date(right.updated_at) - new Date(left.updated_at))[0];
  document.querySelector("#review-state-title").textContent = latestClinician ? "Latest clinician update" : "Awaiting clinician review";
  document.querySelector("#review-state-detail").textContent = latestClinician
    ? `${formatDate(latestClinician.updated_at)} · ${latestClinician.author_name}`
    : "No clinician-authored entry is visible in this view";
}

function renderGlance() {
  const { actor, entries, highlights, conflicts = [], tasks, importance = [] } = state.care;
  const cards = [];

  for (const conflict of conflicts.filter((item) => item.status === "suggested").slice(0, 1)) {
    cards.push({
      tone: "critical",
      label: "CONSISTENCY WATCHER",
      confidence: "Two sources",
      title: conflict.title,
      body: conflict.summary,
      action: `data-action="conflict-passport" data-conflict-id="${escapeHtml(conflict.id)}"`,
      actionLabel: "Review both immutable sources",
    });
  }

  for (const highlight of highlights.filter((item) => item.status !== "rejected").slice(0, 2)) {
    cards.push({
      tone: highlight.status === "accepted" ? "critical" : "attention",
      label: highlight.status === "accepted" ? "CLINICIAN CONFIRMED" : "AI SUGGESTION",
      confidence: highlight.status === "accepted" ? "Accepted" : "Needs review",
      title: highlight.quote,
      body: highlight.risk_reason,
      action: `data-action="passport" data-highlight-id="${escapeHtml(highlight.id)}"`,
      actionLabel: `Open Trust Passport · source v${highlight.entry_version}`,
    });
  }

  for (const task of tasks.filter((item) => item.status === "open").slice(0, 1)) {
    cards.push({
      tone: "action",
      label: "OPEN ACTION",
      confidence: task.assignee_name || "Unassigned",
      title: task.title,
      body: task.due_at ? `Due ${formatDate(task.due_at)}` : "No due date",
      action: `data-action="source-entry" data-entry-id="${escapeHtml(task.source_entry_id)}"`,
      actionLabel: "View task source",
    });
  }

  if (actor.role === "patient") {
    for (const entry of entries.slice(0, 3)) {
      cards.push({
        tone: "action",
        label: "FOR YOU",
        confidence: "Clinician shared",
        title: typeLabel(entry.type),
        body: entry.content,
        action: `data-action="source-entry" data-entry-id="${escapeHtml(entry.id)}"`,
        actionLabel: "View instruction",
      });
    }
  }

  for (const scored of importance) {
    if (cards.length >= 3) break;
    const entry = entries.find((item) => item.id === scored.entry_id);
    const alreadyHighlighted = highlights.some((item) => item.status !== "rejected" && item.entry_id === scored.entry_id);
    if (!entry || alreadyHighlighted || cards.some((card) => card.action.includes(entry.id))) continue;
    cards.push({
      tone: entry.risk_level === "critical" ? "critical" : "attention",
      label: entry.risk_level === "critical" ? "SAFETY" : "CONTEXT",
      confidence: entry.author_role === "clinician" ? "Clinician-authored" : "Prioritized",
      title: typeLabel(entry.type),
      body: entry.content,
      action: `data-action="source-entry" data-entry-id="${escapeHtml(entry.id)}"`,
      actionLabel: `View source · score ${scored.score}`,
    });
  }

  const grid = document.querySelector("#glance-grid");
  grid.innerHTML = cards.length
    ? cards.slice(0, 3).map((card) => `
      <article class="signal signal-${card.tone}">
        <div class="signal-top"><span class="signal-label">${escapeHtml(card.label)}</span><span class="confidence">${escapeHtml(card.confidence)}</span></div>
        <h3>${escapeHtml(card.title)}</h3>
        <p>${escapeHtml(card.body)}</p>
        <button class="source-link" ${card.action}>${escapeHtml(card.actionLabel)}</button>
      </article>`).join("")
    : `<div class="empty-state">No internal risk cards are exposed in this view. Patient-facing instructions remain below.</div>`;
}

function renderTasks() {
  const tasks = state.care.tasks || [];
  document.querySelector(".context-card h3").textContent = `${tasks.filter((item) => item.status === "open").length} actions`;
  document.querySelector("#task-list").innerHTML = tasks.length
    ? tasks.map((task) => {
      const canUpdate = ["staff", "clinician"].includes(state.care.actor.role)
        && (state.care.actor.role === "clinician" || !task.assignee_id || task.assignee_id === state.care.actor.id);
      const detail = task.status === "done"
        ? `Completed by ${task.completed_by_name || "clinical user"}${task.completed_at ? ` · ${formatDate(task.completed_at)}` : ""}`
        : task.assignee_name || "Unassigned";
      return `<button type="button" class="task-toggle ${task.status === "done" ? "task-done" : ""}" data-action="task-status" data-task-id="${escapeHtml(task.id)}" data-current-status="${escapeHtml(task.status)}" ${canUpdate ? "" : "disabled"} aria-pressed="${task.status === "done"}">
        <span class="task-check" aria-hidden="true">${task.status === "done" ? "✓" : ""}</span>
        <span><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(detail)}</small></span>
      </button>`;
    }).join("")
    : `<p class="legend-note">No open actions in this view.</p>`;
}

function renderComments(comments = []) {
  return comments.map((comment) => `
    <div class="comment">
      <span>${comment.status === "resolved" ? "✓" : "1"}</span>
      <p><strong>${escapeHtml(comment.author_name)}</strong> ${escapeHtml(comment.body)}
      ${comment.status === "open" ? `<button data-action="resolve-comment" data-comment-id="${escapeHtml(comment.id)}">Resolve</button>` : ""}</p>
    </div>`).join("");
}

function renderEntry(entry) {
  const actor = state.care.actor;
  const editable = actor.role === entry.author_role && ["patient", "staff", "clinician"].includes(actor.role);
  const clinicalUser = ["staff", "clinician"].includes(actor.role);
  const sourceHighlight = (state.care.highlights || []).find((item) => item.entry_id === entry.id);
  const icon = entry.author_role === "system" ? "AI" : entry.author_role === "clinician" ? "DR" : entry.author_role === "staff" ? "ST" : "PT";
  const iconClass = entry.author_role === "clinician" ? "clinician-icon" : entry.author_role === "staff" ? "staff-icon" : "";
  const actions = [];
  const freshness = entry.freshness || {};
  const teachBack = (state.care.teach_backs || []).find((item) => item.instruction_entry_id === entry.id);
  if (editable) actions.push(`<button data-action="edit" data-entry-id="${escapeHtml(entry.id)}">Edit</button>`);
  if (actor.role !== "patient") actions.push(`<button data-action="history" data-entry-id="${escapeHtml(entry.id)}">View history</button>`);
  if (clinicalUser) actions.push(`<button data-action="comment" data-entry-id="${escapeHtml(entry.id)}">Comment</button>`);
  if (clinicalUser && entry.author_role === "system") actions.push(`<button data-action="highlight" data-entry-id="${escapeHtml(entry.id)}">Highlight phrase</button>`);
  if (sourceHighlight) actions.push(`<button data-action="passport" data-highlight-id="${escapeHtml(sourceHighlight.id)}">Trust Passport</button>`);
  if (actor.role === "clinician") actions.push(`<button data-action="verification-card" data-entry-id="${escapeHtml(entry.id)}">${freshness.state === "fresh" ? "Re-verify" : "Review current version"}</button>`);
  if (actor.role === "patient" && entry.type === "patient_instruction") actions.push(`<button data-action="teach-back" data-entry-id="${escapeHtml(entry.id)}">Teach back in my own words</button>`);
  if (actor.role === "clinician" && teachBack?.status === "pending") actions.push(`<button data-action="teach-back-review" data-teach-back-id="${escapeHtml(teachBack.id)}">Review teach-back</button>`);
  const decisionTrail = sourceHighlight?.status === "accepted" || sourceHighlight?.status === "rejected"
    ? `<div class="decision-trail decision-${sourceHighlight.status}">
        <strong>${sourceHighlight.status === "accepted" ? "✓ Accepted by clinician" : "× Rejected by clinician"}</strong>
        <span>${escapeHtml(sourceHighlight.decided_by_name || "Clinician")} · ${sourceHighlight.decided_at ? formatDate(sourceHighlight.decided_at) : "Decision timestamp unavailable"}</span>
        <button type="button" data-action="passport" data-highlight-id="${escapeHtml(sourceHighlight.id)}">Open Trust Passport</button>
      </div>`
    : "";
  return `
    <article class="timeline-entry" id="${escapeHtml(entry.id)}">
      <div class="entry-rail"><span class="entry-icon ${iconClass}">${icon}</span><span class="rail-line"></span></div>
      <div class="entry-content">
        <div class="entry-meta"><strong>${escapeHtml(typeLabel(entry.type))}</strong><span>${formatDate(entry.created_at)}</span><span>${escapeHtml(entry.author_name || entry.author_role)}</span><span class="version">v${entry.version}</span><span class="freshness freshness-${escapeHtml(freshness.state || "unknown")}">${escapeHtml(freshnessLabel(freshness))}</span>${entry.provenance_pointer ? `<span class="provenance">${escapeHtml(entry.provenance_pointer)}</span>` : ""}</div>
        <p>${highlightedContent(entry.content, entry.id)}</p>
        ${entry.entities?.length ? `<div class="entity-row">${entry.entities.map((entity) => `<span>${escapeHtml(entity.replaceAll("_", " "))}</span>`).join("")}</div>` : ""}
        ${decisionTrail}
        ${teachBack ? `<div class="teachback-trail"><strong>Teach-back · ${escapeHtml(teachBack.status.replaceAll("_", " "))}</strong> · ${(Number(teachBack.coverage) * 100).toFixed(0)}% keyword coverage · instruction v${teachBack.instruction_version}</div>` : ""}
        ${renderComments(entry.comments)}
        <div class="entry-actions">${actions.join("")}</div>
      </div>
    </article>`;
}

function renderTimeline() {
  const groups = new Map();
  for (const entry of state.care.entries) {
    const label = formatDate(entry.created_at, false);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(entry);
  }
  const body = [...groups.entries()].map(([date, entries]) => `
    <div class="date-separator"><span>${escapeHtml(date)}</span></div>
    ${entries.map(renderEntry).join("")}`).join("") || `<p class="legend-note">No entries are visible to this role.</p>`;
  const banner = state.returnContext?.kind === "conflict"
    ? `<div class="return-banner"><span>Viewing a source from “${escapeHtml(state.returnContext.title || "a consistency conflict")}”</span><button type="button" data-action="return-context">← Back to conflict review</button></div>`
    : "";
  document.querySelector("#timeline-stream").innerHTML = banner + body;
}

async function loadCareNote({ announce = false } = {}) {
  document.body.classList.add("loading");
  try {
    if (!state.token) await startSession(state.actorId);
    state.care = await api(`/api/care-note?patient_id=${encodeURIComponent(state.patientId)}`);
    renderHeader();
    renderGlance();
    renderTimeline();
    renderTasks();
    if (announce) notify(`Switched to ${roleLabel(state.care.actor.role)} view`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    document.body.classList.remove("loading");
  }
}

function openAddNote() {
  const role = state.care.actor.role;
  const type = { clinician: "clinician_note", staff: "staff_note", patient: "patient_insight" }[role];
  if (!type) return notify("Admins have oversight access and cannot author clinical notes.", true);
  showDialog("Add a timeline entry", `${roleLabel(role).toUpperCase()} NOTE`, `
    <div class="form-field"><label for="note-content">What changed?</label><textarea id="note-content" placeholder="Write a concise, source-aware update..."></textarea></div>
    <div class="form-field"><label for="note-section">Section</label><select id="note-section"><option value="assessment">Assessment</option><option value="plan">Plan</option><option value="coordination">Coordination</option><option value="patient_context">Patient context</option></select></div>
    <div class="form-field"><label for="note-risk">Risk level</label><select id="note-risk"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select></div>
    <div class="dialog-actions"><button value="cancel">Cancel</button><button type="button" class="primary-button" id="save-note">Add to timeline</button></div>`);
  document.querySelector("#save-note").addEventListener("click", async () => {
    try {
      await api(`/api/patients/${encodeURIComponent(state.patientId)}/entries`, {
        method: "POST",
        body: JSON.stringify({ type, content: document.querySelector("#note-content").value, section_key: document.querySelector("#note-section").value, risk_level: document.querySelector("#note-risk").value }),
      });
      dialog.close();
      await loadCareNote();
      notify("Note added with version 1 and an audit event");
    } catch (error) { notify(error.message, true); }
  });
}

function openScribe() {
  const role = state.care.actor.role;
  const labels = {
    clinician: "doctor consultation",
    staff: "nurse/staff consultation",
    patient: "patient session",
  };
  if (!labels[role]) return notify("Admins cannot create scribed clinical content.", true);
  showDialog("Create an AI-scribed entry", "NO-PHI GATEWAY", `
    <p class="legend-note">Paste synthetic session notes. Known names, Singapore IDs, and phone numbers are redacted before the local summarizer runs. The resulting AI note remains distinct from clinician-authored facts.</p>
    <div class="form-field"><label for="scribe-text">Synthetic ${escapeHtml(labels[role])} transcript</label><textarea id="scribe-text" placeholder="Example: Maya Tan reports cough is worse at night. Call 9123 4567 to follow up."></textarea></div>
    <div id="redaction-preview"></div>
    <div class="dialog-actions"><button value="cancel">Cancel</button><button type="button" id="preview-redaction">Preview privacy boundary</button><button type="button" class="primary-button" id="save-scribe">Create AI note</button></div>`);
  document.querySelector("#preview-redaction").addEventListener("click", async () => {
    const rawText = document.querySelector("#scribe-text").value;
    if (!rawText.trim()) return notify("Add a synthetic transcript first.", true);
    try {
      const preview = await api(`/api/patients/${encodeURIComponent(state.patientId)}/redaction-preview`, {
        method: "POST",
        body: JSON.stringify({ raw_text: rawText }),
      });
      document.querySelector("#redaction-preview").innerHTML = `
        <div class="redaction-grid"><section><p class="eyebrow">BROWSER MEMORY ONLY</p><pre>${escapeHtml(rawText)}</pre></section><section><p class="eyebrow">PAYLOAD AVAILABLE TO SUMMARIZER</p><pre>${escapeHtml(preview.redacted_text)}</pre></section></div>
        <div class="redaction-proof"><span>${Object.entries(preview.redaction_counts).map(([key, value]) => `${escapeHtml(key)} ${Number(value)}`).join(" · ")}</span><code>sha256 ${escapeHtml(preview.payload_sha256.slice(0, 16))}…</code><strong>Not persisted · no external call</strong></div>`;
    } catch (error) { notify(error.message, true); }
  });
  document.querySelector("#save-scribe").addEventListener("click", async () => {
    const rawText = document.querySelector("#scribe-text").value;
    if (!rawText.trim()) return notify("Add a synthetic transcript first.", true);
    try {
      const result = await api(`/api/patients/${encodeURIComponent(state.patientId)}/scribe`, {
        method: "POST",
        body: JSON.stringify({ raw_text: rawText, interaction_type: role === "patient" ? "patient_session" : role === "staff" ? "nurse_consult" : "doctor_consult" }),
      });
      await loadCareNote();
      const redacted = Object.values(result.redaction_report || {}).reduce((sum, count) => sum + count, 0);
      document.querySelector("#dialog-content").innerHTML = `
        <div class="privacy-success"><span>✓</span><div><strong>${role === "patient" ? "Session queued for clinician review" : "AI note created behind the privacy boundary"}</strong><p>${redacted} identifiers redacted before summarization.</p></div></div>
        <div class="redaction-grid"><section><p class="eyebrow">EXACT REDACTED PAYLOAD</p><pre>${escapeHtml(result.redacted_preview || "")}</pre></section><section><p class="eyebrow">AUDIT PROOF</p><pre>sha256 ${escapeHtml(result.payload_sha256 || "")}</pre></section></div>
        <p class="legend-note">The raw transcript was not written to the audit trail. The generated entry remains visibly AI-authored.</p>
        <div class="dialog-actions"><button value="cancel">Close</button></div>`;
      notify(role === "patient" ? "Session queued for clinical review" : "AI note created");
    } catch (error) { notify(error.message, true); }
  });
}

async function editEntry(entryId) {
  const entry = state.care.entries.find((item) => item.id === entryId);
  const content = window.prompt("Edit this role-owned section", entry.content);
  if (content === null || content === entry.content) return;
  try {
    await api(`/api/entries/${entryId}`, { method: "PATCH", body: JSON.stringify({ content, expected_version: entry.version }) });
    await loadCareNote();
    notify(`Saved as version ${entry.version + 1}`);
  } catch (error) {
    if (error.status === 409) notify(`Conflict: current version is ${error.details.current_version}. Reloaded safely.`, true);
    else notify(error.message, true);
    await loadCareNote();
  }
}

async function showHistory(entryId) {
  try {
    const versions = await api(`/api/entries/${entryId}/versions`);
    const current = state.care.entries.find((item) => item.id === entryId);
    showDialog("Revision history", "IMMUTABLE VERSIONS", versions.map((version) => `
      <article class="history-item"><header><strong>Version ${version.version}</strong><span>${formatDate(version.changed_at)}</span></header><p>${escapeHtml(version.change_reason)} · ${escapeHtml(version.changed_by_name || version.changed_by)}</p>
      ${version.version !== current.version && current.author_role === state.care.actor.role ? `<button type="button" data-action="revert" data-entry-id="${entryId}" data-target-version="${version.version}" data-current-version="${current.version}">Revert to this content</button>` : ""}</article>`).join(""));
  } catch (error) { notify(error.message, true); }
}

async function showAudit() {
  try {
    const events = await api(`/api/audit?patient_id=${encodeURIComponent(state.patientId)}`);
    const eventList = events.length ? events.map((event) => `
      <article class="audit-item"><header><strong>${escapeHtml(event.action)}</strong><span>${formatDate(event.created_at)}</span></header><p>${escapeHtml(event.actor_name || event.actor_id)} · ${escapeHtml(event.entity_type)} ${escapeHtml(event.entity_id)} · ${escapeHtml(JSON.stringify(event.metadata))}</p></article>`).join("") : `<p class="legend-note">No audit events yet. Clinical content is intentionally excluded from logs.</p>`;
    showDialog("Audit trail", "METADATA ONLY", `<div class="dialog-actions"><button type="button" class="primary-button" data-action="verify-audit-chain">Verify SHA-256 chain</button></div>${eventList}`);
  } catch (error) { notify(error.message, true); }
}

async function showWhy() {
  const ranked = state.care.importance || [];
  showDialog("Why these items?", "EXPLAINABLE IMPORTANCE", `
    <p class="legend-note">The score combines recency, explicit risk, unresolved actions, clinical entities, clinician confirmation, and learned interaction signals. No LLM decides access or silently writes the record.</p>
    ${ranked.slice(0, 6).map((score) => {
      const entry = state.care.entries.find((item) => item.id === score.entry_id);
      const movement = score.rank_change > 0
        ? `<span class="rank-change rank-up">↑ ${score.rank_change} from base rank</span>`
        : score.rank_change < 0
          ? `<span class="rank-change rank-down">↓ ${Math.abs(score.rank_change)} from base rank</span>`
          : `<span class="rank-change">No rank change</span>`;
      return `<div class="score-explainer"><div><strong>#${score.current_rank} · ${escapeHtml(typeLabel(entry?.type || "entry"))}</strong>${movement}</div><p>Score ${score.score} = base ${score.base_score} + learned ${score.learned_boost}. Base rank #${score.base_rank}.</p></div>`;
    }).join("")}`);
}

function showReviewQueue() {
  const queue = state.care.review_queue || [];
  const actionFor = (item) => {
    if (item.kind === "contradiction") return `<button type="button" class="primary-button" data-action="conflict-passport" data-conflict-id="${escapeHtml(item.id)}">Review two sources</button>`;
    if (item.kind === "ai_suggestion") return `<button type="button" class="primary-button" data-action="passport" data-highlight-id="${escapeHtml(item.id)}">Review evidence</button>`;
    if (item.kind === "teach_back") return `<button type="button" class="primary-button" data-action="teach-back-review" data-teach-back-id="${escapeHtml(item.id)}">Review patient response</button>`;
    return `<button type="button" class="primary-button" data-action="verification-card" data-entry-id="${escapeHtml(item.source_entry_ids[0])}">Review current version</button>`;
  };
  showDialog("Bounded review queue", "REVIEW · MAXIMUM 7", `
    <p class="legend-note review-intro">No infinite feed and no “accept all”. Every card explains why it returned and ends with one human decision.</p>
    ${queue.length ? queue.map((item, index) => `
      <article class="review-card review-${escapeHtml(item.severity)}">
        <div class="review-card-top"><span>${index + 1} / ${queue.length}</span><span>${escapeHtml(item.kind.replaceAll("_", " "))}</span></div>
        <h3>${escapeHtml(item.title)}</h3>
        <p><strong>Why now:</strong> ${escapeHtml(item.reason)}</p>
        ${actionFor(item)}
      </article>`).join("") : `<div class="queue-cleared"><strong>✓ Review queue cleared</strong><p>Nothing else is due under the prototype review policy.</p></div>`}
  `);
}

function showVerificationCard(entryId) {
  const entry = state.care.entries.find((item) => item.id === entryId);
  if (!entry) return notify("Source entry is no longer visible.", true);
  const freshness = entry.freshness || {};
  showDialog("Verify current fact", "EVIDENCE SIDE", `
    <article class="verification-card">
      <div class="review-card-top"><span>${escapeHtml(typeLabel(entry.type))}</span><span>immutable v${entry.version}</span></div>
      <blockquote>${escapeHtml(entry.content)}</blockquote>
      <dl class="evidence-meta"><div><dt>Author</dt><dd>${escapeHtml(entry.author_name || entry.author_role)}</dd></div><div><dt>Pointer</dt><dd>${escapeHtml(entry.provenance_pointer || "manual")}</dd></div></dl>
      <div class="freshness-panel freshness-${escapeHtml(freshness.state || "unknown")}"><strong>${escapeHtml(freshnessLabel(freshness))}</strong><span>${escapeHtml(freshness.reason || "")}</span><small>${escapeHtml(freshness.policy || "")}</small></div>
      ${state.care.actor.role === "clinician" ? `<div class="evidence-actions"><span>This action verifies only the exact version shown above.</span><button type="button" data-action="verify-entry" data-entry-id="${escapeHtml(entry.id)}" data-entry-version="${entry.version}" data-outcome="needs_review">Needs review</button><button type="button" class="primary-button" data-action="verify-entry" data-entry-id="${escapeHtml(entry.id)}" data-entry-version="${entry.version}" data-outcome="confirmed">Confirm v${entry.version}</button></div>` : ""}
    </article>
  `);
}

function openTeachBack(entryId) {
  const instruction = state.care.entries.find((item) => item.id === entryId);
  if (!instruction) return notify("Patient instruction is no longer visible.", true);
  showDialog("Teach back in your own words", "PATIENT UNDERSTANDING", `
    <p class="legend-note">Read the instruction, then explain what you would do. A keyword checklist may flag possible gaps, but only your clinician confirms understanding.</p>
    <article class="teachback-summary"><p class="eyebrow">INSTRUCTION · IMMUTABLE V${instruction.version}</p><blockquote>${escapeHtml(instruction.content)}</blockquote></article>
    <div class="form-field"><label for="teachback-response">What did you understand?</label><textarea id="teachback-response" placeholder="In my own words, I should..."></textarea></div>
    <div class="dialog-actions"><button value="cancel">Cancel</button><button type="button" class="primary-button" id="submit-teachback">Submit for clinician review</button></div>`);
  document.querySelector("#submit-teachback").addEventListener("click", async () => {
    try {
      const result = await api(`/api/entries/${encodeURIComponent(entryId)}/teach-back`, {
        method: "POST",
        body: JSON.stringify({ response_text: document.querySelector("#teachback-response").value }),
      });
      await loadCareNote();
      document.querySelector("#dialog-content").innerHTML = `
        <div class="privacy-success"><span>✓</span><div><strong>Queued for clinician confirmation</strong><p>${escapeHtml(result.disclaimer || "A clinician makes the final decision.")}</p></div></div>
        <div class="teachback-summary"><strong>${(Number(result.coverage) * 100).toFixed(0)}% keyword coverage</strong>
          <div class="concept-list">${result.matched_concepts.map((item) => `<span>✓ ${escapeHtml(item)}</span>`).join("")}${result.missing_concepts.map((item) => `<span class="missing">Review: ${escapeHtml(item)}</span>`).join("")}</div>
        </div><div class="dialog-actions"><button value="cancel">Close</button></div>`;
      notify("Teach-back queued for clinician review");
    } catch (error) { notify(error.message, true); }
  });
}

async function showTeachBackReview(attemptId) {
  try {
    const attempt = await api(`/api/teach-backs/${encodeURIComponent(attemptId)}`);
    const decision = attempt.status === "pending" && state.care.actor.role === "clinician"
      ? `<div class="evidence-actions"><span>Keyword coverage is a screening aid, not the final decision.</span><button type="button" data-action="teach-back-decision" data-teach-back-id="${escapeHtml(attempt.id)}" data-decision="needs_clarification">Needs clarification</button><button type="button" class="primary-button" data-action="teach-back-decision" data-teach-back-id="${escapeHtml(attempt.id)}" data-decision="confirmed">Confirm understanding</button></div>`
      : `<div class="decision-trail decision-${attempt.status === "confirmed" ? "accepted" : "rejected"}"><strong>${escapeHtml(attempt.status.replaceAll("_", " "))}</strong><span>${escapeHtml(attempt.decided_by_name || "Awaiting clinician")}${attempt.decided_at ? ` · ${formatDate(attempt.decided_at)}` : ""}</span></div>`;
    showDialog("Patient teach-back", "HUMAN-CONFIRMED UNDERSTANDING", `
      <div class="passport-head"><div><span class="passport-kicker">Instruction v${attempt.instruction_version}</span><h3>${escapeHtml(attempt.patient_name)} explained the instruction</h3></div><span class="passport-status status-${escapeHtml(attempt.status)}">${escapeHtml(attempt.status.replaceAll("_", " "))}</span></div>
      <article class="teachback-summary"><p class="eyebrow">PATIENT'S OWN WORDS</p><blockquote>${escapeHtml(attempt.response_text)}</blockquote></article>
      <div class="teachback-summary"><strong>${(Number(attempt.coverage) * 100).toFixed(0)}% deterministic keyword coverage</strong><div class="concept-list">${attempt.matched_concepts.map((item) => `<span>✓ ${escapeHtml(item)}</span>`).join("")}${attempt.missing_concepts.map((item) => `<span class="missing">Possible gap: ${escapeHtml(item)}</span>`).join("")}</div></div>
      <p class="legend-note">No diagnosis or instruction is generated. The response is bound to the exact instruction version shown above.</p>${decision}`);
  } catch (error) { notify(error.message, true); }
}

async function showAccessReport() {
  try {
    const report = await api(`/api/patient-access-report?patient_id=${encodeURIComponent(state.patientId)}`);
    showDialog("Who viewed my record?", "PATIENT ACCESS TRANSPARENCY", `
      <div class="brief-banner"><strong>${report.total_accesses} recorded accesses in the last 7 days</strong><span>${escapeHtml(report.disclaimer)}</span></div>
      <section class="brief-section">${report.visitors.length ? report.visitors.map((visitor) => `<article class="access-row"><div><strong>${escapeHtml(visitor.display_name)}</strong><small>${escapeHtml(roleLabel(visitor.role))} · ${escapeHtml(visitor.purpose.replaceAll("_", " "))}</small></div><div><strong>${visitor.view_count}×</strong><small>Last ${formatDate(visitor.last_accessed_at)}</small></div></article>`).join("") : `<p class="legend-note">No clinical-user access has been recorded in this seven-day window.</p>`}</section>`);
  } catch (error) { notify(error.message, true); }
}

async function showSecuritySandbox() {
  try {
    const report = await api("/api/security/sandbox", {
      method: "POST",
      body: JSON.stringify({ patient_id: state.patientId }),
    });
    showDialog("Security attack sandbox", "SAFE LOCAL POLICY PROBES", `
      <div class="brief-banner"><strong>${report.all_blocked ? "All synthetic attacks blocked" : "A policy probe needs attention"}</strong><span>${escapeHtml(report.disclaimer)}</span></div>
      <section class="brief-section">${report.scenarios.map((item) => `<article class="security-result ${escapeHtml(item.status)}"><header><strong>${escapeHtml(item.id.replaceAll("_", " "))}</strong><span class="freshness freshness-${item.status === "blocked" ? "fresh" : "overdue"}">${escapeHtml(item.status)}</span></header><p><strong>Attempt:</strong> ${escapeHtml(item.attack)}</p><p><strong>Observed:</strong> ${escapeHtml(item.observed)}</p><p><strong>Control:</strong> ${escapeHtml(item.protection)}</p></article>`).join("")}</section>`);
  } catch (error) { notify(error.message, true); }
}

async function verifyAuditChain() {
  try {
    const result = await api(`/api/audit/verify?patient_id=${encodeURIComponent(state.patientId)}`);
    showDialog("Audit chain verification", "TAMPER-EVIDENT METADATA", `
      <div class="chain-proof ${result.valid ? "" : "invalid"}"><strong>${result.valid ? "✓ Chain verified" : "× Chain break detected"}</strong><span>${result.event_count} content-free audit events · ${escapeHtml(result.algorithm)}</span><code>head ${escapeHtml(result.head_hash)}</code>${result.first_broken_event_id ? `<span>First broken event: ${result.first_broken_event_id}</span>` : ""}</div>
      <p class="legend-note">${escapeHtml(result.claim)} Scope: ${escapeHtml(result.scope)}.</p>
      <div class="dialog-actions"><button type="button" data-action="show-audit">Back to audit trail</button></div>`);
  } catch (error) { notify(error.message, true); }
}

function showTimeMachine() {
  const presets = [
    { label: "25 Aug · 08:30 — before the cough note", at: "2026-08-25T08:30:00Z" },
    { label: "25 Aug · 09:00 — before spirometry & reconciliation", at: "2026-08-25T09:00:00Z" },
    { label: "25 Aug · 09:15 — after spirometry, before reconciliation", at: "2026-08-25T09:15:00Z" },
  ];
  showDialog("Time machine", "MEDICO-LEGAL REPLAY", `
    <p class="legend-note">Reconstruct the ten-second view a clinician saw at a past moment. Entry content and versions are exact (immutable); learned priority and task decision state are current-only approximations.</p>
    <div class="time-presets">${presets.map((preset) => `<button type="button" class="quiet-button" data-action="time-travel" data-at="${escapeHtml(preset.at)}">${escapeHtml(preset.label)}</button>`).join("")}</div>
    <div id="time-machine-output"><p class="legend-note">Choose a moment above to replay the Glance.</p></div>`);
}

function renderTimeMachineGlance(snapshot) {
  const toneFor = (risk) => (risk === "critical" ? "critical" : risk === "high" ? "attention" : "action");
  const thenCards = (snapshot.glance || []).map((item) => `
    <article class="signal signal-${toneFor(item.risk_level)}">
      <div class="signal-top"><span class="signal-label">${escapeHtml(typeLabel(item.type))}</span><span class="confidence">v${item.version} · score ${item.score}</span></div>
      <p>${escapeHtml(item.content)}</p>
    </article>`).join("") || `<p class="legend-note">No entries existed at this moment.</p>`;
  const nowTop = (state.care.importance || []).slice(0, 3).map((item) => {
    const entry = state.care.entries.find((candidate) => candidate.id === item.entry_id);
    return `<li>${escapeHtml(typeLabel(entry?.type || "entry"))} <span class="confidence">score ${item.score}</span></li>`;
  }).join("");
  return `
    <div class="time-machine-split">
      <section><p class="eyebrow">AT ${escapeHtml(snapshot.at)}</p><div class="time-cards">${thenCards}</div></section>
      <section><p class="eyebrow">NOW</p><ul class="legend-list">${nowTop || "<li>No current ranking</li>"}</ul></section>
    </div>
    <p class="legend-note">${escapeHtml(snapshot.precision.disclaimer)}</p>`;
}

async function showConflictPassport(conflictId) {
  try {
    const passport = await api(`/api/patients/${encodeURIComponent(state.patientId)}/conflicts/${encodeURIComponent(conflictId)}/passport`);
    const decisionButtons = passport.status === "suggested" && state.care.actor.role === "clinician" && passport.evidence_token
      ? `<div class="evidence-actions"><span>Both immutable sources witnessed.</span><button type="button" data-action="conflict-decision" data-conflict-id="${escapeHtml(passport.id)}" data-decision="dismissed" data-evidence-token="${escapeHtml(passport.evidence_token)}">Dismiss rule match</button><button type="button" class="primary-button" data-action="conflict-decision" data-conflict-id="${escapeHtml(passport.id)}" data-decision="acknowledged" data-evidence-token="${escapeHtml(passport.evidence_token)}">Acknowledge conflict</button></div>`
      : `<div class="decision-trail decision-${passport.status === "dismissed" ? "rejected" : "accepted"}"><strong>${escapeHtml(passport.status)}</strong><span>${escapeHtml(passport.decided_by_name || "Clinician")} · ${passport.decided_at ? formatDate(passport.decided_at) : ""}</span></div>`;
    clearReturnContext();
    showDialog("Consistency Watcher", "TWO-SOURCE EVIDENCE", `
      <div class="passport-head"><div><span class="passport-kicker">${escapeHtml(passport.rule_id)}</span><h3>${escapeHtml(passport.title)}</h3></div><span class="passport-status">${escapeHtml(passport.status)}</span></div>
      <p class="conflict-summary">${escapeHtml(passport.summary)}</p>
      <div class="conflict-sources">${passport.sources.map((source, index) => `
        <section class="passport-card passport-source">
          <p class="eyebrow">SOURCE ${index + 1} · ${escapeHtml(source.source_type.replaceAll("_", " "))}</p>
          <blockquote>${escapeHtml(source.quote)}</blockquote>
          <dl><div><dt>Immutable version</dt><dd>${escapeHtml(source.entry_id)} · v${source.entry_version}</dd></div><div><dt>Exact span</dt><dd>${source.start_offset}–${source.end_offset}</dd></div></dl>
          <button type="button" class="quiet-button" data-action="source-span" data-conflict-id="${escapeHtml(passport.id)}" data-entry-id="${escapeHtml(source.entry_id)}" data-start-offset="${source.start_offset}" data-end-offset="${source.end_offset}">Verify in timeline</button>
        </section>`).join("")}</div>
      <p class="legend-note">Rule-based consistency check only. It does not diagnose, prescribe, or silently change the record.</p>
      ${decisionButtons}
    `);
  } catch (error) { notify(error.message, true); }
}

async function showPrevisitBrief() {
  try {
    const brief = await api(`/api/previsit-brief?patient_id=${encodeURIComponent(state.patientId)}`);
    const sourceButton = (entryId) => `<button type="button" class="source-link" data-action="source-entry" data-entry-id="${escapeHtml(entryId)}">View source</button>`;
    showDialog("Pre-visit Brief", "DETERMINISTIC · SOURCE-LINKED", `
      <div class="brief-banner"><strong>What to discuss in this visit</strong><span>${escapeHtml(brief.disclaimer)}</span></div>
      <section class="brief-section"><h3>Safety and high-risk facts</h3>${brief.safety_facts.map((item) => `<article><div><strong>${escapeHtml(typeLabel(item.type))}</strong><span class="freshness freshness-${escapeHtml(item.freshness.state)}">${escapeHtml(freshnessLabel(item.freshness))}</span></div><p>${escapeHtml(item.content)}</p>${sourceButton(item.entry_id)}</article>`).join("") || `<p class="legend-note">No high-risk facts.</p>`}</section>
      <section class="brief-section"><h3>Consistency alerts</h3>${brief.consistency_alerts.map((item) => `<article><div><strong>${escapeHtml(item.title)}</strong><span class="severity-critical">critical</span></div><p>${escapeHtml(item.summary)}</p><button type="button" class="source-link" data-action="conflict-passport" data-conflict-id="${escapeHtml(item.id)}">Review both sources</button></article>`).join("") || `<p class="legend-note">No active rule-based conflicts.</p>`}</section>
      <section class="brief-section"><h3>Open work</h3>${brief.open_tasks.map((task) => `<article><strong>${escapeHtml(task.title)}</strong><p>${escapeHtml(task.assignee_name || "Unassigned")}${task.due_at ? ` · due ${formatDate(task.due_at)}` : ""}</p>${task.source_entry_id ? sourceButton(task.source_entry_id) : ""}</article>`).join("") || `<p class="legend-note">No open work.</p>`}</section>
      <section class="brief-section"><h3>Patient questions</h3>${brief.patient_questions.map((item) => `<article><blockquote>${escapeHtml(item.question)}</blockquote>${sourceButton(item.entry_id)}</article>`).join("") || `<p class="legend-note">No unresolved question was extracted.</p>`}</section>
      <section class="brief-section"><h3>Recent changes</h3>${brief.recent_changes.map((item) => `<article><div><strong>${escapeHtml(typeLabel(item.type))}</strong><span>${formatDate(item.created_at)}</span></div><p>${escapeHtml(item.content)}</p>${sourceButton(item.entry_id)}</article>`).join("")}</section>
    `);
  } catch (error) { notify(error.message, true); }
}

async function showPassport(highlightId) {
  try {
    const passport = await api(`/api/highlights/${highlightId}/passport`);
    const learning = passport.learning;
    const budget = learning.influence_budget || { used: 0, cap: 4, remaining: 4 };
    const decision = passport.decision;
    const statusLabel = decision.status === "suggested" ? "Needs clinician review" : decision.status;
    const movement = learning.rank_change > 0
      ? `Moved up ${learning.rank_change} place${learning.rank_change === 1 ? "" : "s"}`
      : learning.rank_change < 0
        ? `Moved down ${Math.abs(learning.rank_change)} place${learning.rank_change === -1 ? "" : "s"}`
        : "No movement from base rank";
    const redactions = passport.privacy.redaction_counts
      ? Object.entries(passport.privacy.redaction_counts)
        .map(([key, count]) => `<span>${escapeHtml(key.replaceAll("_", " "))}: ${Number(count)}</span>`).join("")
      : "";
    const privacyDetail = passport.privacy.evidence === "scribe_audit"
      ? `Pre-processing audit recorded${passport.privacy.payload_sha256 ? ` · payload hash ${escapeHtml(passport.privacy.payload_sha256.slice(0, 12))}…` : ""}`
      : passport.privacy.evidence === "seeded_synthetic_record"
        ? "Synthetic seed record; no external model or PHI payload was used."
        : "Human-authored source; AI gateway is not applicable.";
    const decisionButtons = decision.status === "suggested" && state.care.actor.role === "clinician" && passport.evidence_token
      ? `<div class="evidence-actions">
          <span>Evidence witnessed · decision will bind to v${passport.evidence.entry_version}</span>
          <button type="button" data-action="decision" data-highlight-id="${escapeHtml(passport.highlight_id)}" data-decision="rejected" data-evidence-token="${escapeHtml(passport.evidence_token)}">Reject</button>
          <button type="button" class="primary-button" data-action="decision" data-highlight-id="${escapeHtml(passport.highlight_id)}" data-decision="accepted" data-evidence-token="${escapeHtml(passport.evidence_token)}">Accept evidence</button>
        </div>`
      : "";
    showDialog("Trust Passport", "EVIDENCE-TO-DECISION", `
      <div class="passport-head">
        <div><span class="passport-kicker">${escapeHtml(passport.authority.source_type.replaceAll("_", " "))}</span><h3>${escapeHtml(passport.risk_reason)}</h3></div>
        <span class="passport-status status-${escapeHtml(decision.status)}">${escapeHtml(statusLabel)}</span>
      </div>
      ${passport.evidence.superseded ? `<div class="evidence-warning"><strong>Source superseded.</strong> This passport resolves immutable v${passport.evidence.entry_version}; the timeline now has v${passport.evidence.current_entry_version}. This freshly opened evidence token is required before deciding.</div>` : ""}
      <ol class="passport-flow" aria-label="Trust evidence flow">
        <li><span>1</span><strong>Source bound</strong><small>Exact span + immutable version</small></li>
        <li><span>2</span><strong>Clinician controlled</strong><small>${decision.final ? "Final decision recorded" : "Awaiting final decision"}</small></li>
        <li><span>3</span><strong>Learning visible</strong><small>Base rank #${learning.base_rank} → #${learning.current_rank}</small></li>
        <li><span>4</span><strong>Retention explained</strong><small>${escapeHtml(passport.retention.tier)} tier</small></li>
      </ol>
      <div class="passport-grid">
        <section class="passport-card passport-source"><p class="eyebrow">PROVENANCE</p><blockquote>${escapeHtml(passport.evidence.quote)}</blockquote><dl><div><dt>Pointer</dt><dd>${escapeHtml(passport.evidence.provenance_pointer || "manual")}</dd></div><div><dt>Immutable span</dt><dd>v${passport.evidence.entry_version} · ${passport.evidence.start_offset}–${passport.evidence.end_offset}</dd></div></dl><button type="button" class="quiet-button" data-action="source" data-highlight-id="${escapeHtml(passport.highlight_id)}">Verify exact source in timeline</button></section>
        <section class="passport-card"><p class="eyebrow">CLINICAL AUTHORITY</p><strong class="passport-value">${decision.final ? escapeHtml(decision.status) : "Pending"}</strong><p>${decision.final ? `${escapeHtml(decision.decided_by_name || "Clinician")} · ${decision.decided_at ? formatDate(decision.decided_at) : "timestamp unavailable"}` : "Only a clinician can make the one final accept/reject decision."}</p></section>
        <section class="passport-card"><p class="eyebrow">LEARNING IMPACT</p><strong class="passport-value">#${learning.base_rank} → #${learning.current_rank}</strong><p>${escapeHtml(movement)} · score ${learning.score} = ${learning.base_score} base + ${learning.learned_boost} learned.</p><div class="budget-meter"><span style="width:${Math.min(100, (Number(budget.used) / Number(budget.cap || 1)) * 100)}%"></span></div><small>AI ranking influence ${Number(budget.used).toFixed(3)} / ${Number(budget.cap).toFixed(3)} · ${Number(budget.remaining).toFixed(3)} remaining</small>${learning.matched_features?.length ? `<div class="passport-tags">${learning.matched_features.slice(0, 5).map((feature) => `<span>${escapeHtml(feature.replace(/^kw:|^entity:/, ""))}</span>`).join("")}</div>` : ""}</section>
        <section class="passport-card"><p class="eyebrow">VERIFICATION FRESHNESS</p><strong class="passport-value">${escapeHtml(freshnessLabel(passport.verification))}</strong><p>${escapeHtml(passport.verification.reason)} · ${escapeHtml(passport.verification.policy)}</p></section>
        <section class="passport-card"><p class="eyebrow">PRIVACY BOUNDARY</p><strong class="passport-value">${passport.privacy.generated_by_ai ? "Local deterministic demo" : "Human source"}</strong><p>${escapeHtml(privacyDetail)}</p>${redactions ? `<div class="passport-tags">${redactions}</div>` : ""}</section>
        <section class="passport-card"><p class="eyebrow">DATA LIFECYCLE</p><strong class="passport-value"><span class="tier-pill tier-${escapeHtml(passport.retention.tier)}">${escapeHtml(passport.retention.tier)}</span></strong><p>${passport.retention.age_days} days old · ${passport.retention.protected ? "Safety protected" : "Standard retention"}</p><small>${escapeHtml(passport.retention.policy)}</small></section>
      </div>
      ${decisionButtons}`);
  } catch (error) { notify(error.message, true); }
}

async function showStorage() {
  try {
    const rows = await api(`/api/decay-preview?patient_id=${encodeURIComponent(state.patientId)}`);
    const counts = rows.reduce((result, row) => ({ ...result, [row.tier]: (result[row.tier] || 0) + 1 }), {});
    showDialog("Storage & retention lens", "HYBRID DATA LIFECYCLE", `
      <p class="legend-note">This is a policy preview: the prototype classifies records without pretending that physical archival has already occurred.</p>
      <div class="storage-summary">
        ${["hot", "warm", "cold"].map((tier) => `<div><strong>${counts[tier] || 0}</strong><span>${tier}</span></div>`).join("")}
      </div>
      <div class="storage-list">${rows.map((row) => {
        const entry = state.care.entries.find((item) => item.id === row.entry_id);
        return `<article class="storage-item">
          <div><span class="tier-pill tier-${row.tier}">${escapeHtml(row.tier)}</span><strong>${escapeHtml(typeLabel(entry?.type || "entry"))}</strong></div>
          <p>${row.age_days} days old · ${row.protected ? "Safety protected" : "Standard retention"}</p>
          <small>${escapeHtml(row.policy)}</small>
        </article>`;
      }).join("")}</div>`);
  } catch (error) { notify(error.message, true); }
}

async function focusHighlight(highlightId) {
  try {
    const source = await api(`/api/highlights/${highlightId}/source`);
    state.focus = {
      entryId: source.entry_id,
      startOffset: source.start_offset,
      endOffset: source.end_offset,
    };
    renderTimeline();
    document.getElementById(source.entry_id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    notify(`Resolved to immutable source v${source.entry_version}`);
  } catch (error) { notify(error.message, true); }
}

async function handleAction(button) {
  const action = button.dataset.action;
  if (action === "time-travel") {
    const output = document.querySelector("#time-machine-output");
    if (output) output.innerHTML = `<p class="legend-note">Replaying…</p>`;
    try {
      const snapshot = await api(`/api/care-note/as-of?patient_id=${encodeURIComponent(state.patientId)}&at=${encodeURIComponent(button.dataset.at)}`);
      if (output) output.innerHTML = renderTimeMachineGlance(snapshot);
    } catch (error) {
      if (output) output.innerHTML = "";
      notify(error.message, true);
    }
    return;
  }
  if (action === "teach-back") return openTeachBack(button.dataset.entryId);
  if (action === "teach-back-review") return showTeachBackReview(button.dataset.teachBackId);
  if (action === "verify-audit-chain") return verifyAuditChain();
  if (action === "show-audit") return showAudit();
  if (action === "return-context") {
    if (state.returnContext?.kind === "conflict") return showConflictPassport(state.returnContext.id);
    return;
  }
  if (action === "source") {
    if (dialog.open) dialog.close();
    return focusHighlight(button.dataset.highlightId);
  }
  if (action === "passport") return showPassport(button.dataset.highlightId);
  if (action === "conflict-passport") return showConflictPassport(button.dataset.conflictId);
  if (action === "verification-card") return showVerificationCard(button.dataset.entryId);
  if (action === "source-span") {
    if (dialog.open) dialog.close();
    const conflict = (state.care.conflicts || []).find((item) => item.id === button.dataset.conflictId);
    state.returnContext = { kind: "conflict", id: button.dataset.conflictId, title: conflict?.title };
    updateReturnContext();
    state.focus = {
      entryId: button.dataset.entryId,
      startOffset: Number(button.dataset.startOffset),
      endOffset: Number(button.dataset.endOffset),
    };
    renderTimeline();
    document.getElementById(button.dataset.entryId)?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (action === "source-entry") {
    if (dialog.open) dialog.close();
    state.focus = null;
    renderTimeline();
    return document.getElementById(button.dataset.entryId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (action === "edit") return editEntry(button.dataset.entryId);
  if (action === "history") return showHistory(button.dataset.entryId);
  if (action === "comment") {
    const body = window.prompt("Add an internal threaded comment");
    if (!body) return;
    try {
      await api(`/api/entries/${button.dataset.entryId}/comments`, { method: "POST", body: JSON.stringify({ body, assignee_id: state.care.actor.role === "staff" ? "clinician-lim" : "staff-jia" }) });
      await loadCareNote();
      notify("Comment added and audit metadata recorded");
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "resolve-comment") {
    try {
      await api(`/api/comments/${button.dataset.commentId}`, { method: "PATCH", body: JSON.stringify({ resolved: true }) });
      await loadCareNote();
      notify("Comment resolved");
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "decision") {
    try {
      await api(`/api/highlights/${button.dataset.highlightId}/decision`, { method: "POST", body: JSON.stringify({ decision: button.dataset.decision, evidence_token: button.dataset.evidenceToken }) });
      if (dialog.open) dialog.close();
      await loadCareNote();
      notify(`Suggestion ${button.dataset.decision}`);
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "conflict-decision") {
    try {
      await api(`/api/patients/${encodeURIComponent(state.patientId)}/conflicts/${encodeURIComponent(button.dataset.conflictId)}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision: button.dataset.decision, evidence_token: button.dataset.evidenceToken }),
      });
      if (dialog.open) dialog.close();
      clearReturnContext();
      await loadCareNote();
      notify(`Consistency alert ${button.dataset.decision}`);
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "teach-back-decision") {
    try {
      await api(`/api/teach-backs/${encodeURIComponent(button.dataset.teachBackId)}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision: button.dataset.decision }),
      });
      if (dialog.open) dialog.close();
      await loadCareNote();
      notify(button.dataset.decision === "confirmed" ? "Understanding confirmed by clinician" : "Clarification requested");
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "verify-entry") {
    try {
      await api(`/api/entries/${encodeURIComponent(button.dataset.entryId)}/verify`, {
        method: "POST",
        body: JSON.stringify({ expected_version: Number(button.dataset.entryVersion), outcome: button.dataset.outcome }),
      });
      if (dialog.open) dialog.close();
      await loadCareNote();
      notify(button.dataset.outcome === "confirmed" ? "Current version verified" : "Marked for further review");
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "task-status") {
    const targetStatus = button.dataset.currentStatus === "done" ? "open" : "done";
    button.disabled = true;
    try {
      await api(`/api/tasks/${button.dataset.taskId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: targetStatus }),
      });
      await loadCareNote();
      notify(targetStatus === "done" ? "Task completed · Glance and audit updated" : "Task reopened · Glance updated");
    } catch (error) {
      notify(error.message, true);
      await loadCareNote();
    }
    return;
  }
  if (action === "highlight") {
    const entry = state.care.entries.find((item) => item.id === button.dataset.entryId);
    const quote = window.prompt("Exact phrase to highlight", entry.content.split(".")[0]);
    if (!quote) return;
    const start = entry.content.indexOf(quote);
    if (start < 0) return notify("The phrase must exactly match the source text.", true);
    try {
      await api(`/api/patients/${encodeURIComponent(state.patientId)}/highlights`, { method: "POST", body: JSON.stringify({ entry_id: entry.id, start_offset: start, end_offset: start + quote.length, risk_reason: "Clinician manually highlighted this source phrase", status: state.care.actor.role === "clinician" ? "accepted" : "suggested" }) });
      await loadCareNote();
      notify("Highlight created with immutable provenance");
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === "revert") {
    try {
      await api(`/api/entries/${button.dataset.entryId}/revert`, { method: "POST", body: JSON.stringify({ target_version: Number(button.dataset.targetVersion), expected_version: Number(button.dataset.currentVersion) }) });
      dialog.close();
      await loadCareNote();
      notify("Reverted by creating a new immutable version");
    } catch (error) { notify(error.message, true); }
  }
}

roleButton.addEventListener("click", () => { roleMenu.hidden = !roleMenu.hidden; });
roleMenu.addEventListener("click", async (event) => {
  const option = event.target.closest("[data-actor]");
  if (!option) return;
  state.focus = null;
  clearReturnContext();
  roleMenu.hidden = true;
  try {
    await startSession(option.dataset.actor);
    await loadCareNote({ announce: true });
  } catch (error) { notify(error.message, true); }
});
document.addEventListener("click", (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) handleAction(actionButton);
  if (!event.target.closest(".role-switch")) roleMenu.hidden = true;
});
document.querySelector("#add-note-button").addEventListener("click", openAddNote);
document.querySelector("#scribe-button").addEventListener("click", openScribe);
document.querySelector("#audit-button").addEventListener("click", showAudit);
document.querySelector("#storage-button").addEventListener("click", showStorage);
document.querySelector("#why-button").addEventListener("click", showWhy);
document.querySelector("#brief-button").addEventListener("click", showPrevisitBrief);
document.querySelector("#review-button").addEventListener("click", showReviewQueue);
document.querySelector("#access-report-button").addEventListener("click", showAccessReport);
document.querySelector("#security-button").addEventListener("click", showSecuritySandbox);
document.querySelector("#time-machine-button").addEventListener("click", showTimeMachine);

loadCareNote();
