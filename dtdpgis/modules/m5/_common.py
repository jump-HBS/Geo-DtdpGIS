"""M5 工具共享帮助函数"""

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
    """加载资产清单，支持两种模式：

    1. 增量扫描模式(input_dirs 存在):
       直接调用 M1 的 InventoryBatchScan 对指定目录重新扫描，
       实时生成 assets.json:适合 M5 工具被用户单独调起时
       （此时没有前置流水线产出的 assets.json 可复用）。

    2. 常规读取模式(input_dirs 不存在):
       直接读取已有的 assets.json 文件，优先从 params["assets_file"]
       指定路径取，没指定则回退到 ctx.outputs_dir / "assets.json"。

    两模式最终都返回同一结构:list[dict]，每个 dict 是一个资产条目。
    """
    # ---------- 模式 1：增量扫描 ----------
    # 如果调用方显式传入 input_dirs，说明希望绕过上游流水线直接扫描
    # 延迟导入 InventoryBatchScan 避免未使用 M1 时加载其依赖
    if params.get("input_dirs"):
        from dtdpgis.modules.m1.inventory import InventoryBatchScan

        # 直接调用 M1 扫描，扫描结果会自动写入 ctx.outputs_dir / "assets.json"
        inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"]})
        # 扫描失败则返回空列表，后续流程会全部跳过
        if inventory_result.status != "success":
            return []
        # 扫描产出的 assets.json 固定路径就是 outputs_dir / "assets.json"
        assets_file = ctx.outputs_dir / "assets.json"

    # ---------- 模式 2：常规读取 ----------
    else:
        # 优先用调用方指定的 assets_file 路径，否则用默认路径
        assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")

    # ---------- 统一解析 ----------
    # read_text_auto 自动检测编码（utf-8/gbk/gb2312 等），避免中文路径/文件名乱码
    data = json.loads(read_text_auto(assets_file))
    # 兼容缺失 assets 键的异常文件，返回空列表兜底
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
    if asset.get("source_path"):
        candidate = Path(asset["source_path"])
        if candidate.exists():
            return candidate
    relative = Path(asset["relative_path"])
    for candidate in (relative, ctx.project_root / relative, ctx.project_root / "inputs" / relative):
        if candidate.exists():
            return candidate
    return ctx.project_root / relative


def audit_fail_assets(ctx: Any) -> set[str]:
    """从 crs_audit.csv 中提取失败的资产 ID"""
    rows = read_csv_rows(ctx.outputs_dir / "crs_audit.csv")
    return {row["asset_id"] for row in rows if row.get("level") == "fail"}


def quarantined_assets(ctx: Any) -> set[str]:
    """从 quarantine_manifest.csv 中提取被隔离的资产 ID"""
    rows = read_csv_rows(ctx.outputs_dir / "quarantine_manifest.csv")
    return {row["asset_id"] for row in rows if row.get("asset_id")}


def source_stage(ctx: Any, asset_id: str) -> str:
    """根据资产 ID 查看其来源阶段"""
    rows = read_csv_rows(ctx.outputs_dir / "crs_audit.csv")
    if any(row.get("asset_id") == asset_id and row.get("rule_id") == "CRS_ASSUMED_BY_CONFIG" for row in rows):
        return "m2_assumed_crs"
    return "m2_reprojected" if not any(row.get("asset_id") == asset_id and row.get("level") == "fail" for row in rows) else "skipped"


def resolve_pointcloud_inputs(ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """解析点云资产的输入路径"""
    assets = [a for a in load_assets(ctx, params) if a.get("category") == "pointcloud"]
    quarantined = quarantined_assets(ctx)# 从 quarantine_manifest.csv 中提取被隔离的资产 ID
    fails = audit_fail_assets(ctx)# 从 crs_audit.csv 中提取失败的资产 ID
    resolved = []# 解析后的资产输入路径
    for asset in assets:
        asset_id = asset["asset_id"]
        if asset_id in quarantined:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m1_quarantined"})
        elif asset_id in fails:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m2_audit_fail"})
        else:
            resolved.append({"asset": asset, "input_path": source_path_from_asset(ctx, asset), "source_stage": source_stage(ctx, asset_id), "skip_reason": ""})
    return resolved


def resolve_oblique_inputs(ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """解析倾斜摄影资产的输入路径"""
    assets = [a for a in load_assets(ctx, params) if a.get("category") == "oblique"]
    quarantined = quarantined_assets(ctx)
    fails = audit_fail_assets(ctx)
    resolved = []
    for asset in assets:
        asset_id = asset["asset_id"]
        if asset_id in quarantined:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m1_quarantined"})
        elif asset_id in fails:
            resolved.append({"asset": asset, "input_path": None, "source_stage": "skipped", "skip_reason": "m2_audit_fail"})
        else:
            resolved.append({"asset": asset, "input_path": source_path_from_asset(ctx, asset), "source_stage": source_stage(ctx, asset_id), "skip_reason": ""})
    return resolved