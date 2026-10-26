"""提供 M4 模块的通用工具函数"""

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
    """加载资产清单列表，支持两种运行模式。

    1. 增量模式: 提供 input_dirs 时自动调用 M1 InventoryBatchScan 扫描目录，
       实时生成 assets.json:扫描失败则返回空列表。
    2. 常规模式: 直接读取已存在的资产清单文件，路径优先级为
       params["assets_file"] > ctx.outputs_dir/assets.json。

    读取时使用 read_text_auto 兼容 UTF-8 / GBK 等多种编码，
    JSON 解析后取 "assets" 键；键不存在时返回空列表作为兜底。
    """
    # 增量模式: 用户传了 input_dirs → 现场触发 M1 扫描生成资产清单
    if params.get("input_dirs"):
        from dtdpgis.modules.m1.inventory import InventoryBatchScan

        inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"]})
        if inventory_result.status != "success":
            return []                               # 扫描失败直接返回空，下游不会报错
        assets_file = ctx.outputs_dir / "assets.json"
    else:
        # 常规模式: 直接读文件，优先级 params 指定路径 > 默认路径
        assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")
    # read_text_auto 自动识别 UTF-8 / GBK 编码，避免中文路径下 JSON 解析失败
    data = json.loads(read_text_auto(assets_file))
    return data.get("assets", [])                  # 键缺失兜底空列表，防御性编程


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
    """根据资产清单项，返回其原始路径。
    优先使用 source_path 键，若不存在则根据 relative_path 键查找。
    若仍不存在则返回项目根目录下的相对路径。
    """
    if asset.get("source_path"):
        candidate = Path(asset["source_path"])
        if candidate.exists():
            return candidate
    relative = Path(asset["relative_path"])
    for candidate in (relative, ctx.project_root / relative, ctx.project_root / "inputs" / relative):
        if candidate.exists():
            return candidate
    return ctx.project_root / relative


def resolve_raster_inputs(ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """根据资产清单项，解析栅格输入路径。
    1. 从资产清单中筛选出栅格资产。
    2. 读取 reproject_manifest.csv 文件，根据 asset_id 查找 reproject 记录。
    3. 读取 crs_audit.csv 文件，根据 asset_id 查找 audit 记录。
    4. 读取 quarantine_manifest.csv 文件，根据 asset_id 查找 quarantine 记录。
    5. 遍历栅格资产，根据 reproject 记录和 audit 记录，解析输入路径。
    """
    assets = [a for a in load_assets(ctx, params) if a.get("category") == "raster"]
    reproject_rows = read_csv_rows(Path(params.get("raster_manifest") or ctx.outputs_dir / "raster_reproject_manifest.csv"))
    reproject_by_asset = {row["asset_id"]: row for row in reproject_rows if row.get("asset_id")}
    audit_rows = read_csv_rows(ctx.outputs_dir / "crs_audit.csv")
    fail_by_asset: dict[str, set[str]] = {}
    for row in audit_rows:
        if row.get("level") == "fail":
            fail_by_asset.setdefault(row["asset_id"], set()).add(row["rule_id"])
    quarantine_rows = read_csv_rows(ctx.outputs_dir / "quarantine_manifest.csv")
    quarantined = {row["asset_id"] for row in quarantine_rows if row.get("asset_id")}

    resolved: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = asset["asset_id"]
        if asset_id in quarantined:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m1_quarantined"})
            continue
        reproject = reproject_by_asset.get(asset_id)
        if reproject and reproject.get("status") == "success" and reproject.get("output_path"):
            stage = "m2_reprojected"
            if any(row.get("rule_id") == "CRS_ASSUMED_BY_CONFIG" and row.get("asset_id") == asset_id for row in audit_rows):
                stage = "m2_assumed_crs"
            resolved.append({"asset": asset, "input_path": Path(reproject["output_path"]), "source_stage": stage, "skip_reason": ""})
            continue
        if fail_by_asset.get(asset_id):
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m2_audit_fail"})
            continue
        resolved.append(
            {"asset": asset, "input_path": source_path_from_asset(ctx, asset), "source_stage": "m1_raw", "skip_reason": ""}
        )
    return resolved


def raster_type_for_asset(ctx: Any, asset: dict[str, Any]) -> tuple[str, bool]:
    """根据资产清单项，返回其栅格类型和是否需要转换。
    1. 从资产清单中查找 asset_id 对应的配置项。
    2. 如果配置项中包含 asset_id:则返回 asset_id 对应的栅格类型和 False。
    3. 如果配置项中包含 patterns 键，则根据 relative_path 匹配 patterns 中的模式，返回匹配到的栅格类型和 False。
    4. 如果 relative_path 包含 dem 或 dsm,则返回 dem 和 False。
    5. 如果 relative_path 包含 dom 或 tdom,则返回 dom 和 False。
    6. 如果 relative_path 包含 classification、landuse 或 lulc,则返回 classification 和 False。
    7. 否则返回 dem 和 True。
    """
    config = ctx.profile.get("m4", {}).get("raster_types", {})
    by_asset = config.get("by_asset_id", {})
    asset_id = asset["asset_id"]
    if asset_id in by_asset:
        return str(by_asset[asset_id]).lower(), False
    relative = asset["relative_path"].lower()
    for pattern, raster_type in (config.get("patterns") or {}).items():
        if pattern.lower() in relative:
            return str(raster_type).lower(), False
    if "dem" in relative or "dsm" in relative:
        return "dem", False
    if "dom" in relative or "tdom" in relative:
        return "dom", False
    if "classification" in relative or "landuse" in relative or "lulc" in relative:
        return "classification", False
    return "dem", True


def output_path_for_asset(ctx: Any, directory: str, asset: dict[str, Any]) -> Path:
    """根据资产清单项，返回其输出路径。
    1. 从资产清单中查找 asset_id 对应的配置项。
    2. 如果配置项中包含 output_path:则返回 output_path 键的值。
    3. 否则根据 relative_path 键生成输出路径。
    """
    config = ctx.profile.get("m4", {}).get("raster_types", {})
    by_asset = config.get("by_asset_id", {})
    asset_id = asset["asset_id"]
    if asset_id in by_asset:
        return Path(by_asset[asset_id])
    relative = asset["relative_path"]
    stem = Path(asset["relative_path"]).stem
    return ctx.outputs_dir / directory / f"{asset['asset_id']}_{stem}.tif"