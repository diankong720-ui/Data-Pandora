# Domain Pack Guide

A domain pack is the only context-specific configuration layer in this skill family.

Its purpose is to tune how the LLM interprets business vocabulary and prioritizes investigation families. It does not replace the shared contracts or the core methodology.

---

## File Layout

```text
domain-packs/
├── generic/
│   └── pack.json
├── custom_context/
│   └── pack.json
└── DOMAIN_PACK_GUIDE.md
```

---

## Pack Schema

```json
{
  "pack_id": "custom_context",
  "label": "Custom Context BI Research Pack",
  "description": "Context-specific vocabulary and investigation tuning.",
  "taxonomy": {
    "problem_types": [
      {
        "id": "root_cause_analysis",
        "label": "Root Cause Analysis",
        "summary": "Explain why a business metric changed.",
        "keywords": ["why did", "drop", "decline", "increase"]
      }
    ]
  },
  "lexicon": {
    "metrics": [
      { "canonical": "sales_amount", "aliases": ["revenue", "transaction value"] },
      { "canonical": "order_count", "aliases": ["orders", "transactions"] },
      { "canonical": "buyer_count", "aliases": ["buyers", "customers"] }
    ],
    "dimensions": [
      { "id": "channel", "aliases": ["platform", "traffic source"] },
      { "id": "product", "aliases": ["SKU", "category"] },
      { "id": "region", "aliases": ["city", "zone"] }
    ],
    "business_aliases": [
      { "phrase": "context_scope_label", "maps_to": "business_scope" }
    ],
    "unsupported_dimensions": [
      { "id": "gender", "reason": "No gender field exists in the current warehouse." }
    ]
  },
  "performance_risks": [
    { "field": "last_update", "risk": "Expensive on large rolling windows; prefer order_date." }
  ],
  "driver_family_templates": {
    "audit_scope": {
      "class": "audit",
      "statement": "The observed issue is affected by scope mismatch.",
      "base_prior": 0.55,
      "operator": "audit_baseline"
    },
    "demand_order_shift": {
      "class": "driver",
      "statement": "Order volume movement explains most of the headline change.",
      "base_prior": 0.60,
      "operator": "demand_decomposition"
    },
    "value_aov_shift": {
      "class": "driver",
      "statement": "Per-order value movement explains most of the headline change.",
      "base_prior": 0.50,
      "operator": "value_decomposition"
    }
  },
  "domain_priors": {
    "demand_order_shift": 0.65,
    "value_aov_shift": 0.45
  },
  "operator_preferences": {
    "dimension_priority": ["channel", "product"],
    "dimension_mode_hints": {
      "channel": ["mix_delta", "concentration_shift"],
      "product": ["top_movers", "bounded_compare"]
    }
  }
}
```

---

## Consumer Matrix

Every pack field must have a defined consumer.

| Pack field | Consumed by | Effect |
|---|---|---|
| `taxonomy.problem_types` | `intent-recognition` | Raises or lowers canonical problem type scores |
| `lexicon.metrics` | `intent-recognition` | Maps user metric vocabulary to canonical metric ids |
| `lexicon.dimensions` | `intent-recognition` | Maps user dimension vocabulary to canonical dimension ids |
| `lexicon.business_aliases` | `intent-recognition` | Resolves context-specific business scope phrases |
| `lexicon.unsupported_dimensions` | `intent-recognition`, `data-discovery` | Flags infeasible requests and discovery-time schema risk |
| `performance_risks` | `hypothesis-engine` | Prunes risky or load-sensitive query patterns |
| `driver_family_templates` | `hypothesis-engine` | Adds or specializes candidate hypothesis families |
| `domain_priors` | `hypothesis-engine` | Tunes hypothesis relevance scoring |
| `operator_preferences` | `hypothesis-engine` | Guides dimension and operator selection |

If a field does not have a consumer, remove it from the pack schema rather than documenting dead configuration.

---

## Field Guidance

### `taxonomy.problem_types`

Use canonical ids from `contracts.md` when possible.

The pack may tune scoring, but it must not invent a separate incompatible problem-type universe.

### `lexicon.*`

Keep aliases semantic.

Do not place table names, SQL snippets, or physical field names in the lexicon.

### `performance_risks`

Document fields or patterns that are slow, fragile, or admission-sensitive. The planning stage uses these hints when warehouse load is elevated.

### `driver_family_templates`

Use these to specialize hypothesis families without breaking the shared five-layer methodology.

Each template should include:

- `class`
- `statement`
- `base_prior`
- `operator`

### `domain_priors`

Use these to override or tune the default relevance bias from the family template layer.

### `operator_preferences`

Use these only for planning preference, not for hard execution guarantees.

---

## Minimal Pack

A minimal pack may omit planning-specific tuning and still remain valid.

```json
{
  "pack_id": "custom_context",
  "label": "Custom Context Pack",
  "description": "Minimal vocabulary layer.",
  "taxonomy": {
    "problem_types": []
  },
  "lexicon": {
    "metrics": [
      { "canonical": "sales_amount", "aliases": ["<your revenue term>"] },
      { "canonical": "order_count", "aliases": ["<your order term>"] },
      { "canonical": "buyer_count", "aliases": ["<your customer term>"] }
    ],
    "dimensions": [
      { "id": "channel", "aliases": ["<your channel term>"] }
    ],
    "business_aliases": [],
    "unsupported_dimensions": []
  },
  "performance_risks": [],
  "driver_family_templates": {},
  "domain_priors": {},
  "operator_preferences": {}
}
```

When `taxonomy.problem_types` is empty, the system still uses the default canonical taxonomy from `contracts.md`.

---

## Domain Pack Suggestion Synthesis Output

Post-session enrichment may suggest updates to these same fields only:

- `taxonomy.problem_types`
- `lexicon.metrics`
- `lexicon.dimensions`
- `lexicon.business_aliases`
- `lexicon.unsupported_dimensions`
- `performance_risks`
- `driver_family_templates`
- `domain_priors`
- `operator_preferences`

Do not invent a separate suggestion schema disconnected from the pack schema.

`target_pack_id` rule:

- if the relevant context pack already exists, reuse that pack's `pack_id`
- if no context pack exists yet, create `target_pack_id` with a deterministic slug

Deterministic slug guidance:

- start from the most stable business label available, such as a context label, platform label, or business object label
- normalize to lowercase ASCII snake_case
- replace spaces and punctuation with underscores
- collapse repeated underscores
- trim leading and trailing underscores
- use the same source label for the same business context so future sessions regenerate the same slug
