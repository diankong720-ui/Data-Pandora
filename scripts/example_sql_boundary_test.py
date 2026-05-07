#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:  # type: ignore[misc]
        return False

from runtime.interface import QueryResult, WarehouseClient
from runtime.tools import execute_sql


DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_QUERY_TIMEOUT = 60.0
DEFAULT_MAX_ROWS = 200
DEFAULT_REPORT_PATH = Path("example_sql_boundary_report.md")
DEFAULT_RAW_PATH = Path("RESEARCH") / "example_sql_boundary_results.json"
EXAMPLE_DOTENV = Path.home() / ".claude" / "skills" / "example-data-query" / ".env"

DATE_COLUMN_CANDIDATES = (
    "event_time",
    "create_time",
    "created_at",
    "update_time",
    "updated_at",
    "pay_time",
    "date",
    "dt",
    "day",
)
LOW_CARDINALITY_HINTS = ("status", "type", "state", "source", "channel", "pay", "is_", "sex")
HIGH_CARDINALITY_HINTS = ("id", "no", "code", "sn", "phone", "mobile", "user", "event")
KNOWN_EXAMPLE_TABLES = ("example_fact", "example_aux_entity", "example_subject", "example_source", "example_dimension")


def _load_env() -> None:
    load_dotenv(dotenv_path=EXAMPLE_DOTENV if EXAMPLE_DOTENV.exists() else None)
    load_dotenv()


def _first_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _scrub(text: str | None) -> str | None:
    if text is None:
        return None
    for key in ("SECRET", "VENDOR_WAREHOUSE_SECRET"):
        secret = os.getenv(key)
        if secret:
            text = text.replace(secret, "<secret>")
    base_url = _first_env("BASE_URL", "VENDOR_WAREHOUSE_BASE_URL")
    path = _first_env("URL_PATH", "VENDOR_WAREHOUSE_PATH")
    channel = _first_env("CHANNEL", "VENDOR_WAREHOUSE_CHANNEL")
    if base_url:
        text = text.replace(base_url.rstrip("/"), "<warehouse-base-url>")
    if path:
        text = text.replace(path, "<warehouse-path>")
    if channel:
        text = text.replace(channel, "<channel>")
    text = re.sub(r"https?://[^\s;|]+", "<warehouse-url>", text)
    text = re.sub(r"([Ss]ignature[\"']?\s*[:=]\s*[\"']?)[a-f0-9]{32}", r"\1<signature>", text)
    text = re.sub(r"\b[a-f0-9]{32}\b", "<md5-like-redacted>", text)
    return text[:500]


def _redacted_identity(config: "ExampleConfig") -> str:
    return f"example-http://<warehouse-base-url><warehouse-path>#{_scrub(config.channel)}"


def _single_line(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _identifier(name: str) -> str:
    if not re.match(r"^[A-Za-z_][\w.]*$", name):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return name


def _extract_tables(rows: list[dict[str, Any]]) -> list[str]:
    tables: list[str] = []
    for row in rows:
        for value in row.values():
            if isinstance(value, str) and value and value not in tables:
                tables.append(value)
                break
    return tables


def _extract_describe_columns(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    columns: list[dict[str, str]] = []
    for row in rows:
        lowered = {str(k).lower(): v for k, v in row.items()}
        name = lowered.get("field") or lowered.get("name") or lowered.get("column") or lowered.get("column_name")
        dtype = lowered.get("type") or lowered.get("data_type") or lowered.get("datatype") or ""
        if name:
            columns.append({"name": str(name), "type": str(dtype)})
    if columns:
        return columns
    for row in rows:
        values = list(row.values())
        if values:
            columns.append({"name": str(values[0]), "type": str(values[1]) if len(values) > 1 else ""})
    return columns


def _looks_date_column(column: dict[str, str]) -> bool:
    name = column["name"].lower()
    dtype = column.get("type", "").lower()
    return name in DATE_COLUMN_CANDIDATES or "date" in dtype or "time" in dtype


def _choose_date_column(columns: list[dict[str, str]]) -> str | None:
    for candidate in DATE_COLUMN_CANDIDATES:
        for column in columns:
            if column["name"].lower() == candidate:
                return column["name"]
    for column in columns:
        if _looks_date_column(column):
            return column["name"]
    return None


def _choose_group_columns(columns: list[dict[str, str]]) -> tuple[str | None, str | None]:
    low: str | None = None
    high: str | None = None
    for column in columns:
        name = column["name"]
        lowered = name.lower()
        if low is None and any(hint in lowered for hint in LOW_CARDINALITY_HINTS):
            low = name
        if high is None and any(hint in lowered for hint in HIGH_CARDINALITY_HINTS):
            high = name
    if low is None:
        for column in columns:
            if not _looks_date_column(column):
                low = column["name"]
                break
    if high is None:
        for column in reversed(columns):
            if not _looks_date_column(column) and column["name"] != low:
                high = column["name"]
                break
    return low, high


def _date_filter(column: str, days: int) -> str:
    return f"{_identifier(column)} >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY)"


@dataclass(frozen=True)
class ExampleConfig:
    base_url: str
    path: str
    channel: str
    secret: str
    connect_timeout: float
    query_timeout: float
    max_rows: int

    @classmethod
    def from_env(cls) -> "ExampleConfig":
        _load_env()
        base_url = _first_env("BASE_URL", "VENDOR_WAREHOUSE_BASE_URL")
        path = _first_env("URL_PATH", "VENDOR_WAREHOUSE_PATH")
        channel = _first_env("CHANNEL", "VENDOR_WAREHOUSE_CHANNEL")
        secret = _first_env("SECRET", "VENDOR_WAREHOUSE_SECRET")
        missing = [
            name
            for name, value in (
                ("BASE_URL", base_url),
                ("URL_PATH", path),
                ("CHANNEL", channel),
                ("SECRET", secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing example connection values: "
                + ", ".join(missing)
                + ". Provide BASE_URL/URL_PATH/CHANNEL/SECRET or VENDOR_WAREHOUSE_* equivalents."
            )
        return cls(
            base_url=str(base_url).rstrip("/"),
            path=str(path),
            channel=str(channel),
            secret=str(secret),
            connect_timeout=float(_first_env("HTTP_CONNECT_TIMEOUT", "VENDOR_WAREHOUSE_CONNECT_TIMEOUT", default=str(DEFAULT_CONNECT_TIMEOUT))),
            query_timeout=float(_first_env("HTTP_QUERY_TIMEOUT", "VENDOR_WAREHOUSE_QUERY_TIMEOUT", default=str(DEFAULT_QUERY_TIMEOUT))),
            max_rows=int(_first_env("HTTP_MAX_ROWS", "VENDOR_WAREHOUSE_MAX_ROWS", default=str(DEFAULT_MAX_ROWS))),
        )


class ExampleRuntimeClient(WarehouseClient):
    def __init__(self, config: ExampleConfig) -> None:
        self.config = config

    @property
    def identity(self) -> str:
        return f"example-http://{self.config.base_url}{self.config.path}#{self.config.channel}"

    def quote_identifier(self, name: str) -> str:
        return _identifier(name)

    def _sign(self, body: str, ts: int) -> str:
        payload = f"POST\n{self.config.path}\n{self.config.channel}\n{ts}\n{self.config.secret}\n{body}\n"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def execute(self, sql: str, *, timeout: float = 30.0, max_rows: int = 10_000) -> QueryResult:
        if requests is None:
            return QueryResult.from_error("requests package is required.")
        output_name = "result"
        body = json.dumps({"sqls": {output_name: sql}}, ensure_ascii=False, separators=(",", ":"))
        ts = int(time.time())
        try:
            response = requests.post(
                self.config.base_url + self.config.path,
                data=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Example-Channel": self.config.channel,
                    "Example-Ts": str(ts),
                    "Example-Signature": self._sign(body, ts),
                },
                timeout=(self.config.connect_timeout, timeout or self.config.query_timeout),
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", {})
            if not isinstance(data, dict):
                raise ValueError("Unexpected response: `data` must be an object.")
            rows = data.get(output_name)
            if rows is None and len(data) == 1:
                rows = next(iter(data.values()))
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise ValueError("Unexpected response: rows must be a list.")
            normalized = [dict(row) for row in rows if isinstance(row, dict)]
            limit = min(max_rows, self.config.max_rows) if max_rows > 0 else self.config.max_rows
            if limit > 0:
                normalized = normalized[:limit]
            columns: list[str] = []
            for row in normalized:
                for key in row:
                    if key not in columns:
                        columns.append(key)
            return QueryResult(rows=normalized, columns=columns)
        except requests.exceptions.Timeout:
            return QueryResult.from_error("Query timed out.", timed_out=True)
        except Exception as exc:
            text = str(exc)
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    text = f"{text}; body={response.text[:300]}"
                except Exception:
                    pass
            timed_out = "timeout" in text.lower() or "timed out" in text.lower()
            return QueryResult.from_error(_scrub(text) or "Query failed.", timed_out=timed_out)


class BoundaryRunner:
    def __init__(self, *, client: ExampleRuntimeClient, timeout: float, max_rows: int, sleep_seconds: float) -> None:
        self.client = client
        self.timeout = timeout
        self.max_rows = max_rows
        self.sleep_seconds = sleep_seconds
        self.results: list[dict[str, Any]] = []

    def run(self, case_id: str, category: str, sql: str, *, cost_class: str = "cheap") -> dict[str, Any]:
        started = time.time()
        result = execute_sql(
            self.client,
            sql,
            output_name=case_id,
            cost_class=cost_class,
            allow_cache=False,
            timeout=self.timeout,
            max_rows=self.max_rows,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        error = result.get("error")
        record = {
            "case_id": case_id,
            "category": category,
            "status": result.get("status"),
            "elapsed_ms": elapsed_ms,
            "row_count": result.get("row_count"),
            "cost_class": cost_class,
            "sql_template": _single_line(sql),
            "error": _scrub(error),
            "warehouse_snapshot": result.get("warehouse_snapshot"),
        }
        self.results.append(record)
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        return record

    def run_introspection(self, case_id: str, sql: str, *, max_rows: int = 200) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        started = time.time()
        result = self.client.execute(sql, timeout=min(self.timeout, 15.0), max_rows=max_rows)
        elapsed_ms = int((time.time() - started) * 1000)
        status = "success" if result.ok else ("timeout" if result.timed_out else "failed")
        record = {
            "case_id": case_id,
            "category": "schema",
            "status": status,
            "elapsed_ms": elapsed_ms,
            "row_count": result.row_count,
            "cost_class": "cheap",
            "sql_template": _single_line(sql),
            "error": _scrub(result.error),
            "warehouse_snapshot": None,
        }
        self.results.append(record)
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        return record, result.rows if result.ok else []

    def should_stop_category(self, category: str) -> bool:
        records = [r for r in self.results if r["category"] == category]
        if not records:
            return False
        last = records[-1]
        if last["status"] in {"failed", "timeout"}:
            return True
        if last.get("error") and ("500" in str(last["error"]) or "Internal Server Error" in str(last["error"])):
            return True
        if len(records) >= 2 and all(int(r["elapsed_ms"]) > self.timeout * 1000 * 0.8 for r in records[-2:]):
            return True
        return False


def _discover_tables(runner: BoundaryRunner, limit: int) -> list[str]:
    record, rows = runner.run_introspection("schema_show_tables", "SHOW TABLES", max_rows=limit)
    return _extract_tables(rows) if record["status"] == "success" else []


def _describe_table(runner: BoundaryRunner, table: str) -> list[dict[str, str]]:
    record, rows = runner.run_introspection(f"describe_{table}", f"DESCRIBE {_identifier(table)}", max_rows=200)
    return _extract_describe_columns(rows) if record["status"] == "success" else []


def _select_example_tables(tables: list[str], max_tables: int) -> list[str]:
    ordered: list[str] = []
    for table in KNOWN_EXAMPLE_TABLES:
        if table not in ordered:
            ordered.append(table)
    preferred = [table for table in tables if "example" in table.lower() or table.lower().startswith("example_")]
    for table in preferred or tables:
        if table not in ordered:
            ordered.append(table)
    return ordered[:max_tables]


def _build_cases(table: str, columns: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    safe_table = _identifier(table)
    date_column = _choose_date_column(columns)
    low_group, high_group = _choose_group_columns(columns)
    cases: list[tuple[str, str, str, str]] = []

    for limit in (1, 10, 100):
        cases.append((f"{table}_limit_{limit}", "metadata_limit", f"SELECT * FROM {safe_table} LIMIT {limit}", "cheap"))

    cases.append((f"{table}_count_all", "aggregation_count", f"SELECT COUNT(*) AS cnt FROM {safe_table}", "standard"))

    if date_column:
        for days in (1, 7, 30, 90):
            cases.append((
                f"{table}_filter_{days}d",
                "filtered_window",
                f"SELECT COUNT(*) AS cnt FROM {safe_table} WHERE {_date_filter(date_column, days)}",
                "standard",
            ))
        cases.append((
            f"{table}_ordered_filtered",
            "order_by",
            f"SELECT * FROM {safe_table} WHERE {_date_filter(date_column, 7)} ORDER BY {_identifier(date_column)} DESC LIMIT 100",
            "standard",
        ))
    else:
        cases.append((f"{table}_unfiltered_limit_1000", "filtered_window", f"SELECT * FROM {safe_table} LIMIT 1000", "standard"))

    if low_group:
        cases.append((
            f"{table}_group_low",
            "group_by",
            f"SELECT {_identifier(low_group)} AS dim, COUNT(*) AS cnt FROM {safe_table}"
            + (f" WHERE {_date_filter(date_column, 30)}" if date_column else "")
            + f" GROUP BY {_identifier(low_group)} LIMIT 100",
            "standard",
        ))
    if high_group:
        cases.append((
            f"{table}_group_high",
            "group_by",
            f"SELECT {_identifier(high_group)} AS dim, COUNT(*) AS cnt FROM {safe_table}"
            + (f" WHERE {_date_filter(date_column, 30)}" if date_column else "")
            + f" GROUP BY {_identifier(high_group)} LIMIT 100",
            "standard",
        ))
        cases.append((f"{table}_order_unfiltered", "order_by", f"SELECT * FROM {safe_table} ORDER BY {_identifier(high_group)} LIMIT 100", "standard"))

    cases.append((
        f"{table}_cte_1",
        "cte",
        "WITH base AS ("
        + f" SELECT * FROM {safe_table}"
        + (f" WHERE {_date_filter(date_column, 30)}" if date_column else " LIMIT 1000")
        + ") SELECT COUNT(*) AS cnt FROM base",
        "standard",
    ))
    if low_group:
        cases.append((
            f"{table}_cte_2",
            "cte",
            "WITH base AS ("
            + f" SELECT {_identifier(low_group)} FROM {safe_table}"
            + (f" WHERE {_date_filter(date_column, 30)}" if date_column else " LIMIT 1000")
            + f"), agg AS (SELECT {_identifier(low_group)}, COUNT(*) AS cnt FROM base GROUP BY {_identifier(low_group)}) "
            + "SELECT * FROM agg ORDER BY cnt DESC LIMIT 50",
            "standard",
        ))
    return cases


def _build_join_cases(table_profiles: dict[str, list[dict[str, str]]]) -> list[tuple[str, str, str, str]]:
    if len(table_profiles) < 2:
        return []
    tables = list(table_profiles)
    base = "example_fact" if "example_fact" in table_profiles else tables[0]
    base_cols = {c["name"] for c in table_profiles[base]}
    cases: list[tuple[str, str, str, str]] = []
    for other in tables:
        if other == base:
            continue
        other_cols = {c["name"] for c in table_profiles[other]}
        common = [
            c for c in ("entity_id", "entity_code", "subject_id", "dimension_id", "source_id", "id")
            if c in base_cols and c in other_cols
        ]
        if not common:
            common = sorted(base_cols & other_cols)[:1]
        if not common:
            continue
        join_col = common[0]
        date_col = _choose_date_column(table_profiles[base])
        where = f" WHERE {_date_filter(date_col, 7)}" if date_col else ""
        cases.append((
            f"join_{base}_{other}_{join_col}",
            "join",
            f"SELECT COUNT(*) AS cnt FROM {_identifier(base)} a JOIN {_identifier(other)} b ON a.{_identifier(join_col)} = b.{_identifier(join_col)}{where}",
            "standard",
        ))
        if date_col:
            cases.append((
                f"join_{base}_{other}_{join_col}_30d",
                "join",
                f"SELECT COUNT(*) AS cnt FROM {_identifier(base)} a JOIN {_identifier(other)} b ON a.{_identifier(join_col)} = b.{_identifier(join_col)} WHERE {_date_filter('a.' + date_col, 30)}",
                "standard",
            ))
        break
    return cases


def _build_example_fact_stress_cases(columns: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    names = {column["name"] for column in columns}
    cases: list[tuple[str, str, str, str]] = [
        ("example_fact_stress_limit_1000", "stress_example_fact", "SELECT * FROM example_fact LIMIT 1000", "standard"),
        ("example_fact_stress_limit_5000", "stress_example_fact", "SELECT * FROM example_fact LIMIT 5000", "standard"),
    ]
    if "event_time" in names:
        cases.extend(
            [
                (
                    "example_fact_stress_event_time_1000",
                    "stress_example_fact",
                    "SELECT id, subject_id, event_time, amount FROM example_fact ORDER BY event_time DESC LIMIT 1000",
                    "standard",
                ),
                (
                    "example_fact_stress_day_entity_group",
                    "stress_example_fact",
                    "SELECT DATE(event_time) AS d, entity_code, COUNT(*) AS cnt FROM example_fact "
                    "GROUP BY DATE(event_time), entity_code ORDER BY d DESC LIMIT 1000",
                    "standard",
                ),
            ]
        )
    if {"entity_code", "subject_id"}.issubset(names):
        cases.append(
            (
                "example_fact_stress_entity_subject_id_group",
                "stress_example_fact",
                "SELECT entity_code, subject_id, COUNT(*) AS cnt FROM example_fact GROUP BY entity_code, subject_id LIMIT 1000",
                "standard",
            )
        )
    if "id" in names:
        cases.append(
            (
                "example_fact_stress_id_group",
                "stress_example_fact",
                "SELECT id, COUNT(*) AS cnt FROM example_fact GROUP BY id LIMIT 1000",
                "standard",
            )
        )
    distinct_columns = [column for column in ("subject_id", "entity_code", "id", "event_name") if column in names]
    if distinct_columns:
        cases.append(
            (
                "example_fact_stress_distincts",
                "stress_example_fact",
                "SELECT "
                + ", ".join(f"COUNT(DISTINCT {column}) AS distinct_{column}" for column in distinct_columns)
                + " FROM example_fact",
                "standard",
            )
        )
    return cases


def _markdown_table(rows: list[list[Any]], headers: list[str]) -> str:
    def fmt(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(fmt(value) for value in row) + " |")
    return "\n".join(lines)


def _derive_guidance(results: list[dict[str, Any]], table_profiles: dict[str, list[dict[str, str]]]) -> list[str]:
    guidance: list[str] = []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_category.setdefault(result["category"], []).append(result)

    if any(r["status"] == "success" for r in by_category.get("metadata_limit", [])):
        guidance.append("探索明细表时使用 `LIMIT` 从 1/10/100 逐级放大，禁止直接 `SELECT *` 拉全表。")
    if any(r["status"] in {"failed", "timeout"} for r in by_category.get("filtered_window", [])):
        guidance.append("日期/分区窗口超过失败边界时必须缩小时间范围，优先按 1 天、7 天、30 天递增。")
    elif any(r["status"] == "success" for r in by_category.get("filtered_window", [])):
        guidance.append("涉及事实表统计时优先添加日期/时间过滤；本次测试中的过滤窗口可作为默认查询模板。")
    if any(r["status"] in {"failed", "timeout"} for r in by_category.get("group_by", [])):
        guidance.append("高基数字段 `GROUP BY` 已出现失败或超时风险，应先加时间过滤并限制输出维度数量。")
    elif any(r["status"] == "success" for r in by_category.get("group_by", [])):
        guidance.append("聚合查询应先过滤再 `GROUP BY`，并保留 `LIMIT` 控制返回维度数量。")
    if any(r["status"] in {"failed", "timeout"} for r in by_category.get("order_by", [])):
        guidance.append("无过滤 `ORDER BY` 是高风险模式；必须先缩小数据集，再排序和分页。")
    if any(r["status"] in {"failed", "timeout"} for r in by_category.get("join", [])):
        guidance.append("Join 查询必须先过滤事实表或先聚合后 join，避免大窗口明细 join。")
    elif any(r["status"] == "success" for r in by_category.get("join", [])):
        guidance.append("Join 查询可采用强过滤后 join 的模式；扩大窗口前应先验证 join key 的基数和重复度。")
    if any(r["status"] in {"failed", "timeout"} for r in by_category.get("stress_example_fact", [])):
        guidance.append("`example_fact` 强边界测试出现失败或超时后，应避免对应的大 LIMIT、无过滤排序或高基数聚合模式。")
    elif any(r["status"] == "success" for r in by_category.get("stress_example_fact", [])):
        guidance.append("本次 `example_fact` 强边界用例未触发业务查询 500，但大 LIMIT 和无过滤排序耗时更高，应作为灰区模式谨慎使用。")
    if any(_choose_date_column(cols) for cols in table_profiles.values()):
        guidance.append("优先使用已发现的日期/时间字段作为扫描边界；没有日期字段的表只做小样本维表查询。")
    return guidance


def _write_report(
    *,
    path: Path,
    raw_path: Path,
    config: ExampleConfig,
    started_at: str,
    completed_at: str,
    tables: list[str],
    table_profiles: dict[str, list[dict[str, str]]],
    results: list[dict[str, Any]],
) -> None:
    summary_rows = [
        [r["case_id"], r["category"], r["status"], r["elapsed_ms"], r["row_count"], r.get("error") or ""]
        for r in results
    ]
    guidance = _derive_guidance(results, table_profiles)
    failed = [r for r in results if r["status"] in {"failed", "timeout"} or r.get("error")]
    profile_rows = [
        [table, ", ".join(c["name"] + (f" ({c['type']})" if c.get("type") else "") for c in columns[:12])]
        for table, columns in table_profiles.items()
    ]
    content = [
        "# Example 数仓 SQL 边界测试报告",
        "",
        "## 执行信息",
        "",
        _markdown_table(
            [
                ["started_at", started_at],
                ["completed_at", completed_at],
                ["client_identity", _redacted_identity(config)],
                ["query_timeout_seconds", config.query_timeout],
                ["max_rows_recorded", config.max_rows],
                ["raw_results", str(raw_path)],
            ],
            ["字段", "值"],
        ),
        "",
        "## 发现的 Example 表与字段",
        "",
        _markdown_table(profile_rows or [["<none>", "未发现可测试表"]], ["表", "字段样例"]),
        "",
        "## 测试矩阵结果",
        "",
        _markdown_table(summary_rows, ["case_id", "类型", "状态", "耗时ms", "返回行数", "错误摘要"]),
        "",
        "## 500 / Timeout / 失败样例",
        "",
        _markdown_table(
            [[r["case_id"], r["category"], r["status"], r["elapsed_ms"], r.get("error") or ""] for r in failed]
            or [["<none>", "", "", "", "本次测试未捕获失败样例"]],
            ["case_id", "类型", "状态", "耗时ms", "错误摘要"],
        ),
        "",
        "## Example SQL 编写语法说明",
        "",
    ]
    if guidance:
        content.extend(f"- {item}" for item in guidance)
    else:
        content.append("- 本次测试没有足够成功样例形成查询规范；请先确认连接和 schema discovery。")
    content.extend(
        [
            "- 所有查询必须是单条 `SELECT` 或 `WITH`，禁止 DDL/DML、存储过程、导出和多语句。",
            "- 用于排查的 SQL 必须保留可复现边界：表名、过滤字段、时间窗口、聚合字段、`LIMIT`。",
            "- 当某类查询出现 500/timeout 后，不要继续扩大同类窗口；改为缩小日期范围、先聚合、或拆成多条小查询。",
            "",
            "## SQL 模板",
            "",
            "```sql",
            "SELECT COUNT(*) AS cnt",
            "FROM example_fact",
            "WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY);",
            "```",
            "",
            "```sql",
            "SELECT status, COUNT(*) AS cnt",
            "FROM example_fact",
            "WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)",
            "GROUP BY status",
            "LIMIT 100;",
            "```",
            "",
            "```sql",
            "WITH base AS (",
            "  SELECT entity_code, event_time",
            "  FROM example_fact",
            "  WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)",
            ")",
            "SELECT entity_code, COUNT(*) AS cnt",
            "FROM base",
            "GROUP BY entity_code",
            "ORDER BY cnt DESC",
            "LIMIT 100;",
            "```",
            "",
            "## 说明",
            "",
            "- 报告只使用本次真实接口执行记录生成；未使用历史缓存。",
            "- 错误信息已脱敏，未记录密钥、签名或明细数据。",
        ]
    )
    path.write_text("\n".join(content) + "\n", encoding="utf-8")


def run_boundary_tests(args: argparse.Namespace) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    config = ExampleConfig.from_env()
    if args.timeout is not None:
        config = ExampleConfig(
            base_url=config.base_url,
            path=config.path,
            channel=config.channel,
            secret=config.secret,
            connect_timeout=config.connect_timeout,
            query_timeout=float(args.timeout),
            max_rows=config.max_rows,
        )
    if args.max_rows is not None:
        config = ExampleConfig(
            base_url=config.base_url,
            path=config.path,
            channel=config.channel,
            secret=config.secret,
            connect_timeout=config.connect_timeout,
            query_timeout=config.query_timeout,
            max_rows=int(args.max_rows),
        )
    client = ExampleRuntimeClient(config)
    runner = BoundaryRunner(client=client, timeout=config.query_timeout, max_rows=config.max_rows, sleep_seconds=args.sleep)

    runner.run("preflight_select_1", "preflight", "SELECT 1 AS ok", cost_class="cheap")
    if runner.results[-1]["status"] != "success":
        raise RuntimeError("Preflight SELECT 1 failed; refusing to run boundary tests.")

    tables = _discover_tables(runner, args.max_tables * 5)
    selected_tables = _select_example_tables(tables, args.max_tables)
    table_profiles: dict[str, list[dict[str, str]]] = {}
    for table in selected_tables:
        columns = _describe_table(runner, table)
        if columns:
            table_profiles[table] = columns

    if not table_profiles:
        fallback = "example_fact"
        columns = _describe_table(runner, fallback)
        if columns:
            table_profiles[fallback] = columns

    stopped_categories: set[str] = set()
    for table, columns in table_profiles.items():
        for case_id, category, sql, cost_class in _build_cases(table, columns):
            if category in stopped_categories:
                continue
            runner.run(case_id, category, sql, cost_class=cost_class)
            if runner.should_stop_category(category):
                stopped_categories.add(category)

    for case_id, category, sql, cost_class in _build_join_cases(table_profiles):
        if category in stopped_categories:
            continue
        runner.run(case_id, category, sql, cost_class=cost_class)
        if runner.should_stop_category(category):
            stopped_categories.add(category)
            break

    if "example_fact" in table_profiles:
        for case_id, category, sql, cost_class in _build_example_fact_stress_cases(table_profiles["example_fact"]):
            if category in stopped_categories:
                continue
            runner.run(case_id, category, sql, cost_class=cost_class)
            if runner.should_stop_category(category):
                stopped_categories.add(category)

    raw_path = Path(args.raw_results)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    completed_at = datetime.now(timezone.utc).isoformat()
    raw_payload = {
        "started_at": started_at,
        "completed_at": completed_at,
        "client_identity": _redacted_identity(config),
        "tables": selected_tables,
        "table_profiles": table_profiles,
        "results": runner.results,
    }
    raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(
        path=Path(args.report),
        raw_path=raw_path,
        config=config,
        started_at=started_at,
        completed_at=completed_at,
        tables=selected_tables,
        table_profiles=table_profiles,
        results=runner.results,
    )
    print(json.dumps({"status": "ok", "report": args.report, "raw_results": args.raw_results}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run real Example warehouse SQL boundary tests and write a guidance report.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--raw-results", default=str(DEFAULT_RAW_PATH))
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--max-tables", type=int, default=4)
    parser.add_argument("--sleep", type=float, default=0.5)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_boundary_tests(args)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": _scrub(str(exc))}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
