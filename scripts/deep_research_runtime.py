#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
INVOCATION_CWD = Path.cwd()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)


def _resolve_user_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    invocation_relative = (INVOCATION_CWD / candidate).resolve()
    if invocation_relative.exists():
        return invocation_relative
    return (REPO_ROOT / candidate).resolve()


def _read_json(path: str | Path) -> Any:
    with _resolve_user_path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _merge_policy(base: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_policy(merged[key], value)
        else:
            merged[key] = value
    return merged


def _policy_from_args(args: argparse.Namespace) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    runtime_policy = getattr(args, "runtime_policy", None)
    if runtime_policy:
        loaded = _read_json(runtime_policy)
        if not isinstance(loaded, dict):
            raise ValueError("--runtime-policy must point to a JSON object.")
        policy = _merge_policy(policy, loaded)
    report_policy = getattr(args, "report_policy", None)
    if report_policy:
        loaded = _read_json(report_policy)
        if not isinstance(loaded, dict):
            raise ValueError("--report-policy must point to a JSON object.")
        policy = _merge_policy(policy, {"report_policy": loaded})
    semantic_guard_policy = getattr(args, "semantic_guard_policy", None)
    if semantic_guard_policy:
        loaded = _read_json(semantic_guard_policy)
        if not isinstance(loaded, dict):
            raise ValueError("--semantic-guard-policy must point to a JSON object.")
        policy = _merge_policy(policy, {"semantic_guard_policy": loaded})
    return policy


def _configure_runtime_policy(runtime: Any, args: argparse.Namespace) -> None:
    slug = getattr(args, "slug", None)
    session_id = getattr(args, "session_id", None)
    if not slug or not session_id:
        return
    manifest = runtime.read_artifact(slug, "manifest.json", session_id=session_id, strict_session=True)
    if not isinstance(manifest, dict):
        return
    runtime_policy = manifest.get("runtime_policy")
    if not isinstance(runtime_policy, dict):
        return
    semantic_guard_policy = runtime_policy.get("semantic_guard_policy")
    if isinstance(semantic_guard_policy, dict):
        runtime.configure_semantic_guard_policy(semantic_guard_policy)


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _load_runtime() -> Any:
    import runtime

    return runtime


DEFAULT_CLIENT_FACTORY_REGISTRY = {
    "vendor_http": "runtime.example_clients.vendor_http_client:create_client",
    "http": "runtime.example_clients.http_sql_client:HttpSqlClient",
    "sqlalchemy": "runtime.example_clients.http_sql_client:SqlAlchemyClient",
}

DEFAULT_WEB_CLIENT_FACTORY_REGISTRY = {
    "tavily": "runtime.web_search:TavilySearchClient",
}


def _load_client_factory_registry() -> dict[str, str]:
    registry = dict(DEFAULT_CLIENT_FACTORY_REGISTRY)
    raw_registry = os.getenv("DEEP_RESEARCH_CLIENT_FACTORIES")
    if raw_registry:
        try:
            configured = json.loads(raw_registry)
        except json.JSONDecodeError as exc:
            raise ValueError("DEEP_RESEARCH_CLIENT_FACTORIES must be a JSON object.") from exc
        if not isinstance(configured, dict):
            raise ValueError("DEEP_RESEARCH_CLIENT_FACTORIES must be a JSON object.")
        for alias, spec in configured.items():
            if not isinstance(alias, str) or not alias.strip():
                raise ValueError("Client factory aliases must be non-empty strings.")
            if not isinstance(spec, str) or not spec.strip():
                raise ValueError(f"Client factory spec for alias {alias!r} must be a non-empty string.")
            registry[alias.strip()] = spec.strip()
    return registry


def _load_web_client_factory_registry() -> dict[str, str]:
    registry = dict(DEFAULT_WEB_CLIENT_FACTORY_REGISTRY)
    raw_registry = os.getenv("DEEP_RESEARCH_WEB_CLIENT_FACTORIES")
    if raw_registry:
        try:
            configured = json.loads(raw_registry)
        except json.JSONDecodeError as exc:
            raise ValueError("DEEP_RESEARCH_WEB_CLIENT_FACTORIES must be a JSON object.") from exc
        if not isinstance(configured, dict):
            raise ValueError("DEEP_RESEARCH_WEB_CLIENT_FACTORIES must be a JSON object.")
        for alias, spec in configured.items():
            if not isinstance(alias, str) or not alias.strip():
                raise ValueError("Web client factory aliases must be non-empty strings.")
            if not isinstance(spec, str) or not spec.strip():
                raise ValueError(f"Web client factory spec for alias {alias!r} must be a non-empty string.")
            registry[alias.strip()] = spec.strip()
    return registry


def _load_factory_from_spec(spec: str) -> Any:
    module_ref, separator, attr = spec.partition(":")
    if not separator or not attr:
        raise ValueError("Registered client factory specs must use 'module.path:factory'.")
    if module_ref.endswith(".py") or "/" in module_ref or "\\" in module_ref:
        raise ValueError("Registered client factory specs must be importable module paths, not filesystem paths.")

    module = importlib.import_module(module_ref)

    target = module
    for part in attr.split("."):
        target = getattr(target, part)
    return target() if callable(target) else target


def _resolve_factory(alias: str) -> Any:
    if ":" in alias or "/" in alias or "\\" in alias or alias.endswith(".py"):
        raise ValueError(
            "Client factory must be a registered alias, not a module path. "
            "Set DEEP_RESEARCH_CLIENT_FACTORIES='{\"alias\":\"module.path:factory\"}' in the trusted host environment."
        )
    registry = _load_client_factory_registry()
    spec = registry.get(alias)
    if spec is None:
        available = ", ".join(sorted(registry)) or "<none>"
        raise ValueError(f"Unknown client factory alias {alias!r}. Available aliases: {available}.")
    return _load_factory_from_spec(spec)


def _resolve_web_factory(alias: str) -> Any:
    if ":" in alias or "/" in alias or "\\" in alias or alias.endswith(".py"):
        raise ValueError(
            "Web client factory must be a registered alias, not a module path. "
            "Set DEEP_RESEARCH_WEB_CLIENT_FACTORIES='{\"alias\":\"module.path:factory\"}' in the trusted host environment."
        )
    registry = _load_web_client_factory_registry()
    spec = registry.get(alias)
    if spec is None:
        available = ", ".join(sorted(registry)) or "<none>"
        raise ValueError(f"Unknown web client factory alias {alias!r}. Available aliases: {available}.")
    return _load_factory_from_spec(spec)


def cmd_doctor(_args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _emit(
        {
            "status": "ok",
            "repo_root": str(REPO_ROOT),
            "invocation_cwd": str(INVOCATION_CWD),
            "effective_cwd": str(Path.cwd()),
            "python_executable": sys.executable,
            "python3_on_path": shutil.which("python3"),
            "python_on_path": shutil.which("python"),
            "runtime_file": str(Path(runtime.__file__).resolve()),
            "runtime_import_ok": True,
            "runtime_has_run_research_session": hasattr(runtime, "run_research_session"),
            "visualization_capabilities": runtime.get_visualization_capabilities(),
            "web_search": runtime.get_web_search_configuration_status(),
        }
    )


def cmd_capabilities(_args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _emit(
        {
            "visualization": runtime.get_visualization_capabilities(),
            "available_domain_packs": runtime.load_available_domain_packs(),
            "web_search": runtime.get_web_search_configuration_status(),
        }
    )


def cmd_start_session(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    from runtime.compliance import CHOSEN_SKILL, PROTOCOL_MODE
    from runtime.session_state import initialize_session_state

    runtime_policy = _policy_from_args(args)
    web_status = runtime.get_web_search_configuration_status(mode=args.web_search_mode)
    if args.web_search_mode == "required" and not web_status.get("configured"):
        raise ValueError("Web search mode is required but no provider is configured. Set TAVILY_API_KEY or use a host web provider.")
    runtime_policy.setdefault("web_search", web_status)
    semantic_guard_policy = runtime_policy.get("semantic_guard_policy")
    if isinstance(semantic_guard_policy, dict):
        runtime.configure_semantic_guard_policy(semantic_guard_policy)
    session = runtime.start_session(args.slug, raw_question=args.raw_question, created_at=time.time())
    runtime.persist_manifest(
        args.slug,
        {
            "slug": args.slug,
            "chosen_skill": CHOSEN_SKILL,
            "protocol_mode": PROTOCOL_MODE,
            "raw_question": args.raw_question,
            "current_date": args.current_date,
            "report_locale": args.report_locale,
            "report_template": _read_json(args.report_template) if args.report_template else None,
            "runtime_policy": runtime_policy,
        },
        session_id=session["session_id"],
        strict_session=True,
    )
    initialize_session_state(
        args.slug,
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=session["session_id"],
    )
    session["manifest_path"] = str(
        REPO_ROOT / "RESEARCH" / args.slug / "sessions" / session["session_id"] / "manifest.json"
    )
    _emit({"status": "ok", **session})


def cmd_persist_intent(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    result = runtime.persist_intent_stage(
        args.slug,
        _read_json(args.input),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit({"status": "ok", **result})


def cmd_persist_discovery(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    path = runtime.persist_discovery_stage(
        args.slug,
        _read_json(args.input),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit({"status": "ok", "path": path})


def cmd_persist_plan(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    path = runtime.persist_plan_stage(
        args.slug,
        _read_json(args.input),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit({"status": "ok", "path": path})


def cmd_probe_schema(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    client = _resolve_factory(args.client_factory)
    result = runtime.probe_schema(
        client,
        tables=_read_json(args.tables) if args.tables else None,
        sample_limit=args.sample_limit,
        list_tables_sql=args.list_tables_sql,
    )
    _emit(result)


def cmd_execute_contract(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    client = _resolve_factory(args.client_factory)
    web_client = (
        _resolve_web_factory(args.web_client_factory)
        if args.web_client_factory
        else runtime.resolve_default_web_client(mode=args.web_search_mode)
    )
    bundle = runtime.persist_round_execution_stage(
        client,
        args.slug,
        _read_json(args.contract),
        web_client=web_client,
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
        timeout=args.timeout,
        max_rows=args.max_rows,
        max_cache_age_seconds=args.max_cache_age_seconds,
        web_timeout=args.web_timeout,
        web_max_results=args.web_max_results,
    )
    _emit({"status": "ok", "bundle": bundle})


def cmd_persist_evaluation(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    path = runtime.persist_round_evaluation_stage(
        args.slug,
        _read_json(args.input),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit({"status": "ok", "path": path})


def cmd_persist_finalization(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    path = runtime.persist_finalization_stage(
        args.slug,
        _read_json(args.final_answer),
        report_evidence=_read_json(args.report_evidence),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit({"status": "ok", "path": path})


def cmd_persist_chart_spec(args: argparse.Namespace) -> None:
    if not args.trusted_legacy_chart_spec:
        raise SystemExit(
            "persist-chart-spec is reserved for trusted legacy ChartSpecBundle inputs. "
            "Use prepare-chart-affordances and compile-chart-spec for LLM-authored visualization plans, "
            "or pass --trusted-legacy-chart-spec for a non-governed compatibility import."
        )
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    path = runtime.persist_chart_spec_stage(
        args.slug,
        _read_json(args.input),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit(
        {
            "status": "ok",
            "path": path,
            "stage": "chart_spec",
            "next_stage": "chart_render",
            "next_command": "render-charts",
            "charts_rendered": False,
        }
    )


def cmd_prepare_chart_affordances(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    bundle = runtime.persist_chart_affordance_bundle(args.slug, session_id=args.session_id)
    _emit({"status": "ok", "bundle": bundle})


def cmd_compile_chart_spec(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    chart_affordance_bundle = runtime.read_artifact(
        args.slug,
        "chart_affordances.json",
        session_id=args.session_id,
        strict_session=True,
    )
    if not isinstance(chart_affordance_bundle, dict):
        chart_affordance_bundle = runtime.persist_chart_affordance_bundle(args.slug, session_id=args.session_id)[
            "chart_affordances"
        ]
    compiled = runtime.compile_chart_specs_from_affordance_plan(
        _read_json(args.input),
        chart_affordance_bundle,
    )
    compile_report_path = runtime.persist_artifact(
        args.slug,
        "chart_compile_report.json",
        compiled["chart_compile_report"],
        session_id=args.session_id,
        strict_session=True,
    )
    chart_spec_path = runtime.persist_chart_spec_stage(
        args.slug,
        compiled["chart_spec_bundle"],
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit(
        {
            "status": "ok",
            "chart_spec_path": chart_spec_path,
            "chart_compile_report_path": compile_report_path,
            "chart_compile_report": compiled["chart_compile_report"],
        }
    )


def cmd_render_charts(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    client = _resolve_factory(args.client_factory) if args.client_factory else None
    bundle = runtime.persist_chart_render_stage(
        args.slug,
        client=client,
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
        rehydrate_missing_result_rows=args.rehydrate_missing_result_rows,
        timeout=args.timeout,
        max_rows=args.max_rows,
        max_cache_age_seconds=args.max_cache_age_seconds,
    )
    _emit({"status": "ok", "bundle": bundle})


def cmd_assemble_report(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    bundle = runtime.persist_report_assembly_stage(
        args.slug,
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        session_id=args.session_id,
    )
    _emit({"status": "ok", "bundle": bundle})


def cmd_persist_suggestions(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _configure_runtime_policy(runtime, args)
    path = runtime.persist_suggestion_synthesis_stage(
        args.slug,
        _read_json(args.input),
        session_mode=runtime.SESSION_MODE_ORCHESTRATED_ONLY,
        business_label=args.business_label,
        session_id=args.session_id,
    )
    _emit({"status": "ok", "path": path})


def cmd_session_evidence(args: argparse.Namespace) -> None:
    runtime = _load_runtime()
    _emit(runtime.load_session_evidence(args.slug, session_id=args.session_id, strict_session=True))


def _add_session_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slug", required=True)
    parser.add_argument("--session-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bridge CLI that exposes the Deep Research runtime to local skill agents."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Verify runtime import and environment wiring.")
    doctor.set_defaults(func=cmd_doctor)

    capabilities = subparsers.add_parser("capabilities", help="Show runtime capabilities and domain packs.")
    capabilities.set_defaults(func=cmd_capabilities)

    start = subparsers.add_parser("start-session", help="Create a session root, manifest, and session_state.json.")
    start.add_argument("--slug", required=True)
    start.add_argument("--raw-question", required=True)
    start.add_argument("--current-date", required=True)
    start.add_argument("--report-locale")
    start.add_argument("--report-template")
    start.add_argument("--runtime-policy")
    start.add_argument("--report-policy")
    start.add_argument("--semantic-guard-policy")
    start.add_argument("--web-search-mode", choices=["auto", "skip", "required"], default="auto")
    start.set_defaults(func=cmd_start_session)

    persist_intent = subparsers.add_parser("persist-intent", help="Persist an IntentRecognitionResult JSON file.")
    _add_session_args(persist_intent)
    persist_intent.add_argument("--input", required=True)
    persist_intent.set_defaults(func=cmd_persist_intent)

    persist_discovery = subparsers.add_parser("persist-discovery", help="Persist a DataContextBundle JSON file.")
    _add_session_args(persist_discovery)
    persist_discovery.add_argument("--input", required=True)
    persist_discovery.set_defaults(func=cmd_persist_discovery)

    persist_plan = subparsers.add_parser("persist-plan", help="Persist a PlanBundle JSON file.")
    _add_session_args(persist_plan)
    persist_plan.add_argument("--input", required=True)
    persist_plan.set_defaults(func=cmd_persist_plan)

    probe_schema = subparsers.add_parser("probe-schema", help="Run runtime.schema_probe.probe_schema with a registered host client.")
    probe_schema.add_argument("--client-factory", required=True)
    probe_schema.add_argument("--tables", help="Optional JSON array of explicit tables to probe.")
    probe_schema.add_argument("--sample-limit", type=int, default=3)
    probe_schema.add_argument("--list-tables-sql", default="SHOW TABLES")
    probe_schema.set_defaults(func=cmd_probe_schema)

    execute_contract = subparsers.add_parser("execute-contract", help="Execute and persist one InvestigationContract.")
    _add_session_args(execute_contract)
    execute_contract.add_argument("--contract", required=True)
    execute_contract.add_argument("--client-factory", required=True)
    execute_contract.add_argument("--web-client-factory")
    execute_contract.add_argument("--web-search-mode", choices=["auto", "skip", "required"], default="auto")
    execute_contract.add_argument("--timeout", type=float, default=30.0)
    execute_contract.add_argument("--max-rows", type=int, default=10_000)
    execute_contract.add_argument("--max-cache-age-seconds", type=float)
    execute_contract.add_argument("--web-timeout", type=float, default=30.0)
    execute_contract.add_argument("--web-max-results", type=_positive_int)
    execute_contract.set_defaults(func=cmd_execute_contract)

    persist_evaluation = subparsers.add_parser("persist-evaluation", help="Persist a RoundEvaluationResult JSON file.")
    _add_session_args(persist_evaluation)
    persist_evaluation.add_argument("--input", required=True)
    persist_evaluation.set_defaults(func=cmd_persist_evaluation)

    persist_finalization = subparsers.add_parser("persist-finalization", help="Persist final answer and report evidence.")
    _add_session_args(persist_finalization)
    persist_finalization.add_argument("--final-answer", required=True)
    persist_finalization.add_argument("--report-evidence", required=True)
    persist_finalization.set_defaults(func=cmd_persist_finalization)

    persist_chart_spec = subparsers.add_parser(
        "persist-chart-spec",
        help="Persist a trusted legacy ChartSpecBundle JSON file outside governed LLM chart planning.",
    )
    _add_session_args(persist_chart_spec)
    persist_chart_spec.add_argument("--input", required=True)
    persist_chart_spec.add_argument("--trusted-legacy-chart-spec", action="store_true")
    persist_chart_spec.set_defaults(func=cmd_persist_chart_spec)

    prepare_chart_affordances = subparsers.add_parser(
        "prepare-chart-affordances",
        help="Build and persist runtime-owned chart-ready dataset affordances.",
    )
    _add_session_args(prepare_chart_affordances)
    prepare_chart_affordances.set_defaults(func=cmd_prepare_chart_affordances)

    compile_chart_spec = subparsers.add_parser(
        "compile-chart-spec",
        help="Compile an affordance-selection visualization plan into a ChartSpecBundle.",
    )
    _add_session_args(compile_chart_spec)
    compile_chart_spec.add_argument("--input", required=True)
    compile_chart_spec.set_defaults(func=cmd_compile_chart_spec)

    render_charts = subparsers.add_parser("render-charts", help="Render chart artifacts from chart_spec_bundle.json.")
    _add_session_args(render_charts)
    render_charts.add_argument("--client-factory")
    render_charts.add_argument("--rehydrate-missing-result-rows", action="store_true")
    render_charts.add_argument("--timeout", type=float, default=30.0)
    render_charts.add_argument("--max-rows", type=_positive_int, default=10_000)
    render_charts.add_argument("--max-cache-age-seconds", type=float)
    render_charts.set_defaults(func=cmd_render_charts)

    assemble_report = subparsers.add_parser("assemble-report", help="Assemble report.md and compliance artifacts.")
    _add_session_args(assemble_report)
    assemble_report.set_defaults(func=cmd_assemble_report)

    suggestions = subparsers.add_parser("persist-suggestions", help="Persist DomainPackSuggestionBundle JSON.")
    _add_session_args(suggestions)
    suggestions.add_argument("--input", required=True)
    suggestions.add_argument("--business-label")
    suggestions.set_defaults(func=cmd_persist_suggestions)

    evidence = subparsers.add_parser("session-evidence", help="Print aggregated persisted session evidence.")
    _add_session_args(evidence)
    evidence.set_defaults(func=cmd_session_evidence)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        _emit({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
