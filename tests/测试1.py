"""填充栅格中的空洞。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.modules.m4._common import (
    QC_FIELDS,
    output_path_for_asset,
    raster_type_for_asset,
    resolve_raster_inputs,
    write_csv,
)


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "output_path",
    "mask_path",
    "raster_type",
    "hole_count",
    "filled_holes",
    "large_holes",
    "filled_pixels",
    "source_stage",
    "status",
    "message",
]


def _hole_stats(mask: Any) -> list[dict[str, Any]]:
    """对 Nodata 掩膜做连通域标记，统计每个空洞的像元数。

    输入是一个二维布尔数组 (True = Nodata 像元,False = 有效值)。
    通过 8 邻域连通域标记把彼此连通的 Nodata 像元归为同一个"空洞":
    再计算每个空洞的面积 (像元个数)。

    Args:
        mask: 二维 numpy.ndarray,dtype=bool, True 表示 Nodata 像元。

    Returns:
        list[dict] 每个连通空洞一个 dict, 包含:
            - label: 空洞编号 (从 1 开始)
            - size: 该空洞包含的像元数
        若无任何空洞 (全 False) 则返回空列表。
    """
    from scipy import ndimage

    # ndimage.label: 对 mask=True 的连通区域做编号 (默认 8 邻域)
    # labels 是同形状数组, 每个 True 像元被赋值其所属连通域编号 (1..count)
    # count 是独立连通域 (空洞) 的总数
    labels, count = ndimage.label(mask)# 对 mask=True 的连通区域做编号 (默认 8 邻域)
    if count == 0:# 无任何空洞 (全 False)
        return []
    # ndimage.sum: 在 labels 的每个连通域范围内对 mask 求和
    # 等价于统计每个空洞包含多少个像元 (因为 True=1, False=0)
    sizes = ndimage.sum(mask, labels, range(1, count + 1))
    return [{"label": int(i + 1), "size": int(sizes[i])} for i in range(count)]


@register
class RasterBatchFillHoles(BaseTool):
    spec = ToolSpec(
        id="raster.batch_fill_holes",
        name="栅格空洞填充",
        module="m4",
        description="按 DEM/DOM/分类栅格分治填充 Nodata 空洞，输出填充掩膜。",
        params={"input_dirs": {"type": "list[path]", "title": "输入栅格数据目录", "required": False}},
        inputs=["assets.json", "raster_reproject_manifest.csv"],
        outputs=["raster_filled/", "raster_filled_masks/", "raster_hole_qc.csv", "raster_fill_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_raster_inputs(ctx, params)
        return {"raster_assets": len(resolved), "skipped": sum(1 for item in resolved if item["input_path"] is None)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import numpy as np
        import rasterio
        from rasterio.fill import fillnodata

        config = ctx.profile.get("m4", {}).get("fill_holes", {})
        output_dir = ctx.outputs_dir / "raster_filled"# 输出填充后的栅格
        mask_dir = ctx.outputs_dir / "raster_filled_masks"# 输出填充掩膜
        output_dir.mkdir(parents=True, exist_ok=True)
        mask_dir.mkdir(parents=True, exist_ok=True)
        resolved = resolve_raster_inputs(ctx, params)
        manifest_rows: list[dict[str, Any]] = []
        qc_rows: list[dict[str, Any]] = []
        items: list[ItemResult] = []

        for item in resolved:
            asset = item["asset"]
            asset_id = asset["asset_id"]
            if item["input_path"] is None:
                message = f"跳过，{item['skip_reason']}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", "", "", "", 0, 0, 0, 0, item["source_stage"], "skip", message))
                continue

            raster_type, unknown_type = raster_type_for_asset(ctx, asset)
            type_config = config.get(raster_type, {})
            max_hole = int(type_config.get("max_hole_pixels", 100))
            try:
                with rasterio.open(item["input_path"]) as src:
                    profile = src.profile.copy()
                    nodata = src.nodata
                    if nodata is None:
                        nodata = 0
                    filled_bands = []
                    mask_bands = []
                    hole_components = 0
                    filled_holes = 0
                    large_holes = 0
                    filled_pixels = 0

                    for band_index in range(1, src.count + 1):
                        data = src.read(band_index).astype(np.float32)
                        nodata_mask = data == nodata
                        holes = _hole_stats(nodata_mask)
                        hole_components += len(holes)
                        fill_mask = np.zeros_like(nodata_mask, dtype=np.uint8)
                        if raster_type == "classification":
                            for hole in holes:
                                if hole["size"] <= max_hole:
                                    large_holes += 0
                                else:
                                    large_holes += 1
                            filled_bands.append(data)
                            mask_bands.append(fill_mask)
                            continue

                        if raster_type in {"dem", "dom"}:
                            for hole in holes:
                                if hole["size"] <= max_hole:
                                    filled_holes += 1
                                    filled_pixels += hole["size"]
                                else:
                                    large_holes += 1
                            if filled_holes and type_config.get("action") != "mark_only":
                                max_dist = float(type_config.get("max_dist", 20))
                                smooth = int(type_config.get("smooth_iterations", 0))
                                filled = fillnodata(data, mask=nodata_mask, max_search_distance=max_dist, smoothing_iterations=smooth)
                                fill_mask = (~nodata_mask & (filled != data)).astype(np.uint8)
                                filled_bands.append(filled.astype(profile["dtype"]))
                            else:
                                filled_bands.append(data)
                            mask_bands.append(fill_mask)

                    output_path = output_path_for_asset(ctx, "raster_filled", asset)
                    mask_path = mask_dir / f"{asset['asset_id']}_{Path(asset['relative_path']).stem}_filled_mask.tif"
                    with rasterio.open(output_path, "w", **profile) as dst:
                        for band_index, band in enumerate(filled_bands, start=1):
                            dst.write(band, band_index)
                    mask_profile = profile.copy()
                    mask_profile.update(count=1, dtype="uint8", nodata=0)
                    with rasterio.open(mask_path, "w", **mask_profile) as dst:
                        dst.write(mask_bands[0] if mask_bands else np.zeros((src.height, src.width), dtype=np.uint8), 1)

                if unknown_type:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "RASTER_TYPE_UNKNOWN", "warn", "栅格类型未识别，按 DEM 保守处理", "已识别", "unknown"))
                if hole_components:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "HOLE_COUNT", "warn", "存在 Nodata 空洞", 0, hole_components))
                if filled_holes:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "HOLE_FILLED", "pass", "小空洞已填充", 0, f"{filled_holes} 个空洞，{filled_pixels} 像元"))
                if large_holes:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "LARGE_HOLE", "warn", "大空洞仅标记未填充", f"≤ {max_hole} 像元", f"{large_holes} 个空洞"))
                if raster_type == "classification" and hole_components:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "CLASSIFICATION_HOLE_MARKED", "warn", "分类栅格 Nodata 未插值，仅标记", "保持原值", "已标记"))

                status = "pass" if large_holes == 0 else "warn"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.PASS if status == "pass" else QCLevel.WARN, message=status))
                manifest_rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        str(item["input_path"]),
                        str(output_path),
                        str(mask_path),
                        raster_type,
                        hole_components,
                        filled_holes,
                        large_holes,
                        filled_pixels,
                        item["source_stage"],
                        status,
                        "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), "", "", raster_type, 0, 0, 0, 0, item["source_stage"], "error", message))

        manifest_path = ctx.outputs_dir / "raster_fill_manifest.csv"
        qc_path = ctx.outputs_dir / "raster_hole_qc.csv"
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        write_csv(qc_path, QC_FIELDS, qc_rows)
        counts = {"raster_assets": len(resolved), "pass": sum(1 for r in manifest_rows if r["status"] == "pass"), "warn": sum(1 for r in manifest_rows if r["status"] == "warn"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(mask_dir), str(manifest_path), str(qc_path)], summary=counts)

    def _qc_row(self, run_id, asset, rule_id, level, message, expected, actual):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "category": asset["category"],
            "format": asset["format"],
            "relative_path": asset["relative_path"],
            "rule_id": rule_id,
            "level": level,
            "message": message,
            "expected": expected,
            "actual": actual,
        }

    def _manifest_row(self, run_id, asset, source_path, output_path, mask_path, raster_type, hole_count, filled_holes, large_holes, filled_pixels, source_stage, status, message):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "output_path": output_path,
            "mask_path": mask_path,
            "raster_type": raster_type,
            "hole_count": hole_count,
            "filled_holes": filled_holes,
            "large_holes": large_holes,
            "filled_pixels": filled_pixels,
            "source_stage": source_stage,
            "status": status,
            "message": message,
        }