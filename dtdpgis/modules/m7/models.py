"""插件模型"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PluginSpec(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    backend_type: str
    provider: str = "custom"
    python_executable: str | None = None
    entry_command: list[str]
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 300
    health_check: dict[str, Any] = Field(default_factory=dict)
    connection: dict[str, Any] = Field(default_factory=dict)


class PluginExecutionResult(BaseModel):
    plugin_id: str
    status: str
    output_dir: Path
    message: str = ""
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
