# Core Deep Research Methodology

This document defines the domain-agnostic investigation method used by the deep research loop.

It does not define shared object schemas. Shared contracts live in `contracts.md`.

---

## 1. Guiding Principles

1. **Baseline before claims**: verify that the headline issue is real before promoting any driver explanation.
2. **Bounded investigation**: every round is governed by an explicit `InvestigationContract`.
3. **Traceable evidence**: every promoted claim must trace to concrete SQL, web, or evaluation evidence.
4. **Graceful degradation**: when load or runtime constraints block decisive tests, produce an honest partial answer rather than widening the scan.
5. **Honest uncertainty**: contradictions and unresolved rival explanations must stay visible.

---

## 2. Five Analysis Layers

The investigation proceeds across five layers.

### Layer 1: Audit

Question: is the observed issue real, and is the analytical frame valid?

This layer checks:

- headline metric existence for the intended scope
- correct business object and time field
- scope or definition mismatch that would invalidate the question

Audit is always first. No driver claim should be promoted before audit is resolved or explicitly bypassed with a documented reason.

### Layer 2: Demand

Question: did activity volume change?

Typical checks:

- transaction count movement
- buyer or active-entity movement

### Layer 3: Value

Question: did value per unit of activity change?

Typical checks:

- average order value or analogous per-transaction value
- activity frequency per entity

### Layer 4: Structure

Question: did composition across supported dimensions shift?

Typical checks:

- mix changes across channel, product, region, seller, or other supported dimensions
- concentration shifts in specific segments

### Layer 5: Fulfillment

Question: did operational or supply-side factors influence the headline?

Typical checks:

- operational availability or completion metrics
- fulfillment-side constraints aligned with the headline movement

Fulfillment is legal only when the domain pack and discovered schema both support it.

---

## 3. Residual Logic

Residual measures how much of the headline issue is still not convincingly explained.

Residual has two parts:

- arithmetic residual: how much movement has not been attributed
- epistemic residual: how much uncertainty remains because evidence is weak, contradictory, or untested

### Residual State Expectations

At the end of every round, the evaluator must maintain:

- active explained components
- revoked components
- layer explained shares
- current unexplained ratio
- confidence band
- stalled and negative streak counters
- operator gain note

The evaluator must also output:

- `residual_score`
- `residual_band`
- `open_questions`
- `recommended_next_action`
- `conclusion_state`
- `continuation_guidance` whenever a new round is authorized

### Residual Bands

- `very_high`: the explanation is not yet trustworthy
- `high`: the direction may be useful but major uncertainty remains
- `medium`: the main direction is understood but meaningful gaps remain
- `low`: most of the issue is explained; residual is secondary
- `very_low`: explanation is sufficiently closed for final reporting

### Residual Scoring Discipline

Use rounded judgments, not false precision.

Move residual downward when:

- audit risk is closed
- a primary driver is directly evidenced
- a rival explanation is weakened or rejected by primary evidence
- open questions are materially reduced

Move residual upward when:

- a supported claim is revoked
- a contradiction affects the headline explanation
- a plausible rival remains materially alive
- runtime pressure prevented the next decisive test

Structure-only evidence should rarely push residual below `medium` by itself.

Continuation discipline:

- a new round is justified only when residual remains materially open and the evaluator can name a higher-value unresolved question with a clearer next test
- remaining round budget is not itself evidence that another round is warranted
- continuation should narrow the research problem, not replay the prior round with broader narration

---

## 4. Hypothesis Lifecycle

A hypothesis may move through these states:

- `proposed`
- `supported`
- `weakened`
- `rejected`
- `not_tested`
- `blocked_by_load`

Interpretation:

- `supported`: current evidence positively supports the hypothesis
- `weakened`: evidence exists but is insufficient or directionally weak
- `rejected`: successful evidence contradicts the hypothesis
- `not_tested`: the hypothesis cannot be tested with the discovered schema
- `blocked_by_load`: runtime admission or load prevented testing

Revocation rule:

- if a previously supported hypothesis is later weakened or rejected, move its explanation component to revoked state and set `correction_mode = true`

---

## 5. Round Structure

Every round is governed by an explicit `InvestigationContract`.

Required contract concepts:

- operator id
- target hypotheses
- query budget
- allowed cost classes
- executable queries
- pass conditions
- pivot conditions
- max rounds

Budget rule:

- `max_rounds` is a hard safety ceiling
- it is not an execution target and should not be "used up" for its own sake

Operator families:

- `audit_baseline`
- `compare_baseline`
- `demand_decomposition`
- `value_decomposition`
- `dimension_contribution`
- `fulfillment_probe`
- `contradiction_audit`

Round 1 rule:

- Round 1 is audit-first
- Round 1 target hypotheses must all be audit-layer hypotheses
- if audit requires primary metric validation, Round 1 queries must explicitly validate the headline metric

---

## 6. Continue, Pivot, Stop, Restart

### Continue with `refine`

Recommend `refine` when:

- meaningful progress was made
- residual remains materially open
- warehouse conditions still allow decisive next tests
- the next round can name a narrower residual target and stronger or more discriminating evidence path

### Recommend `pivot`

Recommend `pivot` when:

- a round stalled or weakened the current path
- a better remaining hypothesis exists
- the session should not stop yet
- the next round will materially change operator or primary target hypotheses rather than merely restating the same path

Pivot review rule:

- two consecutive `flat` or `negative` rounds should trigger pivot review before direct stop

### Recommend `stop`

Recommend `stop` when:

- `current_unexplained_ratio <= 0.30` and residual is in `low` or `very_low`
- warehouse conditions prevent the next decisive test and a partial answer is still possible
- the round budget is exhausted
- no testable hypotheses remain
- two consecutive negative rounds show the path is getting worse and no better pivot exists
- no high-value unresolved question remains that can justify a full additional round

### Recommend `restart`

Recommend `restart` only when an audit finding invalidates the original question framing.

That means a scope, definition, or time-field conflict made the frozen `NormalizedIntent` fundamentally wrong.

---

## 7. Conclusion States

Use the shared enum from `contracts.md`.

- `completed`: explanation is sufficiently closed
- `partial_answer_available`: some claims are supported, but meaningful uncertainty remains because of budget, load, correction mode, or schema limits
- `restart_required`: the intent frame was fundamentally wrong
- `blocked_runtime`: runtime blocking prevented all usable evidence

Rules:

- `correction_mode` is not a conclusion state
- `blocked_runtime` is reserved for sessions with no successful or cached evidence at all

---

## 8. Guardrails

- Do not redefine shared contracts here.
- Do not rewrite `NormalizedIntent` inline.
- Do not promote claims without traceable evidence.
- Do not widen the query scope beyond discovered and validated mappings.
- Do not smooth away contradictions.
- Respect runtime admission and load limits.
