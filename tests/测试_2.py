"""点云地面滤波工具(CSF布料模拟算法)"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.modules.m5._common import QC_FIELDS, resolve_pointcloud_inputs, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "ground_path",
    "dem_path",
    "ground_count",
    "nonground_count",
    "ground_ratio",
    "source_stage",
    "status",
    "message",
]


@register
class PointcloudBatchGroundFilter(BaseTool):
    spec = ToolSpec(
        id="pointcloud.batch_ground_filter",
        name="点云地面滤波",
        module="m5",
        description="使用 PDAL CSF 提取地面点，输出地面点 LAS 和 DEM。",
        params={"input_dirs": {"type": "list[path]", "title": "输入点云数据目录", "required": False}},
        inputs=["assets.json", "crs_audit.csv"],
        outputs=["pointcloud_ground/", "pointcloud_ground_filter_manifest.csv", "pointcloud_ground_filter_qc.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_pointcloud_inputs(ctx, params)
        return {"pointcloud_assets": len(resolved), "skipped": sum(1 for item in resolved if item["input_path"] is None)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """延迟导入重型依赖:pdal(点云处理引擎),numpy、rasterio
        PDAL 依赖 LASlib 和 GDAL,体积较大且 Windows 上需预先编译，
        所以只在 execute 里导入，避免 dry_run 等轻量场景加载"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import numpy as np
        import pdal
        import rasterio

        # 从 profile 读取配置，分三个层级：
        #   config          全局（地面点比例告警阈值等）
        #   csf_cfg         CSF 滤波器参数（分辨率、刚性、迭代次数等）
        #   dem_cfg         DEM 生成参数（分辨率、nodata 值等）
        config = ctx.profile.get("m5", {}).get("ground_filter", {})
        csf_cfg = config.get("csf", {})
        dem_cfg = config.get("dem", {})

        # ground_class: PDAL LAS 标准分类码，2=地面点，默认 2
        # CSF 滤波完成后会把地面点的 Classification 字段设为这个值
        ground_class = int(csf_cfg.get("ground_class", 2))

        # ratio_warn: 地面点占比告警阈值，默认 0.7
        # 如果地面点超过 70%，说明滤波可能过度（把建筑物/植被也归为地面了）
        ratio_warn = float(config.get("ground_ratio_warn", 0.7))

        output_dir = ctx.outputs_dir / "pointcloud_ground"
        output_dir.mkdir(parents=True, exist_ok=True)

        # resolve_pointcloud_inputs 统一处理 quarantine 隔离、CRS 审计失败等跳过逻辑
        resolved = resolve_pointcloud_inputs(ctx, params)

        manifest_rows: list[dict[str, Any]] = []
        qc_rows: list[dict[str, Any]] = []
        items: list[ItemResult] = []

        # 逐点云资产处理循环
        for item in resolved:
            asset = item["asset"]
            asset_id = asset["asset_id"]

            # ---------- 跳过上游标记为不可处理的资产 ----------
            if item["input_path"] is None:
                message = f"跳过，{item['skip_reason']}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", "", "", 0, 0, 0, item["source_stage"], "skip", message))
                continue

            try:
                """PDAL Pipeline 构建 —— JSON 声明式流水线
                PDAL 不像 rasterio 那样一个函数一个步骤，而是定义一个
                JSON 流水线，包含 reader → filter → writer 等 stage，
                最后一次性 execute() 执行全部"""

                # Stage 1: readers.las —— 读入 LAS/LAZ 点云文件
                pipeline_json = [{"type": "readers.las", "filename": str(item["input_path"])}]

                # Stage 2: filters.csf —— CSF (Cloth Simulation Filter) 布料模拟滤波
                # 这是点云地面滤波的主流算法：把点云反转视为"山峰倒挂"，
                # 用虚拟布料从上方落下，布料粘到的点就是地面点
                csf_stage: dict[str, Any] = {"type": "filters.csf"}
                # 从 profile 动态注入 CSF 参数，用户可覆盖默认值：
                #   resolution   布料分辨率，默认 0.5
                #   threshold    分类阈值（点到布料的距离），默认 0.5
                #   hdiff        高度差分阈值，默认 1.0
                #   rigidness    布料刚性，默认 2（值越大布料越硬，越不容易贴合地面）
                #   step         布料下落步长，默认 0.65
                #   iterations   迭代次数，默认 100
                for key in ("resolution", "threshold", "hdiff", "rigidness", "step", "iterations"):
                    if key in csf_cfg:
                        csf_stage[key] = csf_cfg[key]
                pipeline_json.append(csf_stage)

                # 构建并执行 Pipeline
                pipeline = pdal.Pipeline(json=json.dumps(pipeline_json))
                pipeline.execute()

                # execute() 后：
                #   pipeline.arrays[0]       ← 分类后的点云（numpy 结构化数组）
                #   pipeline.pipeline_wkt    ← 源点云的 CRS WKT（从 readers.las 继承）
                array = pipeline.arrays[0]
                src_wkt = pipeline.pipeline_wkt or "EPSG:4490"

                # ---------- 地面点/非地面点分离 ----------
                ground_mask = array["Classification"] == ground_class
                ground = array[ground_mask]
                nonground_count = int((~ground_mask).sum())
                ground_count = int(ground_mask.sum())
                ratio = ground_count / len(array) if len(array) else 0.0

                stem = Path(asset["relative_path"]).stem
                ground_path = output_dir / f"{asset_id}_{stem}_ground.las"

                # ---------- 输出地面点 LAS ----------
                if ground_count:
                    # 传递源点云 CRS（不再硬编码 EPSG:4490）
                    writer = pdal.Pipeline(
                        json=json.dumps([{"type": "writers.las", "filename": str(ground_path), "a_srs": src_wkt}]),
                        arrays=[ground],
                    )
                    writer.execute()
                else:
                    ground_path.write_bytes(b"")

                # ---------- 生成 DEM ----------
                dem_path = output_dir / f"{asset_id}_{stem}_dem.tif"
                self._write_dem(ground, dem_path, dem_cfg, src_wkt)

                # 质量检查记录

                # 1. 完成标记（永远 pass）
                qc_rows.append(self._qc_row(ctx.run_id, asset, "GROUND_FILTER_DONE", "pass", "CSF 地面滤波完成", "地面点已提取", f"地面点 {ground_count}，非地面点 {nonground_count}"))

                # 2. 地面点比例告警（超过 ratio_warn 说明可能过度分类）
                if ratio > ratio_warn:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "GROUND_RATIO", "warn", "地面点比例偏高，可能过度分类", f"≤ {ratio_warn}", f"{ratio:.3f}"))

                # 3. CRS 假定来源提示（上游 M2 没法确认 CRS 时用了配置假定值，结果仅供参考）
                if item["source_stage"] == "m2_assumed_crs":
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "GROUND_FILTER_DONE", "pass", "基于 assumed_crs 处理，结果仅供参考", "CRS 已确认", item["source_stage"]))

                # ---------- 资产级状态汇总 ----------
                # 只有地面点比例超标才 warn，其余 pass
                status = "warn" if ratio > ratio_warn else "pass"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel(status), message=status))

                manifest_rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        str(item["input_path"]),
                        str(ground_path),
                        str(dem_path),
                        ground_count,
                        nonground_count,
                        round(ratio, 4),
                        item["source_stage"],
                        status,
                        "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), "", "", 0, 0, 0, item["source_stage"], "error", message))

        # 汇总输出
        manifest_path = ctx.outputs_dir / "pointcloud_ground_filter_manifest.csv"
        qc_path = ctx.outputs_dir / "pointcloud_ground_filter_qc.csv"
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        write_csv(qc_path, QC_FIELDS, qc_rows)

        counts = {"pointcloud_assets": len(resolved), "pass": sum(1 for r in manifest_rows if r["status"] == "pass"), "warn": sum(1 for r in manifest_rows if r["status"] == "warn"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(manifest_path), str(qc_path)], summary=counts)

    def _write_dem(self, ground: Any, output_path: Path, dem_cfg: dict[str, Any], src_wkt: str) -> None:
        """用地面点的 XYZ 坐标栅格化生成 GeoTIFF DEM"""
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        if len(ground) == 0:
            return
        resolution = float(dem_cfg.get("resolution", 1.0))
        nodata = float(dem_cfg.get("output_nodata", -9999))
        x = ground["X"].astype(np.float64)
        y = ground["Y"].astype(np.float64)
        z = ground["Z"].astype(np.float32)
        minx, maxx = float(x.min()), float(x.max())
        miny, maxy = float(y.min()), float(y.max())
        width = max(1, int(np.ceil((maxx - minx) / resolution)))
        height = max(1, int(np.ceil((maxy - miny) / resolution)))

        # ---------- Bug 修复：栅格化 ----------
        # 之前用 nodata（通常 -9999）初始化 grid，再调 np.minimum.at：
        #   min(-9999, 500) = -9999  ← nodata 永远更小，grid 永远不被填入真实高程！
        # 修复：用正无穷初始化，保证任何真实高程都能"覆盖"初始值；
        # 栅格化完成后再把没变的（没被点覆盖的）位置设回 nodata
        grid = np.full((height, width), np.inf, dtype=np.float32)

        cols = ((x - minx) / resolution).astype(np.int64)
        rows = ((maxy - y) / resolution).astype(np.int64)
        valid = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
        rows, cols, z = rows[valid], cols[valid], z[valid]
        if len(z):
            # 同一栅格单元若落入多个地面点，取最低 Z 值（np.minimum 语义）
            np.minimum.at(grid, (rows, cols), z)
        # 没被任何点覆盖的栅格 → 恢复为 nodata
        grid[grid == np.inf] = nodata

        # ---------- Bug 修复：CRS 不再硬编码 ----------
        # src_wkt 来自 execute() 里已执行过的 PDAL pipeline（零额外开销），
        # 直接复用源点云原始 CRS，避免硬编码 EPSG:4490 导致投影坐标被当作经纬度
        profile = {
            "driver": "GTiff",
            "height": height,
            "width": width,
            "count": 1,
            "dtype": "float32",
            "crs": src_wkt,
            "transform": from_origin(minx, maxy, resolution, resolution),
            "nodata": nodata,
        }
        with rasterio.open(output_path, "w", **profile) as dataset:
            dataset.write(grid, 1)

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

    def _manifest_row(self, run_id, asset, source_path, ground_path, dem_path, ground_count, nonground_count, ground_ratio, source_stage, status, message):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "ground_path": ground_path,
            "dem_path": dem_path,
            "ground_count": ground_count,
            "nonground_count": nonground_count,
            "ground_ratio": ground_ratio,
            "source_stage": source_stage,
            "status": status,
            "message": message,
        }