# Demo script (4–5 minutes)

## 0:00–0:25 — Frame the trust problem

“A longitudinal record should help a clinician see what matters, bring forgotten facts back at the right time, and still require a human to witness the evidence before acting.”

Show Maya Tan’s clinician view. Point out that Glance stays at three cards and that AI, staff, and clinician entries remain visibly distinct.

## 0:25–1:05 — Consistency Watcher: two sources, one human decision

1. Open **Review both immutable sources** on the medication–allergy conflict.
2. Show the exact `penicillin allergy` span from the clinician note and `amoxicillin` span from medication reconciliation.
3. State that this is a deterministic consistency rule—not a diagnosis or prescribing engine.
4. Click **Verify in timeline**, then use the floating **Back to conflict review** button to return without scrolling.
5. Point out that both source versions must be witnessed before **Acknowledge conflict** or **Dismiss rule match** is available.

## 1:05–1:40 — Bounded review + pre-visit assembly

1. Open **Review queue** and show that it is capped at seven cards, ordered by severity, with a visible “Why now” and no “accept all”.
2. Open **Pre-visit brief**.
3. Walk through safety/high-risk facts, consistency alerts, open work, patient questions, and recent changes.
4. Emphasize that every item links back to its source and the brief generates no diagnosis or treatment recommendation.

## 1:40–2:25 — Trust Passport: evidence before decision

1. Open the cough suggestion’s **Trust Passport**.
2. Show the exact immutable phrase, source version and offsets.
3. Show **AI ranking influence 0 / 4** and explain that learned priority has a hard budget.
4. Show verification freshness: the system knows whether this exact version was verified, not merely when the record was last edited.
5. Click **Verify exact source in timeline**, reopen the Passport, and then accept or reject. The decision request is bound to the witnessed version; a later source edit invalidates the old token.
6. Reopen the Passport to show reviewer, time, outcome, and retained source.

## 2:25–3:05 — Visible no-PHI boundary + patient question workflow

1. Click **AI scribe** and paste a synthetic sentence such as: `Maya Tan, NRIC S1234567A, phone 91234567, asks whether the new inhaler causes night cough?`
2. Click **Preview privacy boundary**.
3. Show browser-memory text beside the summarizer payload: `[REDACTED_NAME]`, `[REDACTED_ID]`, `[REDACTED_PHONE]`, counts, and SHA-256 digest.
4. Point out **Not persisted · no external call**. The reviewer can inspect the privacy transformation before creating the AI note.
5. Explain that a patient question becomes a real clinician review task; AI generates the question, never the medical answer.

## 3:05–3:50 — Patient agency: teach-back + access transparency

1. Switch to **Patient**; show that internal content disappears.
2. On the patient instruction, open **Teach back in my own words**. Explain that keyword coverage can flag a gap, but only a clinician confirms understanding and the attempt is bound to the instruction version.
3. Open **Who viewed my record?** and show role, count, and last-access time without any clinical content.
4. Switch back to **Clinician**; show that a submitted teach-back appears in the bounded queue for a final human decision.

## 3:50–4:30 — Attack sandbox + tamper-evident audit

1. Open **Security sandbox** and show the six safe local probes: cross-clinic read, patient internal read, staff final decision, forged token, stored XSS, and weak production secret.
2. Open **Audit trail** and click **Verify SHA-256 chain**. State that this is tamper-evident metadata, not a blockchain or external notarisation claim.
3. Mention that competing edits still use `expected_version`, so one succeeds and the stale writer receives HTTP 409.

## 4:30–4:55 — Time machine: what did the clinician see at decision time?

1. Click **Time machine** and pick **25 Aug 09:00**.
2. Show the reconstructed Glance: allergy + cough, but no spirometry and no amoxicillin reconciliation (neither had arrived yet).
3. Switch to **09:15**, then compare with **NOW** — the spirometry action now leads the ten-second view.
4. State the honesty boundary: entry content is exact via immutable versions; learned priority and task decision state are current-only approximations.

## 4:55–5:25 — Engineering proof and close

Show terminal output:

```bash
./scripts/test.sh
python3 scripts/benchmark_glance.py
python3 scripts/benchmark_http.py
```

Close with: “Glance makes important facts visible; Review makes them return when due; Teach-back closes the patient-understanding loop. Every AI decision is evidence-bound, every audit event is chain-verifiable, and patients can see who accessed their record. The prototype demonstrates that trust architecture with 33 automated tests and a healthy non-root Docker container.”
