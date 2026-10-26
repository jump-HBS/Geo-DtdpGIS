"""M3工具的共享帮助函数"""

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
    """加载资产清单，返回资产字典列表。

    两种路径:
    1. 若提供了 input_dirs → 自动调用 M1 资产扫描生成 assets.json,失败则返回空列表
    2. 否则直接读取已存在的 assets_file (显式指定或默认 ctx.outputs_dir/assets.json)
    """
    if params.get("input_dirs"):
        # 增量模式: 先触发 M1 InventoryBatchScan 扫描目录，生成/刷新 assets.json
        from dtdpgis.modules.m1.inventory import InventoryBatchScan

        inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"]})
        # 扫描失败则放弃，返回空列表让调用方自行处理
        if inventory_result.status != "success":
            return []
        # 扫描成功后 assets.json 已被写入 outputs_dir
        assets_file = ctx.outputs_dir / "assets.json"
    else:
        # 常规模式: 直接读取已有资产文件 (优先显式传入路径，回退默认位置)
        assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")
    # 用 read_text_auto 兼容 UTF-8 / GBK 等多种编码，再解析 JSON
    data = json.loads(read_text_auto(assets_file))
    # 取 "assets" 列表，键缺失时安全返回空列表
    return data.get("assets", [])


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """读取CSV文件,返回字典列表，每个字典对应一行数据，键为列名，值为单元格值"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    """写入CSV文件,根据fieldnames和rows生成CSV文件"""
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_path_from_asset(ctx: Any, asset: dict[str, Any]) -> Path:
    """根据资产字典，返回资产的原始路径"""
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
    """根据资产清单和重投影清单，返回矢量输入路径列表"""
    assets = [a for a in load_assets(ctx, params) if a.get("category") == "vector"]# 筛选出矢量资产
    reproject_rows = read_csv_rows(Path(params.get("vector_manifest") or ctx.outputs_dir / "vector_reproject_manifest.csv"))# 读取重投影清单
    reproject_by_asset = {row["asset_id"]: row for row in reproject_rows if row.get("asset_id")}# 按资产ID索引重投影清单
    audit_rows = read_csv_rows(ctx.outputs_dir / "crs_audit.csv")# 读取CRS审核清单
    fail_by_asset: dict[str, set[str]] = {}# 按资产ID索引CRS审核失败规则
    for row in audit_rows:
        if row.get("level") == "fail":# 筛选出CRS审核失败的资产
            fail_by_asset.setdefault(row["asset_id"], set()).add(row["rule_id"])# 紛集CRS审核失败规则
    quarantine_rows = read_csv_rows(ctx.outputs_dir / "quarantine_manifest.csv")# 读取隔离清单清单
    quarantined = {row["asset_id"] for row in quarantine_rows if row.get("asset_id")}# 紛集隔离清单资产ID

    resolved: list[dict[str, Any]] = []# 初始化已解决资产列表
    # 遍历所有矢量资产
    for asset in assets:
        asset_id = asset["asset_id"]
        if asset_id in quarantined:# 筛选出隔离清单资产
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m1_quarantined", "layer": None})# 加入已解决资产列表
            continue
        reproject = reproject_by_asset.get(asset_id)# 获取重投影清单
        if reproject and reproject.get("status") == "success" and reproject.get("output_path"):  # 筛选出重投影成功的资产
            stage = "m2_reprojected"
            if any(row.get("rule_id") == "CRS_ASSUMED_BY_CONFIG" and row.get("asset_id") == asset_id for row in audit_rows):# 筛选出CRS审核通过的资产
                stage = "m2_assumed_crs"# 设置阶段为CRS审核通过
            resolved.append(
                {
                    "asset": asset,
                    "input_path": Path(reproject["output_path"]),
                    "source_stage": stage,
                    "skip_reason": "",
                    "layer": reproject.get("layer") or None,
                }
            )
            continue
        if fail_by_asset.get(asset_id):# 筛选出CRS审核失败的资产
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m2_audit_fail", "layer": None})# 加入已解决资产列表
            continue
        resolved.append(
            {
                "asset": asset,
                "input_path": source_path_from_asset(ctx, asset),
                "source_stage": "m1_raw",
                "skip_reason": "",
                "layer": None,
            }
        )
    return resolved


def output_path_for_asset(ctx: Any, directory: str, asset: dict[str, Any]) -> Path:
    """根据资产字典，返回资产的输出路径"""
    stem = Path(asset["relative_path"]).stem
    return ctx.outputs_dir / directory / f"{asset['asset_id']}_{stem}.gpkg"
