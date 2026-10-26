"""工具注册与发现模块工具。"""

from __future__ import annotations

from collections.abc import Iterable# 可迭代对象

from dtdpgis.core.tools import BaseTool# 基础工具类

TOOL_REGISTRY: dict[str, type[BaseTool]] = {}


def register(tool_cls: type[BaseTool]) -> type[BaseTool]:
    """注册工具类。"""
    spec = getattr(tool_cls, "spec", None)
    if spec is None:
        raise TypeError(f"{tool_cls.__name__} is missing a ToolSpec")# 工具类必须有ToolSpec属性
    TOOL_REGISTRY[spec.id] = tool_cls# 注册工具类
    return tool_cls


def get_tool(tool_id: str) -> type[BaseTool]:
    try:
        return TOOL_REGISTRY[tool_id]
    except KeyError as exc:
        raise KeyError(f"unknown tool: {tool_id}") from exc


def discover_tools(
    module_names: Iterable[str] = (
        "dtdpgis.modules.m1.tools",
        "dtdpgis.modules.m2.tools",
        "dtdpgis.modules.m3.tools",
        "dtdpgis.modules.m4.tools",
        "dtdpgis.modules.m5.tools",
        "dtdpgis.modules.m6.tools",
    ),
) -> None:
    import importlib

    for module_name in module_names:
        importlib.import_module(module_name)
