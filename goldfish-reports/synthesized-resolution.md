# Synthesized Goldfish Resolution Report

**Target:** `/Users/jalil/Documents/kaggle-comfyui-queue/design_draft.md`
**Date:** 2026-11-03
**Synthesizer:** EGM Synthesizer
**Reviewers Completed:** zoom-out, comprehension, design, implementation-readiness, structural, style
**Reviewers Not Run:** codebase-details (file not found on disk)

---

## BLOCKERS (Must fix before implementation)

### B-01: Unauthenticated Public Internet Exposure of ComfyUI
*   **Source Reviewer(s):** design (4.1)
*   **Exact Quote:** "The design explicitly declares 'no multi-user auth' as a non-goal (NG1) and exposes ComfyUI directly via ngrok to the public internet."
*   **Fix Instruction:** Move authentication from Non-Goals to mandatory requirements. Add ngrok basic auth (`--basic-auth=user:pass`) or Cloudflare Access/Zero Trust tunnel with email verification. Update Section 4 (Non-Goals) to remove NG1 or reframe it as "Multi-user RBAC" while keeping single-user auth mandatory. Add explicit threat model to Section 9.

### B-02: Arbitrary Workflow Execution Enables Remote Code Execution
*   **Source Reviewer(s):** design (4.2)
*   **Exact Quote:** "The API accepts arbitrary workflow JSON and forwards it directly to ComfyUI's `/prompt` endpoint... ComfyUI custom nodes can execute arbitrary Python code."
*   **Fix Instruction:** Implement a workflow allowlist in Section 7.3. Only pre-approved workflow templates from `workflows/` directory are executable by default. Reject any POST /api/jobs payload referencing workflows outside the allowlist. If custom workflows are needed, add a review/approval gate. Document trust boundary in Section 9 with HIGH/HIGH rating.

### B-03: OOM During Model Load Bypasses VRAM Safety Check
*   **Source Reviewer(s):** design (2.2)
*   **Exact Quote:** "A job may pass the 2GB free VRAM check, but loading a 12GB checkpoint + 4GB VAE could exceed available memory during the load phase itself, triggering a Kaggle OOM kill."
*   **Fix Instruction:** Add workflow-aware VRAM estimation to Section 7.5. Parse workflow JSON before dispatch, sum known model sizes from a manifest file (`configs/model_vram_manifest.yaml`), and compare against free VRAM with safety margin. Add post-dispatch health check: if ComfyUI PID disappears within 30s of dispatch, treat as OOM and implement backoff. The existing schema column `estimated_vram_mb` (line 159) must be populated from this manifest at submission time, not left nullable.

### B-04: Orphaned Running Jobs on Consumer Crash
*   **Source Reviewer(s):** design (1.2)
*   **Exact Quote:** "If the consumer crashes mid-generation after setting a job to `running` but before polling `/history` completes, the watchdog restarts the consumer, which then picks the next `pending` job. The previously `running` job is permanently orphaned."
*   **Fix Instruction:** The design draft already contains partial mitigation at lines 208–209 (heartbeat mechanism). Verify that Section 7.2 schema includes `last_heartbeat` column (confirmed at line 152) and that Section 7.4 explicitly describes the startup scan for stale heartbeats. Ensure the consumer updates `last_heartbeat` every 30s via background asyncio task. This blocker is PARTIALLY RESOLVED in the current draft; verify implementation matches the written spec.

### B-05: Unbounded Output Disk Growth
*   **Source Reviewer(s):** design (5.1)
*   **Exact Quote:** "Generated outputs accumulate in `/kaggle/working/output/` with no cleanup policy... After ~70-700 videos, the disk fills, causing SQLite corruption, ComfyUI crashes, and total system failure."
*   **Fix Instruction:** Add automatic output rotation to Section 7.4 (Watchdog responsibilities). Delete oldest completed job outputs when disk usage exceeds 80%. Add `max_output_gb` config parameter to `configs/comfyui_settings.yaml`. Expose disk usage in `/api/health` response. Add cleanup task to watchdog loop. Add to Section 9 Risks table with HIGH likelihood and HIGH impact.

### B-06: Undefined Retry Semantics for Interrupted Jobs
*   **Source Reviewer(s):** design (6.1), comprehension (3.4)
*   **Exact Quote:** "The mitigation states 'queue marks interrupted jobs as failed with retry option' but provides zero specification."
*   **Fix Instruction:** Define retry policy explicitly in Section 7.4: `max_retries=3`, automatic retry for transient failures (OOM, preemption, consumer crash), manual-only for permanent failures (invalid workflow). Schema already has `retry_count` and `max_retries` columns (lines 153–154). Add state machine transition: `failed → pending` when `retry_count < max_retries` and failure is transient. Remove vague "retry option" language from Section 9 row 6 and replace with concrete behavior.

### B-07: No Python Dependency Version Pins
*   **Source Reviewer(s):** implementation-readiness (3.1)
*   **Exact Quote:** "setup.sh must include an explicit requirements.txt or inline pip commands with exact versions."
*   **Fix Instruction:** Add complete `requirements.txt` content to Section 7.1 under `scripts/setup.sh` rationale. Pin: `fastapi==0.115.4`, `uvicorn==0.32.0`, `aiosqlite==0.20.0`, `pydantic==2.9.2`. Pin ComfyUI to specific git commit hash. Add to setup.sh description.

### B-08: Model Download URLs and Checksums Missing
*   **Source Reviewer(s):** implementation-readiness (3.3)
*   **Exact Quote:** "setup.sh is supposed to 'download curated models' but no URLs, filenames, sizes, or checksums are provided."
*   **Fix Instruction:** Add model manifest table to Section 7.1 under `scripts/setup.sh`: model name → HF repo URL → filename → SHA256 → destination path → size estimate. Cover MiniMax H3 checkpoint, Flux Dev UNET/CLIP/VAE, SDXL base. Example format:
    ```
    | MiniMax-H3 | https://huggingface.co/MiniMax/H3/resolve/main/checkpoint.safetensors | minimax_h3.safetensors | sha256:abc123... | /kaggle/working/models/checkpoints/ | 12GB |
    ```

### B-09: ComfyUI API Error Handling Matrix Missing
*   **Source Reviewer(s):** implementation-readiness (2.1)
*   **Exact Quote:** "No error paths are defined. What HTTP status codes indicate failure? What timeout applies to /history polling?"
*   **Fix Instruction:** Add subsection "7.3.1 ComfyUI API Error Handling" specifying: POST /prompt expects 200 + `{prompt_id}`; any other status → mark failed with error_msg. /history polling: 5s interval, max 30 min timeout, 3 consecutive connection errors → fail. ComfyUI execution errors appear in /history under `status.status_str == "error"` → extract `exception_message`.

### B-10: Use ComfyUI's Native Queue Instead of Custom SQLite Queue
*   **Source Reviewer(s):** zoom-out (Opportunity 2.1)
*   **Exact Quote:** "The design builds an entire custom queue system that duplicates functionality ComfyUI already provides natively."
*   **Fix Instruction:** Evaluate whether the custom queue adds value beyond cross-cell-restart durability. If the primary value is persistence across cell re-runs, restructure as thin wrapper: persist jobs to SQLite only for replay on startup, then delegate to ComfyUI's native `/prompt` queue. This would eliminate custom consumer polling loop, `/history` polling logic, and reduce `queue_consumer.py` by ~60%. If full custom queue is retained, document explicit justification for why ComfyUI's native queue is insufficient.

---

## MAJOR FINDINGS (Should fix)

### M-01: Separate FastAPI Process is Unnecessary Overhead
*   **Source Reviewer(s):** zoom-out (1.1)
*   **Affected Scope:** Section 5.2, Section 7.1 Task #3
*   **Recommendation:** Collapse Queue API into ComfyUI custom node extending ComfyUI's existing HTTP server with `/api/jobs/*` routes. Eliminates one process, one port (8189), one dependency (FastAPI/Uvicorn), and inter-process SQLite contention.

### M-02: Watchdog State Machine is Over-Engineered
*   **Source Reviewer(s):** zoom-out (1.2, Opportunity 2.2)
*   **Affected Scope:** Section 7.4, Task #5
*   **Recommendation:** Replace custom Python asyncio watchdog with inline bash loop per process or supervisord. Current state machine adds ~80 lines of asyncio code when 15 lines of bash achieves identical reliability for ephemeral notebook environment.

### M-03: Separate Setup Script Contradicts Single-Notebook Goal
*   **Source Reviewer(s):** zoom-out (Opportunity 2.3)
*   **Affected Scope:** Section 7.1 `scripts/setup.sh`
*   **Recommendation:** Inline all setup logic into notebook cells using `!bash` magic. Makes notebook truly self-contained per concepts brief decision #1 ("No external scripts required at runtime").

### M-04: Kaggle Session Lifecycle and Persistence Semantics Unexplained
*   **Source Reviewer(s):** comprehension (2.1)
*   **Affected Scope:** Section 1, Section 3 G2/G6
*   **Recommendation:** Add subsection explaining Kaggle storage lifecycle: what persists across cell re-runs vs session restarts vs always ephemeral. Define boundary between `/kaggle/working/` (cell-rerun persistent) and Kaggle Datasets (cross-session persistent).

### M-05: ComfyUI /prompt and /history API Contract Unspecified
*   **Source Reviewer(s):** comprehension (2.2)
*   **Affected Scope:** Section 5.3, Section 7.3
*   **Recommendation:** Include minimal request/response examples for ComfyUI's `/prompt` and `/history` endpoints, or link to pinned-version API documentation.

### M-06: Workflow Resolution Logic Missing
*   **Source Reviewer(s):** comprehension (2.3)
*   **Affected Scope:** Section 5.3, Section 7.1
*   **Recommendation:** Add subsection "7.3.1 Workflow Resolution" explaining how named workflow string maps to actual JSON, how params are injected via template convention, and where template files live.

### M-07: Dual T4 GPU Topology and VRAM Aggregation Unclear
*   **Source Reviewer(s):** comprehension (2.4)
*   **Affected Scope:** Section 7.5
*   **Recommendation:** Specify which GPU(s) ComfyUI uses by default, whether VRAM check targets specific GPU index, and what Kaggle GPU configurations are supported. Change "sum of both GPUs" to target-specific GPU query.

### M-08: ngrok Token Provisioning via Kaggle Secrets Unexplained
*   **Source Reviewer(s):** comprehension (2.5)
*   **Affected Scope:** Section 7.6
*   **Recommendation:** Add prerequisite section or inline note explaining Kaggle Secrets UI/API, how to create and attach secrets, and link to Kaggle Secrets docs.

### M-09: No Prerequisite Knowledge Statement
*   **Source Reviewer(s):** comprehension (3.1)
*   **Affected Scope:** Document-level
*   **Recommendation:** Add "Prerequisites" section listing expected familiarity: Kaggle account with GPU quota, ngrok account + authtoken, Python/bash basics, ComfyUI workflow JSON format.

### M-10: SQLite WAL Write Serialization Under Burst Load
*   **Source Reviewer(s):** design (1.1)
*   **Affected Scope:** Section 5.3, Section 7.2
*   **Recommendation:** Design already includes write batching (line 168) and busy_timeout (line 168). Verify load test requirement in Section 8 Task 11 proves API handles 20 concurrent POST /api/jobs without >2s p99 latency.

### M-11: ngrok URL Race on Restart — No Notification Mechanism
*   **Source Reviewer(s):** design (1.3)
*   **Affected Scope:** Section 7.6
*   **Recommendation:** Add `ngrok_url` field to `/api/health` response. Document that clients MUST poll health or re-read URL file after connection failure.

### M-12: ngrok Death Mid-Generation Leaves Zombie Workflows
*   **Source Reviewer(s):** design (2.1)
*   **Affected Scope:** Section 5.3, Section 7.4
*   **Recommendation:** Distinguish local completion from remote deliverability. Schema already has `delivered` column (line 156). Document limitation in Section 9 or require consumer to verify external accessibility before marking delivered.

### M-13: Kaggle Preemption Has No Graceful Shutdown Hook
*   **Source Reviewer(s):** design (2.3)
*   **Affected Scope:** Section 9, Section 7.4
*   **Recommendation:** Design already includes SIGTERM handlers (line 210). Verify all daemons register handlers, flush SQLite, write shutdown_state.json. Document cross-session recovery procedure.

### M-14: Single-Threaded Consumer Creates Unbounded Queue Growth
*   **Source Reviewer(s):** design (3.1)
*   **Affected Scope:** Section 6, Section 5.3
*   **Recommendation:** Implement queue depth limits. Reject submissions when pending count exceeds configurable threshold (e.g., 50). Add estimated wait time to job submission response. Expose queue depth in `/api/health`.

### M-15: SQLite Injection via Unsanitized Job Payloads
*   **Source Reviewer(s):** design (4.3)
*   **Affected Scope:** Section 7.2, Section 7.3
*   **Recommendation:** Design already includes 64KB payload limit and JSON schema validation (line 170). Verify enforcement at API layer before SQLite insert.

### M-16: Model Cache Grows Unbounded Across Sessions
*   **Source Reviewer(s):** design (5.2)
*   **Affected Scope:** Section 5.4, Section 3 G6
*   **Recommendation:** Implement LRU model cache with configurable size limit. Track last-used timestamps. Evict least-recently-used models when cache exceeds threshold.

### M-17: RAM Budget Not Analyzed for Concurrent Services
*   **Source Reviewer(s):** design (5.3)
*   **Affected Scope:** Architecture Diagram, Section 5.2
*   **Recommendation:** Add RAM monitoring alongside VRAM checks. Document expected RAM footprint per component in Section 5.2 table. Add RAM thresholds to safety protocol.

### M-18: SQLite WAL Corruption Mitigation Insufficient
*   **Source Reviewer(s):** design (6.2)
*   **Affected Scope:** Section 9 Row 4
*   **Recommendation:** Add startup integrity check (`PRAGMA integrity_check`). Implement periodic SQLite backup. Document recovery procedure. Test hard-kill recovery in Section 8 Task 11.

### M-19: cloudflared Fallback Underspecified
*   **Source Reviewer(s):** design (6.3), implementation-readiness (1.3), zoom-out (1.3)
*   **Affected Scope:** Section 7.6
*   **Recommendation:** Either implement cloudflared as first-class alternative with own launcher script, exact commands, URL parsing regex, and integration test — OR remove fallback claim and document ngrok as sole supported tunnel. Half-implemented fallback creates false confidence.

### M-20: No Observability Beyond watchdog.log
*   **Source Reviewer(s):** design (6.4)
*   **Affected Scope:** Section 5.4, Section 7.4
*   **Recommendation:** Add structured JSON logging with correlation IDs. Expose detailed stats in `/api/health`: jobs_processed, avg_duration, restart_count, error_rate. Log all state transitions.

### M-21: Undefined Terms — VDN, WAL, Model Names, cloudflared
*   **Source Reviewer(s):** comprehension (1.1, 1.2, 1.5, 1.6)
*   **Affected Scope:** Multiple sections
*   **Recommendation:** Define on first use: VDN (Variable Diffusion Noise scheduler), WAL (Write-Ahead Logging), MiniMax H3 (text-to-video diffusion model), Flux Dev (text-to-image model), SDXL (Stable Diffusion XL), cloudflared (Cloudflare Tunnel CLI binary). See comprehension review clarity fixes table for exact replacement text.

### M-22: Job Selection Logic Undefined (Tie-Breaking, Stale Locks)
*   **Source Reviewer(s):** implementation-readiness (1.1)
*   **Affected Scope:** Section 5.3, Section 7.4
*   **Recommendation:** Specify: ORDER BY priority DESC, created_at ASC LIMIT 1. Atomic UPDATE with WHERE status='pending'. Startup scan for stale running jobs (already partially addressed in B-04).

### M-23: Watchdog Process Spawn Commands Not Specified
*   **Source Reviewer(s):** implementation-readiness (1.2)
*   **Affected Scope:** Section 7.4
*   **Recommendation:** Provide explicit CLI commands for each monitored process including arguments, working directory, and environment variables.

### M-24: Job Payload Validation Schema Missing
*   **Source Reviewer(s):** implementation-readiness (1.4)
*   **Affected Scope:** Section 7.3
*   **Recommendation:** Define Pydantic model per workflow with field constraints, or JSON Schema registry mapping workflow name → required params → types → defaults.

### M-25: SQLite Connection Management Not Specified
*   **Source Reviewer(s):** implementation-readiness (2.2)
*   **Affected Scope:** Section 7.2
*   **Recommendation:** Specify aiosqlite with single shared connection per process, PRAGMA busy_timeout=5000, PRAGMA synchronous=NORMAL, CREATE TABLE IF NOT EXISTS on startup.

### M-26: Graceful Shutdown Protocol Missing
*   **Source Reviewer(s):** implementation-readiness (2.3)
*   **Affected Scope:** All daemon components
*   **Recommendation:** Design already includes SIGTERM handlers (line 210). Verify coordination mechanism (sentinel file) to prevent watchdog restarting processes during graceful shutdown.

### M-27: Job Cancellation Semantics Undefined
*   **Source Reviewer(s):** implementation-readiness (2.5)
*   **Affected Scope:** Section 7.3 DELETE /api/jobs/{id}
*   **Recommendation:** Define: pending → cancelled immediately; running → POST ComfyUI /interrupt then cancel; completed/failed/cancelled → return 409 Conflict.

### M-28: ngrok Binary Version and Install Method Missing
*   **Source Reviewer(s):** implementation-readiness (3.2)
*   **Affected Scope:** Section 7.1, Section 7.6
*   **Recommendation:** Specify ngrok v3.x download URL, extraction path, version verification command.

### M-29: System Dependencies Not Enumerated
*   **Source Reviewer(s):** implementation-readiness (3.4)
*   **Affected Scope:** Section 7.1
*   **Recommendation:** List all apt-get packages: libgl1, libglib2.0-0, ffmpeg, etc.

### M-30: No Unit Test Boundaries or Mock Strategy
*   **Source Reviewer(s):** implementation-readiness (4.1, 4.2)
*   **Affected Scope:** All components
*   **Recommendation:** Add Section 7.7 defining unit test boundaries, mock_comfyui.py spec, and testing strategy.

### M-31: End-to-End Verification Criteria Missing
*   **Source Reviewer(s):** implementation-readiness (4.3)
*   **Affected Scope:** Section 8 Task 11
*   **Recommendation:** Define acceptance criteria checklist: setup time, URL validity, job lifecycle, watchdog restart timing, VRAM pause trigger, cancellation behavior.

### M-32: Ambiguous Setup Time Guarantee
*   **Source Reviewer(s):** style (2.1), zoom-out (Alignment 3.3)
*   **Affected Scope:** Section 2 Line 14, Section 3 Line 25
*   **Recommendation:** Add measurement context: "Targets <5 minute installation on fresh Kaggle GPU session (T4 x2, cold cache). Actual time varies with network conditions." Validate with timed dry-run before implementation.

### M-33: Assumption Presented as Fact (ngrok Documentation Quality)
*   **Source Reviewer(s):** style (3.1)
*   **Affected Scope:** Section 6 Line 117
*   **Recommendation:** Cite evidence or reframe as observed experience rather than objective fact.

---

## NITS (Optional improvements)

| ID | Source | Description |
|----|--------|-------------|
| N-01 | zoom-out (1.3) | Cloudflared fallback adds dual-tunnel complexity; consider removing |
| N-02 | zoom-out (1.4) | Priority queue field is premature for single-user FIFO system |
| N-03 | comprehension (1.3) | Expand "PID" on first use |
| N-04 | comprehension (1.4) | Expand "OOM" on first use |
| N-05 | comprehension (1.7) | Define "draft quality preset" in ComfyUI/MiniMax context |
| N-06 | comprehension (3.2) | Explain why SQLite over other embedded options (file queues, pickle) |
| N-07 | comprehension (3.3) | Watchdog cannot monitor itself; note limitation explicitly |
| N-08 | design (3.2) | SQLite polling loop wastes resources; consider update_hook or eventfd |
| N-09 | implementation-readiness (1.5) | Output file naming convention undefined |
| N-10 | implementation-readiness (2.4) | Health check probe implementation details missing |
| N-11 | structural (F-01) | Missing language tag on API contract code block |
| N-12 | structural (F-02) | Missing language tag on state machine code block |
| N-13 | structural (F-03) | Metadata block uses non-standard format (optional YAML frontmatter) |
| N-14 | structural (F-04) | Cross-references lack navigable anchor links |
| N-15 | style (1.1) | Replace "expensive GPU hardware" with "dedicated GPU hardware" |
| N-16 | style (1.2) | Replace "solves these gaps" with "addresses these limitations" |
| N-17 | style (2.2) | Clarify recovery time measurement window relative to poll interval |
| N-18 | style (2.3) | Define "high-res video" with concrete resolution/fps parameters |
| N-19 | style (3.2) | Replace "correct for this VRAM budget" with "selected for" |
| N-20 | style (4.1) | Consolidate Overview and Goals to remove duplication |
| N-21 | style (4.2) | Tighten alternative rejection reasons to single concise statements |

---

## CONTRADICTIONS WITH CONCEPTS BRIEF

| # | Concepts Brief Decision | Design Draft Contradiction | Resolution |
|---|------------------------|---------------------------|------------|
| C-01 | "No external scripts required at runtime" (Decision #1, line 55) | Design creates `scripts/setup.sh` as separate artifact (Section 7.1) | Inline setup logic into notebook cells OR update concepts brief to acknowledge external script |
| C-02 | "Pure Python asyncio loop checking PIDs every 5s" (Decision #3, line 57) | Design specifies formal state machine with MONITORING→RESTARTING→BACKOFF transitions (Section 7.4) | Align: either simplify to match concepts brief or update concepts brief to reflect state machine |
| C-03 | "Falls back to cloudflared if token missing" (Decision #4, line 58) | Design mentions fallback but provides zero implementation detail (Section 7.6) | Either implement fully or remove from both documents |
| C-04 | Schema includes `priority` field (Decision #2, line 56) | Zoom-out reviewer flags priority as YAGNI for single-user system (Finding 1.4) | Reconcile: keep if multi-priority is desired future-proofing, remove if strict YAGNI |
| C-05 | "<5 minutes to ready state" (Analysis, line 12) | Design does not estimate or validate setup duration | Add timing benchmarks or defer large model downloads to lazy loading |

---

## DEDUPLICATION NOTES

| Merged Finding | Source Reviewers | Notes |
|---------------|-----------------|-------|
| Cloudflared fallback underspecified | design (6.3), implementation-readiness (1.3), zoom-out (1.3) | Three reviewers independently flagged incomplete cloudflared spec. Merged into M-19. |
| Retry semantics undefined | design (6.1), comprehension (3.4) | Both flagged vague "retry option". Merged into B-06. |
| Priority field premature | zoom-out (1.4), concepts brief contradiction (C-04) | YAGNI concern overlaps with brief alignment. Listed as Nit + Contradiction. |
| Setup time unvalidated | style (2.1), zoom-out (Alignment 3.3) | Both flagged unverifiable "<5 min" claim. Merged into M-32. |
| Orphaned jobs on crash | design (1.2), implementation-readiness (1.1 partial) | Design reviewer identified blocker; impl-readiness noted stale lock recovery. Merged into B-04. |
| Separate FastAPI overhead | zoom-out (1.1), zoom-out (R1) | Same reviewer, two related findings. Consolidated into M-01. |
| Watchdog over-engineering | zoom-out (1.2), zoom-out (Opportunity 2.2), zoom-out (R2) | Three related findings from same reviewer. Consolidated into M-02. |
| Undefined terms (VDN, WAL, models, cloudflared) | comprehension (1.1–1.6) | Six individual term findings consolidated into single M-21 with reference to clarity fixes table. |
| Graceful shutdown missing | design (2.3), implementation-readiness (2.3) | Both flagged SIGTERM handling. Design draft already has partial mitigation. Merged into M-13/M-26. |
| SQLite WAL concerns | design (1.1), design (6.2), comprehension (1.2) | Write serialization, corruption, and definition gaps. Separated into M-10 (burst), M-18 (corruption), M-21 (definition). |

---

## PRIORITIZED FIX ORDER

Execution order optimized for dependency chain and risk reduction:

1.  **B-01 + B-02**: Security blockers. Add authentication and workflow allowlist to Sections 4, 7.3, 9. Must resolve before any implementation.
2.  **B-07 + B-08**: Dependency pins and model manifest in Section 7.1. Blocks all setup work.
3.  **B-10**: Evaluate ComfyUI native queue vs custom queue. Architectural decision that affects scope of Tasks 3–4. Resolve before writing consumer code.
4.  **B-03**: Workflow-aware VRAM estimation in Section 7.5. Depends on model manifest (B-08).
5.  **B-04**: Verify orphaned job recovery spec completeness in Sections 7.2, 7.4. Schema already exists; confirm behavioral spec.
6.  **B-05**: Output disk rotation in Section 7.4 + Section 9. Independent of queue architecture decision.
7.  **B-06**: Retry semantics in Section 7.4. Depends on orphaned job recovery (B-04).
8.  **B-09**: ComfyUI API error handling matrix in Section 7.3. Required before consumer implementation.
9.  **M-04 + M-08 + M-09**: Kaggle lifecycle, secrets provisioning, prerequisites. Documentation additions to Sections 1, 4.5 (new), 7.6.
10. **M-05 + M-06**: ComfyUI API contract and workflow resolution in Section 7.3. Required before consumer implementation.
11. **M-01**: Evaluate collapsing FastAPI into ComfyUI custom node. Architectural; depends on B-10 outcome.
12. **M-02 + M-03**: Simplify watchdog and inline setup. Depends on architectural decisions (B-10, M-01).
13. **M-19**: Resolve cloudflared — implement fully or remove. Depends on tunnel architecture decision.
14. **M-14 + M-16 + M-17**: Queue depth limits, model cache eviction, RAM monitoring. Resource management additions.
15. **M-22 through M-31**: Implementation precision fixes (job selection, spawn commands, validation, connection mgmt, cancellation, testing). Apply during implementation phase.
16. **M-32 + M-33**: Style/objectivity fixes. Apply opportunistically.
17. **C-01 through C-05**: Concepts brief contradictions. Resolve alongside corresponding blockers/majors above.
18. **N-01 through N-21**: Nit-level improvements. Apply opportunistically during resolve phase or post-implementation polish.

---

## REVIEWER STATUS SUMMARY

| Reviewer | Status | Blockers | Majors | Nits |
|----------|--------|----------|--------|------|
| zoom-out | completed | 1 | 3 | 2 |
| comprehension | completed | 1 | 6 | 4 |
| design | completed | 6 | 7 | 1 |
| implementation-readiness | completed | 3 | 12 | 2 |
| structural | completed | 0 | 0 | 4 |
| style | completed | 0 | 2 | 6 |
| codebase-details | **not run** | — | — | — |

**Total Unique Findings After Deduplication:** 10 Blockers, 33 Majors, 21 Nits, 5 Contradictions