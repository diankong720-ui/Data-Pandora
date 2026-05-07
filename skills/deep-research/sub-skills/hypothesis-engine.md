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
- decide whether a hypothesis should use SQL, web search, or mixed evidence

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
- external-environment candidates when the user question, domain priors, or
  business context make web evidence relevant

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
- whether the hypothesis needs internal metric evidence, external mechanism evidence, or a mixed bridge

Use `evidence_basis` to cite concrete discovery findings.

For each newly authored hypothesis, add `evidence_channel_plan`:

- `lanes = ["warehouse_sql"]` when SQL can directly test the hypothesis
- `lanes = ["web_search"]` when the hypothesis is about external events, policies,
  market context, competitors, weather, supply chain, or public information
- `lanes = ["warehouse_sql", "web_search"]` when the explanation requires an
  internal movement plus external mechanism bridge

Do not make web search conditional on SQL residual being high. Treat evidence
lane selection as part of hypothesis design.

### 4. Build executable Round 1

Round 1 is audit-first.

Rules:

- `round_1_contract.target_hypotheses` may only contain audit-layer hypotheses
- `round_1_contract.operator_id` must be an audit operator
- if the audit contract needs headline verification, its queries must explicitly verify the primary metric
- do not substitute placeholder `order_count` or `buyer_count` queries for headline metric verification

Every `round_1_contract.queries[]` item must be a full `QueryExecutionRequest` with explicit SQL.
Every `round_1_contract.web_searches[]` item, when present, must be a full
`WebSearchRequest` with explicit question, query, time window, entity scope,
source policy, freshness requirement, expected signal, and residual binding.

Example SQL rules:

- read `skills/deep-research/references/example-sql-rules.md` before authoring SQL for Example data
- every Example query must be a single `SELECT` or `WITH` statement
- every Example query must explicitly name target tables, time fields, time windows, metrics, dimensions, and row limits where multi-row output is possible
- `example_fact` fact queries must use `event_time` unless discovery proves a safer task-specific time field
- default to 1-7 day windows for samples, 7-30 day windows for normal aggregations, and at most 90 days for trend or low-cost count checks
- 90+ day Example analysis requires a prior cheap validation query, such as `COUNT(*)` or low-cardinality aggregation
- avoid unfiltered high-cardinality `GROUP BY`, unfiltered `ORDER BY`, and stacked `COUNT(DISTINCT ...)`
- if distinct is required, use a bounded time window and keep the distinct target set small
- join by filtering or aggregating the fact table first, then joining dimension tables such as `example_dimension`
- structure CTEs so each layer narrows data: filter/project first, aggregate second, sort/limit last
- set `cost_class = "cheap"` for bounded probes and narrow audit checks; use `standard` only when the query still satisfies Example limits and is necessary

### 5. Build explanatory `query_plan`

For each hypothesis, `query_plan` explains how contract queries support the hypothesis.

Rules:

- `query_plan` may reference `supports_contract_query_id`
- web-oriented plan notes may reference `supports_contract_search_id`
- `query_plan` is not executable by itself
- execution happens only through `InvestigationContract.queries[]` and
  `InvestigationContract.web_searches[]`

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
- All executable web searches must appear in `round_1_contract.web_searches[]` as full `WebSearchRequest` objects.
- All Example executable SQL must pass `skills/deep-research/references/example-sql-rules.md` before persistence.
- Do not assume downstream code will compile semantic query plans into SQL.
- Do not assume downstream code will rewrite web search questions or add missing web refinements.
- Round 1 must be audit-first.
- Keep `query_plan` and `round_1_contract` aligned by query id.
- Limit planning commitments to Round 1 plus the candidate search space. Later rounds must be re-authored from the latest evaluation.
