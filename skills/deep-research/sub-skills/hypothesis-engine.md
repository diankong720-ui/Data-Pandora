---
name: hypothesis-engine
description: Stage 3 planning sub-skill. Produces PlanBundle with a ranked hypothesis board and an executable Round 1 InvestigationContract.
internal_only: true
---

# Hypothesis Engine

This sub-skill owns Stage 3 planning.

It consumes the frozen `NormalizedIntent`, the `DataContextBundle`, and the active domain pack, then produces `PlanBundle` as defined in `skills/deep-research/references/contracts.md`.

The runtime will not:

- choose hypothesis families
- choose the next round
- compile semantic plans into SQL
- fill in missing field names or joins

---

## Inputs

- frozen `NormalizedIntent`
- `DataContextBundle`
- active domain pack

---

## Output

Produce `PlanBundle`:

- `hypothesis_board`
- `round_1_contract`
- `planning_notes`
- `max_rounds`

`round_1_contract` must be directly executable.
`max_rounds` is a hard ceiling for the entire session budget and must default to `20` unless the host explicitly overrides it.

---

## Planning Workflow

### 1. Generate candidate hypotheses

Use the five-layer framework from `core-methodology.md`:

- audit
- demand
- value
- structure
- fulfillment

Each hypothesis must be falsifiable and mapped to one family.

Primary hypothesis family sources:

- default methodology families
- domain-pack `driver_family_templates`

If the active pack provides family templates, use them to enrich or specialize the candidate set. Do not invent pack-specific families with no methodological anchor.

### 2. Filter by schema feasibility

Use `DataContextBundle` only.

Typical feasibility checks:

- audit: always feasible when the headline metric has at least one plausible mapping
- demand: demand metrics are discoverable in `metric_mapping`
- value: required headline and denominator metrics are discoverable
- structure: at least one dimension is `ga` or `beta`
- fulfillment: both pack semantics and discovered schema support a fulfillment metric path

If not testable:

- set `schema_feasibility = "not_testable"`
- set `status = "not_tested"`
- set `relevance_score = 0.0`
- leave `query_plan = []`

### 3. Score relevance

Reason holistically from:

- `NormalizedIntent.intent_profile`
- domain-pack `domain_priors`
- domain-pack `operator_preferences`
- discovery risk signals
- warehouse load status
- comparison feasibility

Use `evidence_basis` to cite concrete discovery findings.

### 4. Build executable Round 1

Round 1 is audit-first.

Rules:

- `round_1_contract.target_hypotheses` may only contain audit-layer hypotheses
- `round_1_contract.operator_id` must be an audit operator
- if the audit contract needs headline verification, its queries must explicitly verify the primary metric
- do not substitute placeholder `order_count` or `buyer_count` queries for headline metric verification

Every `round_1_contract.queries[]` item must be a full `QueryExecutionRequest` with explicit SQL.

### 5. Build explanatory `query_plan`

For each hypothesis, `query_plan` explains how contract queries support the hypothesis.

Rules:

- `query_plan` may reference `supports_contract_query_id`
- `query_plan` is not executable by itself
- execution happens only through `InvestigationContract.queries[]`

### 6. Apply load-sensitive pruning

If warehouse load is `constrained` or `degraded`:

- keep audit-first legality intact
- prefer `cheap` queries for Round 1
- use domain-pack `performance_risks` to avoid expensive or fragile fields and patterns
- record pruning decisions in `planning_notes`

### 7. Preserve downstream iteration freedom

Planning defines the candidate explanation space, not a fixed multi-round script.

Rules:

- do not pre-plan Round 2+ agendas, operator order, or a default round-by-round path
- do not imply that hypothesis-board ranking is the execution order for future rounds
- keep `planning_notes` informative, but do not use them to smuggle a fixed continuation script

---

## Domain Pack Consumption

This stage consumes:

- `driver_family_templates`
- `domain_priors`
- `operator_preferences`
- `performance_risks`

It may also rely on metric and dimension canonicals already normalized earlier.

---

## Non-Negotiable Rules

- Follow the shared contracts in `contracts.md`.
- Emit `PlanBundle`, not just `HypothesisBoard`.
- All executable queries must appear in `round_1_contract.queries[]` as full `QueryExecutionRequest` objects.
- Do not assume downstream code will compile semantic query plans into SQL.
- Round 1 must be audit-first.
- Keep `query_plan` and `round_1_contract` aligned by query id.
- Limit planning commitments to Round 1 plus the candidate search space. Later rounds must be re-authored from the latest evaluation.
