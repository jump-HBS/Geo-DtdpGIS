"""基础工具类，定义了工具接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from dtdpgis.core.models import RunContext, ToolResult, ToolSpec


class BaseTool(ABC): # ← ABC = Abstract Base Class，抽象基类，本身不应该被实例化，只用来当"模板"
    spec: ClassVar[ToolSpec]# 工具规格

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        """作用：模拟运行工具，返回结果。"""
        return {"tool_id": self.spec.id, "note": "dry-run is not implemented for this tool"}

    @abstractmethod#强制所有子类必须重写这个方法，否则子类也不能实例化
    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """作用：执行工具，返回结果。"""
