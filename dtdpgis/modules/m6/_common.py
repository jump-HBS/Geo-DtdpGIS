"""M6工具的共享帮助函数"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from dtdpgis.core.encoding import read_text_auto


QC_FIELDS = [
    "run_id",
    "asset_id",
    "category",
    "format",
    "relative_path",
    "rule_id",
    "level",
    "message",
    "expected",
    "actual",
]


def load_assets(ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """加载资产列表"""
    if params.get("input_dirs"):
        from dtdpgis.modules.m1.inventory import InventoryBatchScan

        inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"]})
        if inventory_result.status != "success":
            return []
        assets_file = ctx.outputs_dir / "assets.json"
    else:
        assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")
    data = json.loads(read_text_auto(assets_file))
    return data.get("assets", [])


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_path_from_asset(ctx: Any, asset: dict[str, Any]) -> Path:
    """从资产中获取源路径"""
    if asset.get("source_path"):
        candidate = Path(asset["source_path"])
        if candidate.exists():
            return candidate
    relative = Path(asset["relative_path"])
    for candidate in (relative, ctx.project_root / relative, ctx.project_root / "inputs" / relative):
        if candidate.exists():
            return candidate
    return ctx.project_root / relative


def resolve_vector_inputs(ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """解析矢量输入资产"""
    assets = [a for a in load_assets(ctx, params) if a.get("category") == "vector"]
    normalized = {row["asset_id"]: row for row in read_csv_rows(ctx.outputs_dir / "attribute_normalize_manifest.csv") if row.get("asset_id") and row.get("status") in {"pass", "warn"} and row.get("output_path")}
    repaired = {row["asset_id"]: row for row in read_csv_rows(ctx.outputs_dir / "topology_repair_manifest.csv") if row.get("asset_id") and row.get("status") in {"pass", "partial_success"} and row.get("output_path")}
    reprojected = {row["asset_id"]: row for row in read_csv_rows(ctx.outputs_dir / "vector_reproject_manifest.csv") if row.get("asset_id") and row.get("status") == "success" and row.get("output_path")}
    audit_fail = {row["asset_id"] for row in read_csv_rows(ctx.outputs_dir / "crs_audit.csv") if row.get("level") == "fail"}
    quarantined = {row["asset_id"] for row in read_csv_rows(ctx.outputs_dir / "quarantine_manifest.csv") if row.get("asset_id")}

    resolved: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = asset["asset_id"]
        if asset_id in quarantined:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m1_quarantined", "layer": None})
            continue
        if asset_id in normalized:
            row = normalized[asset_id]
            resolved.append({"asset": asset, "input_path": Path(row["output_path"]), "source_stage": "m3_normalized", "skip_reason": "", "layer": row.get("layer") or None})
            continue
        if asset_id in repaired:
            row = repaired[asset_id]
            resolved.append({"asset": asset, "input_path": Path(row["output_path"]), "source_stage": "m3_repaired", "skip_reason": "", "layer": row.get("layer") or None})
            continue
        if asset_id in reprojected and asset_id not in audit_fail:
            row = reprojected[asset_id]
            resolved.append({"asset": asset, "input_path": Path(row["output_path"]), "source_stage": "m2_reprojected", "skip_reason": "", "layer": row.get("layer") or None})
            continue
        if asset_id in audit_fail:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m2_audit_fail", "layer": None})
            continue
        resolved.append({"asset": asset, "input_path": source_path_from_asset(ctx, asset), "source_stage": "m1_raw", "skip_reason": "", "layer": None})
    return resolved


def role_matches(asset: dict[str, Any], role_config: dict[str, Any] | None) -> bool:
    """检查资产是否匹配角色配置"""
    if not role_config:
        return False
    asset_id = role_config.get("asset_id")
    layer = role_config.get("layer")
    if asset_id and asset.get("asset_id") == asset_id:
        return True
    if layer:
        stem = Path(asset.get("relative_path", "")).stem
        if stem == layer or layer in asset.get("relative_path", ""):
            return True
    return False
