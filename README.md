# Deep Research Skill Family

This repository defines a contract-first, LLM-driven business research skill
stack. It is designed for questions such as "why did this metric change?",
"what drove this segment movement?", or "is this operational trend real?"

The core idea is simple:

- The LLM owns business semantics, hypothesis design, SQL authorship,
  evaluation reasoning, and final conclusions.
- The runtime owns stage order, schema-safe handoffs, SQL execution, cache
  policy, artifact persistence, lineage validation, and compliance reporting.
- The runtime never compiles semantic placeholders into SQL and never invents
  business meaning that the LLM did not explicitly author.

The user-facing entrypoint is
[`deep-research`](skills/deep-research/SKILL.md). The other skill documents are
internal stage protocols used by that orchestrator.

---

## Core Methodology

The methodology is defined in
[`core-methodology.md`](skills/deep-research/references/core-methodology.md) and
is domain-agnostic. Domain packs can tune vocabulary and priors, but they do not
replace the method.

### Guiding Principles

- Baseline before claims: verify that the headline issue and analytical frame
  are valid before promoting driver explanations.
- Bounded investigation: every executable round is governed by an explicit
  `InvestigationContract`.
- Traceable evidence: every supported final claim must trace to concrete query
  results or explicit evaluation lineage.
- Graceful degradation: when schema, load, or runtime constraints block decisive
  tests, return an honest partial answer instead of widening the scan.
- Honest uncertainty: contradictions, residual uncertainty, and unresolved rival
  hypotheses stay visible.

### Five Analysis Layers

The research method uses five layers. A session does not have to exhaust every
layer, but it must respect their order of responsibility.

| Layer | Purpose | Typical evidence |
| --- | --- | --- |
| Audit | Check whether the observed issue and analytical frame are real. | Metric existence, scope validity, time-field validity, definition conflicts. |
| Demand | Check whether activity volume changed. | Transaction count, active entity count, buyer or user count. |
| Value | Check whether value per activity changed. | Average order value, unit value, frequency, rate changes. |
| Structure | Check whether composition changed across supported dimensions. | Channel, product, region, seller, segment, concentration shifts. |
| Fulfillment | Check whether operational or supply-side factors explain movement. | Availability, completion, fulfillment, cancellation, supply constraints. |

Round 1 is audit-first. Later rounds may move into demand, value, structure, or
fulfillment only when the latest evaluation authorizes a sharper next test.

### Residual Logic

The loop is driven by residual uncertainty, not by a desire to "use all rounds".

Residual has two parts:

- Arithmetic residual: how much of the headline movement remains unattributed.
- Epistemic residual: how much uncertainty remains because evidence is weak,
  contradictory, blocked, or not yet tested.

After every round, the evaluator rebuilds residual state, updates hypothesis
states, and chooses `refine`, `pivot`, `stop`, or `restart`. A new round is legal
only when the evaluator can name a high-value unresolved question and a clearer
next test.

---

## Full Execution Flow

The protocol is serial. Stages must not be skipped, reordered, or merged into a
freeform answer.

```text
User question + current_date
  |
  v
1. intent
   -> intent.json + intent_sidecar.json
  |
  v
2. discovery
   -> environment_scan.json
  |
  v
3. planning
   -> plan.json + round_1_contract
  |
  v
4. execution
   -> round bundle with contract + executed_queries
  |
  v
5. evaluation
   -> round evaluation + next-step decision
  |
  +-- refine / pivot --> next InvestigationContract --> Stage 4
  |
  +-- restart -------> return to Stage 1 with a new intent frame
  |
  +-- stop ----------> Stage 6
  |
  v
6. finalization
   -> final_answer.json + report_evidence.json + report_evidence_index.json
  |
  v
7a. chart_spec
   -> chart_spec_bundle.json
  |
  v
7b. chart_render
   -> descriptive_stats.json + visualization_manifest.json + charts/*
  |
  v
7c. report_assembly
   -> report.md + compliance_report.json
  |
  v
8. suggestion_synthesis
   -> domain_pack_suggestions.json when applicable
```

Conceptually, Stage 7 is "data visualization". In the runtime it is split into
`chart_spec`, `chart_render`, and `report_assembly` so chart semantics,
rendering, and final report packaging remain auditable.

The runtime stage sequence is implemented in
[`runtime/session_state.py`](runtime/session_state.py), and the end-to-end
orchestrated flow is implemented by
[`run_research_session`](runtime/session_orchestration.py).

---

## Stage Responsibilities And Collaboration

Each stage owns one kind of decision. Downstream stages consume frozen upstream
artifacts instead of rewriting them.

| Stage | Role | Consumes | Produces | Hands off to |
| --- | --- | --- | --- | --- |
| 1. Intent Recognition | Normalize the user question into a safe research frame. | `raw_question`, `current_date`, domain pack options. | `IntentRecognitionResult`, frozen `NormalizedIntent`, `pack_gaps`. | Discovery receives a semantic intent with no physical schema leakage. |
| 2. Environment Discovery | Inspect available warehouse facts and map schema capabilities. | Frozen intent, runtime probes, legal domain-pack hints. | `DataContextBundle`. | Planning receives metric, time, dimension, joinability, and feasibility context. |
| 3. Planning | Define the candidate explanation space and author executable Round 1. | Frozen intent, discovery bundle, domain pack priors. | `PlanBundle`, `HypothesisBoardItem[]`, `round_1_contract`. | Execution receives a full `InvestigationContract`; evaluation receives the hypothesis board as context. |
| 4. Execution | Run exactly the explicit contract queries and persist evidence. | Current `InvestigationContract`, runtime client, cache/admission policy. | `QueryExecutionResult[]`, execution log, round bundle. | Evaluation receives actual query outcomes and runtime metadata. |
| 5. Evaluation | Interpret the round, update residual state, and authorize the next move. | Contract, executed queries, prior evaluation, plan context. | `RoundEvaluationResult`, continuation token when continuing. | Next-contract producer receives structured guidance, or finalization receives a stop state. |
| 6. Finalization | Synthesize the evidence-backed answer and report evidence bundle. | Latest evaluation and full session evidence. | `FinalAnswer`, `ReportEvidenceBundle`, `ReportEvidenceIndex`. | Chart spec consumes only supported, indexed evidence. |
| 7a. Chart Spec | Author structured chart specifications from persisted evidence. | Final answer, report evidence, session evidence, renderer capabilities. | `chart_spec_bundle.json`. | Chart render receives explicit plot data and high-level `plot_spec` instructions. |
| 7b. Chart Render | Render charts without inventing chart semantics. | Chart specs, persisted evidence, renderer capabilities. | `descriptive_stats.json`, `visualization_manifest.json`, chart files, plot-data snapshots. | Report assembly receives auditable visuals and stats. |
| 7c. Report Assembly | Package the human-readable report. | Final answer, report evidence, chart artifacts, manifest locale/template. | `report.md`, refreshed compliance report. | Suggestion synthesis runs only after report completion. |
| 8. Suggestion Synthesis | Propose domain-pack improvements after the session. | `pack_gaps`, active pack id, completed session context. | `domain_pack_suggestions.json` when useful. | No downstream stage; this is best-effort and non-blocking. |

Important collaboration rules:

- Stage 1 cannot choose tables, fields, joins, or SQL.
- Stage 2 can map schema capabilities but cannot verify the headline movement or
  conclude causes.
- Stage 3 can plan Round 1 but cannot pre-script Round 2+.
- Stage 4 cannot rewrite SQL, add queries, or repair an incomplete contract.
- Stage 5 cannot invent execution evidence; it can only evaluate persisted
  results and authorize the next action.
- Stage 6 cannot introduce unsupported claims or hide contradictions.
- Stage 7 cannot create new analysis, new SQL, or new conclusions.
- Stage 8 cannot block or rewrite the completed answer.

---

## Deep Research Loop Design

The deep research loop is the critical mechanism in this version of the skill.
It prevents the system from turning a hypothesis board into a fixed script while
still allowing multi-round investigation.

### 1. Round 1 Is Seeded By The Plan

`PlanBundle` defines the candidate hypothesis space and includes only one
directly executable contract: `round_1_contract`.

Round 1 must be audit-first. If the user asks about a metric movement, Round 1
must validate the headline metric and frame before later driver claims are
promoted.

### 2. Execution Is Contract-Locked

Execution accepts an `InvestigationContract` and runs only its
`queries[]`. The runtime may enforce SQL safety, cache policy, row limits, and
warehouse admission, but it does not rewrite SQL or infer missing semantics.

This keeps the LLM accountable for every executed query and keeps runtime
behavior auditable.

### 3. Evaluation Is The Control Point

After each round, `investigation-evaluator` produces a
`RoundEvaluationResult`.

The evaluation must:

- update each hypothesis state as `proposed`, `supported`, `weakened`,
  `rejected`, `not_tested`, or `blocked_by_load`
- rebuild residual state and explain whether uncertainty went down, stayed
  flat, or got worse
- identify unresolved open questions
- recommend exactly one next action: `refine`, `pivot`, `stop`, or `restart`
- emit `continuation_guidance` whenever `should_continue = true`

Remaining `max_rounds` is only a safety ceiling. It is never a reason to
continue by itself.

### 4. Continuation Requires Authorization

Round 2+ is not derived by expanding the original plan. It is derived from the
latest evaluation.

When evaluation authorizes `refine` or `pivot`, the runtime records a
continuation token. The next-contract producer must use that token and the
latest `continuation_guidance` as the controlling input.

The next contract must:

- target the authorized residual component
- bind queries to prioritized open question ids
- materially change the parent round through a sharper target, a different
  operator, or different SQL
- preserve lineage back to the parent evaluation

If continuation lacks structured guidance, the session stops at evaluation
instead of improvising the next round.

### 5. Stop And Restart Are Different

`stop` means the current intent frame remains valid and the best available
answer can be finalized, possibly as a partial answer.

`restart` means audit evidence invalidated the frozen intent frame itself. In
that case, `final_answer.json` is illegal until the session returns to Stage 1
and rebuilds intent, discovery, and planning under a new frame.

### 6. Finalization Freezes Claims

Finalization can summarize only what the loop has already supported. It creates
the answer and report evidence, then visualization/report stages package that
evidence for humans. No post-finalization stage may add new analytical claims.

---

## Runtime And Contract Surface

All shared object definitions live in
[`contracts.md`](skills/deep-research/references/contracts.md). If a stage doc
conflicts with `contracts.md`, the contract file wins.

Primary runtime entrypoints:

- [`runtime/tools.py`](runtime/tools.py): `execute_query_request()` and legacy
  `execute_sql()`.
- [`runtime/orchestration.py`](runtime/orchestration.py):
  `execute_investigation_contract()`, `execute_round_and_persist()`, and
  `finalize_session()`.
- [`runtime/session_orchestration.py`](runtime/session_orchestration.py):
  `run_research_session()` and stage persistence gates.
- [`runtime/evaluation.py`](runtime/evaluation.py): round evaluation validation
  and persistence.
- [`runtime/final_answer.py`](runtime/final_answer.py): final answer validation
  and persistence.
- [`runtime/visualization.py`](runtime/visualization.py): chart rendering and
  report assembly from persisted evidence.
- [`runtime/domain_pack_suggestions.py`](runtime/domain_pack_suggestions.py):
  post-session domain-pack suggestion validation and persistence.

Local agents should bind the runtime through
[`scripts/deep_research_runtime.py`](scripts/deep_research_runtime.py) instead
of assuming sibling `runtime/` modules are importable from a skill working
directory:

```bash
python3 scripts/deep_research_runtime.py doctor
```

When the current directory is `skills/deep-research/`, use:

```bash
python3 ../../scripts/deep_research_runtime.py doctor
```

The bridge adds the repository root to `sys.path` and exposes
`start-session`, `capabilities`, `persist-*`, `probe-schema`,
`execute-contract`, `render-charts`, `assemble-report`, and
`persist-suggestions` commands. `start-session` also accepts
`--runtime-policy`, `--report-policy`, and `--semantic-guard-policy` JSON files.
`probe-schema` and `execute-contract` accept
registered client factory aliases only; hosts register custom aliases through
`DEEP_RESEARCH_CLIENT_FACTORIES`.
`run_research_session(...)` remains a host integration API; it requires external
`produce_*` callbacks and is not a standalone LLM runner.

Runtime guarantees:

- validates stage order and prerequisite artifacts
- freezes upstream artifacts such as `intent.json`
- executes only explicit LLM-authored SQL
- enforces `cache_policy = bypass | allow_read | require_read`
- records execution metadata in `execution_log.json`
- persists round bundles as `{ contract, executed_queries, evaluation }`
- validates continuation lineage for Round 2+
- blocks finalization after `restart_required`
- builds `protocol_trace.json`, `evidence_graph.json`, and
  `compliance_report.json`

Runtime does not:

- choose tables, joins, filters, metrics, hypotheses, or next actions
- compile semantic query plans into SQL
- generate evaluator reasoning or final claims
- infer chart types, field roles, or transform semantics for visualization
- hard-code report language; hosts may provide `report_locale`,
  `report_template`, or `runtime_policy.report_policy`

---

## Domain Packs

Domain packs are the only context-specific configuration layer. They help the
LLM map business vocabulary and choose better priors, but they do not override
the shared contracts or the five-layer methodology.

They can tune:

- metric, dimension, and business-object vocabulary
- unsupported-dimension hints
- problem-type scoring hints
- hypothesis-family priors
- operator preferences
- performance-risk hints

They cannot provide physical schema shortcuts to Stage 1, replace discovery, or
remove the requirement that executable SQL is authored explicitly by the LLM.

See
[`DOMAIN_PACK_GUIDE.md`](skills/deep-research/domain-packs/DOMAIN_PACK_GUIDE.md)
for the pack schema and consumer matrix.

---

## Artifact Layout

Each session writes explicit artifacts under `RESEARCH/<slug>/sessions/<session_id>/`.

```text
RESEARCH/<slug>/
  latest_session.json
  sessions/
    <session_id>/
      manifest.json
      session_state.json
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
      protocol_trace.json
      evidence_graph.json
      compliance_report.json
      domain_pack_suggestions.json
```

Artifact semantics:

- `intent.json` stores the frozen `NormalizedIntent`.
- `intent_sidecar.json` stores `pack_gaps`.
- `environment_scan.json` stores the `DataContextBundle`.
- `plan.json` stores the `PlanBundle`.
- `rounds/<generation_id>/<round_id>.json` stores the round contract, executed
  query results, and evaluation.
- `final_answer.json` stores the supported answer.
- `report_evidence.json` and `report_evidence_index.json` store claim lineage
  for report and visualization stages.
- `visualization_manifest.json` stores chart lineage and render outcomes.
- `domain_pack_suggestions.json` is optional and best-effort.

For consumers that need the complete context, use `load_session_evidence(slug)`.

---

## Canonical Documents

- [`skills/deep-research/SKILL.md`](skills/deep-research/SKILL.md): official
  user-facing protocol.
- [`contracts.md`](skills/deep-research/references/contracts.md): shared object
  source of truth.
- [`core-methodology.md`](skills/deep-research/references/core-methodology.md):
  residual logic, round policy, and conclusion-state discipline.
- [`intent-recognition/SKILL.md`](skills/intent-recognition/SKILL.md): Stage 1
  intent normalization.
- [`data-discovery/SKILL.md`](skills/data-discovery/SKILL.md): Stage 2
  environment discovery.
- [`hypothesis-engine.md`](skills/deep-research/sub-skills/hypothesis-engine.md):
  Stage 3 planning.
- [`investigation-evaluator.md`](skills/deep-research/sub-skills/investigation-evaluator.md):
  Stage 5 evaluation.
- [`data-visualization/SKILL.md`](skills/data-visualization/SKILL.md): Stage 7
  reporting and visualization.

---

## Non-Negotiable Rules

1. Use `deep-research` as the full-session entrypoint.
2. Treat `contracts.md` as the source of truth for shared object shapes.
3. Freeze `NormalizedIntent` once Stage 2 begins.
4. Keep Stage 2 discovery-only.
5. Make Round 1 audit-first.
6. Execute only explicit `InvestigationContract.queries[]`.
7. Continue only when the latest evaluation identifies a better next test.
8. Preserve contradictions and residual uncertainty.
9. Trace every supported final claim to persisted evidence.
10. Do not use visualization or report assembly to introduce new claims.
