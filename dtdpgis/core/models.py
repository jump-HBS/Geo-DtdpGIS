"""工具和运行状态的核心数据模型。"""

from __future__ import annotations

from enum import Enum# 枚举类型
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QCLevel(str, Enum):
    """质量控制级别。"""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class ToolSpec(BaseModel):
    """工具规格。"""
    id: str
    name: str
    module: str
    description: str
    version: str = "1.0"
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)


class ItemResult(BaseModel):
    """项目结果。"""
    asset_id: str
    level: QCLevel
    message: str
    rule_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具结果。"""
    tool_id: str
    run_id: str
    status: str = "success"
    items: list[ItemResult] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RunContext(BaseModel):
    """运行上下文。"""
    run_id: str
    project_root: Path
    outputs_dir: Path
    quarantine_dir: Path
    reports_dir: Path
    state_db: Path
    profile: dict[str, Any] = Field(default_factory=dict)


class Asset(BaseModel):
    """资产。"""
    asset_id: str
    category: str
    format: str
    relative_path: str
    source_path: str | None = None
    source: str | None = None
    batch: str | None = None
    source_name: str | None = None
    purpose: str | None = None
    file_count: int = 1
    size_bytes: int = 0
    sha256: str | None = None
    mtime: str | None = None
    crs: dict[str, Any] | None = None
    bounds: list[float] | None = None
    geom_type: str | None = None
    feature_count: int | None = None
    raster: dict[str, Any] | None = None
    pointcloud: dict[str, Any] | None = None
    oblique: dict[str, Any] | None = None
    sidecars: dict[str, Any] | None = None
    status: str = "scanned"
    errors: list[str] = Field(default_factory=list)
