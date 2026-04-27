---
name: deep-research
description: Official user-facing orchestrator for business research. This is the only user entrypoint in the Deep Research skill family. Once selected, the agent must execute the protocol serially and must not fall back to freeform analysis.
---

# Deep Research

`deep-research` is the official user-facing skill.

It is the only legal entrypoint for a full business research session.

The following documents are internal stage docs, not standalone user entrypoints:

- `skills/intent-recognition/SKILL.md`
- `skills/data-discovery/SKILL.md`
- `skills/deep-research/sub-skills/hypothesis-engine.md`
- `skills/deep-research/sub-skills/investigation-evaluator.md`
- `skills/data-visualization/SKILL.md`

If this skill is selected, do not switch back to generic freeform analysis.
Continue only through the Deep Research protocol below.

---

## Mandatory Bootstrap

Before taking any stage-local action, load the minimum protocol context:

1. Read `references/contracts.md`.
2. Read `references/core-methodology.md`.
3. Read the stage-local internal doc before producing that stage's output.

Minimum stage-local doc mapping:

- Stage 1: `skills/intent-recognition/SKILL.md`
- Stage 2: `skills/data-discovery/SKILL.md`
- Stage 3: `sub-skills/hypothesis-engine.md`
- Stage 5: `sub-skills/investigation-evaluator.md`
- Stage 7: `skills/data-visualization/SKILL.md`

Do not start a stage if you have not loaded the shared contracts and the relevant stage-local rules.

If the required doc cannot be loaded, stop and surface the missing dependency instead of improvising.

### Runtime Binding

This skill is not document-only. Before producing or persisting any stage
artifact, bind the local Python runtime through the bridge CLI:

```bash
python3 scripts/deep_research_runtime.py doctor
```

If the current working directory is this skill directory, call the same bridge
through the repository-relative path:

```bash
python3 ../../scripts/deep_research_runtime.py doctor
```

The `doctor` command must return `runtime_import_ok: true` before Stage 1
continues. If it fails, stop and report the runtime binding error instead of
continuing with document-only analysis.

Use `scripts/deep_research_runtime.py` as the default handoff surface for local
agents:

- `start-session` creates the session root, `manifest.json`, and
  `session_state.json`.
- `capabilities` exposes runtime renderer capabilities and domain packs.
- `persist-intent`, `persist-discovery`, `persist-plan`,
  `persist-evaluation`, `persist-finalization`, and `persist-chart-spec`
  validate and persist LLM-authored stage artifacts.
- `probe-schema` and `execute-contract` call runtime tools with a registered
  host-supplied `WarehouseClient` factory alias. Do not pass module paths or
  filesystem paths from LLM-authored content; the host must register aliases
  through `DEEP_RESEARCH_CLIENT_FACTORIES`.
- `render-charts`, `assemble-report`, `persist-suggestions`, and
  `session-evidence` expose downstream runtime actions.

Do not assume sibling `runtime/` modules are importable from arbitrary skill
working directories. The bridge CLI adds the repository root to `sys.path` and
is the supported local-agent entrypoint.

---

## Protocol Mode

This skill runs as a protocol-governed session.

The runtime is the enforcement layer.
The LLM is the decision-maker.

The runtime may:

- validate stage order
- validate contracts and lineage
- enforce admission and safety
- execute explicit SQL
- persist explicit artifacts
- record protocol trace and compliance artifacts

The runtime must not:

- classify the task for you
- choose tables, joins, filters, or metrics for you
- rewrite SQL
- repair a weak contract by inference
- choose whether to continue, pivot, stop, or restart
- create new business claims on your behalf

If a required field, rationale, or lineage link is missing, you must supply it explicitly.
Do not expect runtime to infer the missing semantics.

### Host Policy Overrides

Some behaviors that may look like runtime truth are now host-configurable policy
surfaces instead of hardcoded defaults.

- semantic regex guards
  - host may enable them with `configure_semantic_guard_policy({...})` or
    observe-mode `configure_semantic_guard_patterns({...})`
  - default behavior: disabled
  - recommended behavior: observe only; strict mode must be an explicit host policy
  - treat them as audit hints only, not as the main definition of stage responsibility
- report copy / locale
  - host may set `report_locale`, `report_template`, or `report_policy` through
    `run_research_session(...)`
  - host may also persist `runtime_policy.report_policy` in `manifest.json`
  - locale resolution order: explicit manifest locale, report policy locale,
    then raw-question fallback inference
  - default behavior: runtime chooses a locale preset only as fallback

Do not author stage outputs to satisfy a specific regex vocabulary or a fixed
report language. Your outputs must remain contract-valid independent of those
host policy choices.

---

## Required Serial Flow

```text
1. Intent Recognition
2. Environment Discovery
3. Planning
4. Execution
5. Evaluation
6. Finalization
7. Data Visualization
8. Domain Pack Suggestion Synthesis
```

Stages must not be skipped.
Stages must not be reordered.
Do not merge multiple stages into one freeform answer.

If a stage fails its completion gate, stop at that stage.
Do not continue because "the next stage is obvious."

---

## Session State Rules

- `NormalizedIntent` becomes frozen once Stage 2 begins.
- `PlanBundle` defines the candidate search space and executable Round 1 only.
- Each round after Round 1 must be explicitly authorized by the latest evaluation.
- `max_rounds` is a hard safety ceiling, not a target to consume.
- `restart` invalidates the current frozen intent frame.
- `final_answer.json` is illegal if the latest evaluation requires restart.
- Visualization and report assembly are post-finalization only.

If any frozen artifact must change, restart the relevant frame instead of mutating it in place.

---

## Stage Contracts

### Stage 1. Intent Recognition

Goal:

- produce `IntentRecognitionResult`
- freeze a valid `NormalizedIntent`
- decide whether clarification is required before downstream work

Required inputs:

- `raw_question`
- `current_date`
- exactly one of:
  - `available_domain_packs[]`
  - `forced_domain_pack_id`

Allowed actions:

- choose the active domain pack unless forced
- normalize business object, metric, time, dimensions, filters, and problem type
- emit `pack_gaps`
- decide whether clarification is required

Forbidden actions:

- do not choose tables
- do not choose fields or join keys
- do not generate SQL
- do not validate schema
- do not repair downstream failures
- do not include physical schema hints in semantic fields

Completion gate:

- persist `intent.json`
- persist `intent_sidecar.json`

Stop conditions:

- if `clarification_needed = true`, stop here and surface `clarification_request`
- do not continue to Stage 2 until clarification is resolved

### Stage 2. Environment Discovery

Goal:

- produce `DataContextBundle`
- map environment facts into schema, metric, time, dimension, and joinability understanding

Required inputs:

- frozen `NormalizedIntent`
- relevant session context
- active domain pack semantic hints where legal

Allowed actions:

- inspect visible tables, headers, samples, cache facts, and warehouse load
- interpret discovery findings into the formal `DataContextBundle`
- record comparison feasibility as capability, not as a result
- record discovery-time risks and conflicts

Forbidden actions:

- do not verify the final headline movement
- do not compute business deltas as conclusions
- do not conclude root causes
- do not emit executable SQL
- do not rank later-round hypotheses as if planning has already happened
- do not choose the next round action

Completion gate:

- persist `environment_scan.json`

Stop conditions:

- if the required frozen intent is missing, stop
- if clarification is still outstanding, stop
- if the payload drifts into planning, evaluation, or finalization responsibilities, stop

### Stage 3. Planning

Goal:

- produce `PlanBundle`
- define the candidate explanation space
- author a directly executable Round 1 contract

Required inputs:

- frozen `NormalizedIntent`
- `DataContextBundle`
- active domain pack

Allowed actions:

- generate a falsifiable hypothesis board
- score schema feasibility and relevance
- author `round_1_contract`
- record concise planning notes

Forbidden actions:

- do not pre-script Round 2+
- do not treat the hypothesis board as a fixed execution order
- do not output semantic query plans that rely on runtime compilation
- do not use non-audit operators for Round 1

Completion gate:

- persist `plan.json`
- Round 1 must be audit-first and executable

Stop conditions:

- if discovery is missing, stop
- if Round 1 is not audit-first, stop
- if Round 1 queries are not full executable `QueryExecutionRequest` objects, stop

### Stage 4. Execution

Goal:

- execute only the explicit `InvestigationContract`
- persist the round's executable evidence

Required inputs:

- persisted `PlanBundle`
- current `InvestigationContract`
- valid round authorization

Allowed actions:

- execute explicit queries in order
- use runtime cache and admission behavior as provided
- record execution metadata and evidence lineage

Forbidden actions:

- do not rewrite SQL
- do not add queries that are not in the contract
- do not mutate the contract during execution
- do not infer missing joins, filters, or fields at runtime

Completion gate:

- persist the round bundle with:
  - `contract`
  - `executed_queries`
  - later `evaluation`

Stop conditions:

- if Round 1 differs from `PlanBundle.round_1_contract`, stop
- if Round 2+ lacks valid continuation authorization, stop
- if execution results do not map to the frozen contract query set, stop

### Stage 5. Evaluation

Goal:

- produce `RoundEvaluationResult`
- update residual state
- decide whether to continue, pivot, stop, or restart

Required inputs:

- current round contract
- current round executed query results
- current hypothesis board / effective hypothesis state
- prior residual state
- current warehouse state

Allowed actions:

- update hypothesis states
- rebuild residual state
- recommend `refine | pivot | stop | restart`
- emit `continuation_guidance` when continuation is authorized

Forbidden actions:

- do not recommend continuation because round budget remains
- do not use failed execution as evidence of falsity
- do not leave open questions vague or decorative
- do not assume runtime will infer the next contract

Completion gate:

- persist `RoundEvaluationResult`
- if continuing, the next round must be explicitly authorized through structured continuation guidance

Stop conditions:

- if continuation is chosen without explicit `continuation_guidance`, stop
- if `restart` is required, return to intent and do not proceed to finalization

### Stage 6. Finalization

Goal:

- produce the evidence-backed final answer
- persist explicit report evidence for downstream reporting

Required inputs:

- latest valid round evaluation
- complete session evidence

Allowed actions:

- produce `FinalAnswer`
- produce `ReportEvidenceBundle`
- summarize only already-supported claims
- keep contradictions and residual uncertainty visible

Forbidden actions:

- do not write `final_answer.json` after restart is required
- do not introduce unsupported claims
- do not hide contradictions to make the narrative cleaner
- do not use finalization as a second execution or planning stage

Completion gate:

- persist `final_answer.json`
- persist `report_evidence.json`
- persist `report_evidence_index.json`

Stop conditions:

- if latest evaluation requires restart, stop
- if supported claims do not have valid lineage, stop

### Stage 7. Data Visualization

Goal:

- render reporting artifacts from already-persisted evidence
- assemble descriptive statistics and chart assets

Required inputs:

- `final_answer.json`
- `report_evidence.json`
- `chart_spec_bundle.json`
- current session round evidence

Allowed actions:

- produce complete `ChartSpec` objects
- render chart assets from persisted evidence
- assemble `report.md`
- explain chart omission when evidence is weak or insufficient

Forbidden actions:

- do not generate new SQL
- do not ask for another investigation round
- do not change `final_answer.json`
- do not introduce new analytical claims in captions or report prose
- do not invent missing semantics for an underspecified chart
- do not assume `report.md` always uses Chinese or always uses English
- do not depend on fixed runtime-owned section titles when preparing chart/report-facing text

Completion gate:

- persist `descriptive_stats.json`
- persist `visualization_manifest.json`
- persist chart assets and plot-data snapshots
- persist `report.md`

Stop conditions:

- if finalization artifacts are missing, stop
- if chart specs reference unknown query or evidence refs, stop

### Stage 8. Domain Pack Suggestion Synthesis

Goal:

- produce best-effort domain pack improvement suggestions after the session is already complete

Trigger:

- `pack_gaps` is non-empty
- or `domain_pack_id = "generic"`

Allowed actions:

- propose additions to taxonomy, lexicon, unsupported dimensions, priors, operator preferences, and related pack metadata

Forbidden actions:

- do not block the final answer on this stage
- do not rewrite prior session artifacts

Completion gate:

- persist `domain_pack_suggestions.json` when applicable

---

## Producer Guidance

If an external orchestrator implements producer functions, those producers must obey the protocol.

`run_research_session(...)` is a host integration API, not a standalone LLM
runner. It requires host-provided `produce_*` callbacks. Local skill agents
that do not provide those callbacks must use the bridge CLI stage commands
above, author one explicit JSON artifact at a time, and let the runtime validate
and persist each transition.

### `produce_evaluation(...)`

Treat evaluation as a closure-and-authorization step.

It must:

- use only the current round's executed evidence
- rebuild residual state explicitly
- decide `refine | pivot | stop | restart`
- emit `continuation_guidance` whenever `should_continue = true`
- explain why another full round is still worthwhile
- name which paths are no longer worth pursuing

It must not:

- continue only because `max_rounds` remains
- emit vague open questions
- authorize continuation without a narrower next test

### `produce_next_contract(...)`

Treat `latest_evaluation` as the controlling input.
Treat `plan_bundle` as background only.

It must:

- start from `latest_evaluation.continuation_guidance`
- bind the next round to prioritized open questions and the target residual component
- include `material_change_reason` with changed axes, why the change is material,
  how it can reduce residual uncertainty, and why it is not repeating the parent round
- avoid exact parent-contract replay
- for `refine`, sharpen the query set toward the authorized residual or open question
- for `pivot`, change `operator_id` or `target_hypotheses` in substance
- map each query to an authorized open question or residual component

It must not:

- continue because the original plan had more hypotheses
- replay the parent round with near-duplicate queries
- emit `pivot` without a substantive operator or target change
- emit `refine` without query-level sharpening

---

## Artifact Contract

Persist only explicit objects defined in `references/contracts.md`.

```text
RESEARCH/<slug>/
  latest_session.json
  sessions/
    <session_id>/
      intent.json
      intent_sidecar.json
      environment_scan.json
      plan.json
      rounds/
        <generation_id>/
          <round_id>.json
      execution_log.json
      final_answer.json
      report_evidence.json
      report_evidence_index.json
      chart_spec_bundle.json
      descriptive_stats.json
      visualization_manifest.json
      charts/*.plot-data.json
      charts/*.png
      report.md
      domain_pack_suggestions.json
      manifest.json
```

Do not write extra artifacts that are not backed by explicit LLM output or runtime facts.

---

## Common Violation Patterns

Treat the following as protocol violations:

- using an internal stage doc as the user entrypoint instead of `deep-research`
- putting table names, field names, or SQL into Stage 1 intent output
- using Stage 2 discovery to start root cause analysis
- using Stage 3 planning to script Round 2+ in advance
- changing the contract during execution
- continuing because budget remains instead of because a better next test exists
- writing `final_answer.json` after `restart_required`
- using visualization or report assembly to introduce new claims

If you detect one of these patterns in your own draft output, stop and correct it before proceeding.

---

## Non-Negotiable Rules

- Follow `references/contracts.md` as the single source of truth for shared object shapes.
- Follow `references/core-methodology.md` for residual logic, round discipline, and stop policy.
- Do not mutate `NormalizedIntent` in place after Stage 2 begins.
- Do not skip audit-first planning for Round 1.
- Do not continue unless the latest evaluation identifies a materially unresolved question with a clearer next test.
- Keep cached evidence explicitly labeled.
- Keep runtime blocking facts explicit when `blocked_runtime` is the final state.
- If a gate condition fails, stop at that stage rather than improvising a recovery path.
