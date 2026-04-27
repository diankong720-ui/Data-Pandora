---
name: data-discovery
description: Stage 2 environment discovery protocol. The runtime exposes warehouse facts; the LLM interprets them into DataContextBundle.
internal_only: true
---

# Data Discovery

This skill owns Stage 2 only.

Its job is to gather environment facts and interpret them into a `DataContextBundle` using the shared contracts in `skills/deep-research/references/contracts.md`.

This stage does not:

- validate the headline metric movement
- compute delta evidence
- execute investigation contracts
- conclude root causes

---

## Inputs

- frozen `NormalizedIntent`
- optional session context from prior user turns
- active domain pack only for semantic hints such as unsupported dimensions

---

## Output

Produce `DataContextBundle` as defined in `contracts.md`.

Required output areas:

- `environment_scan`
- `schema_map`
- `metric_mapping`
- `time_fields`
- `dimension_fields`
- `supported_dimension_capabilities`
- `joinability`
- `comparison_feasibility`
- `warehouse_load_status`
- `report_conflict_hint`
- `quality_report`
- `evidence_status`

---

## Core Principle

- The runtime returns facts.
- The LLM interprets those facts.

The runtime may provide:

- visible tables
- cached headers
- explicit table probes
- sample rows
- cache facts for explicit SQL already known to the session
- warehouse load snapshots

The runtime must not provide:

- recommended candidate tables
- recommended joins
- semantic field mappings
- metric bindings
- SQL templates
- pre-ranked investigation operators

---

## Required Workflow

### 1. Probe the environment

Gather warehouse facts only.

Typical actions:

- list visible tables
- inspect candidate tables explicitly chosen by the LLM
- inspect header information
- inspect sample rows
- record current warehouse load and admission state

### 2. Interpret discovery findings

The LLM maps raw findings into the shared `DataContextBundle` fields.

Required decisions:

- which tables appear to be headline fact tables
- which metric expressions are plausible and safe enough to carry into planning
- which time fields are suitable for the intended question
- which dimensions are groupable and at what support tier
- which join paths are validated, partial, or blocked

### 3. Record comparison feasibility

Set `comparison_feasibility` based on validated discovery findings only.

Meaning:

- `supported`: the intended comparison query can be constructed safely from the discovered schema
- `partial`: some schema support exists but a stable comparison path is not fully validated
- `blocked`: the intended comparison cannot be constructed safely from discovery findings

This field must not imply that comparison queries were already run.

### 4. Record discovery-time risks

Set `report_conflict_hint = true` only when discovery finds risk signals such as:

- multiple plausible source tables for the same semantic metric
- missing critical fields for the requested scope
- suspicious samples or inconsistent field population
- probe failures on critical tables
- unsupported dimensions requested by the user

This field must not imply that audit has already completed.

---

## Domain Pack Consumption

This stage may consume:

- `lexicon.unsupported_dimensions`

This stage does not consume:

- pack taxonomy
- driver priors
- operator preferences

---

## Non-Negotiable Rules

- Follow the shared contracts in `contracts.md`.
- Keep Stage 2 discovery-only.
- Do not place verification outcomes such as `headline_verified` or metric deltas in `DataContextBundle`.
- Do not assume downstream code will infer schema semantics from raw probes.
- Do not emit executable investigation SQL from this stage.
