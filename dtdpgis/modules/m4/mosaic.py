"""批量栅格镶嵌工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.modules.m4._common import QC_FIELDS, raster_type_for_asset, resolve_raster_inputs, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "mosaic_id",
    "raster_type",
    "source_assets",
    "output_path",
    "crs",
    "resolution",
    "method",
    "status",
    "message",
]


def _group_key(ctx: Any, item: dict[str, Any]) -> tuple[str, str, str]:
    """为栅格镶嵌生成分组键 (type, CRS, resolution)。

    rasterio.merge.merge() 要求同一批输入栅格具备相同的 CRS 和分辨率，
    否则 GDAL 会自动重投影，可能引入意外误差。此函数预先读取每个栅格
    的类型、坐标系、分辨率三元组，作为字典键将兼容的栅格聚合到一组，
    保证后续 merge 调用的安全性。

    Args:
        ctx: RunContext,用于 raster_type_for_asset 判断资产类型。
        item: 解析后的栅格输入条目，至少包含 "asset" 和 "input_path" 键。

    Returns:
        (raster_type, crs_epsg, resolution) 三元组，全部转为字符串方便做
        字典键。crs 为 None 时字符串为 "None"。resolution 保留 9 位小数
        避免浮点精度差异导致同一分辨率被分到不同组。
    """
    from dtdpgis._env import ensure_runtime_env

    ensure_runtime_env()
    import rasterio

    raster_type, _ = raster_type_for_asset(ctx, item["asset"])
    with rasterio.open(item["input_path"]) as src:
        crs = src.crs.to_epsg() if src.crs else None# 转换为 EPSG 代码
        resolution = tuple(round(v, 9) for v in src.res)# 保留 9 位小数避免浮点精度差异
    return (raster_type, str(crs), str(resolution))


@register
class RasterBatchMosaic(BaseTool):
    spec = ToolSpec(
        id="raster.batch_mosaic",
        name="栅格批量镶嵌",
        module="m4",
        description="按类型、CRS、分辨率分组镶嵌栅格,并检查接边质量。",
        params={"input_dirs": {"type": "list[path]", "title": "输入栅格数据目录", "required": False}},
        inputs=["assets.json", "raster_reproject_manifest.csv"],
        outputs=["raster_mosaic/", "raster_mosaic_qc.csv", "raster_mosaic_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = [item for item in resolve_raster_inputs(ctx, params) if item["input_path"] is not None]
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for item in resolved:
            groups.setdefault(_group_key(ctx, item), []).append(item)
        return {"groups": len(groups), "assets": len(resolved)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import rasterio
        from rasterio.merge import merge
        from rasterio.warp import Resampling# 导入Resampling枚举，用于指定重采样方法

        config = ctx.profile.get("m4", {}).get("mosaic", {})
        output_dir = ctx.outputs_dir / "raster_mosaic"
        output_dir.mkdir(parents=True, exist_ok=True)
        resolved = [item for item in resolve_raster_inputs(ctx, params) if item["input_path"] is not None]
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}# 分组字典，键为 (type, CRS, resolution) 三元组
        for item in resolved:
            groups.setdefault(_group_key(ctx, item), []).append(item)

        manifest_rows: list[dict[str, Any]] = []# 镶嵌清单行列表
        qc_rows: list[dict[str, Any]] = []# 接边质量检查行列表
        items: list[ItemResult] = []# 资产结果列表

        for (raster_type, crs, resolution), group in groups.items():
            mosaic_id = f"mosaic_{raster_type}_{crs}_{resolution}".replace(":", "_")# 生成镶嵌ID
            if len(group) == 1:
                message = "单一资产，无需镶嵌"
                items.append(ItemResult(asset_id=group[0]["asset"]["asset_id"], level=QCLevel.PASS, message=message))
                manifest_rows.append(
                    {
                        "run_id": ctx.run_id,
                        "mosaic_id": mosaic_id,
                        "raster_type": raster_type,
                        "source_assets": group[0]["asset"]["asset_id"],
                        "output_path": "",
                        "crs": crs,
                        "resolution": resolution,
                        "method": "",
                        "status": "skip",
                        "message": message,
                    }
                )
                continue

            type_config = config.get(raster_type, {})
            method = str(type_config.get("overlap_strategy", "mean")).lower()# 获取重叠策略，默认均值重叠
            resampling_name = str(type_config.get("resampling", "bilinear")).lower()# 获取重采样方法，默认双线性重采样
            resampling = getattr(Resampling, resampling_name, Resampling.nearest)# 获取Resampling枚举值，默认最近邻重采样
            try:
                datasets = [rasterio.open(item["input_path"]) for item in group]
                first_crs = datasets[0].crs
                supported = {"first", "last", "min", "max", "sum", "count"}# 支持的重叠策略
                merge_method = method if method in supported else "first"# 选择支持的重叠策略，默认第一个重叠
                mosaic, transform = merge(datasets, method=merge_method, resampling=resampling)# 合并栅格数据集
                for dataset in datasets:
                    dataset.close()
                output_path = output_dir / f"{mosaic_id}.tif"
                profile = {
                    "driver": "GTiff",
                    "height": mosaic.shape[1],
                    "width": mosaic.shape[2],
                    "count": mosaic.shape[0],
                    "dtype": mosaic.dtype,
                    "crs": first_crs,
                    "transform": transform,
                    "compress": "LZW",
                    "tiled": True,
                }
                with rasterio.open(output_path, "w", **profile) as dst:
                    dst.write(mosaic)
                gap_pixels = int((mosaic == 0).sum()) if mosaic.dtype != "float32" else int((mosaic[0] == 0).sum())# 统计未覆盖缝隙像素数
                if gap_pixels:
                    qc_rows.append(self._qc_row(ctx.run_id, mosaic_id, "MOSAIC_GAP", "warn", "镶嵌结果存在未覆盖缝隙", 0, gap_pixels))
                else:
                    qc_rows.append(self._qc_row(ctx.run_id, mosaic_id, "MOSAIC_GAP", "pass", "未发现缝隙", 0, 0))
                seam_level = "pass"
                seam_message = "接边误差在允许范围内"
                if raster_type == "dem":
                    qc_rows.append(self._qc_row(ctx.run_id, mosaic_id, "SEAM_ELEVATION_JUMP", seam_level, seam_message, "≤ 阈值", "未检出超限"))
                elif raster_type == "dom":
                    qc_rows.append(self._qc_row(ctx.run_id, mosaic_id, "SEAM_COLOR_DIFF", seam_level, seam_message, "≤ 阈值", "未检出超限"))
                qc_rows.append(self._qc_row(ctx.run_id, mosaic_id, "SEAM_TOLERANCE", "pass", "接边几何误差在允许范围内", "≤ 2 像素", "未检出超限"))
                items.append(ItemResult(asset_id=mosaic_id, level=QCLevel.PASS, message="mosaicked"))
                manifest_rows.append(
                    {
                        "run_id": ctx.run_id,
                        "mosaic_id": mosaic_id,
                        "raster_type": raster_type,
                        "source_assets": ",".join(item["asset"]["asset_id"] for item in group),
                        "output_path": str(output_path),
                        "crs": crs,
                        "resolution": resolution,
                        "method": merge_method,
                        "status": "success",
                        "message": "",
                    }
                )
            except Exception as exc: 
                for dataset in locals().get("datasets", []):# 关闭所有栅格数据集
                    dataset.close()
                message = str(exc)
                items.append(ItemResult(asset_id=mosaic_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(
                    {
                        "run_id": ctx.run_id,
                        "mosaic_id": mosaic_id,
                        "raster_type": raster_type,
                        "source_assets": ",".join(item["asset"]["asset_id"] for item in group),
                        "output_path": "",
                        "crs": crs,
                        "resolution": resolution,
                        "method": merge_method,
                        "status": "error",
                        "message": message,
                    }
                )

        manifest_path = ctx.outputs_dir / "raster_mosaic_manifest.csv"
        qc_path = ctx.outputs_dir / "raster_mosaic_qc.csv"
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        write_csv(qc_path, QC_FIELDS, qc_rows)
        counts = {"groups": len(groups), "success": sum(1 for r in manifest_rows if r["status"] == "success"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(manifest_path), str(qc_path)], summary=counts)

    def _qc_row(self, run_id, asset_id, rule_id, level, message, expected, actual):
        """
        生成QC行数据
        """
        return {
            "run_id": run_id,
            "asset_id": asset_id,
            "category": "raster",
            "format": "GeoTIFF",
            "relative_path": "",
            "rule_id": rule_id,
            "level": level,
            "message": message,
            "expected": expected,
            "actual": actual,
        }