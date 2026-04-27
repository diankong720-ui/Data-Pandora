---
name: data-visualization
description: Post-finalization visualization and report assembly stage. Consumes persisted session evidence and produces descriptive stats, chart assets, and a human-readable markdown report.
internal_only: true
---

# Data Visualization

This skill runs only after both `final_answer.json` and `report_evidence.json`
have been persisted.

Its job is to turn already-persisted research evidence into a readable
artifact bundle for humans:

- `report_evidence.json`
- `chart_spec_bundle.json`
- `descriptive_stats.json`
- `visualization_manifest.json`
- `charts/*.plot-data.json`
- `charts/*.png`
- `report.md`

It is a reporting stage, not a research stage.

## Inputs

- `intent.json`
- `environment_scan.json`
- `plan.json`
- session `round_bundles`
- `final_answer.json`
- `report_evidence.json`
- `chart_spec_bundle.json`

## Responsibilities

- validate structured chart specs authored by the LLM
- consume only already-executed and already-persisted session evidence
- render the LLM-authored high-level `plot_spec` directly, without inferring chart type, field roles, or transform intent
- generate descriptive statistics summaries
- generate chart files, plot-data snapshots, and captions
- persist chart lineage back to the originating `query_refs` and `evidence_refs`
- assemble the final markdown report

## Non-Responsibilities

- do not generate new SQL
- do not request another investigation round
- do not modify `final_answer.json`
- do not create conclusions beyond the already-persisted final answer and report evidence
- do not force chart output when evidence is weak or irrelevant
- do not guess business semantics, chart type, field roles, or transform logic when a chart spec is under-specified

## Chart Admission Rules

Only include a chart when all conditions are true:

- it directly supports the user question or the persisted final answer
- it is backed by stable persisted query results from the current session
- the visual is interpretable without inventing missing semantics
- the referenced `evidence_refs` and `query_refs` resolve within the current session

When these conditions are not met:

- omit the chart
- record the omission in `descriptive_stats.json`
- explain the omission briefly in `report.md`
- keep the failure auditable through `visualization_manifest.json` and `charts/*.plot-data.json`

## Preferred v1 Chart Types

- primary metric trend chart
- primary vs comparison window chart
- top-N segment distribution chart
- numeric relationship chart when the session evidence clearly supports it

Notes:

- these are defaults, not exclusive targets
- the LLM should prefer emitting a complete `ChartSpec` over a vague chart suggestion
- `semantic_chart_type` is a free-form semantic label owned by the LLM
- `renderer_hint` is optional free-form provenance, not a runtime enum
- runtime renders high-level `plot_spec` instructions through matplotlib
- runtime capabilities are declared by `get_visualization_capabilities()`
- preferred matplotlib chart types are:
  `line`, `bar`, `horizontal_bar`, `scatter`
- additional supported chart types are:
  `area`, `histogram`, `box`, `heatmap`

## Chart Spec Guidance

Default target: make every spec directly renderable.

Each chart spec should explicitly provide:

- `source_query_ref`
- `query_refs`
- `evidence_refs`
- `plot_data.items`
- `plot_spec`
- `why_this_chart`
- `renderer_hint` whenever the rendering approach is worth recording for audit
- chart types and field mappings must stay within the current runtime capability declaration

Avoid these patterns:

- only naming a chart idea without structured fields
- leaving runtime to guess layout or visual encoding
- using chart captions to introduce new conclusions
- using runtime-side transform rules as a substitute for explicit plot-data organization
- persisting full source result rows in plot-data snapshots when a smaller chart payload is enough

## Report Rules

`report.md` is the main human-readable deliverable.

Recommended section order:

1. title and question definition
2. headline conclusion
3. key evidence
4. descriptive statistics and chart interpretation
5. contradictions, limitations, and residual questions
6. recommended follow-up

Chart captions must stay descriptive:

- describe what is visible
- connect to existing persisted evidence when relevant
- avoid introducing new judgments
