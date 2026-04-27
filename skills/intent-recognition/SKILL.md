---
name: intent-recognition
description: Internal Stage 1 skill for intent normalization. LLM-driven and domain-pack-guided. Not a standalone user entrypoint.
internal_only: true
---

# Intent Recognition

This skill owns Stage 1 only.

It converts a raw user question into a frozen `IntentRecognitionResult` using the shared contracts in `skills/deep-research/references/contracts.md`.

This stage does not:

- choose tables
- validate schema
- infer physical field names
- generate SQL
- repair downstream execution failures

The official user-facing entrypoint remains `deep-research`.

---

## Inputs

Provide exactly one of these pack-selection inputs:

- `available_domain_packs[]`: the set of packs this stage may choose from
- `forced_domain_pack_id`: explicit pack override

Additional inputs:

- `raw_question`: the original user question
- `current_date`: anchor date in `YYYY-MM-DD` format

If `forced_domain_pack_id` is supplied, do not run pack selection.

---

## Output

Produce `IntentRecognitionResult` as defined in `contracts.md`:

- `normalized_intent`
- `pack_gaps`

Do not embed `pack_gaps` inside `NormalizedIntent`.

---

## Required Reasoning Steps

### 1. Select the active domain pack

If `forced_domain_pack_id` is present, use it.

Otherwise, score `available_domain_packs[]` using:

- pack `label`
- `taxonomy.problem_types[].keywords`
- obvious context-specific vocabulary in the question

If no specific pack clearly matches, use `generic`.

Tie-break rule:

- prefer the pack with the strongest vocabulary match
- if still tied, prefer the more specific non-generic pack
- if still tied, fall back to `generic`

### 2. Detect `question_style`

Use the shared enum:

- `operational`
- `comparative`
- `abstract`

Guidance:

- `operational`: concrete metric, scope, and time window are already present
- `comparative`: explicit or implied comparison across time or segments
- `abstract`: broad request with material ambiguity remaining

### 3. Score canonical `problem_type`

Use these default canonical problem types even if the active pack has empty taxonomy:

- `metric_read`
- `trend_comparison`
- `root_cause_analysis`
- `segment_comparison`
- `distribution_scan`
- `operational_diagnosis`
- `data_quality_audit`

Use pack taxonomy as an enrichment layer, not as the sole source of legal problem types.

Rules:

- choose the top-scoring canonical id as `problem_type`
- set `primary_problem_type` to its display label only
- record all considered scores in `problem_type_scores`
- populate `intent_profile` based on the actual question, not on a preferred future plan

### 4. Normalize business object, metric, dimensions, and filters

Use domain-pack semantic vocabulary only.

- `business_object` describes business scope, not database scope
- `core_metric` must be a canonical metric id
- `dimensions` contains semantic dimension ids only
- `filters` contains semantic filters only

If a term is resolved through general reasoning instead of explicit pack lexicon, record a `pack_gap`.

### 5. Resolve time and comparison scope

Relative time is always anchored to `current_date`.

Examples:

- `last month` -> previous calendar month relative to `current_date`
- `this week` -> current week starting Monday
- `last 30 days` -> rolling window ending on `current_date`
- `this year` -> Jan 1 through `current_date`

Rules:

- do not ask for clarification only because a relative phrase omits a year
- do ask for clarification when the window cannot be determined from `current_date` and sentence structure
- comparison signals fill `comparison_scope`; they do not require schema knowledge at this stage

### 6. Clarification gate

Set `clarification_needed = true` only when a missing or ambiguous slot would make safe downstream planning impossible.

Typical blocking cases:

- `core_metric` cannot be inferred at all
- time intent is materially unresolved even after anchoring to `current_date`
- the request is too abstract and the business object remains too broad for safe planning
- multiple metric interpretations remain equally plausible and would materially change the analysis

When blocked:

- append all reasons to `clarification_reasons`
- populate `clarification_request`
- set `mapping_confidence = "low"`
- stop before Stage 2

When not blocked:

- set `mapping_confidence = "high"`

---

## Domain Pack Consumption

This stage consumes the following pack fields:

- `taxonomy.problem_types`
- `lexicon.metrics`
- `lexicon.dimensions`
- `lexicon.business_aliases`
- `lexicon.unsupported_dimensions`

This stage does not consume:

- physical schema hints
- `driver_family_templates`
- `domain_priors`
- `operator_preferences`

Those belong to later stages.

---

## Pack Gap Rules

Record a `pack_gap` when a resolution depended on best-effort reasoning rather than an explicit pack entry.

Categories:

- `metric_alias`
- `dimension_alias`
- `unsupported_dimension`

Rules:

- if the active pack already contains the alias, do not record a gap
- if `domain_pack_id = "generic"` and the question uses context-specific vocabulary, every best-effort resolution is a candidate gap
- `pack_gaps` is persisted in `intent_sidecar.json` and later consumed only by Domain Pack Suggestion Synthesis

---

## Non-Negotiable Rules

- Follow the shared contracts in `contracts.md`.
- Do not include table names, field names, joins, or SQL.
- Do not mutate `NormalizedIntent` after Stage 2 begins.
- Do not treat missing domain pack detail as an automatic clarification trigger.
- If clarification is needed, stop here and surface `clarification_request` before any downstream stage.
