"""Point cloud thinning tool."""

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
    "output_path",
    "mode",
    "before_count",
    "after_count",
    "kept_ratio",
    "preserve_ground",
    "source_stage",
    "status",
    "message",
]


@register
class PointcloudBatchThin(BaseTool):
    spec = ToolSpec(
        id="pointcloud.batch_thin",
        name="点云抽稀",
        module="m5",
        description="按随机或体素方式抽稀点云，默认保护地面点。",
        params={"input_dirs": {"type": "list[path]", "title": "输入点云数据目录", "required": False}},
        inputs=["assets.json"],
        outputs=["pointcloud_thinned/", "pointcloud_thin_manifest.csv", "pointcloud_thin_qc.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_pointcloud_inputs(ctx, params)
        return {"pointcloud_assets": len(resolved), "skipped": sum(1 for item in resolved if item["input_path"] is None)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """
        点云抽稀 —— 按随机抽样或体素下采样减少点数量，默认保护地面点。

        与 ground.py（PDAL 流水线 + CSF 算法）不同，本工具不走 PDAL filter stage，
        而是自己实现抽稀逻辑：读 LAS → numpy 数组 → 抽稀 → 写 LAS。
        原因：PDAL 的 filters.decimation 只支持按比例/步长，
        没法做到"地面点全保留、只抽稀非地面点"这种保护策略。

        两种抽稀模式：
          random —— 纯随机按比例保留（keep_ratio 默认 0.3）
          voxel  —— 按 3D 体素格下采样，每个体素里保留距质心最近的点（cell_size 默认 0.1 米）
        """
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import numpy as np
        import pdal

        # ---------- 从 profile 读配置 ----------
        # mode: "random" 随机抽稀 | "voxel" 体素抽稀，默认 random
        # preserve_ground: 是否保护地面点不参与抽稀，默认 True
        #   保护逻辑：先按 Classification 码分离，只对非地面点抽稀，最后拼回
        #   场景：DEM 只关心地面点 → 保护地面点可以在不损失 DEM 精度的前提下大幅减点
        # ground_class: PDAL LAS 标准分类码，默认 2（地面点），和 ground.py 保持一致
        config = ctx.profile.get("m5", {}).get("thin", {})
        mode = str(config.get("mode", "random")).lower()
        preserve_ground = bool(config.get("preserve_ground", True))
        ground_class = int(config.get("ground_class", 2))

        output_dir = ctx.outputs_dir / "pointcloud_thinned"
        output_dir.mkdir(parents=True, exist_ok=True)

        resolved = resolve_pointcloud_inputs(ctx, params)
        manifest_rows: list[dict[str, Any]] = []
        qc_rows: list[dict[str, Any]] = []
        items: list[ItemResult] = []

        for item in resolved:
            asset = item["asset"]
            asset_id = asset["asset_id"]

            # ---------- 跳过上游隔离/审计失败的资产 ----------
            if item["input_path"] is None:
                message = f"跳过，{item['skip_reason']}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", "", mode, 0, 0, 0, preserve_ground, item["source_stage"], "skip", message))
                continue

            try:
                # 只读流水线：PDAL readers.las 把 LAS/LAZ 转成 numpy 结构化数组
                # 这和 ground.py 一样，PDAL 的 execute() 才是真正触发读取的地方
                reader = pdal.Pipeline(json=json.dumps([{"type": "readers.las", "filename": str(item["input_path"])}]))
                reader.execute()
                array = reader.arrays[0]
                before_count = len(array)

                # 从源点云读原始 CRS WKT（reader.execute() 后 srswkt2 才可用）
                # 如果源文件本身没写 CRS（srswkt2 为空），writer 也不硬写，
                # 保持输出与输入元数据一致 —— 不凭空造一个错的坐标系
                src_wkt = reader.srswkt2 or ""

                # --------------------------------------------------------
                # 保护地面点策略
                # --------------------------------------------------------
                # 地面点在后续生成 DEM 时要用到，抽稀它们会破坏地形精度。
                # 所以默认行为是：地面点一个不动，只对非地面点（建筑物、植被、噪声）抽稀。
                # preserve_ground=False 时全部点一起抽稀（适合不关心地形的场景，如纯三维建模）
                # --------------------------------------------------------
                ground_mask = array["Classification"] == ground_class

                if preserve_ground:
                    ground = array[ground_mask]       # 地面点 → 全保留，不抽稀
                    candidates = array[~ground_mask]  # 非地面点 → 送进 _thin 抽稀
                    thinned = self._thin(candidates, mode, config)
                    # 拼回：地面点 + 抽稀后的非地面点 → 完整结果
                    result = np.concatenate([ground, thinned]) if len(thinned) else ground
                else:
                    result = self._thin(array, mode, config)

                after_count = len(result)

                # 写出抽稀结果 LAS —— CRS 与源文件保持一致
                # writers.las 的 a_srs 参数只在源文件确实有 CRS 时才写入，
                # 避免硬编码 4490 这种与实际坐标不匹配的错误
                output_path = output_dir / f"{asset_id}_{Path(asset['relative_path']).stem}_thin.las"
                writer_stage: dict[str, Any] = {"type": "writers.las", "filename": str(output_path)}
                if src_wkt:
                    writer_stage["a_srs"] = src_wkt
                writer = pdal.Pipeline(
                    json=json.dumps([writer_stage]),
                    arrays=[result],
                )
                writer.execute()

                # ---------- QC 记录 ----------
                kept_ratio = round(after_count / before_count, 4) if before_count else 0
                qc_rows.append(self._qc_row(ctx.run_id, asset, "THIN_DONE", "pass", "抽稀完成", "保留比例", f"{before_count} → {after_count}，保留比例 {kept_ratio}"))
                # 如果没保护地面点，必须加 warn —— 用户可能不知道自己损失了地形精度
                if not preserve_ground:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "GROUND_POINTS_THINNED", "warn", "地面点未受保护，已参与抽稀", "保护地面点", "未保护"))

                items.append(ItemResult(asset_id=asset_id, level=QCLevel.PASS, message="thinned"))
                manifest_rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        str(item["input_path"]),
                        str(output_path),
                        mode,
                        before_count,
                        after_count,
                        kept_ratio,
                        preserve_ground,
                        item["source_stage"],
                        "pass",
                        "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), "", mode, 0, 0, 0, preserve_ground, item["source_stage"], "error", message))

        # 汇总输出
        manifest_path = ctx.outputs_dir / "pointcloud_thin_manifest.csv"
        qc_path = ctx.outputs_dir / "pointcloud_thin_qc.csv"
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        write_csv(qc_path, QC_FIELDS, qc_rows)
        counts = {"pointcloud_assets": len(resolved), "success": sum(1 for r in manifest_rows if r["status"] == "pass"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(manifest_path), str(qc_path)], summary=counts)

    def _thin(self, array: Any, mode: str, config: dict[str, Any]) -> Any:
        import numpy as np

        if len(array) == 0:
            return array
        if mode == "random":
            keep_ratio = float(config.get("random", {}).get("keep_ratio", 0.3))
            seed = int(config.get("random", {}).get("seed", 42))
            keep = min(1.0, max(0.0, keep_ratio))# 保留比例，[0, 1]
            rng = np.random.default_rng(seed)# 随机数生成器，固定种子
            indices = rng.choice(len(array), size=max(1, int(len(array) * keep)), replace=False)# 随机抽稀，不重复抽样
            return array[indices]
        if mode == "voxel":
            cell_size = float(config.get("voxel", {}).get("cell_size", 0.1))# 体素大小，单位：米
            coords = np.column_stack([array["X"], array["Y"], array["Z"]]).astype(np.float64)# 点云坐标，单位：米
            mins = coords.min(axis=0)# 最小坐标，用于归一化
            voxel_indices = ((coords - mins) / cell_size).astype(np.int64)# 体素索引，单位：米
            unique_voxels, inverse = np.unique(voxel_indices, axis=0, return_inverse=True)# 去重，返回每个点所属的体素索引
            keep_indices = []# 保留的点索引
            for voxel_index in range(len(unique_voxels)):# 遍历每个体素
                member_indices = np.flatnonzero(inverse == voxel_index)# 获取该体素下的所有点索引
                centroid = coords[member_indices].mean(axis=0)# 计算该体素的质心
                nearest = member_indices[np.argmin(np.sum((coords[member_indices] - centroid) ** 2, axis=1))]# 获取该体素下距离质心最近的点索引
                keep_indices.append(nearest)
            return array[np.asarray(keep_indices, dtype=np.int64)]
        return array

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

    def _manifest_row(self, run_id, asset, source_path, output_path, mode, before_count, after_count, kept_ratio, preserve_ground, source_stage, status, message):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "output_path": output_path,
            "mode": mode,
            "before_count": before_count,
            "after_count": after_count,
            "kept_ratio": kept_ratio,
            "preserve_ground": preserve_ground,
            "source_stage": source_stage,
            "status": status,
            "message": message,
        }