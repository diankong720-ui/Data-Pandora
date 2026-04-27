---
name: investigation-evaluator
description: Stage 5 evaluation sub-skill. Updates hypothesis state, residual state, and next-action recommendation using the shared contracts.
internal_only: true
---

# Investigation Evaluator

This sub-skill owns Stage 5 evaluation.

It consumes the completed round contract, executed query results, current hypothesis board, and prior residual state, then produces `RoundEvaluationResult` as defined in `skills/deep-research/references/contracts.md`.

No runtime component will apply these decisions automatically.

---

## Inputs

- current `InvestigationContract`
- `QueryExecutionResult[]` for the round
- current `hypothesis_board`
- previous round's residual state
- current warehouse snapshot

---

## Output

Produce `RoundEvaluationResult` with all required fields from `contracts.md`, including:

- `hypothesis_updates`
- `residual_update`
- `residual_score`
- `residual_band`
- `open_questions`
- `recommended_next_action`
- `should_continue`
- `conclusion_state`
- `continuation_guidance` when `should_continue = true`

---

## Evaluation Workflow

### 1. Categorize query outcomes

Partition round results into:

- usable evidence: `success | cached`
- degraded evidence: `degraded_to_cache`
- failed evidence: `failed | timeout | blocked`

### 2. Update target hypothesis states

Use the round contract and actual query evidence.

Rules:

- successful contradictory evidence may produce `rejected`
- weak or incomplete evidence may produce `weakened`
- runtime blocking or timeout may produce `blocked_by_load`
- schema-level impossibility should remain `not_tested`
- failed execution alone must not produce `rejected`

Audit-specific rule:

- audit is supported only when the round actually validates the intended headline metric and analytical frame
- audit is restart-worthy only when the evidence shows the frozen intent frame is fundamentally wrong

### 3. Run revocation logic

If a previously supported hypothesis is now weakened or rejected:

- move its explanation component to `revoked_components`
- set `correction_mode = true`
- reduce confidence
- state the revocation explicitly in reasoning

### 4. Rebuild residual state

Recompute:

- layer explained shares
- current unexplained ratio
- confidence band
- operator gain note
- `stalled_round_streak`
- `negative_gain_streak`

Counter rules:

- `stalled_round_streak` increments on `flat` or `negative`
- `negative_gain_streak` increments only on `negative`
- any `positive` round resets both counters

### 5. Assign residual score and band

Use `core-methodology.md`.

The output must include:

- `residual_score`
- `residual_band`
- top `open_questions`

Do not omit these fields even when stopping.

### 6. Recommend next action

Use the following policy:

- `refine`: meaningful progress and a same-direction next test remains
- `pivot`: stalled or weak progress and a better remaining path exists
- `stop`: explanation is sufficiently closed, or the session cannot justify another decisive round
- `restart`: audit invalidated the original intent frame

Continuation authorization rule:

- when recommending `refine` or `pivot`, explicitly authorize the next round through `continuation_guidance`
- `continuation_guidance` must name the primary residual component, prioritized open questions, expected gain, and which paths are no longer worth pursuing
- remaining round budget is never a sufficient reason to continue

Stop-policy alignment:

- two consecutive stalled rounds require pivot review before direct stop
- two consecutive negative rounds may justify direct stop or restart if no better pivot exists
- `blocked_runtime` is reserved for sessions with no successful or cached evidence at all and complete runtime blocking
- `correction_mode` is not a conclusion state; it may force `partial_answer_available`

---

## Conclusion State Rules

Use the shared conclusion enum only:

- `completed`
- `partial_answer_available`
- `restart_required`
- `blocked_runtime`

Mapping guidance:

- `completed`: explanation is sufficiently closed and no contradiction threatens the main claim
- `partial_answer_available`: some claims are supported but uncertainty remains because of load, budget, schema gaps, or correction mode
- `restart_required`: audit invalidated the frozen intent frame
- `blocked_runtime`: zero usable evidence because runtime blocked all execution

---

## Non-Negotiable Rules

- Follow the shared contracts in `contracts.md`.
- Every hypothesis update needs explicit reasoning tied to concrete query evidence.
- Do not use failed execution as evidence of falsity.
- Keep `correction_mode` explicit when a prior explanation is revoked.
- Keep `open_questions` limited to issues that materially affect residual reduction.
- Do not assume downstream code will infer the next contract; the next action and its continuation guidance must be explicit.
