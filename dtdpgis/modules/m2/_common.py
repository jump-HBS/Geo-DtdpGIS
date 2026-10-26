"""Shared helpers for M2 tools."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable    #Iterable用于表示可以迭代的对象，如列表、元组、集合等
from dtdpgis.core.encoding import read_text_auto#用于自动检测文件编码并读取文件内容的函数


def ensure_assets_file(ctx: Any, params: dict[str, Any]):
    """确保资产文件存在，返回资产文件路径和执行结果"""
    if params.get("input_dirs"):
        # 如果指定了输入目录，执行资产扫描
        from dtdpgis.modules.m1.inventory import InventoryBatchScan
        # 执行资产扫描
        inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"]})
        if inventory_result.status != "success":#如果资产扫描失败，返回None和执行结果
            return None, inventory_result
        return ctx.outputs_dir / "assets.json", None#如果资产扫描成功，返回资产文件路径和None
    assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")#如果没有指定资产文件路径，默认使用输出目录下的assets.json文件
    return assets_file, None


def load_assets(ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """加载资产文件，返回资产列表"""
    assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")
    data = json.loads(read_text_auto(assets_file))#读取资产文件内容并解析为JSON对象
    return data.get("assets", [])#从JSON对象中提取资产列表，默认值为空列表


def asset_source_path(ctx: Any, asset: dict[str, Any]) -> Path:
    """根据资产对象，返回资产的源路径"""
    if asset.get("source_path"):
        candidate = Path(asset["source_path"])
        if candidate.exists():
            return candidate
    relative = Path(asset["relative_path"])
    for candidate in (relative, ctx.project_root / relative, ctx.project_root / "inputs" / relative):
        # 遍历可能的源路径，包括资产相对路径、项目根目录下的相对路径、项目根目录下的inputs目录下的相对路径
        if candidate.exists():
            return candidate
    return ctx.project_root / relative


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    """将数据写入CSV文件"""
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """读取CSV文件，返回数据行列表"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def aggregate_level(levels: Iterable[str]) -> str:
    """根据多个等级，返回聚合后的等级"""
    values = list(levels)
    if "fail" in values:
        return "fail"
    if "warn" in values:
        return "warn"
    return "pass"
