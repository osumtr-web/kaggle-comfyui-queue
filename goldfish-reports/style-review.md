# Style Review: Kaggle ComfyUI Queue Design Draft

**Reviewer:** Style Goldfish (Technical Copy Editor)
**Target:** `/docs/architecture/design_draft.md`
**Date:** 2026-11-03

---

## Summary

The document is largely well-structured and factual. Most sections are concise and actionable. Findings below target minor instances of subjective framing, filler phrasing, and imprecise terminology that reduce technical precision. No blockers identified; all findings are Major or Nit severity.

---

## Findings

### 1. Subjective / Unverified Claim: "proven Kaggle deployment patterns"

- **Severity:** Major
- **Affected Scope:** Section 1 (Overview), Line 5
- **Style Rationale:** "Proven" is a subjective quality judgment. The document does not define what constitutes proof (e.g., production uptime metrics, test results, peer review). Replace with a factual descriptor such as "documented" or "observed in reference videos."
- **Suggested Revision:** "...two reference videos demonstrating documented Kaggle deployment patterns."

### 2. Filler Phrase: "entirely"

- **Severity:** Nit
- **Affected Scope:** Section 3.2 (Watchdog Mechanism), Line 59
- **Style Rationale:** "This avoids indentation issues entirely" — "entirely" adds no measurable information. The avoidance of indentation issues is binary; either they are avoided or they are not. Remove the adverb.
- **Suggested Revision:** "This avoids indentation issues and matches the pattern from the reference video."

### 3. Imprecise Terminology: "matches the proven pattern"

- **Severity:** Major
- **Affected Scope:** Section 3.2 (Watchdog Mechanism), Line 59
- **Style Rationale:** "Proven pattern" repeats the subjective framing from Finding #1. Additionally, "matches" is vague — does it mean identical implementation, similar architecture, or conceptual alignment? Specify the relationship.
- **Suggested Revision:** "...and follows the same shell-backgrounding approach shown in the reference video."

### 4. Marketing-Adjacent Phrasing: "fast inference"

- **Severity:** Nit
- **Affected Scope:** Section 3.5 (Model Stack), Line 122
- **Style Rationale:** "Fast inference" is a relative, unmeasured claim. Without a benchmark (e.g., "generates 1024×1024 image in <8s on T4"), the term conveys no actionable specification. Either provide a metric or remove the qualifier.
- **Suggested Revision:** Replace "fast inference" with a concrete metric, or simply state "SDXL-based checkpoint."

### 5. Redundant Emphasis: "NEVER hand-edit"

- **Severity:** Nit
- **Affected Scope:** Section 6 (Risk Register), Line 174
- **Style Rationale:** All-caps "NEVER" is typographic emphasis that duplicates the semantic content of the mitigation instruction. Technical documents should convey urgency through precise language, not capitalization. The imperative "Do not hand-edit" carries the same meaning without shouting.
- **Suggested Revision:** "Do not hand-edit; always regenerate via nbformat."

### 6. Vague Reference: "reference video" (multiple occurrences)

- **Severity:** Major
- **Affected Scope:** Lines 5, 59, 204
- **Style Rationale:** "Reference video" appears three times without a stable identifier. A reader cannot locate or verify the source. The file structure lists `video1_metadata.txt` and `video2_metadata.txt` but the prose never links to them by name. Use explicit cross-references.
- **Suggested Revision:** Replace each instance with a specific reference, e.g., "see `docs/transcripts/video1_metadata.txt`" or assign labels like "[Ref-V1]" and "[Ref-V2]."

### 7. Passive Construction: "SQLite persistence deferred to Phase 2 post-MVP"

- **Severity:** Nit
- **Affected Scope:** Section 3.3 (Queue System), Line 108
- **Style Rationale:** "Deferred" is passive and omits the actor. State who made the decision or frame it as an explicit scope boundary.
- **Suggested Revision:** "Phase 2 will add SQLite persistence. MVP uses in-memory storage only."

### 8. Ambiguous Scope: "Document ephemeral nature"

- **Severity:** Major
- **Affected Scope:** Section 6 (Risk Register), Line 172
- **Style Rationale:** "Document ephemeral nature" is an action item disguised as a mitigation. It does not specify what documentation, where it lives, or what content it must contain. As a mitigation, it is unverifiable.
- **Suggested Revision:** "Add warning banner to README §Troubleshooting: 'LocalTunnel URL changes on every kernel restart. Re-copy the URL from kernel logs after each restart.'"

### 9. Synonym Redundancy: "Programmatic, validated, no indentation risk"

- **Severity:** Nit
- **Affected Scope:** Section 8 (Open Decisions), Line 205
- **Style Rationale:** "Validated" and "no indentation risk" overlap. Validation in this context specifically means preventing indentation errors. Consolidate to avoid repeating the same concept.
- **Suggested Revision:** "Programmatic generation prevents JSON indentation corruption."

### 10. Imprecise NFR: "Must fit in 2x T4 (~30GB total)"

- **Severity:** Nit
- **Affected Scope:** Section 9 (Non-Functional Requirements), Line 210
- **Style Rationale:** "~30GB" uses an approximation symbol in a requirements section. NFRs should state exact bounds. If 30GB is the hard limit, state "≤30GB." If it is an estimate, state the measured value and the margin.
- **Suggested Revision:** "GPU memory: model + runtime ≤ 25GB; total allocation ≤ 30GB (2× T4)."

---

## Compliance Summary

| Vector | Status | Count |
|--------|--------|-------|
| Hyperbole & Marketing Fluff | Minor issues | 1 (#4) |
| Jargon & Buzzwords | Clean | 0 |
| Filler Words & Wordiness | Minor issues | 2 (#2, #9) |
| Subjective Quality Judgments | Present | 2 (#1, #3) |
| Synonym Redundancy | Minor issues | 1 (#9) |
| Passive Voice | Minor issues | 1 (#7) |
| Cross-Reference Precision | Deficient | 2 (#6, #8) |
| NFR Measurability | Imprecise | 1 (#10) |

**Overall Assessment:** Document is technically sound and mostly objective. Addressing the three Major findings (#1, #3, #6, #8) will significantly improve verifiability and precision. Nit findings are optional refinements.