"""点云文件轻量级统计工具。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from dtdpgis.core.encoding import read_text_auto

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register# 导入工具注册装饰器
from dtdpgis.core.tools import BaseTool# 导入基础工具类
from dtdpgis.modules.m1.inventory import InventoryBatchScan# 导入批量扫描工具类


def _load_assets(ctx: RunContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """加载资产文件中的资产列表。"""
    assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")
    data = json.loads(read_text_auto(assets_file))#反序列化JSON字符串为Python字典
    return data.get("assets", [])#返回资产列表，默认值为空列表


def _read_point_array(path: Path) -> np.ndarray:
    """读取点云文件中的点数组。"""
    from dtdpgis._env import ensure_runtime_env

    ensure_runtime_env()
    import pdal

    pipeline = pdal.Pipeline(json=json.dumps([{"type": "readers.las", "filename": str(path)}]))# 创建PDAL管道，读取点云文件
    pipeline.execute()# 执行PDAL管道，读取点云文件
    return pipeline.arrays[0]# 返回点数组，包含点云文件中的所有点


def _crs_from_quickinfo(path: Path) -> dict[str, Any] | None:
    """从点云文件的快速信息中提取CRS信息。"""
    from dtdpgis._env import ensure_runtime_env

    ensure_runtime_env()
    import pdal

    pipeline = pdal.Pipeline(json=json.dumps([{"type": "readers.las", "filename": str(path)}]))# 创建PDAL管道，读取点云文件
    info = pipeline.quickinfo.get("readers.las", {})# 获取点云文件的快速信息
    srs_json = (info.get("srs") or {}).get("json") or {}# 获取点云文件的CRS信息
    crs_id = srs_json.get("id") or {}# 获取点云文件的CRS ID
    if crs_id.get("authority") and crs_id.get("code") is not None:# 检查CRS ID是否包含EPSG代码
        return {"authority": crs_id["authority"], "code": int(crs_id["code"])}# 返回CRS ID，包含EPSG代码
    return None


def _median_spacing(x: np.ndarray, y: np.ndarray, sample_size: int) -> float | None:
    """计算点云文件中点之间的中位数间距。
    中位数点间距是点云的"密度体温计"，
    帮你快速判断这份点云够不够密、有没有飞漏、
    能不能支撑后续的 DEM/建筑/电力线等下游处理。"""
    from scipy.spatial import cKDTree   # 导入Scipy的cKDTree类，用于快速查询最近邻

    if len(x) < 2:# 检查点云文件是否包含至少两个点
        return None
    if len(x) > sample_size:# 检查点云文件是否包含超过样本大小的点
        rng = np.random.default_rng(0)# 创建随机数生成器，用于随机选择样本点
        indices = rng.choice(len(x), size=sample_size, replace=False)# 随机选择样本点的索引
        x = x[indices]# 提取样本点的X坐标
        y = y[indices]# 提取样本点的Y坐标
    points = np.column_stack([x, y])# 将X坐标和Y坐标按列堆叠，形成点数组
    distances, _ = cKDTree(points).query(points, k=2)# 查询每个点的最近邻，返回距离和索引
    return float(np.median(distances[:, 1]))# 返回中位数间距，即每个点到最近邻的距离的中位数


def _stats_for_file(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    """计算点云文件的统计信息。"""
    sample_size = int(config.get("sample_size", 200000))# 获取样本大小，默认值为200000
    sentinel_z = set(config.get("sentinel_z", []))# 获取哨兵Z值，默认值为空集
    try:
        array = _read_point_array(path)# 读取点云文件中的点数组
    except Exception as exc:  
        return {"readable": False, "error": str(exc)}

    x = array["X"].astype(np.float64)# 提取点云文件中的X坐标，转换为浮点数数组
    y = array["Y"].astype(np.float64)# 提取点云文件中的Y坐标，转换为浮点数数组
    z = array["Z"].astype(np.float64)# 提取点云文件中的Z坐标，转换为浮点数数组
    # 提取点云文件中的分类，转换为整数数组
    classification = array["Classification"].astype(np.int64) if "Classification" in array.dtype.names else np.zeros(len(x), dtype=np.int64)
    # 统计每个分类的点数
    class_counts = {int(code): int(count) for code, count in zip(*np.unique(classification, return_counts=True))}
    # 提取点云文件中的哨兵Z值，转换为整数数组
    sentinel_hits = [int(v) for v in np.unique(z) if int(v) in sentinel_z]
    spacing = _median_spacing(x, y, sample_size)# 计算点云文件中点之间的中位数间距
    crs = _crs_from_quickinfo(path)# 从点云文件的快速信息中提取CRS信息
    return {
        "readable": True,                      # 点云文件可读
        "point_count": int(len(x)),            # 点云文件中的点数
        "z_min": float(np.min(z)),             # 点云文件中的最小Z值
        "z_max": float(np.max(z)),             # 点云文件中的最大Z值
        "z_p01": float(np.percentile(z, 1)),   # 点云文件中的第1分位数Z值
        "z_p05": float(np.percentile(z, 5)),   # 点云文件中的第5分位数Z值
        "z_p50": float(np.percentile(z, 50)),  # 点云文件中的第50分位数Z值
        "z_p95": float(np.percentile(z, 95)),  # 点云文件中的第95分位数Z值
        "z_p99": float(np.percentile(z, 99)),  # 点云文件中的第99分位数Z值
        "class_count": len(class_counts),      # 点云文件中的分类数
        "class_distribution": json.dumps(class_counts, ensure_ascii=False),
        "avg_spacing": spacing,                # 点云文件中点之间的中位数间距
        "crs": crs,                            # 点云文件的CRS信息    
        "sentinel_z": sentinel_hits,           # 点云文件中的哨兵Z值
        "error": "",                           # 点云文件的错误信息
    }


@register# 注册点云轻量统计工具
class PointcloudBatchStats(BaseTool):
    spec = ToolSpec(
        id="pointcloud.batch_stats",
        name="点云轻量统计",
        module="m1",
        description="批量统计 LAS/LAZ 点数、Z 分位、分类分布、点间距和 CRS。",
        params={"input_dirs": {"type": "list[path]", "title": "输入数据目录", "required": False}},
        inputs=["input_dirs"],
        outputs=["pointcloud_stats.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("input_dirs"):
            return {"mode": "scan_and_stats", "preview": InventoryBatchScan().dry_run(ctx, params)}
        assets = _load_assets(ctx, params)
        pointclouds = [a for a in assets if a.get("category") == "pointcloud" and a.get("format") in {"LAS", "LAZ"}]
        return {"pointcloud_assets": len(pointclouds), "note": "dry-run does not read point arrays"}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行点云轻量统计工具。"""
        if params.get("input_dirs"):
            # 扫描输入目录中的点云文件
            inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"]})
            if inventory_result.status == "failed":# 如果扫描失败，直接返回失败结果
                return inventory_result
            params = dict(params)# 复制参数，避免修改原始参数
            params["assets_file"] = str(ctx.outputs_dir / "assets.json")# 设置资产文件路径
        assets = _load_assets(ctx, params)# 加载资产文件
        # 筛选出点云文件
        pointclouds = [a for a in assets if a.get("category") == "pointcloud" and a.get("format") in {"LAS", "LAZ"}]
        config = ctx.profile.get("m1", {}).get("pointcloud_stats", {})# 获取配置参数，默认值为空字典
        rows: list[dict[str, Any]] = []# 统计结果列表
        items: list[ItemResult] = []# 质量控制结果列表

        for asset in pointclouds:# 遍历点云文件
            path = self._source_path(ctx, asset)# 获取点云文件的绝对路径
            stats = _stats_for_file(path, config)# 统计点云文件的轻量信息
            row = {
                "run_id": ctx.run_id,
                "asset_id": asset["asset_id"],
                "relative_path": asset["relative_path"],
                **stats,
            }
            rows.append(row)
            if not stats.get("readable"):# 如果点云文件不可读
                # 记录质量控制结果
                items.append(ItemResult(asset_id=asset["asset_id"], level=QCLevel.FAIL, message=stats.get("error", "读取失败"), details={"relative_path": asset["relative_path"], "format": asset.get("format")}))
            elif stats.get("sentinel_z"):# 如果点云文件中存在哨兵Z值
                items.append(
                    ItemResult(
                        asset_id=asset["asset_id"],
                        level=QCLevel.WARN,# 警告等级
                        message=f"发现疑似异常 Z 值 {stats['sentinel_z']}",# 警告信息
                        details={"relative_path": asset["relative_path"], "format": asset.get("format")},# 警告详情
                    )
                )
            else:
                items.append(ItemResult(asset_id=asset["asset_id"], level=QCLevel.PASS, message=f"统计完成，点数：{stats.get('point_count')}", details={"relative_path": asset["relative_path"], "format": asset.get("format")}))

        output_path = ctx.outputs_dir / "pointcloud_stats.csv"
        fieldnames = [
            "run_id",
            "asset_id",
            "relative_path",
            "readable",
            "point_count",
            "z_min",
            "z_max",
            "z_p01",
            "z_p05",
            "z_p50",
            "z_p95",
            "z_p99",
            "class_count",
            "class_distribution",
            "avg_spacing",
            "crs",
            "sentinel_z",
            "error",
        ]
        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        return ToolResult(
            tool_id=self.spec.id,
            run_id=ctx.run_id,
            items=items,
            outputs=[str(output_path)],
            summary={"pointcloud_assets": len(pointclouds)},
        )

    def _source_path(self, ctx: RunContext, asset: dict[str, Any]) -> Path:
        """获取点云文件的绝对路径。"""
        relative_path = asset["relative_path"]# 获取点云文件的相对路径
        # 构建候选路径列表
        candidates = [Path(asset["source_path"]) if asset.get("source_path") else None] + [
            Path(relative_path),
            ctx.project_root / relative_path,
            ctx.project_root / "inputs" / relative_path,
        ]
        # 过滤出存在的路径
        candidates = [candidate for candidate in candidates if candidate is not None]
        for candidate in candidates:
            if candidate.exists():
                return candidate# 返回第一个存在的路径
        return candidates[0]
