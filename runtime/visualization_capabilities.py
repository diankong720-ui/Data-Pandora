from __future__ import annotations

from typing import Any


RENDER_ENGINE_ID = "matplotlib_v1"
PREFERRED_CHART_TYPES = (
    "line",
    "bar",
    "horizontal_bar",
    "scatter",
)
ADDITIONAL_CHART_TYPES = (
    "area",
    "histogram",
    "box",
    "heatmap",
)
SUPPORTED_CHART_TYPES = PREFERRED_CHART_TYPES + ADDITIONAL_CHART_TYPES


def get_visualization_capabilities() -> dict[str, Any]:
    """
    Return the renderer capabilities that upstream producers should target.

    This is the single runtime-owned declaration of what chart types
    the current renderer can accept. Skill docs and contract validation should
    stay aligned with this surface.
    """
    return {
        "render_engine": RENDER_ENGINE_ID,
        "preferred_chart_types": list(PREFERRED_CHART_TYPES),
        "additional_chart_types": list(ADDITIONAL_CHART_TYPES),
        "supported_chart_types": list(SUPPORTED_CHART_TYPES),
        "supports_freeform_renderer_hint": True,
        "requires_explicit_plot_data": True,
        "requires_explicit_plot_spec": True,
        "renderer_backend": "matplotlib.Agg",
        "auto_installs_missing_renderer": True,
    }
