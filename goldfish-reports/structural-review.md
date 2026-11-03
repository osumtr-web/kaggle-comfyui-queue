# Structural Review: design_draft.md

**Reviewer:** Structural Goldfish (egm-structural)
**Target:** `/Users/jalil/Documents/kaggle-comfyui-queue/docs/architecture/design_draft.md`
**Date:** 2026-11-03 (Final Update — v3)
**Mode:** REVIEW

---

## Findings

### 1. Missing Required EGM Template Sections

- **Severity:** Major
- **Affected Scope:** Document-level (entire file)
- **Discrepancy Description & Rationale:** The standard EGM design draft template requires the following sections that remain absent or insufficiently addressed:
 - **Goals & Non-Goals**: §9 covers Non-Functional Requirements but there is no explicit "Goals" or "Non-Goals" / "Out of Scope" section. EGM requires these to bound the design explicitly. The Overview (§1) describes what the system does but not what it explicitly excludes.
 - **Dedicated Security Considerations Section**: While §9 NFRs now include a Security bullet (line 401) and §3.3.2 documents authentication requirements, there is no standalone "Security Considerations" section covering threat model, attack surface analysis, secret management lifecycle, or data-at-rest encryption for SQLite. EGM template mandates a dedicated Security section.
 - **Rollback / Recovery Plan**: No section describes how to recover from a failed deployment, roll back to a previous working kernel version, or restore from `queue.db.bak`. The watchdog handles process-level recovery but not deployment-level rollback.
 - **Appendix / References**: External links (Kaggle docs, ComfyUI GitHub, ngrok docs, etc.) are mentioned inline throughout §3 but not collected in a consolidated References section with stable URLs.
 - **Note on Context/Background**: §1 Overview provides adequate context for the current architecture. Satisfied.
 - **Note on Alternatives Considered**: §8 "Resolved Decisions" provides structured comparison with Choice/Rationale columns. This satisfies the Alternatives Considered requirement. Satisfied.

### 2. Heading Hierarchy — H4 Sub-subsection Nesting

- **Severity:** Nit
- **Affected Scope:** Lines 99, 140, 177 — `#### 3.3.1 Storage`, `#### 3.3.2 Queue API`, `#### 3.3.3 Queue Consumer`
- **Discrepancy Description & Rationale:** §3.3 uses H4 sub-subsections (`####`) nested under an H3 parent (`### 3.3 Queue System`). This is valid Markdown heading hierarchy (H2 → H3 → H4 with no skipped levels). However, no other section in the document uses H4 headings, creating an inconsistency in depth. Other complex sections (§3.2 Watchdog, §3.5 Model Stack) use bold sub-headers within H3 rather than H4 sub-subsections. Recommend standardizing: either promote all complex subsections to H4 or convert §3.3.x to bold inline headers for consistency. Not a blocker.

### 3. Previously Resolved Issues (No Longer Applicable)

- **Severity:** Informational
- **Affected Scope:** N/A
- **Discrepancy Description & Rationale:** The following issues from prior review iterations have been resolved in the current document revision:
 - **Fractional H2 numbering (§1.5–§1.8)**: Removed. §1 is now a single H2 with no fractional sub-sections. RESOLVED.
 - **Broken cross-reference "(Section 8 Reference)" in §5 heading**: §5 is now titled `## 5. Implementation Tasks` with no stale parenthetical. RESOLVED.
 - **§8 naming**: Renamed from "Open Decisions" to "Resolved Decisions," which better reflects the finalized state. IMPROVED.

### 4. Section Numbering Consistency — Pass

- **Severity:** Nit
- **Affected Scope:** Lines 3, 7, 43, 276, 315, 333, 346, 380, 393
- **Discrepancy Description & Rationale:** All nine top-level sections use sequential numbered H2 headings (`## 1.` through `## 9.`). Subsections under §3 use `### 3.1`–`### 3.6` (H3). Sub-subsections under §3.3 use `#### 3.3.1`–`#### 3.3.3` (H4). Subsections under §7 use `### 7.1`–`### 7.5` (H3). No skipped heading levels detected. Numbering is internally consistent and sequential. Pass.

### 5. Table Formatting — Pass

- **Severity:** Nit
- **Affected Scope:** Lines 53–60 (Cell Sequence), Lines 68–73 (Managed Processes), Lines 104–120 (SQLite Schema), Lines 146–153 (Job Endpoints), Lines 157–163 (Model Registry Endpoints), Lines 167–169 (System Endpoints), Lines 233–237 (Static Models), Lines 257–274 (Environment Variables), Lines 317–331 (Implementation Tasks), Lines 335–344 (Risk Register), Lines 382–391 (Resolved Decisions)
- **Discrepancy Description & Rationale:** All eleven tables use valid Markdown pipe syntax with header separator rows. Column alignment is left-aligned by default throughout. No malformed borders, missing cells, or broken formatting detected. Tables pass formatting audit.

### 6. Code Block Language Tag Inconsistency

- **Severity:** Nit
- **Affected Scope:** Line 9 (Architecture Diagram), Line 76 (State Machine), Line 103 (SQL Schema), Line 129 (Job Status Lifecycle), Line 278 (File Structure Tree)
- **Discrepancy Description & Rationale:** Five code blocks lack language tags:
 - Line 9: Architecture diagram (should be `` ```text `` or `` ```ascii ``)
 - Line 76: State machine diagram (should be `` ```text ``)
 - Line 129: Job status lifecycle (should be `` ```text ``)
 - Line 278: File structure tree (should be `` ```text ``)
 - Line 103: SQL schema correctly uses `` ```sql `` ✓
 Recommend adding language tags to all bare code blocks for consistent rendering.

### 7. Diagram Clarity — ASCII Art Lacks Legend

- **Severity:** Nit
- **Affected Scope:** Lines 9–41 (Architecture Diagram)
- **Discrepancy Description & Rationale:** The ASCII architecture diagram uses unidirectional arrows (`▼`, `▲`) for data/control flow between components. Unlike the previous version, this diagram no longer uses bidirectional arrows. However, there is still no legend explaining arrow semantics (process supervision vs. data flow vs. HTTP requests). Adding a brief legend below the diagram would improve comprehension for fresh readers.

### 8. File Structure Tree — Status Marker Removal

- **Severity:** Nit
- **Affected Scope:** Lines 278–313 (File Structure)
- **Discrepancy Description & Rationale:** The file tree no longer uses `✅ Created` markers (removed since v2). Only `← This file` remains on line 283. This is an improvement over the prior inconsistent annotation. However, the single remaining marker creates a minor asymmetry. Recommend either removing it entirely or adding a consistent annotation scheme. Low priority.

### 9. List Formatting Consistency — Improved

- **Severity:** Nit
- **Affected Scope:** Lines 82–88 (Key Behaviors), Lines 217–221 (ngrok Configuration), Lines 395–401 (NFRs)
- **Discrepancy Description & Rationale:** Bold lead-ins are now used consistently in §9 NFRs and §3.2 Key Behaviors. §3.4 ngrok Configuration also uses bold lead-ins. Bullet list formatting is now consistent across the document. Minor improvement noted; no action required.

---

## Summary

| Severity | Count |
|----------|-------|
| Blocker | 0 |
| Major | 1 |
| Nit | 7 |

**Critical Path Items (must resolve before implementation):**
1. Add missing EGM-mandated sections: Goals/Non-Goals, dedicated Security Considerations, Rollback/Recovery Plan.

**Recommended Improvements (non-blocking):**
- Add language tags to bare code blocks (architecture diagram, state machine, lifecycle, file tree).
- Standardize H4 usage: either adopt H4 across all complex subsections or convert §3.3.x to bold inline headers.
- Add diagram legend for arrow semantics.
- Remove or standardize the single remaining `← This file` marker in file tree.
- Consolidate inline references into a dedicated References/Appendix section.

**Resolved Since Prior Review:**
- Fractional H2 heading hierarchy (§1.5–§1.8) — RESOLVED
- Broken cross-reference in §5 heading — RESOLVED
- §8 renamed to "Resolved Decisions" — IMPROVED
- Security bullet added to §9 NFRs — PARTIAL (still needs dedicated section)
- File tree status marker inconsistency — IMPROVED

---

## Verification Checklist

| Validation Check | Status | Finding(s) |
|------------------|--------|------------|
| Heading hierarchy | ✅ Pass (with note) | #2 (H4 inconsistency, non-blocking) |
| Section numbering consistency | ✅ Pass | #4 |
| Table formatting | ✅ Pass | #5 |
| Diagram clarity | ⚠️ Minor issues | #7 (no legend) |
| Template compliance (EGM) | ⚠️ Missing sections | #1 (Goals, Security section, Rollback, References) |
| Cross-references | ✅ Pass | #3 (prior broken ref resolved) |