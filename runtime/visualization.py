from __future__ import annotations

import importlib
import io
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from runtime.contracts import (
    normalize_open_questions,
    stable_payload_hash,
    validate_chart_spec_bundle,
    validate_descriptive_stats_bundle,
    validate_visualization_manifest,
)
from runtime.persistence import (
    get_session_context,
    load_session_evidence,
    list_round_bundles,
    persist_artifact,
    persist_binary_artifact,
    persist_round_bundle,
)
from runtime.visualization_capabilities import RENDER_ENGINE_ID, SUPPORTED_CHART_TYPES

USABLE_QUERY_STATUSES = {"success", "cached", "degraded_to_cache"}
REPORT_TEMPLATE_PRESETS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "title": "数据分析报告",
        "section_problem_definition": "标题与问题定义",
        "question_label": "分析问题",
        "conclusion_state_label": "结论状态",
        "section_headline": "核心结论摘要",
        "headline_fallback": "暂无核心结论。",
        "section_key_evidence": "关键证据",
        "evidence_source_label": "来源查询",
        "no_evidence": "本次未记录可展示的关键证据。",
        "section_visualizations": "描述性统计与图表解读",
        "no_chart_intro": "本次未生成图表。原因如下：",
        "no_chart_default": "当前 session 中没有可稳定渲染的 chart spec。",
        "section_limitations": "矛盾点、局限与未解释部分",
        "no_contradictions": "当前未记录显式 contradictions。",
        "unresolved_question_label": "未解释问题",
        "section_follow_up": "后续建议",
        "no_follow_up": "暂无额外 follow-up 建议。",
        "missing_raw_question": "未提供原始问题",
    },
    "en-US": {
        "title": "Data Analysis Report",
        "section_problem_definition": "Question Definition",
        "question_label": "Question",
        "conclusion_state_label": "Conclusion State",
        "section_headline": "Headline Conclusion",
        "headline_fallback": "No headline conclusion was provided.",
        "section_key_evidence": "Key Evidence",
        "evidence_source_label": "Source Queries",
        "no_evidence": "No report-ready supporting evidence was recorded for this session.",
        "section_visualizations": "Descriptive Statistics and Chart Commentary",
        "no_chart_intro": "No charts were generated. Reasons:",
        "no_chart_default": "No stable chart specification was available in this session.",
        "section_limitations": "Contradictions, Limitations, and Residual Gaps",
        "no_contradictions": "No explicit contradictions were recorded.",
        "unresolved_question_label": "Open Question",
        "section_follow_up": "Recommended Follow-up",
        "no_follow_up": "No additional follow-up actions were suggested.",
        "missing_raw_question": "Original question was not provided",
    },
}
_REPORT_TEMPLATE_OVERRIDE: dict[str, str] | None = None


def set_report_template(template: dict[str, str] | None = None, *, locale: str | None = None) -> None:
    """
    Configure report assembly copy outside the runtime implementation.

    The host may provide either a locale preset or a fully customized template.
    """
    global _REPORT_TEMPLATE_OVERRIDE
    if template is not None:
        _REPORT_TEMPLATE_OVERRIDE = {str(key): str(value) for key, value in template.items()}
        return
    if locale is None:
        _REPORT_TEMPLATE_OVERRIDE = None
        return
    if locale not in REPORT_TEMPLATE_PRESETS:
        raise ValueError(f"Unsupported report locale preset: {locale}")
    _REPORT_TEMPLATE_OVERRIDE = dict(REPORT_TEMPLATE_PRESETS[locale])


def _infer_report_locale(raw_question: str) -> str:
    if re.search(r"[\u3400-\u9fff]", raw_question):
        return "zh-CN"
    return "en-US"


def _runtime_report_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    runtime_policy = manifest.get("runtime_policy")
    if not isinstance(runtime_policy, dict):
        return {}
    report_policy = runtime_policy.get("report_policy")
    return report_policy if isinstance(report_policy, dict) else {}


def _merge_report_template(locale: str, template: dict[str, Any]) -> dict[str, str]:
    base = dict(REPORT_TEMPLATE_PRESETS.get(locale, REPORT_TEMPLATE_PRESETS["en-US"]))
    base.update({str(key): str(value) for key, value in template.items()})
    return base


def _resolve_report_locale(
    session_evidence: dict[str, Any],
    manifest: dict[str, Any],
    report_policy: dict[str, Any],
) -> tuple[str, str]:
    locale = manifest.get("report_locale")
    if isinstance(locale, str) and locale in REPORT_TEMPLATE_PRESETS:
        return locale, "manifest.report_locale"
    locale = report_policy.get("locale")
    if isinstance(locale, str) and locale in REPORT_TEMPLATE_PRESETS:
        return locale, "runtime_policy.report_policy.locale"
    locale = report_policy.get("default_locale")
    if isinstance(locale, str) and locale in REPORT_TEMPLATE_PRESETS:
        default_locale = locale
    else:
        default_locale = "en-US"
    intent = session_evidence.get("intent") if isinstance(session_evidence.get("intent"), dict) else {}
    raw_question = str(intent.get("raw_question") or "")
    inferred = _infer_report_locale(raw_question)
    if inferred in REPORT_TEMPLATE_PRESETS:
        return inferred, "raw_question_inference"
    return default_locale, "runtime_policy.report_policy.default_locale"


def _resolve_report_template(session_evidence: dict[str, Any]) -> dict[str, str]:
    if _REPORT_TEMPLATE_OVERRIDE is not None:
        return dict(_REPORT_TEMPLATE_OVERRIDE)

    manifest = session_evidence.get("manifest") if isinstance(session_evidence.get("manifest"), dict) else {}
    report_policy = _runtime_report_policy(manifest)
    locale, _source = _resolve_report_locale(session_evidence, manifest, report_policy)
    if isinstance(manifest.get("report_template"), dict):
        return _merge_report_template(locale, manifest["report_template"])
    if isinstance(report_policy.get("template"), dict):
        return _merge_report_template(locale, report_policy["template"])

    template_profile = report_policy.get("template_profile")
    template_profiles = report_policy.get("template_profiles")
    if (
        isinstance(template_profile, str)
        and isinstance(template_profiles, dict)
        and isinstance(template_profiles.get(template_profile), dict)
    ):
        return _merge_report_template(locale, template_profiles[template_profile])
    return dict(REPORT_TEMPLATE_PRESETS.get(locale, REPORT_TEMPLATE_PRESETS["en-US"]))


def _query_records(session_evidence: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for bundle in session_evidence.get("round_bundles", []):
        if not isinstance(bundle, dict):
            continue
        evaluation = bundle.get("evaluation")
        round_id = evaluation.get("round_id") if isinstance(evaluation, dict) else None
        if not isinstance(round_id, str):
            continue
        for query in bundle.get("executed_queries", []):
            if not isinstance(query, dict):
                continue
            query_id = query.get("query_id")
            if not isinstance(query_id, str) or not query_id:
                continue
            records[(round_id, query_id)] = {
                "generation_id": bundle.get("generation_id"),
                "round_id": round_id,
                "query_id": query_id,
                "description": query.get("description"),
                "output_name": query.get("output_name"),
                "status": query.get("status"),
                "result_rows": query.get("result_rows"),
                "result_rows_persisted": bool(query.get("result_rows_persisted")),
                "retention_mode_applied": query.get("retention_mode_applied"),
                "row_count": query.get("row_count"),
                "rows_preview": query.get("rows_preview"),
                "source_result_hash": query.get("source_result_hash"),
                "result_rows_purged_at": query.get("result_rows_purged_at"),
                "retention_cleanup_status": query.get("retention_cleanup_status"),
                "notes": query.get("notes") if isinstance(query.get("notes"), list) else [],
            }
    return records


def _report_evidence_maps(session_evidence: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], list[str]]]:
    report_evidence = session_evidence.get("report_evidence")
    evidence_by_ref: dict[str, dict[str, Any]] = {}
    query_to_evidence_refs: dict[tuple[str, str], list[str]] = {}
    if not isinstance(report_evidence, dict):
        return evidence_by_ref, query_to_evidence_refs
    entries = report_evidence.get("entries")
    if not isinstance(entries, list):
        return evidence_by_ref, query_to_evidence_refs
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        evidence_ref = entry.get("evidence_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            continue
        evidence_by_ref[evidence_ref] = entry
        for query_ref in entry.get("query_refs", []):
            if not isinstance(query_ref, dict):
                continue
            round_id = query_ref.get("round_id")
            query_id = query_ref.get("query_id")
            if isinstance(round_id, str) and round_id and isinstance(query_id, str) and query_id:
                query_to_evidence_refs.setdefault((round_id, query_id), []).append(evidence_ref)
    return evidence_by_ref, query_to_evidence_refs


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return float(candidate)
        except ValueError:
            return None
    return None


def _safe_artifact_name_component(value: str, *, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return safe or fallback


def _validate_plot_data_items(items: list[dict[str, Any]], source_rows: list[dict[str, Any]]) -> None:
    max_index = len(source_rows) - 1
    for item in items:
        source_indexes: list[int] = []
        single_index = item.get("source_row_index")
        if isinstance(single_index, int):
            source_indexes.append(single_index)
        many_indexes = item.get("source_row_indexes")
        if isinstance(many_indexes, list):
            source_indexes.extend(index for index in many_indexes if isinstance(index, int))
        if not source_indexes:
            raise ValueError("Each plot_data item must declare source_row_index or source_row_indexes.")
        for index in source_indexes:
            if index < 0 or index > max_index:
                raise ValueError("plot_data references source rows outside the persisted result set.")


def _load_matplotlib_pyplot() -> Any:
    try:
        matplotlib = importlib.import_module("matplotlib")
    except ImportError:
        command = [sys.executable, "-m", "pip", "install", "matplotlib>=3.8"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            if len(stderr) > 500:
                stderr = stderr[:500] + "..."
            raise ValueError(
                "matplotlib is required for chart rendering and automatic installation failed. "
                f"Command: {' '.join(command)}. Error: {stderr or 'no installer output'}"
            )
        matplotlib = importlib.import_module("matplotlib")
    matplotlib.use("Agg", force=True)
    return importlib.import_module("matplotlib.pyplot")


def _plot_payload_items(plot_data: dict[str, Any]) -> list[dict[str, Any]]:
    items = plot_data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("ChartSpec.plot_data.items must contain at least one item.")
    payloads: list[dict[str, Any]] = []
    for item in items:
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            raise ValueError("ChartSpec.plot_data.item.payload must be an object.")
        payloads.append(payload)
    return payloads


def _require_plot_field(plot_spec: dict[str, Any], field_name: str) -> str:
    value = plot_spec.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ChartSpec.plot_spec.{field_name} is required for this chart type.")
    return value.strip()


def _field_values(rows: list[dict[str, Any]], field_name: str) -> list[Any]:
    values: list[Any] = []
    for row in rows:
        if field_name not in row:
            raise ValueError(f"plot_data payload is missing required field: {field_name}")
        values.append(row[field_name])
    return values


def _numeric_values(values: list[Any], *, field_name: str) -> list[float]:
    numeric: list[float] = []
    for value in values:
        coerced = _coerce_float(value)
        if coerced is None:
            raise ValueError(f"plot_data field must be numeric for rendering: {field_name}")
        numeric.append(coerced)
    return numeric


def _sorted_payload_rows(rows: list[dict[str, Any]], plot_spec: dict[str, Any]) -> list[dict[str, Any]]:
    sort_mode = str(plot_spec.get("sort") or "source_order")
    if sort_mode == "source_order":
        return rows
    field_name: str | None = None
    reverse = False
    if sort_mode == "x_asc":
        field_name = plot_spec.get("x_field")
    elif sort_mode == "x_desc":
        field_name = plot_spec.get("x_field")
        reverse = True
    elif sort_mode == "y_asc":
        field_name = plot_spec.get("y_field")
    elif sort_mode == "y_desc":
        field_name = plot_spec.get("y_field")
        reverse = True
    else:
        raise ValueError(f"Unsupported ChartSpec.plot_spec.sort value: {sort_mode}")
    if not isinstance(field_name, str) or not field_name.strip():
        raise ValueError("ChartSpec.plot_spec.sort requires the referenced field to be configured.")
    return sorted(rows, key=lambda row: str(row.get(field_name, "")), reverse=reverse)


def _series_groups(rows: list[dict[str, Any]], series_field: Any) -> list[tuple[str | None, list[dict[str, Any]]]]:
    if not isinstance(series_field, str) or not series_field.strip():
        return [(None, rows)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if series_field not in row:
            raise ValueError(f"plot_data payload is missing required field: {series_field}")
        grouped.setdefault(str(row[series_field]), []).append(row)
    return list(grouped.items())


def _apply_axis_labels(ax: Any, plot_spec: dict[str, Any], *, x_field: str | None = None, y_field: str | None = None) -> None:
    ax.set_xlabel(str(plot_spec.get("x_label") or x_field or ""))
    ax.set_ylabel(str(plot_spec.get("y_label") or y_field or ""))


def _render_matplotlib_chart_png(spec: dict[str, Any]) -> bytes:
    plot_spec = spec["plot_spec"]
    chart_type = str(plot_spec.get("chart_type") or "")
    if not chart_type:
        raise ValueError("ChartSpec.plot_spec.chart_type is required for chart rendering.")
    if chart_type not in SUPPORTED_CHART_TYPES:
        raise ValueError(f"Unsupported ChartSpec.plot_spec.chart_type: {chart_type}")

    rows = _sorted_payload_rows(_plot_payload_items(spec["plot_data"]), plot_spec)
    plt = _load_matplotlib_pyplot()
    fig, ax = plt.subplots(figsize=(9.6, 5.4), dpi=100)
    try:
        ax.set_title(str(spec.get("title") or ""))

        if chart_type in {"line", "bar", "horizontal_bar", "scatter", "area"}:
            x_field = _require_plot_field(plot_spec, "x_field")
            y_field = _require_plot_field(plot_spec, "y_field")
            for label, group_rows in _series_groups(rows, plot_spec.get("series_field")):
                x_values = _field_values(group_rows, x_field)
                y_values = _numeric_values(_field_values(group_rows, y_field), field_name=y_field)
                plot_label = label if label is not None else None
                if chart_type == "line":
                    ax.plot(x_values, y_values, marker="o", label=plot_label)
                elif chart_type == "bar":
                    ax.bar([str(value) for value in x_values], y_values, label=plot_label)
                elif chart_type == "horizontal_bar":
                    ax.barh([str(value) for value in x_values], y_values, label=plot_label)
                elif chart_type == "scatter":
                    numeric_x = _numeric_values(x_values, field_name=x_field)
                    ax.scatter(numeric_x, y_values, label=plot_label)
                elif chart_type == "area":
                    ax.fill_between(range(len(x_values)), y_values, alpha=0.35, label=plot_label)
                    ax.plot(range(len(x_values)), y_values)
                    ax.set_xticks(range(len(x_values)))
                    ax.set_xticklabels([str(value) for value in x_values])
            _apply_axis_labels(ax, plot_spec, x_field=x_field, y_field=y_field)

        elif chart_type in {"histogram", "box"}:
            value_field = plot_spec.get("value_field") or plot_spec.get("y_field") or plot_spec.get("x_field")
            if not isinstance(value_field, str) or not value_field.strip():
                raise ValueError("ChartSpec.plot_spec.value_field is required for this chart type.")
            values = _numeric_values(_field_values(rows, value_field), field_name=value_field)
            if chart_type == "histogram":
                bins = int(_coerce_float(plot_spec.get("bins")) or 10)
                ax.hist(values, bins=max(1, bins))
                _apply_axis_labels(ax, plot_spec, x_field=value_field, y_field="count")
            else:
                ax.boxplot(values, vert=True)
                _apply_axis_labels(ax, plot_spec, x_field="", y_field=value_field)

        elif chart_type == "heatmap":
            x_field = _require_plot_field(plot_spec, "x_field")
            y_field = _require_plot_field(plot_spec, "y_field")
            value_field = _require_plot_field(plot_spec, "value_field")
            x_labels = list(dict.fromkeys(str(value) for value in _field_values(rows, x_field)))
            y_labels = list(dict.fromkeys(str(value) for value in _field_values(rows, y_field)))
            matrix = [[0.0 for _ in x_labels] for _ in y_labels]
            for row in rows:
                x_index = x_labels.index(str(row[x_field]))
                y_index = y_labels.index(str(row[y_field]))
                matrix[y_index][x_index] = _numeric_values([row[value_field]], field_name=value_field)[0]
            image = ax.imshow(matrix, aspect="auto")
            fig.colorbar(image, ax=ax)
            ax.set_xticks(range(len(x_labels)))
            ax.set_xticklabels(x_labels)
            ax.set_yticks(range(len(y_labels)))
            ax.set_yticklabels(y_labels)
            _apply_axis_labels(ax, plot_spec, x_field=x_field, y_field=y_field)

        else:
            raise ValueError(f"Unsupported ChartSpec.plot_spec.chart_type: {chart_type}")

        if isinstance(plot_spec.get("series_field"), str) and plot_spec["series_field"].strip():
            ax.legend()
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        output = io.BytesIO()
        fig.savefig(output, format="png")
        return output.getvalue()
    finally:
        plt.close(fig)


def _purge_rendered_result_rows(
    slug: str,
    query_source_hashes: dict[tuple[str, str], str],
    *,
    session_id: str | None,
) -> list[dict[str, Any]]:
    cleanup_results: list[dict[str, Any]] = []
    if not query_source_hashes:
        return cleanup_results
    now = time.time()
    round_bundles = list_round_bundles(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    for bundle in round_bundles:
        if not isinstance(bundle, dict):
            continue
        evaluation = bundle.get("evaluation")
        round_id = evaluation.get("round_id") if isinstance(evaluation, dict) else None
        if not isinstance(round_id, str):
            continue
        executed_queries = bundle.get("executed_queries")
        if not isinstance(executed_queries, list):
            continue
        changed = False
        for query in executed_queries:
            if not isinstance(query, dict):
                continue
            query_id = query.get("query_id")
            key = (round_id, query_id) if isinstance(query_id, str) else None
            if key not in query_source_hashes:
                continue
            if isinstance(query.get("result_rows"), list):
                query.pop("result_rows", None)
                query["result_rows_persisted"] = False
                query["source_result_hash"] = query_source_hashes[key]
                query["result_rows_purged_at"] = now
                query["retention_cleanup_status"] = "purged_after_chart_render"
                changed = True
                cleanup_results.append(
                    {
                        "round_id": round_id,
                        "query_id": query_id,
                        "status": "purged_after_chart_render",
                    }
                )
            else:
                query["retention_cleanup_status"] = query.get("retention_cleanup_status") or "no_result_rows_to_purge"
                cleanup_results.append(
                    {
                        "round_id": round_id,
                        "query_id": query_id,
                        "status": query["retention_cleanup_status"],
                    }
                )
        if changed:
            persist_round_bundle(
                slug,
                round_id,
                bundle.get("contract") if isinstance(bundle.get("contract"), dict) else {},
                executed_queries,
                bundle.get("evaluation") if isinstance(bundle.get("evaluation"), dict) else {},
                generation_id=str(bundle.get("generation_id")) if isinstance(bundle.get("generation_id"), str) else None,
                session_id=session_id,
                strict_session=bool(session_id),
            )
    return cleanup_results


def render_chart_artifacts(
    slug: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    session_evidence = load_session_evidence(slug, session_id=session_id, strict_session=bool(session_id))
    session_context = get_session_context(slug, session_id=session_id, strict_session=bool(session_id))
    chart_spec_bundle = session_evidence.get("chart_spec_bundle")
    if not isinstance(chart_spec_bundle, dict):
        raise ValueError("chart_spec_bundle.json is required before chart rendering.")
    validate_chart_spec_bundle(chart_spec_bundle)

    query_records = _query_records(session_evidence)
    evidence_by_ref, query_to_evidence_refs = _report_evidence_maps(session_evidence)

    summaries: list[dict[str, Any]] = []
    omitted_visuals: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    query_source_hashes_for_cleanup: dict[tuple[str, str], str] = {}

    for spec in chart_spec_bundle["specs"]:
        spec_id = str(spec["spec_id"])
        reason: str | None = None
        source_query_ref = spec["source_query_ref"]
        query_ref_key = (str(source_query_ref["round_id"]), str(source_query_ref["query_id"]))
        query_record = query_records.get(query_ref_key)
        if query_record is None:
            reason = "source query ref is not available in persisted session evidence"
        elif query_record.get("status") not in USABLE_QUERY_STATUSES:
            reason = "source query status is not eligible for chart rendering"
        elif not query_record.get("result_rows_persisted") or not isinstance(query_record.get("result_rows"), list):
            reason = "source query result rows were not retained for rendering"
        else:
            for evidence_ref in spec["evidence_refs"]:
                if evidence_ref not in evidence_by_ref:
                    reason = f"unknown evidence_ref: {evidence_ref}"
                    break
            if reason is None:
                query_linked_evidence = set(query_to_evidence_refs.get(query_ref_key, []))
                if not set(spec["evidence_refs"]).issubset(query_linked_evidence):
                    reason = "chart spec evidence refs are not linked to the referenced query"
        if reason is not None:
            omitted_visuals.append({"spec_id": spec_id, "reason": reason})
            summaries.append(
                {
                    "spec_id": spec_id,
                    "semantic_chart_type": spec["semantic_chart_type"],
                    "rendered": False,
                    "notes": [reason],
                }
            )
            continue

        raw_rows = [row for row in query_record["result_rows"] if isinstance(row, dict)]
        source_result_hash = stable_payload_hash(raw_rows)
        query_source_hashes_for_cleanup[query_ref_key] = source_result_hash
        try:
            plot_data = {
                "spec_id": spec["spec_id"],
                "semantic_chart_type": spec["semantic_chart_type"],
                "renderer_hint": spec.get("renderer_hint"),
                "source_query_ref": dict(spec["source_query_ref"]),
                "items": list(spec["plot_data"]["items"]),
                "plot_spec": dict(spec["plot_spec"]),
            }
            _validate_plot_data_items(plot_data["items"], raw_rows)
            png_bytes = _render_matplotlib_chart_png(spec)
            safe_spec_id = _safe_artifact_name_component(spec_id, fallback="chart")
            chart_id = f"{len(charts) + 1:02d}_{safe_spec_id}"
            plot_data_path = persist_artifact(
                slug,
                f"{chart_id}.plot-data.json",
                plot_data,
                subdir="charts",
                session_id=session_id,
                strict_session=True,
            )
            file_path = persist_binary_artifact(
                slug,
                f"{chart_id}.png",
                png_bytes,
                subdir="charts",
                session_id=session_id,
                strict_session=True,
            )
        except ValueError as exc:
            reason = str(exc)
            omitted_visuals.append({"spec_id": spec_id, "reason": reason})
            summaries.append(
                {
                    "spec_id": spec_id,
                    "semantic_chart_type": spec["semantic_chart_type"],
                    "rendered": False,
                    "notes": [reason],
                }
            )
            continue

        charts.append(
            {
                "chart_id": chart_id,
                "spec_id": spec_id,
                "semantic_chart_type": spec["semantic_chart_type"],
                "render_engine": RENDER_ENGINE_ID,
                "title": spec["title"],
                "caption": spec["caption"],
                "file_path": file_path,
                "plot_data_path": plot_data_path,
                "spec_hash": stable_payload_hash(spec),
                "plot_spec_hash": stable_payload_hash(spec["plot_spec"]),
                "source_result_hash": source_result_hash,
                "query_refs": list(spec["query_refs"]),
                "evidence_refs": list(spec["evidence_refs"]),
                "report_section": spec["report_section"],
            }
        )
        summaries.append(
            {
                "spec_id": spec_id,
                "semantic_chart_type": spec["semantic_chart_type"],
                "rendered": True,
                "notes": [f"Rendered from {source_query_ref['round_id']}:{source_query_ref['query_id']}."],
            }
        )

    retention_cleanup = _purge_rendered_result_rows(
        slug,
        query_source_hashes_for_cleanup,
        session_id=session_id,
    )
    omission_reasons = sorted({item["reason"] for item in omitted_visuals})
    if charts:
        coverage = "charts_generated"
    elif chart_spec_bundle["specs"]:
        coverage = "text_only"
    else:
        coverage = "no_chartable_evidence"

    descriptive_stats = {
        "session_slug": slug,
        "session_id": session_evidence.get("session_id") or "legacy",
        "visualization_coverage": coverage,
        "statistical_summary": summaries,
        "omitted_visuals": omitted_visuals,
        "omission_reasons": omission_reasons,
        "retention_cleanup": retention_cleanup,
        "generated_at": time.time(),
    }
    validate_descriptive_stats_bundle(descriptive_stats)
    descriptive_stats_path = persist_artifact(
        slug,
        "descriptive_stats.json",
        descriptive_stats,
        session_id=session_id,
        strict_session=True,
    )

    visualization_manifest = {
        "session_slug": slug,
        "session_id": session_evidence.get("session_id") or "legacy",
        "report_path": str(Path(session_context["session_root"]) / "report.md"),
        "charts": charts,
        "generated_at": time.time(),
    }
    validate_visualization_manifest(visualization_manifest)
    manifest_path = persist_artifact(
        slug,
        "visualization_manifest.json",
        visualization_manifest,
        session_id=session_id,
        strict_session=True,
    )
    return {
        "descriptive_stats": descriptive_stats,
        "descriptive_stats_path": descriptive_stats_path,
        "visualization_manifest": visualization_manifest,
        "visualization_manifest_path": manifest_path,
    }


def _format_query_refs(query_refs: list[dict[str, Any]]) -> str:
    formatted = [
        f"{item.get('round_id')}:{item.get('query_id')}"
        for item in query_refs
        if isinstance(item, dict)
    ]
    return ", ".join(formatted)


def assemble_report_artifacts(
    slug: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    session_evidence = load_session_evidence(slug, session_id=session_id, strict_session=bool(session_id))
    final_answer = session_evidence.get("final_answer") if isinstance(session_evidence.get("final_answer"), dict) else {}
    report_evidence = session_evidence.get("report_evidence") if isinstance(session_evidence.get("report_evidence"), dict) else {}
    manifest = session_evidence.get("visualization_manifest") if isinstance(session_evidence.get("visualization_manifest"), dict) else {"charts": []}
    descriptive_stats = session_evidence.get("descriptive_stats") if isinstance(session_evidence.get("descriptive_stats"), dict) else {"omission_reasons": []}
    intent = session_evidence.get("intent") if isinstance(session_evidence.get("intent"), dict) else {}
    raw_question = str(intent.get("raw_question") or "")
    report_template = _resolve_report_template(session_evidence)
    session_manifest = session_evidence.get("manifest") if isinstance(session_evidence.get("manifest"), dict) else {}
    report_policy = _runtime_report_policy(session_manifest)
    report_locale, report_locale_source = _resolve_report_locale(
        session_evidence,
        session_manifest,
        report_policy,
    )

    lines = [
        f"# {report_template['title']}",
        "",
        f"## {report_template['section_problem_definition']}",
        "",
        f"- {report_template['question_label']}：{raw_question or report_template['missing_raw_question']}",
        f"- {report_template['conclusion_state_label']}：{final_answer.get('conclusion_state', 'unknown')}",
        "",
        f"## {report_template['section_headline']}",
        "",
        final_answer.get("headline_conclusion", report_template["headline_fallback"]),
        "",
        f"## {report_template['section_key_evidence']}",
        "",
    ]

    entries = report_evidence.get("entries", []) if isinstance(report_evidence.get("entries"), list) else []
    supported_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("section") == "supported_claims"]
    if supported_entries:
        for entry in supported_entries:
            lines.append(f"- {entry.get('text', '')}")
            refs = _format_query_refs(entry.get("query_refs", []))
            if refs:
                lines.append(f"  {report_template['evidence_source_label']}：{refs}")
    else:
        lines.append(f"- {report_template['no_evidence']}")
    lines.append("")

    lines.append(f"## {report_template['section_visualizations']}")
    lines.append("")
    charts = manifest.get("charts", []) if isinstance(manifest.get("charts"), list) else []
    if charts:
        for chart in charts:
            chart_path = Path(str(chart["file_path"]))
            lines.append(f"### {chart['title']}")
            lines.append("")
            lines.append(f"![{chart['title']}](charts/{chart_path.name})")
            lines.append("")
            lines.append(chart["caption"])
            lines.append("")
    else:
        lines.append(report_template["no_chart_intro"])
        lines.append("")
        for reason in descriptive_stats.get("omission_reasons", []):
            lines.append(f"- {reason}")
        if not descriptive_stats.get("omission_reasons"):
            lines.append(f"- {report_template['no_chart_default']}")
        lines.append("")

    lines.append(f"## {report_template['section_limitations']}")
    lines.append("")
    contradiction_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("section") == "contradictions"]
    residual_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("section") == "residual_context"]
    if contradiction_entries:
        for entry in contradiction_entries:
            lines.append(f"- {entry.get('text', '')}")
    else:
        lines.append(f"- {report_template['no_contradictions']}")
    for entry in residual_entries:
        lines.append(f"- {entry.get('text', '')}")
    for question in normalize_open_questions(
        final_answer.get("residual_summary", {}).get("open_questions", []),
        label="FinalAnswer.residual_summary.open_questions",
    ):
        lines.append(f"- {report_template['unresolved_question_label']}：{question.get('text', '')}")
    lines.append("")

    lines.append(f"## {report_template['section_follow_up']}")
    lines.append("")
    follow_ups = final_answer.get("recommended_follow_up", [])
    if follow_ups:
        for item in follow_ups:
            lines.append(f"- {item}")
    else:
        lines.append(f"- {report_template['no_follow_up']}")
    lines.append("")

    report_markdown = "\n".join(lines)
    report_path = persist_artifact(
        slug,
        "report.md",
        report_markdown,
        session_id=session_id,
        strict_session=True,
    )

    manifest["report_path"] = report_path
    manifest["report_locale"] = report_locale
    manifest["report_locale_source"] = report_locale_source
    if isinstance(report_policy.get("template_profile"), str):
        manifest["report_template_profile"] = report_policy["template_profile"]
    validate_visualization_manifest(manifest)
    manifest_path = persist_artifact(
        slug,
        "visualization_manifest.json",
        manifest,
        session_id=session_id,
        strict_session=True,
    )
    return {
        "report_path": report_path,
        "report_markdown": report_markdown,
        "visualization_manifest_path": manifest_path,
        "visualization_manifest": manifest,
    }


def generate_visualization_artifacts(
    slug: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    render_bundle = render_chart_artifacts(slug, session_id=session_id)
    report_bundle = assemble_report_artifacts(slug, session_id=session_id)
    return {
        **render_bundle,
        **report_bundle,
    }
