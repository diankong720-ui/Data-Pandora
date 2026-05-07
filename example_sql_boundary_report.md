# Example 数仓 SQL 边界测试报告

## 执行信息

| 字段 | 值 |
| --- | --- |
| started_at | 2026-05-06T03:11:23.335804+00:00 |
| completed_at | 2026-05-06T03:13:55.313652+00:00 |
| client_identity | example-http://<warehouse-base-url><warehouse-path>#<channel> |
| query_timeout_seconds | 60.0 |
| max_rows_recorded | 200 |
| raw_results | RESEARCH/example_sql_boundary_results.json |

## 发现的 Example 表与字段

| 表 | 字段样例 |
| --- | --- |
| example_fact | id (bigint), subject_id (int), event_name (varchar(90)), event_status (int), event_time (datetime), entity_code (varchar(96)), amount (decimal(10,2)), net_amount (decimal(10,2)), discounted_amount (decimal(10,2)), quantity (int), last_update_time (datetime), source (varchar(96)) |
| example_dimension | id (int), dimension_id (int), inner_code (varchar(150)), dimension_no (varchar(150)), class_id (int), type_id (int), dimension_name (varchar(300)), dimension_alias (varchar(300)), amount (decimal(10,2)), premium_amount (decimal(10,2)), previous_amount (decimal(10,2)), cost_amount (decimal(10,2)) |
| example_entity | id (int), entity_id (varchar(96)), account_id (varchar(765)), name (varchar(765)), alias (varchar(765)), type (varchar(150)), status (tinyint), served_at (datetime), discard_at (datetime), code (varchar(765)), domain_type (int), serial_number (varchar(765)) |
| example_entity_state_history | valid_date (date), entity_id (varchar(1024)), gross_metric (decimal(10,2)), level (varchar(1024)) |
| example_entity_metric | id (bigint), entity_id (varchar(96)), dimension_id (int), metric_value (int), entity_type (varchar(96)) |
| example_entity_metric_history | valid_date (date), entity_id (varchar(1024)), dimension_id (bigint), metric_value (bigint) |

## 测试矩阵结果

| case_id | 类型 | 状态 | 耗时ms | 返回行数 | 错误摘要 |
| --- | --- | --- | --- | --- | --- |
| preflight_select_1 | preflight | success | 192 | 1 |  |
| schema_show_tables | schema | success | 336 | 13 |  |
| describe_example_fact | schema | success | 155 | 30 |  |
| describe_example_aux_entity | schema | failed | 164 | 0 | 500 Server Error: Internal Server Error for url: <warehouse-base-url><warehouse-path>; body= |
| describe_example_subject | schema | failed | 127 | 0 | 500 Server Error: Internal Server Error for url: <warehouse-base-url><warehouse-path>; body= |
| describe_example_source | schema | failed | 251 | 0 | 500 Server Error: Internal Server Error for url: <warehouse-base-url><warehouse-path>; body= |
| describe_example_dimension | schema | success | 771 | 63 |  |
| describe_example_entity | schema | success | 127 | 103 |  |
| describe_example_entity_state_history | schema | success | 1009 | 4 |  |
| describe_example_entity_metric | schema | success | 388 | 5 |  |
| describe_example_entity_metric_history | schema | success | 695 | 4 |  |
| example_fact_limit_1 | metadata_limit | success | 283 | 1 |  |
| example_fact_limit_10 | metadata_limit | success | 332 | 10 |  |
| example_fact_limit_100 | metadata_limit | success | 423 | 100 |  |
| example_fact_count_all | aggregation_count | success | 321 | 1 |  |
| example_fact_filter_1d | filtered_window | success | 100 | 1 |  |
| example_fact_filter_7d | filtered_window | success | 115 | 1 |  |
| example_fact_filter_30d | filtered_window | success | 97 | 1 |  |
| example_fact_filter_90d | filtered_window | success | 240 | 1 |  |
| example_fact_ordered_filtered | order_by | success | 1974 | 100 |  |
| example_fact_group_low | group_by | success | 812 | 7 |  |
| example_fact_group_high | group_by | success | 710 | 100 |  |
| example_fact_order_unfiltered | order_by | success | 1704 | 100 |  |
| example_fact_cte_1 | cte | success | 118 | 1 |  |
| example_fact_cte_2 | cte | success | 323 | 7 |  |
| example_dimension_limit_1 | metadata_limit | success | 321 | 1 |  |
| example_dimension_limit_10 | metadata_limit | success | 136 | 10 |  |
| example_dimension_limit_100 | metadata_limit | success | 245 | 100 |  |
| example_dimension_count_all | aggregation_count | success | 244 | 1 |  |
| example_dimension_filter_1d | filtered_window | success | 492 | 1 |  |
| example_dimension_filter_7d | filtered_window | success | 502 | 1 |  |
| example_dimension_filter_30d | filtered_window | success | 461 | 1 |  |
| example_dimension_filter_90d | filtered_window | success | 308 | 1 |  |
| example_dimension_ordered_filtered | order_by | success | 687 | 100 |  |
| example_dimension_group_low | group_by | success | 356 | 48 |  |
| example_dimension_group_high | group_by | success | 215 | 100 |  |
| example_dimension_order_unfiltered | order_by | success | 869 | 100 |  |
| example_dimension_cte_1 | cte | success | 90 | 1 |  |
| example_dimension_cte_2 | cte | success | 172 | 48 |  |
| example_entity_limit_1 | metadata_limit | success | 111 | 1 |  |
| example_entity_limit_10 | metadata_limit | success | 150 | 10 |  |
| example_entity_limit_100 | metadata_limit | success | 1459 | 100 |  |
| example_entity_count_all | aggregation_count | success | 320 | 1 |  |
| example_entity_filter_1d | filtered_window | success | 245 | 1 |  |
| example_entity_filter_7d | filtered_window | success | 241 | 1 |  |
| example_entity_filter_30d | filtered_window | success | 107 | 1 |  |
| example_entity_filter_90d | filtered_window | success | 149 | 1 |  |
| example_entity_ordered_filtered | order_by | success | 414 | 100 |  |
| example_entity_group_low | group_by | success | 124 | 12 |  |
| example_entity_group_high | group_by | success | 120 | 100 |  |
| example_entity_order_unfiltered | order_by | success | 3524 | 100 |  |
| example_entity_cte_1 | cte | success | 505 | 1 |  |
| example_entity_cte_2 | cte | success | 289 | 12 |  |
| example_entity_state_history_limit_1 | metadata_limit | success | 107 | 1 |  |
| example_entity_state_history_limit_10 | metadata_limit | success | 120 | 10 |  |
| example_entity_state_history_limit_100 | metadata_limit | success | 86 | 100 |  |
| example_entity_state_history_count_all | aggregation_count | success | 94 | 1 |  |
| example_entity_state_history_filter_1d | filtered_window | success | 139 | 1 |  |
| example_entity_state_history_filter_7d | filtered_window | success | 88 | 1 |  |
| example_entity_state_history_filter_30d | filtered_window | success | 505 | 1 |  |
| example_entity_state_history_filter_90d | filtered_window | success | 124 | 1 |  |
| example_entity_state_history_ordered_filtered | order_by | success | 83 | 100 |  |
| example_entity_state_history_group_low | group_by | success | 107 | 100 |  |
| example_entity_state_history_group_high | group_by | success | 94 | 1 |  |
| example_entity_state_history_order_unfiltered | order_by | success | 157 | 100 |  |
| example_entity_state_history_cte_1 | cte | success | 162 | 1 |  |
| example_entity_state_history_cte_2 | cte | success | 123 | 50 |  |
| example_entity_metric_limit_1 | metadata_limit | success | 112 | 1 |  |
| example_entity_metric_limit_10 | metadata_limit | success | 202 | 10 |  |
| example_entity_metric_limit_100 | metadata_limit | success | 136 | 100 |  |
| example_entity_metric_count_all | aggregation_count | success | 142 | 1 |  |
| example_entity_metric_unfiltered_limit_1000 | filtered_window | success | 1060 | 200 |  |
| example_entity_metric_group_low | group_by | success | 293 | 25 |  |
| example_entity_metric_group_high | group_by | success | 170 | 100 |  |
| example_entity_metric_order_unfiltered | order_by | success | 194 | 100 |  |
| example_entity_metric_cte_1 | cte | success | 86 | 1 |  |
| example_entity_metric_cte_2 | cte | success | 139 | 13 |  |
| example_entity_metric_history_limit_1 | metadata_limit | success | 106 | 1 |  |
| example_entity_metric_history_limit_10 | metadata_limit | success | 270 | 10 |  |
| example_entity_metric_history_limit_100 | metadata_limit | success | 756 | 100 |  |
| example_entity_metric_history_count_all | aggregation_count | success | 883 | 1 |  |
| example_entity_metric_history_filter_1d | filtered_window | success | 757 | 1 |  |
| example_entity_metric_history_filter_7d | filtered_window | success | 406 | 1 |  |
| example_entity_metric_history_filter_30d | filtered_window | success | 426 | 1 |  |
| example_entity_metric_history_filter_90d | filtered_window | success | 258 | 1 |  |
| example_entity_metric_history_ordered_filtered | order_by | success | 1156 | 100 |  |
| example_entity_metric_history_group_low | group_by | success | 629 | 100 |  |
| example_entity_metric_history_group_high | group_by | success | 293 | 29 |  |
| example_entity_metric_history_order_unfiltered | order_by | success | 133 | 100 |  |
| example_entity_metric_history_cte_1 | cte | success | 221 | 1 |  |
| example_entity_metric_history_cte_2 | cte | success | 408 | 50 |  |
| join_example_fact_example_dimension_id | join | success | 142 | 1 |  |
| join_example_fact_example_dimension_id_30d | join | success | 234 | 1 |  |
| example_fact_stress_limit_1000 | stress_example_fact | success | 908 | 200 |  |
| example_fact_stress_limit_5000 | stress_example_fact | success | 1315 | 200 |  |
| example_fact_stress_event_time_1000 | stress_example_fact | success | 1014 | 200 |  |
| example_fact_stress_day_entity_group | stress_example_fact | success | 16017 | 200 |  |
| example_fact_stress_entity_subject_id_group | stress_example_fact | success | 5324 | 200 |  |
| example_fact_stress_id_group | stress_example_fact | success | 1127 | 200 |  |
| example_fact_stress_distincts | stress_example_fact | timeout | 39600 | 0 | 503 Server Error: Service Unavailable for url: <warehouse-base-url><warehouse-path>; body=Request Timeout |

## 500 / Timeout / 失败样例

| case_id | 类型 | 状态 | 耗时ms | 错误摘要 |
| --- | --- | --- | --- | --- |
| describe_example_aux_entity | schema | failed | 164 | 500 Server Error: Internal Server Error for url: <warehouse-base-url><warehouse-path>; body= |
| describe_example_subject | schema | failed | 127 | 500 Server Error: Internal Server Error for url: <warehouse-base-url><warehouse-path>; body= |
| describe_example_source | schema | failed | 251 | 500 Server Error: Internal Server Error for url: <warehouse-base-url><warehouse-path>; body= |
| example_fact_stress_distincts | stress_example_fact | timeout | 39600 | 503 Server Error: Service Unavailable for url: <warehouse-base-url><warehouse-path>; body=Request Timeout |

## Example SQL 编写语法说明

- 探索明细表时使用 `LIMIT` 从 1/10/100 逐级放大，禁止直接 `SELECT *` 拉全表。
- 涉及事实表统计时优先添加日期/时间过滤；本次测试中的过滤窗口可作为默认查询模板。
- 聚合查询应先过滤再 `GROUP BY`，并保留 `LIMIT` 控制返回维度数量。
- Join 查询可采用强过滤后 join 的模式；扩大窗口前应先验证 join key 的基数和重复度。
- `example_fact` 强边界测试出现失败或超时后，应避免对应的大 LIMIT、无过滤排序或高基数聚合模式。
- 优先使用已发现的日期/时间字段作为扫描边界；没有日期字段的表只做小样本维表查询。
- 所有查询必须是单条 `SELECT` 或 `WITH`，禁止 DDL/DML、存储过程、导出和多语句。
- 用于排查的 SQL 必须保留可复现边界：表名、过滤字段、时间窗口、聚合字段、`LIMIT`。
- 当某类查询出现 500/timeout 后，不要继续扩大同类窗口；改为缩小日期范围、先聚合、或拆成多条小查询。

## SQL 模板

```sql
SELECT COUNT(*) AS cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY);
```

```sql
SELECT status, COUNT(*) AS cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY status
LIMIT 100;
```

```sql
WITH base AS (
  SELECT entity_code, event_time
  FROM example_fact
  WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
)
SELECT entity_code, COUNT(*) AS cnt
FROM base
GROUP BY entity_code
ORDER BY cnt DESC
LIMIT 100;
```

## 说明

- 报告只使用本次真实接口执行记录生成；未使用历史缓存。
- 错误信息已脱敏，未记录密钥、签名或明细数据。
