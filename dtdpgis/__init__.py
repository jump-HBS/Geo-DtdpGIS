"""DtdpGIS package entry point."""

from dtdpgis._env import configure_runtime

configure_runtime()

from dtdpgis.core.registry import TOOL_REGISTRY, discover_tools  # noqa: E402

discover_tools()

__all__ = ["TOOL_REGISTRY"]
