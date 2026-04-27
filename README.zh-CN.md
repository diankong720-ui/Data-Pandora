# Deep Research Skill Family

这个仓库定义的是一套以 contract 为中心、由 LLM 主导的业务研究 skill 栈。它适合回答
“为什么某个指标变化了？”、“某个分组变化是谁驱动的？”、“这个运营趋势是不是真的？”这类问题。

核心设计很明确：

- LLM 负责业务语义、假设设计、SQL 编写、评估推理和最终结论。
- runtime 负责阶段顺序、schema-safe handoff、SQL 执行、缓存策略、artifact 落盘、lineage 校验和合规报告。
- runtime 永远不会把语义占位符编译成 SQL，也不会替 LLM 发明业务含义。

用户侧唯一正式入口是 [`deep-research`](skills/deep-research/SKILL.md)。其他 skill 文档都是这个 orchestrator 内部使用的 stage 协议。

---

## 核心方法论

方法论定义在 [`core-methodology.md`](skills/deep-research/references/core-methodology.md)，它是 domain-agnostic 的。Domain pack 可以调优词表和先验，但不能替代方法论本身。

### 指导原则

- Baseline before claims：先验证 headline issue 和分析框架是否成立，再提出驱动原因。
- Bounded investigation：每一轮可执行研究都必须由显式 `InvestigationContract` 管住边界。
- Traceable evidence：最终答案里的每个 supported claim 都必须追溯到具体查询结果或显式 evaluation lineage。
- Graceful degradation：当 schema、负载或 runtime 限制阻断决定性测试时，给出诚实的 partial answer，而不是扩大扫描范围。
- Honest uncertainty：矛盾、残余不确定性和未排除的竞争假设必须保留。

### 五层分析框架

研究方法使用五个分析层。一次 session 不一定要跑完所有层，但每层的职责顺序不能混淆。

| Layer | 作用 | 常见证据 |
| --- | --- | --- |
| Audit | 检查观察到的问题和分析框架是否真实成立。 | 指标是否存在、scope 是否有效、时间字段是否正确、定义是否冲突。 |
| Demand | 检查活动量是否变化。 | 交易数、活跃实体数、买家数或用户数。 |
| Value | 检查单次活动价值是否变化。 | 客单价、单价、频率、转化率或比率变化。 |
| Structure | 检查支持的维度结构是否发生 composition shift。 | 渠道、产品、地区、卖家、分组、集中度变化。 |
| Fulfillment | 检查运营或供给侧因素是否解释 headline movement。 | 可用性、履约、取消、供给约束。 |

Round 1 必须 audit-first。后续轮次只有在最新 evaluation 授权了更清晰的下一步测试后，才可以进入 Demand、Value、Structure 或 Fulfillment。

### Residual Logic

Deep research loop 由 residual uncertainty 驱动，而不是由“把轮次数用完”驱动。

Residual 包含两部分：

- Arithmetic residual：headline movement 还有多少没有被归因。
- Epistemic residual：由于证据弱、证据矛盾、运行被阻断或尚未测试，还剩多少认知不确定性。

每轮结束后，evaluator 必须重建 residual state，更新 hypothesis state，并选择 `refine`、`pivot`、`stop` 或 `restart`。只有当 evaluator 能指出一个高价值 unresolved question，并且有更清晰的下一步测试时，新一轮才合法。

---

## 完整执行流程

协议是串行的。Stage 不能跳过、重排，也不能合并成自由发挥式回答。

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

概念上，Stage 7 是 “data visualization”。在 runtime 中它拆成 `chart_spec`、`chart_render` 和 `report_assembly`，这样图表语义、渲染动作和最终报告组装都可以单独审计。

runtime 阶段顺序实现在 [`runtime/session_state.py`](runtime/session_state.py)，端到端 orchestrated flow 由 [`run_research_session`](runtime/session_orchestration.py) 实现。

---

## Stage 职责与协作方式

每个 stage 只拥有一种决策权。下游 stage 消费上游已经冻结的 artifact，而不是重写它们。

| Stage | 角色 | 消费 | 产出 | 如何交给下游 |
| --- | --- | --- | --- | --- |
| 1. Intent Recognition | 把用户问题规范化成安全的研究框架。 | `raw_question`、`current_date`、domain pack 选项。 | `IntentRecognitionResult`、冻结的 `NormalizedIntent`、`pack_gaps`。 | Discovery 拿到没有物理 schema 泄漏的语义 intent。 |
| 2. Environment Discovery | 检查可见仓库事实，并映射 schema 能力。 | 冻结 intent、runtime probes、合法 domain-pack hints。 | `DataContextBundle`。 | Planning 拿到 metric、time、dimension、joinability 和 feasibility 上下文。 |
| 3. Planning | 定义候选解释空间，并编写可执行 Round 1。 | 冻结 intent、discovery bundle、domain pack priors。 | `PlanBundle`、`HypothesisBoardItem[]`、`round_1_contract`。 | Execution 拿到完整 `InvestigationContract`；Evaluation 把 hypothesis board 当作上下文。 |
| 4. Execution | 只运行显式 contract 里的 queries，并落盘证据。 | 当前 `InvestigationContract`、runtime client、cache/admission 策略。 | `QueryExecutionResult[]`、execution log、round bundle。 | Evaluation 拿到真实 query outcomes 和 runtime metadata。 |
| 5. Evaluation | 解释本轮结果，更新 residual state，并授权下一步。 | Contract、executed queries、prior evaluation、plan context。 | `RoundEvaluationResult`，继续时产出 continuation token。 | Next-contract producer 获取结构化 guidance；或 finalization 获取 stop state。 |
| 6. Finalization | 合成 evidence-backed answer 和 report evidence bundle。 | 最新 evaluation 和完整 session evidence。 | `FinalAnswer`、`ReportEvidenceBundle`、`ReportEvidenceIndex`。 | Chart spec 只能消费 supported 且 indexed 的证据。 |
| 7a. Chart Spec | 基于已落盘证据编写结构化图表规格。 | Final answer、report evidence、session evidence、renderer capabilities。 | `chart_spec_bundle.json`。 | Chart render 拿到显式 plot data 和高层 `plot_spec` 指令。 |
| 7b. Chart Render | 渲染图表，但不发明图表语义。 | Chart specs、已落盘证据、renderer capabilities。 | `descriptive_stats.json`、`visualization_manifest.json`、图表文件和 plot-data snapshots。 | Report assembly 拿到可审计 visuals 和 stats。 |
| 7c. Report Assembly | 组装人类可读报告。 | Final answer、report evidence、chart artifacts、manifest locale/template。 | `report.md`、刷新后的 compliance report。 | Suggestion synthesis 只在报告完成后运行。 |
| 8. Suggestion Synthesis | session 结束后提出 domain-pack 改进建议。 | `pack_gaps`、active pack id、完整 session context。 | 需要时写入 `domain_pack_suggestions.json`。 | 没有下游 stage；这是 best-effort 且非阻塞。 |

关键协作规则：

- Stage 1 不能选择 tables、fields、joins 或 SQL。
- Stage 2 可以映射 schema 能力，但不能验证 headline movement，也不能下原因结论。
- Stage 3 可以规划 Round 1，但不能预先脚本化 Round 2+。
- Stage 4 不能改写 SQL、添加 query 或修复不完整 contract。
- Stage 5 不能发明 execution evidence，只能评估已落盘结果并授权下一步。
- Stage 6 不能引入 unsupported claims，也不能隐藏矛盾。
- Stage 7 不能创建新分析、新 SQL 或新结论。
- Stage 8 不能阻塞或重写已经完成的答案。

---

## Deep Research Loop 机制设计

Deep research loop 是这一版 skill 最关键的机制。它避免系统把 hypothesis board 当成固定脚本，同时仍然支持多轮研究。

### 1. Round 1 由 Plan Seed

`PlanBundle` 定义候选 hypothesis space，但只包含一个直接可执行 contract：`round_1_contract`。

Round 1 必须 audit-first。如果用户问的是指标变化，Round 1 必须先验证 headline metric 和分析框架，再允许提升后续 driver claims。

### 2. Execution 被 Contract 锁定

Execution 接收一个 `InvestigationContract`，并且只运行其中的 `queries[]`。runtime 可以执行 SQL safety、cache policy、row limit 和 warehouse admission，但不会改写 SQL，也不会推断缺失语义。

这样每条被执行的 query 都由 LLM 显式负责，runtime 行为也保持可审计。

### 3. Evaluation 是控制点

每轮结束后，`investigation-evaluator` 会产出 `RoundEvaluationResult`。

Evaluation 必须：

- 将每个 hypothesis 更新为 `proposed`、`supported`、`weakened`、`rejected`、`not_tested` 或 `blocked_by_load`
- 重建 residual state，并说明不确定性是下降、持平还是变差
- 识别 unresolved open questions
- 推荐且只推荐一个下一步动作：`refine`、`pivot`、`stop` 或 `restart`
- 当 `should_continue = true` 时必须产出 `continuation_guidance`

剩余的 `max_rounds` 只是安全上限，不能单独构成继续的理由。

### 4. Continuation 必须被授权

Round 2+ 不是从原始 plan 里顺序展开出来的，而是由最新 evaluation 推导出来的。

当 evaluation 授权 `refine` 或 `pivot` 时，runtime 会记录 continuation token。next-contract producer 必须把这个 token 和最新 `continuation_guidance` 当作控制输入。

下一轮 contract 必须：

- 指向被授权的 residual component
- 将 queries 绑定到 prioritized open question ids
- 相比父轮发生实质变化，例如目标更窄、operator 不同或 SQL 不同
- 保留到 parent evaluation 的 lineage

如果 continuation 缺少结构化 guidance，session 会停在 evaluation，而不是即兴生成下一轮。

### 5. Stop 和 Restart 不同

`stop` 表示当前 intent frame 仍然有效，可以基于最佳可用证据 finalization，哪怕结论是 partial answer。

`restart` 表示 audit evidence 已经推翻了冻结的 intent frame。此时写 `final_answer.json` 是非法的，session 必须回到 Stage 1，并在新 frame 下重新生成 intent、discovery 和 planning。

### 6. Finalization 冻结 Claims

Finalization 只能总结 loop 已经支持的内容。它生成答案和 report evidence，之后 visualization/report stages 只是把这些证据包装成人类可读交付物。任何 post-finalization stage 都不能添加新的分析结论。

---

## Runtime 与 Contract 能力面

所有共享对象定义都在 [`contracts.md`](skills/deep-research/references/contracts.md)。如果某个 stage 文档和 `contracts.md` 冲突，以 contract 文件为准。

主要 runtime 入口：

- [`runtime/tools.py`](runtime/tools.py)：`execute_query_request()` 和兼容层 `execute_sql()`。
- [`runtime/orchestration.py`](runtime/orchestration.py)：`execute_investigation_contract()`、`execute_round_and_persist()` 和 `finalize_session()`。
- [`runtime/session_orchestration.py`](runtime/session_orchestration.py)：`run_research_session()` 和各 stage persistence gates。
- [`runtime/evaluation.py`](runtime/evaluation.py)：round evaluation 校验与落盘。
- [`runtime/final_answer.py`](runtime/final_answer.py)：final answer 校验与落盘。
- [`runtime/visualization.py`](runtime/visualization.py)：基于已落盘证据进行 chart rendering 和 report assembly。
- [`runtime/domain_pack_suggestions.py`](runtime/domain_pack_suggestions.py)：post-session domain-pack suggestion 校验与落盘。

本地 agent 应优先通过 [`scripts/deep_research_runtime.py`](scripts/deep_research_runtime.py)
连接 runtime，而不是假设 `runtime/` 会从 skill 目录自动 import：

```bash
python3 scripts/deep_research_runtime.py doctor
```

如果当前目录在 `skills/deep-research/` 内：

```bash
python3 ../../scripts/deep_research_runtime.py doctor
```

这个 bridge 会自动把仓库根目录加入 `sys.path`，并提供
`start-session`、`capabilities`、`persist-*`、`probe-schema`、
`execute-contract`、`render-charts`、`assemble-report` 和
`persist-suggestions` 等命令。`start-session` 还接受 `--runtime-policy`、
`--report-policy` 和 `--semantic-guard-policy` JSON 文件。`probe-schema` 和 `execute-contract` 只接受
已注册的 client factory alias；host 通过 `DEEP_RESEARCH_CLIENT_FACTORIES`
注册自定义 alias。
`run_research_session(...)` 仍然是 host integration API；它需要外部提供
`produce_*` callbacks，不是一个独立的 LLM 自动运行器。

### 示例签名 HTTP 数仓接口

仓库内置了一个占位的签名 HTTP 数仓 client：
[`runtime/example_clients/vendor_http_client.py`](runtime/example_clients/vendor_http_client.py)。
它实现了 `WarehouseClient`，可以直接用于 `execute_sql()`、
`execute_query_request()`、`probe-schema` 和 `execute-contract`。

连接配置通过环境变量提供：

```bash
export VENDOR_WAREHOUSE_BASE_URL="https://<warehouse-host>"
export VENDOR_WAREHOUSE_PATH="/<sql-endpoint>"
export VENDOR_WAREHOUSE_CHANNEL="<channel-or-app-id>"
export VENDOR_WAREHOUSE_SECRET="<request-signing-secret>"
```

可选配置：

```bash
export VENDOR_WAREHOUSE_QUERY_TIMEOUT="60"
export VENDOR_WAREHOUSE_MAX_ROWS="200000"
```

runtime CLI 已注册 `vendor_http`
factory alias，例如：

```bash
python3 scripts/deep_research_runtime.py probe-schema \
  --client-factory vendor_http \
  --list-tables-sql "SHOW TABLES"
```

runtime 保证：

- 校验 stage order 和 prerequisite artifacts
- 冻结 `intent.json` 等上游 artifacts
- 只执行 LLM 显式写好的 SQL
- 执行 `cache_policy = bypass | allow_read | require_read`
- 将执行元数据写入 `execution_log.json`
- 将 round bundle 按 `{ contract, executed_queries, evaluation }` 持久化
- 校验 Round 2+ continuation lineage
- 在 `restart_required` 后阻断 finalization
- 构建 `protocol_trace.json`、`evidence_graph.json` 和 `compliance_report.json`

runtime 不会：

- 选择 tables、joins、filters、metrics、hypotheses 或 next actions
- 将 semantic query plan 编译成 SQL
- 生成 evaluator reasoning 或 final claims
- 为 visualization 推断 chart type、field roles 或 transform semantics
- 写死报告语言；host 可以传入 `report_locale`、`report_template` 或
  `runtime_policy.report_policy`

---

## Domain Packs

Domain pack 是唯一的上下文定制化配置层。它帮助 LLM 映射业务词汇和选择更好的 priors，但不会覆盖 shared contracts 或五层方法论。

它可以调优：

- metric、dimension 和 business-object 词汇
- unsupported-dimension hints
- problem-type scoring hints
- hypothesis-family priors
- operator preferences
- performance-risk hints

它不能向 Stage 1 提供物理 schema 捷径，不能替代 discovery，也不能取消“可执行 SQL 必须由 LLM 显式编写”这条要求。

Pack schema 和 consumer matrix 见 [`DOMAIN_PACK_GUIDE.md`](skills/deep-research/domain-packs/DOMAIN_PACK_GUIDE.md)。

---

## Artifact 布局

每个 session 都会在 `RESEARCH/<slug>/sessions/<session_id>/` 下写入显式 artifacts。

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

artifact 语义：

- `intent.json` 存冻结后的 `NormalizedIntent`。
- `intent_sidecar.json` 存 `pack_gaps`。
- `environment_scan.json` 存 `DataContextBundle`。
- `plan.json` 存 `PlanBundle`。
- `rounds/<generation_id>/<round_id>.json` 存本轮 contract、executed query results 和 evaluation。
- `final_answer.json` 存 supported answer。
- `report_evidence.json` 和 `report_evidence_index.json` 存 report 与 visualization 阶段使用的 claim lineage。
- `visualization_manifest.json` 存 chart lineage 和 render outcomes。
- `domain_pack_suggestions.json` 是可选 best-effort artifact。

如果上层消费者需要完整上下文，使用 `load_session_evidence(slug)`。

---

## 权威文档

- [`skills/deep-research/SKILL.md`](skills/deep-research/SKILL.md)：用户侧正式协议入口。
- [`contracts.md`](skills/deep-research/references/contracts.md)：共享对象唯一事实源。
- [`core-methodology.md`](skills/deep-research/references/core-methodology.md)：residual 逻辑、round 策略和 conclusion-state 纪律。
- [`intent-recognition/SKILL.md`](skills/intent-recognition/SKILL.md)：Stage 1 intent normalization。
- [`data-discovery/SKILL.md`](skills/data-discovery/SKILL.md)：Stage 2 environment discovery。
- [`hypothesis-engine.md`](skills/deep-research/sub-skills/hypothesis-engine.md)：Stage 3 planning。
- [`investigation-evaluator.md`](skills/deep-research/sub-skills/investigation-evaluator.md)：Stage 5 evaluation。
- [`data-visualization/SKILL.md`](skills/data-visualization/SKILL.md)：Stage 7 reporting and visualization。

---

## 不可违反的规则

1. 完整 session 必须以 `deep-research` 作为入口。
2. 共享对象 shape 以 `contracts.md` 为唯一事实源。
3. Stage 2 开始后冻结 `NormalizedIntent`。
4. Stage 2 只做 discovery。
5. Round 1 必须 audit-first。
6. 只执行显式 `InvestigationContract.queries[]`。
7. 只有最新 evaluation 识别出更好的下一步测试时，才能继续。
8. 矛盾和 residual uncertainty 必须保留。
9. 每个 supported final claim 都必须追溯到已落盘证据。
10. 不能用 visualization 或 report assembly 引入新 claim。
