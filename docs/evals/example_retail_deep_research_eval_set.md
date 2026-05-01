# Example Retail Deep Research Eval Set

This document defines a benchmark for evaluating external agents running the
`deep-research` skill family against real Example Retail warehouse data.

The benchmark is designed to test protocol fidelity, business reasoning, SQL
authorship, evidence lineage, and final-answer usefulness. It intentionally
separates two artifacts:

- `gold`: frozen answers generated from Example Retail production data by trusted
  SQL and human review.
- `run`: artifacts produced by the evaluated agent through the skill protocol.

No benchmark case should require the agent to know the gold numbers in advance.
The agent receives only the user prompt, current date, allowed domain pack, and
warehouse credentials.

## Goals

The eval set should answer four questions:

1. Does the agent follow the serial `deep-research` protocol instead of doing
   freeform analysis?
2. Does it correctly map Example Retail business language to warehouse evidence?
3. Does it generate bounded, safe, and useful SQL investigations?
4. Does the final report state only evidence-backed conclusions, including
   uncertainty and unresolved questions when appropriate?

## Non-Goals

- This is not a pure SQL benchmark. A syntactically correct query is insufficient
  if the agent skips audit, invents business meaning, or overclaims.
- This is not a chart aesthetics benchmark. Visualization is scored only for
  evidence fidelity and report usefulness.
- This is not a prompt memorization benchmark. Gold SQL and exact expected
  numbers must not be visible to the evaluated agent.

## Data Requirements

Use a stable Example Retail data snapshot or a fixed production date window. For each
case, the benchmark maintainer should freeze:

- warehouse identity and snapshot timestamp
- table list and relevant schema profile
- gold SQL used by maintainers
- gold numeric results
- reviewed business conclusion
- accepted tolerance bands
- known caveats, exclusions, and competing explanations

Recommended tolerance:

- exact match for counts and categorical labels when using the same filters
- `<= 0.5 percentage points` for rates and shares
- `<= 1.0% relative error` for currency-like totals unless source tables
  are known to update late
- conclusion-level tolerance based on direction, primary driver identity, and
  explained-share band rather than exact prose

## Case Schema

Each case should be represented as JSON or YAML with this shape:

```yaml
case_id: example_retail_q3_q4_metric_driver_001
category: metric_change_driver
difficulty: medium
current_date: "2026-04-27"
user_prompt: "为什么 Example Retail 2025 Q4 交易额 比 Q3 下降？主要是谁驱动的？"
allowed_domain_pack: generic
warehouse_client: example_retail
max_rounds: 3
gold:
  snapshot_id: "example_retail-prod-YYYYMMDD"
  gold_sql_refs:
    - private/gold/example_retail_q3_q4_metric_driver_001_baseline.sql
    - private/gold/example_retail_q3_q4_metric_driver_001_decomp.sql
  headline:
    metric: 交易额
    comparison: "2025 Q4 vs 2025 Q3"
    direction: down
    tolerance: "1.0% relative numeric tolerance"
  accepted_primary_drivers:
    - dimension: "product"
      label: "<gold product/category label>"
      direction: negative
      min_explained_share: 0.35
  required_caveats:
    - "Must distinguish demand volume from value per order."
scoring_focus:
  - audit_first
  - metric_definition
  - decomposition_quality
  - evidence_lineage
  - honest_uncertainty
```

## Benchmark Cases

### A. Intent And Audit Cases

These cases check whether the agent normalizes ambiguous business questions,
audits the headline, and stops for clarification when needed.

| Case ID | User Prompt | Real Success Standard |
| --- | --- | --- |
| `example_retail_intent_001` | `最近销售是不是明显下滑？` | Agent must ask for or infer a bounded comparison window from `current_date`; it must not scan all history and declare a driver without an audit. |
| `example_retail_intent_002` | `Example Retail 上个月订单量怎么样？` | Agent must resolve "上个月" relative to `current_date`, identify an order-count metric, and compare against a reasonable baseline such as previous month or same period prior cycle. |
| `example_retail_intent_003` | `看下 A-001 这台机器为什么变差了` | Agent must normalize `A-001` as a machine/device business object only after schema discovery supports it; if multiple entity types match, it must request clarification. |
| `example_retail_audit_004` | `Q4 交易额 比 Q3 下降了吗？` | Gold verifies whether the headline direction is true. Agent passes only if it first validates Q3/Q4 交易额 before claiming causes. |

### B. Metric Change Driver Cases

These are the main business-research cases. They require multi-layer reasoning
across audit, demand, value, and structure.

| Case ID | User Prompt | Real Success Standard |
| --- | --- | --- |
| `example_retail_q3_q4_metric_driver_001` | `为什么 Example Retail 2025 Q4 交易额 比 Q3 下降？主要是谁驱动的？` | Must match gold headline direction and magnitude within tolerance; identify the accepted primary negative driver dimension and distinguish volume vs value effects. |
| `example_retail_machine_A-001_revenue_002` | `示例设备 A-001 最近 30 天收入下降，原因是什么？` | Must isolate the machine, compare recent 30 days with previous 30 days, and determine whether drop is due to transaction count, average order value, SKU mix, or availability/fulfillment if schema supports it. |
| `example_retail_product_A-001_forecast_003` | `示例设备 A-001 相关商品接下来一周销量会不会继续走低？` | Must avoid unsupported forecasting if only historical SQL evidence exists. A passing answer gives trend evidence, confidence limits, and explicit non-forecast caveat unless a forecast table/model is discovered. |
| `example_retail_channel_shift_004` | `哪个渠道拖累了最近一周销售？` | Must compute channel-level contribution to total change, not just rank by current sales volume. Top negative channel must match gold. |

### C. Composition And Contribution Cases

These cases test whether the agent can answer "who drove it" with contribution
logic instead of descriptive sorting.

| Case ID | User Prompt | Real Success Standard |
| --- | --- | --- |
| `example_retail_sku_mix_001` | `最近 交易额 变化主要是哪些商品结构变化造成的？` | Must compare period shares and contribution to delta by SKU/category; success requires top contributors to match gold set, order-insensitive for top 3. |
| `example_retail_region_mix_002` | `哪些城市/区域的变化解释了本月订单波动？` | Must separate regional mix from total activity volume; if region is unsupported in schema, must report blocked evidence rather than inventing geography. |
| `example_retail_seller_concentration_003` | `销售下滑是不是集中在少数商户/点位？` | Must compute concentration, such as top-N contribution to negative delta, and state whether decline is concentrated or broad-based according to gold threshold. |

### D. Contradiction And Restart Cases

These cases ensure the agent can reverse course when the user's frame is wrong.

| Case ID | User Prompt | Real Success Standard |
| --- | --- | --- |
| `example_retail_false_decline_001` | `上周订单量大跌的原因是什么？` | Gold headline shows no material drop. Agent must reject or weaken the premise after audit and avoid driver claims. |
| `example_retail_wrong_metric_002` | `退款增加导致 交易额 下降了吗？` | If refund fields are absent or refund movement contradicts the premise, agent must mark the hypothesis weakened/rejected and preserve uncertainty. |
| `example_retail_scope_mismatch_003` | `全站都在跌吗？` | Gold shows decline only in a subset. Agent must detect scope mismatch and recommend restart or reframing when the frozen intent is invalid. |

### E. Runtime And Governance Cases

These cases test operational discipline rather than business insight alone.

| Case ID | User Prompt | Real Success Standard |
| --- | --- | --- |
| `example_retail_load_guard_001` | `把所有机器所有商品逐天展开，找出每个异常原因` | Agent must bound query scope, respect admission/load policy, and produce a partial answer or sampling plan rather than a warehouse-heavy exhaustive scan. |
| `example_retail_lineage_002` | `给我一份带图的分析报告` | Final claims must have `query_refs` or `evaluation_refs`; charts must use explicit persisted evidence and must not introduce new unsupported conclusions. |
| `example_retail_domain_pack_003` | `把这次分析沉淀成之后能复用的业务词表` | Agent should write domain-pack suggestions only after report assembly, using observed `pack_gaps`; suggestions must not rewrite completed conclusions. |

## Scoring Rubric

Score each run out of 100.

| Dimension | Weight | Pass Criteria |
| --- | ---: | --- |
| Protocol compliance | 20 | Loads contracts/methodology, uses runtime bridge, persists required artifacts in order, respects continuation tokens. |
| Intent and scope correctness | 12 | Correct time windows, business object, metric, dimensions, filters, and clarification behavior. |
| SQL and evidence quality | 18 | Safe SQL, bounded queries, correct metric definitions, useful decompositions, no hidden query rewriting. |
| Business conclusion accuracy | 22 | Matches gold direction, magnitude band, primary driver, rejected hypotheses, and caveats. |
| Residual and uncertainty discipline | 10 | Proper `stop/refine/pivot/restart`, material open questions, no "used all rounds" behavior. |
| Lineage and artifact integrity | 10 | Claims link to persisted query/evaluation evidence; compliance report has no severe failures. |
| Report and visualization usefulness | 8 | Clear final answer, appropriate charts from evidence, no chart-driven new claims. |

Suggested verdict bands:

- `>= 85`: production-ready on this benchmark slice
- `70-84`: usable with review
- `55-69`: partially useful but not autonomous
- `< 55`: fails skill-family expectations

## Automated Checks

The harness should collect the session directory and run these checks:

1. `python3 scripts/deep_research_runtime.py doctor`
2. Validate existence and order of:
   - `intent.json`
   - `intent_sidecar.json`
   - `environment_scan.json`
   - `plan.json`
   - at least one `rounds/<round_id>.json`
   - `final_answer.json` unless the valid outcome is clarification or restart
   - `report_evidence.json`
   - `compliance_report.json`
3. Parse `compliance_report.json` and fail hard on lineage, stage-order, or
   unsupported-claim violations.
4. Compare gold headline metrics against agent-produced evidence:
   - direction
   - absolute and relative delta
   - top contributors
   - metric denominator consistency
5. Check that Round 1 uses an audit operator and targets audit-layer hypotheses.
6. Check that Round 2+ contracts reference latest evaluation open questions.
7. Check that final supported claims have valid `query_refs` or
   `evaluation_refs`.

## Human Review Checklist

Some qualities need human review. Reviewers should answer:

- Did the agent choose a reasonable business interpretation of the user's words?
- Did it avoid overfitting to table names or sample rows too early?
- Did it explain the difference between observed movement and causal inference?
- Did it make the right call to continue, pivot, stop, or restart?
- Are caveats concrete enough for an Example Retail operator to act on?
- Would the recommended next step reduce residual uncertainty?

## Gold Generation Workflow

For each case:

1. Run trusted maintainer SQL against Example Retail using the same snapshot policy.
2. Save raw gold result tables in a private location outside the evaluated
   agent's context.
3. Write a reviewed business conclusion with accepted alternate phrasings.
4. Record exact metric definitions and exclusion rules.
5. Freeze tolerance bands.
6. Run at least one baseline internal agent to ensure the case is answerable
   within `max_rounds`.
7. Re-run gold after schema or ETL changes; if results drift beyond tolerance,
   bump `snapshot_id` or retire the case.

## Anti-Leakage Rules

- Do not include gold SQL, gold numeric values, or accepted driver labels in the
  prompt shown to the evaluated agent.
- Do not segment_b private gold files under the same working directory used by the
  evaluated agent.
- Redact sensitive table and column details from public benchmark manifests
  unless the agent is also allowed to discover them through Stage 2.
- Randomize case order and use fresh session ids for each run.

## Minimum Viable Eval Pack

Start with these five cases:

1. `example_retail_audit_004`
2. `example_retail_q3_q4_metric_driver_001`
3. `example_retail_machine_A-001_revenue_002`
4. `example_retail_false_decline_001`
5. `example_retail_lineage_002`

This gives one audit-only task, two real driver tasks, one contradiction task,
and one artifact-governance task. It is enough to detect most agents that can
write plausible analysis but cannot execute the skill family correctly.

## Recommended Harness Output

Each evaluated run should produce a compact result record:

```json
{
  "case_id": "example_retail_q3_q4_metric_driver_001",
  "agent_id": "external-agent-name",
  "snapshot_id": "example_retail-prod-YYYYMMDD",
  "score": 82,
  "verdict": "usable_with_review",
  "hard_failures": [],
  "metric_match": {
    "direction": true,
    "magnitude_within_tolerance": true,
    "primary_driver_match": true
  },
  "protocol": {
    "stage_order_valid": true,
    "round_1_audit_first": true,
    "lineage_valid": true
  },
  "review_notes": [
    "Correct primary driver, but residual state understated uncertainty around fulfillment evidence."
  ]
}
```
