from __future__ import annotations

import shutil
import time
import unittest
from pathlib import Path
from typing import Any

import runtime
from runtime.visualization import (
    assemble_report_artifacts,
    build_chart_affordance_bundle_from_session_evidence,
    compile_chart_specs_from_affordance_plan,
    render_chart_artifacts,
)


class ExampleWarehouse(runtime.WarehouseClient):
    @property
    def identity(self) -> str:
        return "example://warehouse"

    def execute(self, sql: str, *, timeout: float = 30.0, max_rows: int = 10_000) -> runtime.QueryResult:
        return runtime.QueryResult(
            rows=[
                {"week": "2026-W01", "example_segment": "a", "example_measure": 10},
                {"week": "2026-W02", "example_segment": "a", "example_measure": 12},
            ],
            columns=["week", "example_segment", "example_measure"],
        )


def session_evidence_with_mixed_rows() -> dict[str, Any]:
    rows = [
        {"metric": "example_metric", "example_total": 1000},
        {"week": "2026-W01", "example_segment": "example_segment_a", "example_measure_share": 0.31},
        {"week": "2026-W02", "example_segment": "example_segment_a", "example_measure_share": 0.36},
        {"example_dimension": "A", "example_measure": 12},
        {"example_dimension": "B", "example_measure": 8},
    ]
    return {
        "slug": "test_example",
        "session_id": "session_test",
        "round_bundles": [
            {
                "evaluation": {"round_id": "round_1"},
                "executed_queries": [
                    {
                        "query_id": "q_mixed_metric",
                        "status": "success",
                        "result_rows_persisted": True,
                        "result_rows": rows,
                    }
                ],
            }
        ],
        "report_evidence": {
            "entries": [
                {
                    "evidence_ref": "ev_metric",
                    "section": "supported_claims",
                    "text": "Metric evidence.",
                    "query_refs": [{"round_id": "round_1", "query_id": "q_mixed_metric"}],
                    "web_refs": [],
                }
            ]
        },
    }


def minimal_intent(raw_question: str = "示例指标趋势是什么？") -> dict[str, Any]:
    return {
        "normalized_intent": {
            "intent_id": "intent_example",
            "raw_question": raw_question,
            "question_style": "operational",
            "problem_type": "trend",
            "primary_problem_type": "trend",
            "business_object": {"label": "example", "entity_type": "business_scope"},
            "core_metric": "example_measure",
            "time_scope": {"primary": {"label": "2026-W01 to 2026-W02", "start": "2026-01-01", "end": "2026-01-14", "grain": "week"}},
            "comparison_scope": {"type": "none", "windows": []},
            "dimensions": [],
            "filters": [],
            "intent_profile": {},
            "problem_type_scores": {"trend": 1.0},
            "domain_pack_id": "example",
            "mapping_confidence": "high",
            "clarification_needed": False,
            "clarification_reasons": [],
            "clarification_request": None,
        },
        "pack_gaps": [],
    }


def minimal_discovery() -> dict[str, Any]:
    return {
        "intent_id": "intent_example",
        "environment_scan": {},
        "schema_map": {},
        "metric_mapping": {},
        "time_fields": [],
        "dimension_fields": [],
        "supported_dimension_capabilities": [],
        "joinability": {"join_paths": []},
        "comparison_feasibility": {"status": "supported", "reason": "Example data is available."},
        "warehouse_load_status": "normal",
        "report_conflict_hint": "",
        "quality_report": {"status": "pass", "issues": []},
        "evidence_status": "available",
    }


def minimal_contract() -> dict[str, Any]:
    return {
        "contract_id": "round_1_example",
        "round_number": 1,
        "operator_id": "audit_baseline",
        "target_hypotheses": ["H1"],
        "sql_budget": 1,
        "allowed_cost_classes": ["cheap"],
        "queries": [
            {
                "query_id": "q_example_trend",
                "description": "Read example trend rows.",
                "sql": "SELECT 1 AS example_measure",
                "workspace": "default",
                "output_name": "example_trend",
                "cache_policy": "bypass",
                "cost_class": "cheap",
            }
        ],
        "pass_conditions": ["Example trend rows are available."],
        "pivot_conditions": ["Example trend rows are unavailable."],
        "max_rounds": 20,
        "notes": [],
    }


def minimal_plan() -> dict[str, Any]:
    contract = minimal_contract()
    return {
        "hypothesis_board": [
            {
                "hypothesis_id": "H1",
                "family": "example",
                "class": "audit",
                "layer": "audit",
                "statement": "Example trend can be measured.",
                "relevance_score": 1.0,
                "evidence_basis": "Example request.",
                "schema_feasibility": "feasible",
                "status": "proposed",
                "query_plan": [],
                "notes": [],
            }
        ],
        "round_1_contract": contract,
        "planning_notes": [],
        "max_rounds": contract["max_rounds"],
    }


def minimal_evaluation() -> dict[str, Any]:
    return {
        "round_id": "round_1",
        "round_number": 1,
        "contract_id": "round_1_example",
        "continuation_decision_ref": "runtime_overwrites_this",
        "hypothesis_updates": [],
        "residual_update": {"confidence_band": "high", "stalled_round_streak": 0, "negative_gain_streak": 0},
        "residual_score": 10,
        "residual_band": "low",
        "open_questions": [],
        "continuation_guidance": None,
        "scores": {"warehouse_burden": "low"},
        "recommended_next_action": "stop",
        "should_continue": False,
        "stop_reason": "Enough example evidence.",
        "operator_gain": "Example evidence gathered.",
        "gain_direction": "positive",
        "confidence_shift": "up",
        "correction_mode": False,
        "conclusion_state": "partial_answer_available",
        "incompleteness_category": "",
    }


def minimal_final_answer(slug: str) -> dict[str, Any]:
    return {
        "session_slug": slug,
        "conclusion_state": "partial_answer_available",
        "headline_conclusion": "Example trend evidence is available.",
        "supported_claims": [
            {
                "claim": "Example trend rows were retrieved.",
                "query_refs": [{"round_id": "round_1", "query_id": "q_example_trend"}],
                "web_refs": [],
                "evaluation_refs": [],
                "evidence_channels": ["warehouse_sql"],
            }
        ],
        "contradictions": [],
        "residual_summary": {
            "residual_score": 10,
            "residual_band": "low",
            "current_unexplained_ratio": 0.1,
            "open_questions": [],
        },
        "correction_mode": False,
        "incompleteness_category": "",
        "recommended_follow_up": [],
    }


def minimal_report_evidence(slug: str, session_id: str) -> dict[str, Any]:
    return {
        "session_slug": slug,
        "session_id": session_id,
        "entries": [
            {
                "evidence_ref": "ev_example_trend",
                "section": "supported_claims",
                "text": "Example trend rows were retrieved.",
                "query_refs": [{"round_id": "round_1", "query_id": "q_example_trend"}],
                "web_refs": [],
            }
        ],
        "generated_at": time.time(),
    }


def direct_chart_spec_bundle(slug: str, session_id: str) -> dict[str, Any]:
    return {
        "session_slug": slug,
        "session_id": session_id,
        "specs": [
            {
                "spec_id": "llm_direct_spec",
                "title": "LLM direct chart spec",
                "caption": "This direct chart spec should be rejected in the governed session path.",
                "semantic_chart_type": "trend_line",
                "narrative_role": "supporting",
                "report_section": "visualizations",
                "evidence_refs": ["ev_example_trend"],
                "query_refs": [{"round_id": "round_1", "query_id": "q_example_trend"}],
                "source_query_ref": {"round_id": "round_1", "query_id": "q_example_trend"},
                "plot_data": {
                    "items": [
                        {"item_id": "row_1", "payload": {"week": "2026-W01", "example_measure": 10}, "source_row_index": 0},
                        {"item_id": "row_2", "payload": {"week": "2026-W02", "example_measure": 12}, "source_row_index": 1},
                    ]
                },
                "plot_spec": {"chart_type": "line", "x_field": "week", "y_field": "example_measure"},
                "why_this_chart": "LLM supplied fields and rows directly.",
            }
        ],
        "generated_at": time.time(),
    }


def run_minimal_session_with_chart_payload(chart_payload_factory: Any) -> dict[str, Any]:
    slug = f"test_governed_chart_{int(time.time() * 1_000_000)}"
    try:
        return runtime.run_research_session(
            ExampleWarehouse(),
            slug,
            raw_question="示例指标趋势是什么？",
            current_date="2026-05-07",
            produce_intent=lambda **_: minimal_intent(),
            produce_discovery=lambda **_: minimal_discovery(),
            produce_plan=lambda **_: minimal_plan(),
            produce_evaluation=lambda **_: minimal_evaluation(),
            produce_final_answer=lambda **_: minimal_final_answer(slug),
            produce_report_evidence=lambda session_id, **_: minimal_report_evidence(slug, session_id),
            produce_chart_specs=lambda session_id, **_: chart_payload_factory(slug, session_id),
        )
    except Exception:
        shutil.rmtree(Path("RESEARCH") / slug, ignore_errors=True)
        raise


class VisualizationAffordanceTests(unittest.TestCase):
    def test_mixed_summary_trend_and_distribution_rows_are_split_into_homogeneous_datasets(self) -> None:
        bundle = build_chart_affordance_bundle_from_session_evidence(
            session_evidence_with_mixed_rows(),
            generated_at=100.0,
        )

        datasets = bundle["datasets"]
        self.assertEqual(len(datasets), 3)
        for dataset in datasets:
            guaranteed_fields = set(dataset["guaranteed_fields"])
            for row in dataset["rows"]:
                self.assertTrue(guaranteed_fields.issubset(row["payload"].keys()))

        summary = next(dataset for dataset in datasets if dataset["semantic_role"] == "summary")
        self.assertEqual(summary["status"], "not_chartable")
        self.assertIn("row_count", summary["omission_reason"])

        trend = next(dataset for dataset in datasets if dataset["grain"] == "weekly")
        self.assertEqual(trend["status"], "chartable")
        self.assertEqual(trend["guaranteed_fields"], ["example_measure_share", "example_segment", "week"])
        self.assertIn("line", trend["eligible_chart_types"])

        distribution = next(dataset for dataset in datasets if dataset["grain"] == "example_dimension")
        self.assertEqual(distribution["status"], "chartable")
        self.assertEqual(distribution["guaranteed_fields"], ["example_dimension", "example_measure"])
        self.assertIn("horizontal_bar", distribution["eligible_chart_types"])

        line_affordance = next(
            affordance
            for affordance in bundle["chart_affordances"]
            if affordance["dataset_id"] == trend["dataset_id"] and affordance["chart_type"] == "line"
        )
        self.assertEqual(line_affordance["x_field"], "week")
        self.assertEqual(line_affordance["y_field"], "example_measure_share")
        self.assertEqual(line_affordance["series_field"], "example_segment")

    def test_compile_rejects_missing_affordance_with_specific_layer(self) -> None:
        affordance_bundle = build_chart_affordance_bundle_from_session_evidence(
            session_evidence_with_mixed_rows(),
            generated_at=100.0,
        )
        compiled = compile_chart_specs_from_affordance_plan(
            {
                "session_slug": "test_example",
                "session_id": "session_test",
                "charts": [
                    {
                        "chart_id": "missing_chart",
                        "affordance_id": "missing_affordance",
                        "title": "Missing",
                        "caption": "Missing affordance.",
                        "narrative_role": "supporting",
                        "report_section": "visualizations",
                        "why_this_chart": "Exercise compile rejection.",
                    }
                ],
                "generated_at": time.time(),
            },
            affordance_bundle,
            generated_at=101.0,
        )

        self.assertEqual(compiled["chart_spec_bundle"]["specs"], [])
        self.assertEqual(compiled["chart_compile_report"]["compiled_count"], 0)
        self.assertEqual(
            compiled["chart_compile_report"]["omitted_visuals"][0]["layer"],
            "plan_selected_invalid_affordance",
        )
        self.assertIn("missing_affordance", compiled["chart_compile_report"]["omitted_visuals"][0]["reason"])

    def test_compile_uses_affordance_fields_and_rows_without_llm_field_inference(self) -> None:
        affordance_bundle = build_chart_affordance_bundle_from_session_evidence(
            session_evidence_with_mixed_rows(),
            generated_at=100.0,
        )
        line_affordance = next(
            affordance
            for affordance in affordance_bundle["chart_affordances"]
            if affordance["chart_type"] == "line"
        )

        compiled = compile_chart_specs_from_affordance_plan(
            {
                "session_slug": "test_example",
                "session_id": "session_test",
                "charts": [
                    {
                        "chart_id": "example_segment_a_share_weekly",
                        "affordance_id": line_affordance["affordance_id"],
                        "title": "Example share weekly trend",
                        "caption": "Example segment share by period.",
                        "narrative_role": "trend_context",
                        "report_section": "visualizations",
                        "why_this_chart": "Shows the weekly movement in a chart-ready dataset.",
                    }
                ],
                "generated_at": time.time(),
            },
            affordance_bundle,
            generated_at=101.0,
        )

        specs = compiled["chart_spec_bundle"]["specs"]
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["affordance_id"], line_affordance["affordance_id"])
        self.assertEqual(spec["plot_spec"]["chart_type"], "line")
        self.assertEqual(spec["plot_spec"]["x_field"], "week")
        self.assertEqual(spec["plot_spec"]["y_field"], "example_measure_share")
        self.assertEqual(spec["plot_spec"]["series_field"], "example_segment")
        self.assertEqual([item["source_row_index"] for item in spec["plot_data"]["items"]], [1, 2])
        for item in spec["plot_data"]["items"]:
            self.assertIn("week", item["payload"])
            self.assertIn("example_measure_share", item["payload"])

    def test_text_only_report_exposes_compile_omission_reason_when_no_specs_compile(self) -> None:
        slug = f"test_chart_text_only_{int(time.time() * 1_000_000)}"
        session = runtime.start_session(slug, raw_question="为什么没有图表？", created_at=time.time())
        session_id = session["session_id"]
        try:
            runtime.persist_artifact(
                slug,
                "final_answer.json",
                {
                    "session_slug": slug,
                    "conclusion_state": "partial_answer_available",
                    "headline_conclusion": "No stable chart was generated.",
                    "residual_summary": {"open_questions": []},
                    "recommended_follow_up": [],
                },
                session_id=session_id,
                strict_session=True,
            )
            runtime.persist_artifact(
                slug,
                "report_evidence.json",
                {"session_slug": slug, "session_id": session_id, "entries": [], "generated_at": time.time()},
                session_id=session_id,
                strict_session=True,
            )
            runtime.persist_artifact(
                slug,
                "chart_spec_bundle.json",
                {"session_slug": slug, "session_id": session_id, "specs": [], "generated_at": time.time()},
                session_id=session_id,
                strict_session=True,
            )
            runtime.persist_artifact(
                slug,
                "chart_compile_report.json",
                {
                    "session_slug": slug,
                    "session_id": session_id,
                    "compiled_count": 0,
                    "omitted_visuals": [
                        {
                            "layer": "plan_selected_invalid_affordance",
                            "chart_id": "missing_chart",
                            "affordance_id": "missing_affordance",
                            "reason": "Unknown chart affordance_id: missing_affordance",
                        }
                    ],
                    "omission_reasons": ["Unknown chart affordance_id: missing_affordance"],
                    "generated_at": time.time(),
                },
                session_id=session_id,
                strict_session=True,
            )

            render_bundle = render_chart_artifacts(slug, session_id=session_id)
            self.assertEqual(render_bundle["descriptive_stats"]["visualization_status"], "text_only")
            self.assertEqual(render_bundle["descriptive_stats"]["charts_generated_count"], 0)
            self.assertEqual(render_bundle["descriptive_stats"]["omitted_visuals"][0]["layer"], "plan_selected_invalid_affordance")

            report_bundle = assemble_report_artifacts(slug, session_id=session_id)
            self.assertIn("Unknown chart affordance_id: missing_affordance", report_bundle["report_markdown"])
        finally:
            shutil.rmtree(Path("RESEARCH") / slug, ignore_errors=True)

    def test_governed_session_rejects_direct_chart_spec_bundle_from_chart_producer(self) -> None:
        result = run_minimal_session_with_chart_payload(direct_chart_spec_bundle)
        try:
            self.assertEqual(result["chart_spec_bundle"]["specs"], [])
            self.assertEqual(result["chart_compile_report"]["compiled_count"], 0)
            self.assertEqual(
                result["chart_compile_report"]["omitted_visuals"][0]["layer"],
                "plan_selected_invalid_affordance",
            )
            self.assertIn("Direct ChartSpecBundle", result["chart_compile_report"]["omitted_visuals"][0]["reason"])
            self.assertEqual(result["descriptive_stats"]["visualization_status"], "text_only")
        finally:
            shutil.rmtree(Path("RESEARCH") / result["slug"], ignore_errors=True)

    def test_governed_session_persists_compile_omission_for_malformed_plan(self) -> None:
        result = run_minimal_session_with_chart_payload(
            lambda slug, session_id: {
                "session_slug": slug,
                "session_id": session_id,
                "charts": "bad",
                "generated_at": time.time(),
            }
        )
        try:
            self.assertEqual(result["chart_spec_bundle"]["specs"], [])
            self.assertEqual(result["chart_compile_report"]["compiled_count"], 0)
            self.assertEqual(
                result["chart_compile_report"]["omitted_visuals"][0]["reason"],
                "Visualization plan charts must be a list",
            )
            self.assertEqual(result["descriptive_stats"]["visualization_status"], "text_only")
        finally:
            shutil.rmtree(Path("RESEARCH") / result["slug"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
