# Example 数仓 SQL 编写语法规则

本文档基于 `example_sql_boundary_report.md` 中的真实数仓接口压测结果整理。测试覆盖 `example_fact`、`example_dimension`、`example_entity`、`example_entity_state_history`、`example_entity_metric`、`example_entity_metric_history` 等表；最终一轮共 100 条测试记录，其中 96 条成功、3 条 `DESCRIBE` 返回 500、1 条 `example_fact` 强边界查询返回 503 / Request Timeout。

## 1. 基础安全规则

1. 只允许单条 `SELECT` 或 `WITH` 查询。
2. 禁止执行 `INSERT`、`UPDATE`、`DELETE`、`CREATE`、`DROP`、`ALTER`、`TRUNCATE`、`CALL`、`EXPORT`、`UNLOAD`、多语句拼接。
3. 探索表结构或样例数据时必须加 `LIMIT`，默认 `LIMIT 10`，最多 `LIMIT 100`。
4. 查询必须明确写出：
   - 目标表
   - 日期/时间过滤字段
   - 时间窗口
   - 聚合指标
   - 分组维度
   - 返回行数上限
5. 不允许把“查一下整体情况”写成全表明细扫描；必须先转成可聚合、可过滤的问题。

## 2. 时间过滤规则

1. 查询事实表，尤其是 `example_fact`，默认必须使用 `event_time` 过滤。
2. 日常分析默认窗口：
   - 明细抽样：1 天到 7 天
   - 常规聚合：7 天到 30 天
   - 趋势或低成本计数：最多 90 天
3. 90 天以上查询必须先用 `COUNT(*)` 或低基数字段聚合验证耗时，再扩大范围。
4. 禁止在 `example_fact` 上直接做无时间过滤的高基数聚合、distinct 或大排序。

推荐：

```sql
SELECT COUNT(*) AS cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY);
```

不推荐：

```sql
SELECT COUNT(DISTINCT subject_id), COUNT(DISTINCT entity_code), COUNT(DISTINCT id)
FROM example_fact;
```

压测中，上述多 distinct 模式触发了 503 / Request Timeout。

## 3. 明细查询规则

1. 探索字段和值域时使用：

```sql
SELECT *
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 1 DAY)
LIMIT 10;
```

2. 不要依赖大 `LIMIT` 拉取数据。接口或运行时可能截断返回行数，大 `LIMIT` 也会增加数仓压力。
3. 明细查询必须优先选择必要字段，不要长期使用 `SELECT *`。

推荐：

```sql
SELECT id, subject_id, event_status, event_time, entity_code, amount
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
LIMIT 100;
```

## 4. 聚合规则

1. 聚合前必须先过滤，特别是对 `example_fact`。
2. 优先使用低基数字段聚合，例如 `event_status`、`type`、`status`。
3. 高基数字段聚合必须加时间窗口和 `LIMIT`。
4. 多维聚合要谨慎。压测中：
   - `DATE(event_time), entity_code` 无过滤聚合耗时约 16s
   - `entity_code, subject_id` 无过滤聚合耗时约 5.3s
5. 聚合结果必须限制返回规模，默认 `LIMIT 100`。

推荐：

```sql
SELECT event_status, COUNT(*) AS cnt, SUM(amount) AS total_money
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY event_status
LIMIT 100;
```

谨慎使用：

```sql
SELECT DATE(event_time) AS d, entity_code, COUNT(*) AS cnt
FROM example_fact
GROUP BY DATE(event_time), entity_code
ORDER BY d DESC
LIMIT 1000;
```

## 5. DISTINCT 规则

1. `COUNT(DISTINCT ...)` 是高风险操作，尤其是在 `example_fact` 上无过滤执行。
2. 多个 distinct 不能直接堆在同一个全表查询中。
3. 如果必须统计 distinct，必须：
   - 加时间窗口
   - 一次只统计少量字段
   - 先从 1 天或 7 天窗口试探

推荐：

```sql
SELECT COUNT(DISTINCT subject_id) AS user_cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY);
```

禁止：

```sql
SELECT
  COUNT(DISTINCT subject_id),
  COUNT(DISTINCT entity_code),
  COUNT(DISTINCT id),
  COUNT(DISTINCT event_name)
FROM example_fact;
```

## 6. 排序规则

1. `ORDER BY` 前必须尽量缩小数据集。
2. 排序字段优先使用时间字段或聚合后的指标。
3. 禁止在大事实表上做无过滤排序后再分页。

推荐：

```sql
SELECT id, subject_id, event_time, amount
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
ORDER BY event_time DESC
LIMIT 100;
```

推荐的聚合后排序：

```sql
SELECT entity_code, COUNT(*) AS cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY entity_code
ORDER BY cnt DESC
LIMIT 100;
```

## 7. JOIN 规则

1. Join 必须先过滤事实表，再 join 维表。
2. 不要直接做大窗口明细 join。
3. 优先使用稳定 join key，例如 `dimension_id`、`entity_id`、`entity_code`。
4. 如果 join 后还要聚合，优先先在事实表聚合，再 join 维表补充名称或属性。

推荐：

```sql
WITH order_agg AS (
  SELECT dimension_id, COUNT(*) AS order_cnt, SUM(amount) AS total_money
  FROM example_fact
  WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
  GROUP BY dimension_id
)
SELECT p.dimension_name, a.order_cnt, a.total_money
FROM order_agg a
JOIN example_dimension p ON a.dimension_id = p.dimension_id
ORDER BY a.order_cnt DESC
LIMIT 100;
```

## 8. CTE / 子查询规则

1. `WITH` 可以使用，但必须让每层 CTE 收窄数据，而不是扩大数据。
2. 第一层 CTE 应完成时间过滤、字段裁剪。
3. 第二层 CTE 可做聚合。
4. 最外层只负责排序、筛选和限制返回。

推荐结构：

```sql
WITH base AS (
  SELECT entity_code, event_time, amount
  FROM example_fact
  WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
),
agg AS (
  SELECT entity_code, COUNT(*) AS cnt, SUM(amount) AS total_money
  FROM base
  GROUP BY entity_code
)
SELECT *
FROM agg
ORDER BY cnt DESC
LIMIT 100;
```

## 9. 元数据查询规则

1. `SHOW TABLES` 本次压测成功，可用于发现可见表。
2. `DESCRIBE example_fact`、`DESCRIBE example_dimension` 等成功。
3. `DESCRIBE example_aux_entity`、`DESCRIBE example_subject`、`DESCRIBE example_source` 在压测中返回 500。
4. 遇到 `DESCRIBE` 500 时，不要反复重试；改用已知 schema、`SELECT * FROM table LIMIT 1` 小样本探查，或确认表名是否可用。

## 10. 失败后的降级规则

当查询出现 500、503、timeout 或耗时异常升高时，按以下顺序降级：

1. 缩小时间窗口：90 天改 30 天，30 天改 7 天，7 天改 1 天。
2. 删除高基数字段聚合或 distinct。
3. 先聚合再 join。
4. 删除无必要的 `ORDER BY`。
5. 将一个大 SQL 拆成多条小 SQL。
6. 只保留必要字段，避免 `SELECT *`。

## 11. Example 查询模板

示例事实数量：

```sql
SELECT COUNT(*) AS order_cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY);
```

示例状态分布：

```sql
SELECT event_status, COUNT(*) AS cnt
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY event_status
ORDER BY cnt DESC
LIMIT 100;
```

示例实体维度表现：

```sql
SELECT entity_code, COUNT(*) AS order_cnt, SUM(amount) AS total_money
FROM example_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY entity_code
ORDER BY order_cnt DESC
LIMIT 100;
```

示例维度表现：

```sql
WITH order_agg AS (
  SELECT dimension_id, COUNT(*) AS order_cnt, SUM(amount) AS total_money
  FROM example_fact
  WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
  GROUP BY dimension_id
)
SELECT p.dimension_name, a.order_cnt, a.total_money
FROM order_agg a
JOIN example_dimension p ON a.dimension_id = p.dimension_id
ORDER BY a.order_cnt DESC
LIMIT 100;
```

## 12. 默认审查清单

提交或执行 SQL 前检查：

1. 是否只有一条 `SELECT` 或 `WITH`？
2. 是否命中了事实表，尤其是 `example_fact`？
3. 是否有日期/时间过滤？
4. 时间窗口是否从小到大逐步验证？
5. 是否存在多个 `COUNT(DISTINCT ...)`？
6. 是否有无过滤 `ORDER BY`？
7. 是否有高基数字段多维 `GROUP BY`？
8. 是否设置了合理 `LIMIT`？
9. Join 是否先过滤或先聚合？
10. 查询失败后是否已降级，而不是继续扩大扫描范围？
