from __future__ import annotations

import os
import shutil
import time
import unittest
from pathlib import Path
from typing import Any

import runtime


class DummyWarehouse(runtime.WarehouseClient):
    @property
    def identity(self) -> str:
        return "dummy://warehouse"

    def execute(self, sql: str, *, timeout: float = 30.0, max_rows: int = 10_000) -> runtime.QueryResult:
        return runtime.QueryResult(rows=[{"metric": 10}], columns=["metric"])


class DummyWeb(runtime.WebSearchClient):
    @property
    def provider(self) -> str:
        return "dummy_web"

    def search(
        self,
        request: dict[str, Any],
        *,
        timeout: float = 30.0,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        return {
            "results": [
                {
                    "title": f"Result for {request['search_id']}",
                    "url": f"https://example.com/{request['search_id']}",
                    "content": "External signal exists.",
                }
            ],
            "fetched_pages": [],
            "source_quality_notes": [],
        }


def sql_query(query_id: str = "q_headline") -> dict[str, Any]:
    return {
        "query_id": query_id,
        "description": "Verify headline metric.",
        "sql": "SELECT 1 AS metric",
        "workspace": "default",
        "output_name": query_id,
        "cache_policy": "bypass",
        "cost_class": "cheap",
    }


def web_search(search_id: str = "w_external", **overrides: Any) -> dict[str, Any]:
    payload = {
        "search_id": search_id,
        "question": "Is there an external event?",
        "query": "external event market change",
        "time_window": {"start": "2026-03-01", "end": "2026-03-31"},
        "geo_scope": "global",
        "entity_scope": ["market"],
        "source_policy": {"preferred_source_types": ["official", "news"], "max_results": 3},
        "freshness_requirement": "same month",
        "expected_signal": "Find an external mechanism.",
        "addresses_residual_component": "external_driver",
    }
    payload.update(overrides)
    return payload


def mixed_contract() -> dict[str, Any]:
    return {
        "contract_id": "round_1_audit",
        "round_number": 1,
        "operator_id": "audit_baseline",
        "target_hypotheses": ["H1"],
        "sql_budget": 1,
        "allowed_cost_classes": ["cheap"],
        "queries": [sql_query()],
        "evidence_lanes": ["warehouse_sql", "web_search"],
        "web_search_budget": 1,
        "web_refinement_budget": 1,
        "web_searches": [web_search()],
        "pass_conditions": ["Audit and external context gathered."],
        "pivot_conditions": ["Evidence conflicts."],
        "max_rounds": 20,
        "notes": [],
    }


def recall_assessment(
    search_id: str,
    *,
    conclusion: str = "usable_supporting",
    needs_refinement: bool = False,
    refinement_requests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "assessment_id": f"assess_{search_id}",
        "search_id": search_id,
        "scores": {
            "temporal_fit": 4,
            "entity_fit": 4,
            "source_authority": 3,
            "source_independence": 3,
            "corroboration_strength": 3,
            "specificity": 4,
            "freshness": 4,
            "retrieval_diversity": 3,
            "contradiction_signal": 0,
            "actionability": 4,
        },
        "conclusion": conclusion,
        "rationale": "The returned sources are specific enough for evaluation.",
        "needs_refinement": needs_refinement,
        "refinement_requests": refinement_requests or [],
    }


class WebEvidenceRuntimeTests(unittest.TestCase):
    def test_web_configuration_status_reports_invalid_optional_env_without_secret(self) -> None:
        old_key = os.environ.get("TAVILY_API_KEY")
        old_max_results = os.environ.get("TAVILY_MAX_RESULTS")
        try:
            os.environ["TAVILY_API_KEY"] = "secret-key"
            os.environ["TAVILY_MAX_RESULTS"] = "not-a-number"
            status = runtime.get_web_search_configuration_status()
            self.assertFalse(status["enabled"])
            self.assertFalse(status["configured"])
            self.assertIn("TAVILY_MAX_RESULTS", status["invalid"])
            self.assertNotIn("secret-key", repr(status))
            self.assertIsNone(runtime.resolve_default_web_client(mode="auto"))
            with self.assertRaisesRegex(ValueError, "invalid: TAVILY_MAX_RESULTS"):
                runtime.resolve_default_web_client(mode="required")
        finally:
            if old_key is None:
                os.environ.pop("TAVILY_API_KEY", None)
            else:
                os.environ["TAVILY_API_KEY"] = old_key
            if old_max_results is None:
                os.environ.pop("TAVILY_MAX_RESULTS", None)
            else:
                os.environ["TAVILY_MAX_RESULTS"] = old_max_results

    def test_sql_only_contract_remains_valid(self) -> None:
        contract = {
            "contract_id": "round_1_audit",
            "round_number": 1,
            "operator_id": "audit_baseline",
            "target_hypotheses": ["H1"],
            "sql_budget": 1,
            "allowed_cost_classes": ["cheap"],
            "queries": [sql_query()],
            "pass_conditions": ["Metric exists."],
            "pivot_conditions": ["Metric missing."],
            "max_rounds": 20,
            "notes": [],
        }
        runtime.validate_investigation_contract(contract)

    def test_mixed_contract_requires_scoped_web_request(self) -> None:
        runtime.validate_investigation_contract(mixed_contract())
        bad = mixed_contract()
        bad["web_searches"] = [web_search(entity_scope=[])]
        with self.assertRaisesRegex(ValueError, "entity_scope"):
            runtime.validate_investigation_contract(bad)

    def test_web_recall_contradiction_is_valid_quality_signal(self) -> None:
        assessment = recall_assessment("w_external", conclusion="usable_contradicting")
        assessment["scores"]["contradiction_signal"] = 5
        assessment["contradiction_summary"] = "External sources contradict the SQL segment pattern."
        runtime.validate_web_recall_assessment(assessment)

    def test_execute_evidence_contract_runs_refinement_when_assessment_authorizes_it(self) -> None:
        def assessor(**kwargs: Any) -> dict[str, Any]:
            request = kwargs["request"]
            if request["search_id"] == "w_external":
                return recall_assessment(
                    "w_external",
                    conclusion="needs_refinement",
                    needs_refinement=True,
                    refinement_requests=[
                        web_search(
                            "w_external_refined",
                            parent_search_id="w_external",
                            recall_gap="Need official-source corroboration.",
                            refined_question="Is there official corroboration?",
                            changed_axes=["source_policy"],
                            expected_new_signal="Official source confirms or denies the event.",
                        )
                    ],
                )
            return recall_assessment(request["search_id"])

        bundle = runtime.execute_evidence_contract(
            DummyWarehouse(),
            mixed_contract(),
            web_client=DummyWeb(),
            produce_web_recall_assessment=assessor,
        )
        self.assertEqual([item["query_id"] for item in bundle["executed_queries"]], ["q_headline"])
        self.assertEqual(
            [item["search_id"] for item in bundle["executed_web_searches"]],
            ["w_external", "w_external_refined"],
        )
        self.assertEqual(len(bundle["web_recall_assessments"]), 2)

    def test_final_answer_accepts_web_lineage(self) -> None:
        slug = f"test_web_lineage_{int(time.time() * 1_000_000)}"
        session = runtime.start_session(slug, raw_question="why changed?", created_at=time.time())
        session_id = session["session_id"]
        try:
            runtime.persist_round_bundle(
                slug,
                "round_1",
                mixed_contract(),
                [],
                {
                    "round_id": "round_1",
                    "round_number": 1,
                    "contract_id": "round_1_audit",
                    "continuation_decision_ref": "decision_1",
                    "conclusion_state": "partial_answer_available",
                },
                executed_web_searches=[
                    {
                        "search_id": "w_external",
                        "status": "success",
                        "provider": "dummy_web",
                        "retrieved_at": time.time(),
                        "results": [{"title": "External event", "url": "https://example.com/event"}],
                        "fetched_pages": [],
                        "source_quality_notes": [],
                    }
                ],
                session_id=session_id,
                strict_session=True,
            )
            final_answer = {
                "session_slug": slug,
                "conclusion_state": "partial_answer_available",
                "headline_conclusion": "External event is plausible but internal impact remains open.",
                "supported_claims": [
                    {
                        "claim_ref": "claim_external",
                        "claim": "A relevant external event was found.",
                        "query_refs": [],
                        "web_refs": [{"round_id": "round_1", "search_id": "w_external"}],
                        "evidence_channels": ["web_search"],
                        "evaluation_refs": [],
                    }
                ],
                "contradictions": [],
                "residual_summary": {
                    "residual_score": 60,
                    "residual_band": "high",
                    "current_unexplained_ratio": 0.6,
                    "open_questions": [],
                },
                "correction_mode": False,
                "incompleteness_category": "",
                "recommended_follow_up": [],
            }
            runtime.validate_final_answer(
                final_answer,
                slug=slug,
                latest_evaluation={"conclusion_state": "partial_answer_available", "recommended_next_action": "stop"},
                session_id=session_id,
            )
        finally:
            shutil.rmtree(Path("RESEARCH") / slug, ignore_errors=True)

    def test_final_answer_requires_evidence_channels_for_sql_lineage(self) -> None:
        slug = f"test_sql_channel_lineage_{int(time.time() * 1_000_000)}"
        session = runtime.start_session(slug, raw_question="why changed?", created_at=time.time())
        session_id = session["session_id"]
        try:
            runtime.persist_round_bundle(
                slug,
                "round_1",
                mixed_contract(),
                [{"query_id": "q_headline", "status": "success"}],
                {
                    "round_id": "round_1",
                    "round_number": 1,
                    "contract_id": "round_1_audit",
                    "continuation_decision_ref": "decision_1",
                    "conclusion_state": "partial_answer_available",
                },
                session_id=session_id,
                strict_session=True,
            )
            final_answer = {
                "session_slug": slug,
                "conclusion_state": "partial_answer_available",
                "headline_conclusion": "Internal metric movement is supported by SQL evidence.",
                "supported_claims": [
                    {
                        "claim_ref": "claim_sql",
                        "claim": "The metric moved in the warehouse data.",
                        "query_refs": [{"round_id": "round_1", "query_id": "q_headline"}],
                        "evaluation_refs": [],
                    }
                ],
                "contradictions": [],
                "residual_summary": {
                    "residual_score": 20,
                    "residual_band": "low",
                    "current_unexplained_ratio": 0.2,
                    "open_questions": [],
                },
                "correction_mode": False,
                "incompleteness_category": "",
                "recommended_follow_up": [],
            }
            with self.assertRaisesRegex(ValueError, "evidence_channels"):
                runtime.validate_final_answer(
                    final_answer,
                    slug=slug,
                    latest_evaluation={"conclusion_state": "partial_answer_available", "recommended_next_action": "stop"},
                    session_id=session_id,
                )
            final_answer["supported_claims"][0]["evidence_channels"] = ["warehouse_sql"]
            runtime.validate_final_answer(
                final_answer,
                slug=slug,
                latest_evaluation={"conclusion_state": "partial_answer_available", "recommended_next_action": "stop"},
                session_id=session_id,
            )
        finally:
            shutil.rmtree(Path("RESEARCH") / slug, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
