#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)


EXAMPLE_RETAIL_MANIFEST = REPO_ROOT / "docs" / "evals" / "example_retail_eval_manifest.json"
DEFAULT_WEIGHTS = {
    "protocol_compliance": 20,
    "intent_and_scope_correctness": 12,
    "sql_and_evidence_quality": 18,
    "business_conclusion_accuracy": 22,
    "residual_and_uncertainty_discipline": 10,
    "lineage_and_artifact_integrity": 10,
    "report_and_visualization_usefulness": 8,
}
SENSITIVE_ENV_KEYS = (
    "VENDOR_WAREHOUSE_SECRET",
    "VENDOR_WAREHOUSE_CHANNEL",
    "EXAMPLE_RETAIL_ACCESS_TOKEN",
    "EXAMPLE_RETAIL_WAREHOUSE_TOKEN",
    "EXAMPLE_RETAIL_WAREHOUSE_SECRET",
)
LIVE_REQUIRED_ENV = (
    "VENDOR_WAREHOUSE_BASE_URL",
    "VENDOR_WAREHOUSE_PATH",
    "VENDOR_WAREHOUSE_CHANNEL",
    "VENDOR_WAREHOUSE_SECRET",
)
TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".txt",
    ".log",
    ".csv",
    ".tsv",
    ".yaml",
    ".yml",
}


class EvalHarnessError(RuntimeError):
    """Raised when the eval harness cannot run safely."""


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    difficulty: str
    max_rounds: int
    user_prompt: str
    current_date: str
    allowed_domain_pack: str
    success_standard: str = ""
    scoring_focus: tuple[str, ...] = ()
    gold: dict[str, Any] | None = None

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        default_current_date: str,
        default_domain_pack: str,
        gold: dict[str, Any] | None = None,
    ) -> "EvalCase":
        return cls(
            case_id=str(payload["case_id"]),
            category=str(payload.get("category") or "uncategorized"),
            difficulty=str(payload.get("difficulty") or "unknown"),
            max_rounds=int(payload.get("max_rounds") or 3),
            user_prompt=str(payload["user_prompt"]),
            current_date=str(payload.get("current_date") or default_current_date),
            allowed_domain_pack=str(payload.get("allowed_domain_pack") or default_domain_pack),
            success_standard=str(payload.get("success_standard") or ""),
            scoring_focus=tuple(str(item) for item in payload.get("scoring_focus", []) if isinstance(item, str)),
            gold=gold,
        )


class FakeEvalWarehouseClient:
    """Deterministic WarehouseClient used by the mock project eval suite."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        from runtime.interface import QueryResult

        self._query_result = QueryResult
        self._rows = rows or [
            {
                "period": "previous",
                "channel": "segment_a",
                "metric_value": 1200.0,
                "orders": 120,
                "metric_delta": -300.0,
            },
            {
                "period": "previous",
                "channel": "segment_b",
                "metric_value": 800.0,
                "orders": 80,
                "metric_delta": -50.0,
            },
            {
                "period": "current",
                "channel": "segment_a",
                "metric_value": 900.0,
                "orders": 90,
                "metric_delta": -300.0,
            },
            {
                "period": "current",
                "channel": "segment_b",
                "metric_value": 750.0,
                "orders": 76,
                "metric_delta": -50.0,
            },
        ]

    @property
    def identity(self) -> str:
        return "mock-eval://example_retail"

    def quote_identifier(self, name: str) -> str:
        return ".".join(f"`{part}`" for part in name.split("."))

    def execute(self, sql: str, *, timeout: float = 30.0, max_rows: int = 10_000) -> Any:
        rows = [dict(row) for row in self._rows[:max_rows]]
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        return self._query_result(rows=rows, columns=columns)


class ArtifactReplayAdapter:
    """Stable adapter that authors a complete, deterministic protocol run."""

    def __init__(self, case: EvalCase) -> None:
        self.case = case

    def produce_intent(self, *, raw_question: str, current_date: str, **_: Any) -> dict[str, Any]:
        return {
            "normalized_intent": {
                "intent_id": f"intent_{self.case.case_id}",
                "raw_question": raw_question,
                "question_style": "comparative",
                "problem_type": "metric_change_driver",
                "primary_problem_type": "Metric Change Driver",
                "business_object": {"label": "Example Retail", "entity_type": "business_scope"},
                "core_metric": "metric value",
                "time_scope": {
                    "primary": {
                        "label": "current mock period",
                        "start": "2026-04-01",
                        "end": current_date,
                        "grain": "day",
                    }
                },
                "comparison_scope": {
                    "type": "explicit",
                    "windows": [{"label": "previous mock period"}],
                },
                "dimensions": [{"label": "channel", "entity_type": "channel"}],
                "filters": [],
                "intent_profile": {"requires_audit_first": True},
                "problem_type_scores": [{"problem_type": "metric_change_driver", "score": 0.92}],
                "domain_pack_id": self.case.allowed_domain_pack,
                "mapping_confidence": "high",
                "clarification_needed": False,
                "clarification_reasons": [],
                "clarification_request": None,
            },
            "pack_gaps": [],
        }

    def produce_discovery(self, *, normalized_intent: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {
            "intent_id": normalized_intent["intent_id"],
            "environment_scan": {
                "warehouse_identity": "mock-eval://example_retail",
                "snapshot_label": "mock_project_eval_snapshot",
                "visible_objects": ["mock_sales_summary"],
            },
            "schema_map": {
                "tables": [
                    {
                        "name": "mock_sales_summary",
                        "available_columns": ["period", "channel", "metric_value", "orders", "metric_delta"],
                    }
                ]
            },
            "metric_mapping": {
                "metric": "metric value",
                "measure_candidates": ["metric_value"],
                "change_measure_candidates": ["metric_delta"],
            },
            "time_fields": [{"name": "period", "grain": "day"}],
            "dimension_fields": [{"name": "channel", "semantic_role": "channel"}],
            "supported_dimension_capabilities": [{"dimension": "channel", "status": "available"}],
            "joinability": {"join_paths": []},
            "comparison_feasibility": {
                "status": "supported",
                "reason": "Mock snapshot contains current and previous period rows.",
            },
            "warehouse_load_status": "normal",
            "report_conflict_hint": "",
            "quality_report": {"status": "pass", "issues": []},
            "evidence_status": "available",
        }

    def produce_plan(
        self,
        *,
        normalized_intent: dict[str, Any],
        discovery_bundle: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        query = {
            "query_id": "q_audit_headline",
            "description": "Audit metric value movement and channel contribution in the mock snapshot.",
            "sql": (
                "SELECT channel, period, metric_value, orders, metric_delta "
                "FROM mock_sales_summary ORDER BY metric_delta ASC LIMIT 20"
            ),
            "workspace": "mock",
            "output_name": "mock_channel_rows",
            "cache_policy": "bypass",
            "cost_class": "cheap",
        }
        contract = {
            "contract_id": f"{self.case.case_id}_round_1_contract",
            "round_number": 1,
            "operator_id": "audit_baseline",
            "target_hypotheses": ["H_audit_headline"],
            "sql_budget": 1,
            "allowed_cost_classes": ["cheap"],
            "queries": [query],
            "pass_conditions": ["Current period metric value can be compared to the previous mock period."],
            "pivot_conditions": ["The mock snapshot lacks comparable period rows."],
            "max_rounds": self.case.max_rounds,
            "notes": ["Round 1 validates the headline before any driver claim."],
        }
        return {
            "hypothesis_board": [
                {
                    "hypothesis_id": "H_audit_headline",
                    "family": "headline_audit",
                    "class": "audit",
                    "layer": "audit",
                    "statement": "metric value moved downward in the current mock period.",
                    "relevance_score": 0.95,
                    "evidence_basis": [],
                    "schema_feasibility": "feasible",
                    "status": "proposed",
                    "query_plan": [],
                    "notes": [],
                },
                {
                    "hypothesis_id": "H_driver_segment_a",
                    "family": "driver",
                    "class": "driver",
                    "layer": "structure",
                    "statement": "Segment A channel contributes the largest negative movement.",
                    "relevance_score": 0.8,
                    "evidence_basis": [],
                    "schema_feasibility": "feasible",
                    "status": "not_tested",
                    "query_plan": [],
                    "notes": [],
                },
            ],
            "round_1_contract": contract,
            "planning_notes": ["Keep the first executable round bounded to a headline audit."],
            "max_rounds": self.case.max_rounds,
        }

    def produce_evaluation(
        self,
        *,
        contract: dict[str, Any],
        executed_queries: list[dict[str, Any]],
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "round_id": f"round_{contract['round_number']}",
            "round_number": contract["round_number"],
            "contract_id": contract["contract_id"],
            "continuation_decision_ref": "pending_runtime_decision",
            "hypothesis_updates": [
                {
                    "hypothesis_id": "H_audit_headline",
                    "status": "supported",
                    "reason": "The mock evidence contains negative metric value movement rows.",
                    "query_refs": [{"round_id": "round_1", "query_id": "q_audit_headline"}],
                }
            ],
            "residual_update": {
                "summary": "The mock run has enough evidence for a completed answer.",
                "confidence_band": "high",
                "stalled_round_streak": 0,
                "negative_gain_streak": 0,
            },
            "residual_score": 0.08,
            "residual_band": "very_low",
            "open_questions": [],
            "continuation_guidance": {},
            "scores": {"warehouse_burden": "low", "evidence_gain": 0.9},
            "recommended_next_action": "stop",
            "should_continue": False,
            "stop_reason": "Mock headline and primary contributor evidence are sufficient.",
            "operator_gain": "The audit query confirms a downward metric value movement and a primary negative channel.",
            "gain_direction": "positive",
            "confidence_shift": "up",
            "correction_mode": False,
            "conclusion_state": "completed",
            "incompleteness_category": "",
        }

    def produce_final_answer(
        self,
        *,
        latest_round_evaluation: dict[str, Any],
        session_slug: str,
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "session_slug": session_slug,
            "conclusion_state": latest_round_evaluation["conclusion_state"],
            "headline_conclusion": "Mock evidence shows Example Retail metric value is down, led by the segment_a channel.",
            "supported_claims": [
                {
                    "claim_ref": "claim_mock_direction",
                    "claim": "metric value is down in the current mock period.",
                    "query_refs": [{"round_id": "round_1", "query_id": "q_audit_headline"}],
                    "evaluation_refs": ["round_1:evaluation"],
                },
                {
                    "claim_ref": "claim_mock_driver",
                    "claim": "The segment_a channel is the largest negative contributor in the mock evidence.",
                    "query_refs": [{"round_id": "round_1", "query_id": "q_audit_headline"}],
                    "evaluation_refs": ["round_1:evaluation"],
                },
            ],
            "contradictions": [],
            "residual_summary": {
                "residual_score": latest_round_evaluation["residual_score"],
                "residual_band": latest_round_evaluation["residual_band"],
                "current_unexplained_ratio": 0.08,
                "open_questions": latest_round_evaluation["open_questions"],
            },
            "correction_mode": False,
            "incompleteness_category": "",
            "recommended_follow_up": ["Review the mock channel rows before operational action."],
        }

    def produce_report_evidence(
        self,
        *,
        session_slug: str,
        session_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "session_slug": session_slug,
            "session_id": session_id,
            "entries": [
                {
                    "evidence_ref": "ev_mock_channel_change",
                    "section": "supported_claims",
                    "text": "Current mock metric value is down; segment_a channel has the largest negative contribution.",
                    "query_refs": [{"round_id": "round_1", "query_id": "q_audit_headline"}],
                    "evaluation_refs": ["round_1:evaluation"],
                    "importance": 1,
                    "chartability_note": "Channel contribution can be charted from runtime query rows.",
                }
            ],
            "generated_at": time.time(),
        }

    def produce_chart_specs(
        self,
        *,
        session_slug: str,
        session_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "session_slug": session_slug,
            "session_id": session_id,
            "specs": [
                {
                    "spec_id": "mock_channel_metric_delta",
                    "title": "Mock metric value Change by Channel",
                    "caption": "metric value movement by channel from runtime materialized rows.",
                    "semantic_chart_type": "bar",
                    "narrative_role": "supporting_evidence",
                    "report_section": "visualizations",
                    "evidence_refs": ["ev_mock_channel_change"],
                    "query_refs": [{"round_id": "round_1", "query_id": "q_audit_headline"}],
                    "source_query_ref": {"round_id": "round_1", "query_id": "q_audit_headline"},
                    "plot_data": {"items": []},
                    "plot_spec": {
                        "chart_type": "bar",
                        "x_field": "channel",
                        "y_field": "metric_delta",
                    },
                    "why_this_chart": "It shows which channel contributes most to the movement.",
                }
            ],
            "generated_at": time.time(),
        }


class CallbackAdapter:
    """Thin adapter around host-provided producer callbacks."""

    REQUIRED_METHODS = (
        "produce_intent",
        "produce_discovery",
        "produce_plan",
        "produce_evaluation",
        "produce_final_answer",
        "produce_report_evidence",
        "produce_chart_specs",
    )

    def __init__(self, producer: Any) -> None:
        self.producer = producer
        missing = [name for name in self.REQUIRED_METHODS if not self._resolve(name)]
        if missing:
            raise EvalHarnessError("Callback adapter missing required producers: " + ", ".join(missing))

    def _resolve(self, name: str) -> Callable[..., dict[str, Any]] | None:
        if isinstance(self.producer, dict):
            value = self.producer.get(name)
        else:
            value = getattr(self.producer, name, None)
        return value if callable(value) else None

    def __getattr__(self, name: str) -> Callable[..., dict[str, Any]]:
        resolved = self._resolve(name)
        if resolved is None:
            raise AttributeError(name)
        return resolved


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "case"


def _load_factory_from_spec(spec: str) -> Any:
    module_ref, separator, attr = spec.partition(":")
    if not separator or not module_ref or not attr:
        raise EvalHarnessError("Callback factory specs must use 'module.path:factory'.")
    if module_ref.endswith(".py") or "/" in module_ref or "\\" in module_ref:
        raise EvalHarnessError("Callback factories must be importable module paths, not filesystem paths.")
    module = importlib.import_module(module_ref)
    target: Any = module
    for part in attr.split("."):
        target = getattr(target, part)
    return target() if callable(target) else target


def _load_secret_values() -> list[str]:
    values: list[str] = []
    for key in SENSITIVE_ENV_KEYS:
        value = os.getenv(key)
        if value and len(value) >= 4 and value not in values:
            values.append(value)
    return values


def _load_mock_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="mock_full_flow_001",
            category="flow_completion",
            difficulty="easy",
            max_rounds=2,
            user_prompt="为什么 Example Retail 最近 metric value 下降？主要是谁驱动的？",
            current_date="2026-04-27",
            allowed_domain_pack="generic",
            success_standard="Complete the full protocol with evidence-backed claims and a chart.",
            scoring_focus=("protocol_compliance", "flow_completion", "chart_fidelity"),
            gold={
                "headline": {
                    "direction": "down",
                    "required_terms": ["metric_value", "down"],
                },
                "accepted_primary_drivers": [
                    {"dimension": "channel", "label": "segment_a", "direction": "negative"}
                ],
            },
        )
    ]


def _load_example_retail_cases(manifest_path: Path, gold_root: Path | None) -> list[EvalCase]:
    manifest = _read_json(manifest_path)
    default_current_date = str(manifest.get("current_date") or "2026-04-27")
    default_domain_pack = str(manifest.get("default_domain_pack") or "generic")
    cases = []
    for payload in manifest.get("cases", []):
        if not isinstance(payload, dict):
            continue
        gold = None
        if gold_root is not None:
            gold_path = gold_root / f"{payload.get('case_id')}.json"
            if gold_path.exists():
                loaded_gold = _read_json(gold_path)
                if isinstance(loaded_gold, dict):
                    gold = loaded_gold
        cases.append(
            EvalCase.from_payload(
                payload,
                default_current_date=default_current_date,
                default_domain_pack=default_domain_pack,
                gold=gold,
            )
        )
    return cases


def load_cases(args: argparse.Namespace) -> list[EvalCase]:
    gold_root = Path(args.gold_root).expanduser().resolve() if args.gold_root else None
    if args.suite == "mock":
        cases = _load_mock_cases()
    elif args.suite == "example_retail":
        manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else EXAMPLE_RETAIL_MANIFEST
        cases = _load_example_retail_cases(manifest_path, gold_root)
    else:
        raise EvalHarnessError(f"Unsupported case suite: {args.suite}")
    if args.case:
        selected = set(args.case)
        cases = [case for case in cases if case.case_id in selected]
        missing = selected - {case.case_id for case in cases}
        if missing:
            raise EvalHarnessError("Unknown case id(s): " + ", ".join(sorted(missing)))
    if not cases:
        raise EvalHarnessError("No eval cases selected.")
    return cases


def build_adapter(args: argparse.Namespace, case: EvalCase) -> Any:
    if args.agent == "artifact_replay":
        if args.suite != "mock":
            raise EvalHarnessError("artifact_replay is only supported for the mock suite.")
        return ArtifactReplayAdapter(case)
    factory_spec = args.callback_factory or os.getenv("DEEP_RESEARCH_EVAL_CALLBACK_FACTORY")
    if not factory_spec:
        raise EvalHarnessError(
            "callback agent requires --callback-factory or DEEP_RESEARCH_EVAL_CALLBACK_FACTORY."
        )
    return CallbackAdapter(_load_factory_from_spec(factory_spec))


def build_client(args: argparse.Namespace) -> Any:
    if args.suite == "mock":
        return FakeEvalWarehouseClient()
    if not args.live_example_retail:
        raise EvalHarnessError("Example Retail evals require explicit --live-example-retail.")
    missing = [key for key in LIVE_REQUIRED_ENV if not os.getenv(key)]
    if missing:
        raise EvalHarnessError("Missing live Example Retail warehouse env vars: " + ", ".join(missing))
    from runtime.example_clients.vendor_http_client import create_client

    return create_client()


def run_doctor() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "deep_research_runtime.py"), "doctor"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    payload: dict[str, Any]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"status": "error", "stdout": result.stdout, "stderr": result.stderr}
    payload["returncode"] = result.returncode
    return payload


def run_protocol_regressions() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def run_session(case: EvalCase, adapter: Any, client: Any, args: argparse.Namespace, *, run_index: int) -> dict[str, Any]:
    import runtime

    slug = _safe_slug(f"eval_{case.case_id}_r{run_index}_{int(time.time() * 1000)}")
    return runtime.run_research_session(
        client,
        slug,
        raw_question=case.user_prompt,
        current_date=case.current_date,
        available_domain_packs=[],
        forced_domain_pack_id=case.allowed_domain_pack,
        produce_intent=adapter.produce_intent,
        produce_discovery=adapter.produce_discovery,
        produce_plan=adapter.produce_plan,
        produce_evaluation=adapter.produce_evaluation,
        produce_final_answer=adapter.produce_final_answer,
        produce_report_evidence=adapter.produce_report_evidence,
        produce_chart_specs=adapter.produce_chart_specs,
        produce_next_contract=getattr(adapter, "produce_next_contract", None),
        produce_domain_pack_suggestions=getattr(adapter, "produce_domain_pack_suggestions", None),
        timeout=args.timeout,
        max_rows=args.max_rows,
        max_cache_age_seconds=args.max_cache_age_seconds,
    )


def artifact_completeness(session_root: Path, *, terminal_status: str) -> dict[str, Any]:
    required = [
        "manifest.json",
        "session_state.json",
        "intent.json",
        "intent_sidecar.json",
        "environment_scan.json",
        "plan.json",
    ]
    if terminal_status == "completed":
        required.extend(
            [
                "final_answer.json",
                "report_evidence.json",
                "report_evidence_index.json",
                "chart_spec_bundle.json",
                "descriptive_stats.json",
                "visualization_manifest.json",
                "report.md",
                "compliance_report.json",
                "evidence_graph.json",
            ]
        )
    elif terminal_status == "restart_required":
        required.append("protocol_trace.json")
    round_paths = sorted((session_root / "rounds").glob("*/*.json"))
    missing = [name for name in required if not (session_root / name).exists()]
    if not round_paths:
        missing.append("rounds/<generation>/round_*.json")
    return {
        "session_root": str(session_root),
        "terminal_status": terminal_status,
        "required_artifacts": required,
        "missing_artifacts": missing,
        "round_bundle_count": len(round_paths),
        "completion_rate": (
            (len(required) + 1 - len(missing)) / (len(required) + 1)
            if required
            else 1.0
        ),
        "hard_failures": [] if not missing else ["missing_required_artifacts"],
    }


def _iter_text_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            paths.append(path)
    return paths


def _load_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _collect_round_bundles(session_root: Path) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for path in sorted((session_root / "rounds").glob("*/*.json")):
        payload = _load_json_file(path)
        if isinstance(payload, dict):
            payload["_path"] = str(path)
            bundles.append(payload)
    return bundles


def security_scan(
    session_root: Path,
    *,
    secret_values: list[str],
    live_mode: bool,
) -> dict[str, Any]:
    from runtime.tools import _validate_sql

    findings: list[dict[str, Any]] = []
    for path in _iter_text_files(session_root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for secret in secret_values:
            if secret and secret in text:
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "secret_leak",
                        "path": str(path),
                        "message": "Sensitive environment value was found in an eval artifact.",
                    }
                )
        for marker in ("X-Warehouse-Signature", "Authorization", "access_token"):
            if marker in text:
                findings.append(
                    {
                        "severity": "high",
                        "kind": "transport_secret_marker",
                        "path": str(path),
                        "message": f"Artifact contains transport credential marker {marker!r}.",
                    }
                )

    for bundle in _collect_round_bundles(session_root):
        contract = bundle.get("contract") if isinstance(bundle.get("contract"), dict) else {}
        contract_queries = {
            query.get("query_id"): query
            for query in contract.get("queries", [])
            if isinstance(query, dict)
        }
        for query in contract.get("queries", []):
            if not isinstance(query, dict):
                continue
            sql = query.get("sql")
            if isinstance(sql, str):
                error = _validate_sql(sql)
                if error:
                    findings.append(
                        {
                            "severity": "critical",
                            "kind": "unsafe_sql",
                            "query_id": query.get("query_id"),
                            "message": error,
                        }
                    )
        for executed in bundle.get("executed_queries", []):
            if not isinstance(executed, dict):
                continue
            query_id = executed.get("query_id")
            request = contract_queries.get(query_id)
            result_rows_present = isinstance(executed.get("result_rows"), list)
            if result_rows_present and executed.get("result_rows_persisted") is not True:
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "raw_rows_flag_mismatch",
                        "query_id": query_id,
                        "message": "result_rows are present while result_rows_persisted is not true.",
                    }
                )
            if executed.get("retention_mode_applied") == "deny":
                if result_rows_present or executed.get("rows_preview"):
                    findings.append(
                        {
                            "severity": "critical",
                            "kind": "deny_policy_leak",
                            "query_id": query_id,
                            "message": "Denied rows are visible in result rows or preview.",
                        }
                    )
            request_persisted = isinstance(request, dict) and request.get("persist_result_rows") is True
            retention_mode = executed.get("retention_mode_applied")
            if (
                live_mode
                and result_rows_present
                and not request_persisted
                and retention_mode not in {"temporary_full_rows", "redacted_rows"}
            ):
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "unauthorized_full_row_persistence",
                        "query_id": query_id,
                        "message": "Live eval persisted full rows without explicit query retention authorization.",
                    }
                )

    state = _load_json_file(session_root / "session_state.json")
    final_answer_exists = (session_root / "final_answer.json").exists()
    if isinstance(state, dict) and state.get("restart_count", 0) and final_answer_exists:
        findings.append(
            {
                "severity": "critical",
                "kind": "finalized_after_restart",
                "message": "Session has restart history but also finalized an active answer.",
            }
        )

    critical = [item for item in findings if item["severity"] == "critical"]
    return {
        "session_root": str(session_root),
        "live_mode": live_mode,
        "findings": findings,
        "hard_failures": [item["kind"] for item in critical],
        "status": "fail" if critical else ("warn" if findings else "pass"),
    }


def warehouse_query_summary(session_root: Path) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_cost_class: dict[str, int] = {}
    total_rows_observed = 0
    total_queries = 0
    for bundle in _collect_round_bundles(session_root):
        for query in bundle.get("executed_queries", []):
            if not isinstance(query, dict):
                continue
            total_queries += 1
            status = str(query.get("status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            cost_class = str(query.get("cost_class") or "unknown")
            by_cost_class[cost_class] = by_cost_class.get(cost_class, 0) + 1
            row_count = query.get("row_count")
            if isinstance(row_count, int):
                total_rows_observed += row_count
    return {
        "total_queries": total_queries,
        "by_status": dict(sorted(by_status.items())),
        "by_cost_class": dict(sorted(by_cost_class.items())),
        "total_rows_observed": total_rows_observed,
    }


def _read_artifact(session_root: Path, name: str) -> Any | None:
    return _load_json_file(session_root / name)


def _all_text_for_quality(session_root: Path) -> str:
    parts: list[str] = []
    for name in ("final_answer.json", "report_evidence.json", "report.md"):
        path = session_root / name
        if path.exists():
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(parts).lower()


def _score_gold(case: EvalCase, session_root: Path) -> tuple[int | None, list[str], list[str]]:
    gold = case.gold
    if not isinstance(gold, dict):
        return None, ["Gold bundle is unavailable; formal business scoring skipped."], []
    text = _all_text_for_quality(session_root)
    notes: list[str] = []
    failures: list[str] = []
    score = 0
    headline = gold.get("headline") if isinstance(gold.get("headline"), dict) else {}
    direction = str(headline.get("direction") or "").lower()
    if direction:
        direction_terms = {
            "down": ("down", "decline", "下降", "下滑", "negative"),
            "up": ("up", "increase", "上升", "增长", "positive"),
            "flat": ("flat", "stable", "持平", "稳定"),
        }.get(direction, (direction,))
        if any(term in text for term in direction_terms):
            score += 8
        else:
            failures.append("gold_headline_direction_mismatch")
    for term in headline.get("required_terms", []):
        if isinstance(term, str) and term.lower() in text:
            score += 2
        elif isinstance(term, str):
            notes.append(f"Required headline term not found: {term}")
    drivers = gold.get("accepted_primary_drivers")
    if isinstance(drivers, list) and drivers:
        matched = False
        for driver in drivers:
            if not isinstance(driver, dict):
                continue
            label = str(driver.get("label") or "").lower()
            if label and label in text:
                matched = True
                break
        if matched:
            score += 10
        else:
            failures.append("gold_primary_driver_not_found")
    else:
        score += 10
    return min(score, DEFAULT_WEIGHTS["business_conclusion_accuracy"]), notes, failures


def score_case(
    case: EvalCase,
    session_root: Path,
    *,
    completeness: dict[str, Any],
    security: dict[str, Any],
    terminal_status: str,
) -> dict[str, Any]:
    compliance = _read_artifact(session_root, "compliance_report.json")
    visualization = _read_artifact(session_root, "visualization_manifest.json")
    final_answer = _read_artifact(session_root, "final_answer.json")
    round_bundles = _collect_round_bundles(session_root)

    hard_failures = list(completeness.get("hard_failures", [])) + list(security.get("hard_failures", []))
    dimension_scores: dict[str, dict[str, Any]] = {}

    compliance_verdict = compliance.get("final_verdict") if isinstance(compliance, dict) else None
    if compliance_verdict == "pass":
        protocol_score = DEFAULT_WEIGHTS["protocol_compliance"]
    elif compliance_verdict == "warn":
        protocol_score = 12
    else:
        protocol_score = 0
        hard_failures.append("protocol_compliance_failed")
    dimension_scores["protocol_compliance"] = {
        "score": protocol_score,
        "weight": DEFAULT_WEIGHTS["protocol_compliance"],
        "verdict": compliance_verdict or "missing",
    }

    intent_scope_score = DEFAULT_WEIGHTS["intent_and_scope_correctness"]
    if terminal_status not in {"completed", "restart_required"}:
        intent_scope_score = 0
        hard_failures.append("illegal_terminal_status")
    dimension_scores["intent_and_scope_correctness"] = {
        "score": intent_scope_score,
        "weight": DEFAULT_WEIGHTS["intent_and_scope_correctness"],
        "verdict": "pass" if intent_scope_score else "fail",
    }

    sql_score = DEFAULT_WEIGHTS["sql_and_evidence_quality"]
    if any(item in security.get("hard_failures", []) for item in ("unsafe_sql", "unauthorized_full_row_persistence")):
        sql_score = 0
    elif any(
        not isinstance(query, dict) or query.get("status") not in {"success", "cached"}
        for bundle in round_bundles
        for query in bundle.get("executed_queries", [])
    ):
        sql_score = 10
    dimension_scores["sql_and_evidence_quality"] = {
        "score": sql_score,
        "weight": DEFAULT_WEIGHTS["sql_and_evidence_quality"],
        "verdict": "pass" if sql_score >= 10 else "fail",
    }

    business_score, gold_notes, gold_failures = _score_gold(case, session_root)
    if business_score is None:
        dimension_scores["business_conclusion_accuracy"] = {
            "score": None,
            "weight": DEFAULT_WEIGHTS["business_conclusion_accuracy"],
            "verdict": "pending_gold",
            "notes": gold_notes,
        }
    else:
        dimension_scores["business_conclusion_accuracy"] = {
            "score": business_score,
            "weight": DEFAULT_WEIGHTS["business_conclusion_accuracy"],
            "verdict": "pass" if not gold_failures else "fail",
            "notes": gold_notes,
        }
        hard_failures.extend(gold_failures)

    residual_score = DEFAULT_WEIGHTS["residual_and_uncertainty_discipline"]
    if isinstance(final_answer, dict):
        residual_summary = final_answer.get("residual_summary")
        if not isinstance(residual_summary, dict):
            residual_score = 0
    elif terminal_status == "completed":
        residual_score = 0
    dimension_scores["residual_and_uncertainty_discipline"] = {
        "score": residual_score,
        "weight": DEFAULT_WEIGHTS["residual_and_uncertainty_discipline"],
        "verdict": "pass" if residual_score else "fail",
    }

    lineage_score = DEFAULT_WEIGHTS["lineage_and_artifact_integrity"]
    if completeness.get("hard_failures") or (isinstance(compliance, dict) and compliance.get("claims_without_lineage")):
        lineage_score = 0
        hard_failures.append("lineage_or_artifact_integrity_failed")
    dimension_scores["lineage_and_artifact_integrity"] = {
        "score": lineage_score,
        "weight": DEFAULT_WEIGHTS["lineage_and_artifact_integrity"],
        "verdict": "pass" if lineage_score else "fail",
    }

    report_score = DEFAULT_WEIGHTS["report_and_visualization_usefulness"]
    charts = visualization.get("charts") if isinstance(visualization, dict) else []
    if terminal_status == "completed" and not (session_root / "report.md").exists():
        report_score = 0
    elif terminal_status == "completed" and isinstance(charts, list) and not charts:
        report_score = 5
    dimension_scores["report_and_visualization_usefulness"] = {
        "score": report_score,
        "weight": DEFAULT_WEIGHTS["report_and_visualization_usefulness"],
        "verdict": "pass" if report_score else "fail",
    }

    scored_total = 0
    scored_weight = 0
    pending_dimensions: list[str] = []
    for key, item in dimension_scores.items():
        score = item.get("score")
        weight = item.get("weight")
        if isinstance(score, (int, float)) and isinstance(weight, (int, float)):
            scored_total += int(score)
            scored_weight += int(weight)
        else:
            pending_dimensions.append(key)

    total_score = scored_total if not pending_dimensions else None
    automated_score_without_gold = round(scored_total * 100 / scored_weight, 2) if scored_weight else None
    hard_failures = sorted(set(hard_failures))
    return {
        "case_id": case.case_id,
        "terminal_status": terminal_status,
        "dimension_scores": dimension_scores,
        "total_score": total_score,
        "automated_score_without_gold": automated_score_without_gold,
        "pending_dimensions": pending_dimensions,
        "hard_failures": hard_failures,
        "verdict": "fail" if hard_failures else "pass",
        "gold_available": isinstance(case.gold, dict),
    }


def write_quality_review(path: Path, *, case: EvalCase, score: dict[str, Any]) -> None:
    lines = [
        f"# Quality Review: {case.case_id}",
        "",
        f"- Verdict: {score['verdict']}",
        f"- Total score: {score.get('total_score')}",
        f"- Automated score without pending gold: {score.get('automated_score_without_gold')}",
        f"- Hard failures: {', '.join(score.get('hard_failures') or []) or 'none'}",
        "",
        "## Dimension Scores",
    ]
    for name, item in score.get("dimension_scores", {}).items():
        lines.append(f"- {name}: {item.get('score')} / {item.get('weight')} ({item.get('verdict')})")
        for note in item.get("notes", []) if isinstance(item.get("notes"), list) else []:
            lines.append(f"  - {note}")
    lines.extend(
        [
            "",
            "## Human Review Prompts",
            "- Did the interpretation match the business language in the prompt?",
            "- Did the answer separate observed movement from causal certainty?",
            "- Are caveats concrete enough for an operator to act on?",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_case(case: EvalCase, args: argparse.Namespace, *, run_index: int, eval_root: Path) -> dict[str, Any]:
    adapter = build_adapter(args, case)
    client = build_client(args)
    case_output = eval_root / case.case_id / f"run_{run_index}"
    case_output.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    try:
        result = run_session(case, adapter, client, args, run_index=run_index)
        terminal_status = str(result.get("status") or "completed")
        session_root = Path(str(result["session_root"]))
        error = None
    except Exception as exc:
        terminal_status = "error"
        session_root = case_output
        error = {"type": type(exc).__name__, "message": str(exc)}
        result = {"status": "error", "error": error}

    completeness = artifact_completeness(session_root, terminal_status=terminal_status)
    security = security_scan(
        session_root,
        secret_values=_load_secret_values(),
        live_mode=bool(args.live_example_retail),
    )
    score = score_case(
        case,
        session_root,
        completeness=completeness,
        security=security,
        terminal_status=terminal_status,
    )
    if error:
        score["hard_failures"] = sorted(set(score.get("hard_failures", []) + ["case_execution_error"]))
        score["verdict"] = "fail"
        score["error"] = error

    case_result = {
        "case": {
            "case_id": case.case_id,
            "category": case.category,
            "difficulty": case.difficulty,
            "max_rounds": case.max_rounds,
            "current_date": case.current_date,
            "allowed_domain_pack": case.allowed_domain_pack,
            "scoring_focus": list(case.scoring_focus),
        },
        "run_index": run_index,
        "started_at": started_at,
        "duration_seconds": round(time.time() - started_at, 3),
        "session_root": str(session_root),
        "terminal_status": terminal_status,
        "result_summary": {
            "status": result.get("status", "completed") if isinstance(result, dict) else "completed",
            "report_path": result.get("report_path") if isinstance(result, dict) else None,
        },
        "artifact_completeness": completeness,
        "security_scan": security,
        "warehouse_query_summary": warehouse_query_summary(session_root),
        "case_score": score,
    }
    _write_json(case_output / "artifact_completeness.json", completeness)
    _write_json(case_output / "security_scan.json", security)
    _write_json(case_output / "case_score.json", score)
    _write_json(case_output / "case_result.json", case_result)
    write_quality_review(case_output / "quality_review.md", case=case, score=score)
    return case_result


def summarize_results(results: list[dict[str, Any]], *, stability_report: bool) -> dict[str, Any]:
    scored_values = [
        result["case_score"]["total_score"]
        for result in results
        if isinstance(result.get("case_score"), dict)
        and isinstance(result["case_score"].get("total_score"), (int, float))
    ]
    automated_values = [
        result["case_score"]["automated_score_without_gold"]
        for result in results
        if isinstance(result.get("case_score"), dict)
        and isinstance(result["case_score"].get("automated_score_without_gold"), (int, float))
    ]
    hard_failures = [
        failure
        for result in results
        for failure in result.get("case_score", {}).get("hard_failures", [])
    ]
    terminal_statuses = [result.get("terminal_status") for result in results]
    query_summary = {
        "total_queries": sum(result.get("warehouse_query_summary", {}).get("total_queries", 0) for result in results),
        "total_rows_observed": sum(
            result.get("warehouse_query_summary", {}).get("total_rows_observed", 0) for result in results
        ),
        "by_status": {},
        "by_cost_class": {},
    }
    for result in results:
        for key in ("by_status", "by_cost_class"):
            aggregate = query_summary[key]
            for label, count in result.get("warehouse_query_summary", {}).get(key, {}).items():
                aggregate[label] = aggregate.get(label, 0) + count
    summary: dict[str, Any] = {
        "run_count": len(results),
        "completed_count": sum(1 for status in terminal_statuses if status == "completed"),
        "restart_required_count": sum(1 for status in terminal_statuses if status == "restart_required"),
        "error_count": sum(1 for status in terminal_statuses if status == "error"),
        "hard_failure_count": len(hard_failures),
        "hard_failures": sorted(set(hard_failures)),
        "average_score": round(sum(scored_values) / len(scored_values), 2) if scored_values else None,
        "average_automated_score_without_gold": (
            round(sum(automated_values) / len(automated_values), 2) if automated_values else None
        ),
        "formal_scoring_available": bool(scored_values),
        "warehouse_query_summary": query_summary,
        "case_results": [
            {
                "case_id": result["case"]["case_id"],
                "run_index": result["run_index"],
                "terminal_status": result["terminal_status"],
                "score": result["case_score"].get("total_score"),
                "automated_score_without_gold": result["case_score"].get("automated_score_without_gold"),
                "verdict": result["case_score"].get("verdict"),
                "session_root": result["session_root"],
            }
            for result in results
        ],
    }
    if stability_report:
        by_case: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            by_case.setdefault(result["case"]["case_id"], []).append(result)
        stability: dict[str, Any] = {}
        for case_id, case_results in by_case.items():
            values = [
                item["case_score"].get("total_score")
                if item["case_score"].get("total_score") is not None
                else item["case_score"].get("automated_score_without_gold")
                for item in case_results
            ]
            numeric = [float(value) for value in values if isinstance(value, (int, float))]
            mean = sum(numeric) / len(numeric) if numeric else None
            variance = sum((value - mean) ** 2 for value in numeric) / len(numeric) if numeric and mean is not None else None
            stddev = math.sqrt(variance) if variance is not None else None
            stability[case_id] = {
                "run_count": len(case_results),
                "all_completed": all(item["terminal_status"] == "completed" for item in case_results),
                "hard_failure_count": sum(len(item["case_score"].get("hard_failures", [])) for item in case_results),
                "score_stddev": round(stddev, 3) if stddev is not None else None,
                "passes_default_threshold": (
                    len(case_results) > 0
                    and all(item["terminal_status"] == "completed" for item in case_results)
                    and sum(len(item["case_score"].get("hard_failures", [])) for item in case_results) == 0
                    and (stddev is None or stddev <= 8.0)
                ),
            }
        summary["stability"] = stability
    summary["verdict"] = "fail" if summary["hard_failure_count"] or summary["error_count"] else "pass"
    return summary


def configure_runtime_root(args: argparse.Namespace) -> None:
    if not args.research_root:
        return
    import runtime.persistence as persistence

    persistence.RESEARCH_ROOT = Path(args.research_root).expanduser().resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project-level eval harness for Deep Research.")
    parser.add_argument("--suite", choices=("mock", "example_retail", "protocol"), default="mock")
    parser.add_argument("--agent", choices=("artifact_replay", "callback"), default="artifact_replay")
    parser.add_argument("--callback-factory", help="Import spec for callback adapter factory, e.g. package.module:create_adapter.")
    parser.add_argument("--case", action="append", help="Case id to run. May be repeated.")
    parser.add_argument("--manifest", help="Override eval manifest path.")
    parser.add_argument("--gold-root", default=os.getenv("EXAMPLE_RETAIL_GOLD_ROOT"), help="Private gold bundle directory.")
    parser.add_argument("--output-root", help="Directory for eval summary outputs.")
    parser.add_argument("--research-root", help="Override runtime RESEARCH_ROOT for this process.")
    parser.add_argument(
        "--live-example-retail",
        dest="live_example_retail",
        action="store_true",
        help="Enable live Example Retail warehouse execution.",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--stability-report", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-rows", type=int, default=10_000)
    parser.add_argument("--max-cache-age-seconds", type=float)
    parser.add_argument("--skip-doctor", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.repeat <= 0:
        raise SystemExit("--repeat must be positive.")
    configure_runtime_root(args)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else REPO_ROOT / "RESEARCH" / "eval_runs" / f"{args.suite}_{timestamp}"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    if args.suite == "protocol":
        result = run_protocol_regressions()
        _write_json(output_root / "protocol_regression.json", result)
        _write_json(
            output_root / "eval_summary.json",
            {"suite": "protocol", "verdict": "pass" if result["returncode"] == 0 else "fail", "protocol": result},
        )
        print(json.dumps({"status": result["status"], "output_root": str(output_root)}, ensure_ascii=False))
        return 0 if result["returncode"] == 0 else 1

    doctor = None if args.skip_doctor else run_doctor()
    if doctor is not None and doctor.get("returncode") != 0:
        _write_json(output_root / "doctor.json", doctor)
        print(json.dumps({"status": "error", "error": "doctor_failed", "output_root": str(output_root)}, ensure_ascii=False))
        return 1
    if doctor is not None:
        _write_json(output_root / "doctor.json", doctor)

    try:
        cases = load_cases(args)
        results: list[dict[str, Any]] = []
        for case in cases:
            for run_index in range(1, args.repeat + 1):
                results.append(run_case(case, args, run_index=run_index, eval_root=output_root))
        summary = summarize_results(results, stability_report=args.stability_report or args.repeat > 1)
        summary.update(
            {
                "suite": args.suite,
                "agent": args.agent,
                "live_example_retail": bool(args.live_example_retail),
                "output_root": str(output_root),
                "generated_at": time.time(),
            }
        )
        _write_json(output_root / "eval_summary.json", summary)
        print(json.dumps({"status": summary["verdict"], "output_root": str(output_root)}, ensure_ascii=False))
        return 0 if summary["verdict"] == "pass" else 1
    except EvalHarnessError as exc:
        payload = {"suite": args.suite, "verdict": "fail", "error": str(exc), "output_root": str(output_root)}
        _write_json(output_root / "eval_summary.json", payload)
        print(json.dumps({"status": "error", "error": str(exc), "output_root": str(output_root)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
