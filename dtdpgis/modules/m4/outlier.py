"""Raster outlier repair tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.modules.m4._common import QC_FIELDS, output_path_for_asset, raster_type_for_asset, resolve_raster_inputs, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "output_path",
    "mask_path",
    "raster_type",
    "hard_count",
    "soft_count",
    "repaired_count",
    "source_stage",
    "status",
    "message",
]


@register
class RasterBatchOutlierFix(BaseTool):
    spec = ToolSpec(
        id="raster.batch_outlier_fix",
        name="栅格异常值修复",
        module="m4",
        description="按邻域中值残差检测并修复 DEM/DOM 异常值。",
        params={"input_dirs": {"type": "list[path]", "title": "输入栅格数据目录", "required": False}},
        inputs=["assets.json", "raster_reproject_manifest.csv"],
        outputs=["raster_outlier_fixed/", "raster_outlier_masks/", "raster_outlier_qc.csv", "raster_outlier_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_raster_inputs(ctx, params)
        return {"raster_assets": len(resolved), "skipped": sum(1 for item in resolved if item["input_path"] is None)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """延迟导入重型依赖:scipy/rasterio/numpy,确保轻量场景不触发加载"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import numpy as np
        import rasterio
        from scipy import ndimage

        # 从 profile 读取当前工具的配置字典，按栅格类型（dem/dom/classification）
        # 分别设置阈值、窗口大小、修复策略等
        config = ctx.profile.get("m4", {}).get("outlier_fix", {})

        # 定义两个输出目录：修复后栅格、异常值掩膜栅格
        output_dir = ctx.outputs_dir / "raster_outlier_fixed"
        mask_dir = ctx.outputs_dir / "raster_outlier_masks"
        output_dir.mkdir(parents=True, exist_ok=True)
        mask_dir.mkdir(parents=True, exist_ok=True)

        # 复用 resolve_raster_inputs 统一解析输入路径，自动处理 quarantine、
        # CRS 审计失败、重投影路径选择等上游判断逻辑
        resolved = resolve_raster_inputs(ctx, params)

        # 初始化三个结果容器：清单行、质量检查行、资产结果项
        manifest_rows: list[dict[str, Any]] = []
        qc_rows: list[dict[str, Any]] = []
        items: list[ItemResult] = []

        # 逐资产处理循环
        for item in resolved:
            asset = item["asset"]
            asset_id = asset["asset_id"]

            # ---------- 跳过上游标记为不可处理的资产 ----------
            # input_path=None 的情况包括：被 m1_quarantined 隔离、
            # m2 CRS 审计失败等，直接记录 skip 清单并 continue
            if item["input_path"] is None:
                message = f"跳过，{item['skip_reason']}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", "", "", "", 0, 0, 0, item["source_stage"], "skip", message))
                continue

            # ---------- 栅格类型识别 + 参数读取 ----------
            # raster_type_for_asset 根据 asset_id 或文件名关键词（dem/dom/classification）
            # 返回类型标签和是否 unknown（unknown=True 表示未能匹配任何预设模式）
            raster_type, unknown_type = raster_type_for_asset(ctx, asset)
            type_config = config.get(raster_type, {})

            # 三个核心参数，均可通过 profile 覆盖默认值：
            #   window_size      邻域中值滤波窗口大小，默认 5×5
            #   soft_threshold   软阈值：残差超过此值标记为可疑异常，但不修复
            #   hard_threshold   硬阈值：残差超过此值标记为粗差，用邻域中值替换
            window_size = int(type_config.get("window_size", 5))
            soft_threshold = float(type_config.get("soft_threshold", 20))
            hard_threshold = float(type_config.get("hard_threshold", 50))
            action = type_config.get("action", "repair_hard_only")

            try:
                # ---------- 打开栅格，准备逐波段处理 ----------
                with rasterio.open(item["input_path"]) as src:
                    profile = src.profile.copy()
                    # 若源文件未声明 nodata，默认用 0 作为哨兵值
                    nodata = src.nodata if src.nodata is not None else 0

                    repaired_bands = []  # 存放修复后的各波段数据
                    mask_bands = []      # 存放各波段的异常值掩膜 (uint8: 0=正常, 1=异常)
                    hard_count = 0       # 累计粗差像元数（跨波段）
                    soft_count = 0       # 累计可疑异常像元数（跨波段）
                    repaired_count = 0   # 累计被实际修复的像元数（跨波段）

                    # 逐波段异常值检测与修复（核心算法）
                    for band_index in range(1, src.count + 1):
                        # 读入单波段数据并转 float32，便于后续中值滤波做数值运算
                        data = src.read(band_index).astype(np.float32)

                        # 标记 nodata 像元，中值滤波时这些位置用 nan 填充避免干扰
                        nodata_mask = data == nodata

                        # 备份一份，把 nodata 位置替换为 NaN 再喂给 generic_filter
                        # np.nanmedian 会自动忽略窗口内的 NaN，只统计有效像元
                        values = data.copy()
                        values[nodata_mask] = np.nan

                        # scipy.ndimage.generic_filter 逐像元滑窗，窗口默认 5×5
                        # mode="constant" 表示边界用 cval=np.nan 填充，避免边界像元
                        # 被邻域外的假值污染
                        # 输出 median 和原始 data 形状一致，每个位置是该像元邻域的中值
                        median = ndimage.generic_filter(values, np.nanmedian, size=window_size, mode="constant", cval=np.nan)

                        # 残差 = 原始值 - 邻域中值 的绝对值
                        # 中值滤波天然抵抗极端值干扰，所以残差大说明该像元在邻域内"鹤立鸡群"
                        residual = np.abs(data - median)

                        # 三级判定（都排除 nodata 位置）：
                        #   hard_mask: 残差 > hard_threshold → 粗差/极端异常，必须修复
                        #   soft_mask: soft_threshold < 残差 ≤ hard_threshold → 可疑，只标记
                        #   其余：残差 ≤ soft_threshold → 正常
                        hard_mask = (~nodata_mask) & (residual > hard_threshold)
                        soft_mask = (~nodata_mask) & (residual > soft_threshold) & (residual <= hard_threshold)

                        # 统计当前波段的粗差和软异常像元数
                        band_hard = int(hard_mask.sum())
                        band_soft = int(soft_mask.sum())
                        hard_count += band_hard
                        soft_count += band_soft

                        # 合并两类异常为单波段掩膜：uint8 类型，1=异常(硬或软)，0=正常
                        # 后续多波段掩膜取 max 合成到一个输出文件（同 fill.py 的 mask 策略）
                        outlier_mask = (hard_mask | soft_mask).astype(np.uint8)
                        mask_bands.append(outlier_mask)

                        # 修复策略：
                        #   - classification 栅格（林地=1/耕地=2 这种离散标签）不修复
                        #     因为"中值插值"对类别标签没有数学意义，只标记让人工审核
                        #   - 仅修复 hard_mask（粗差），软异常只标记不改动
                        #   - 修复值 = 邻域中值（和残差计算用的是同一个 median 数组）
                        if raster_type != "classification" and action == "repair_hard_only" and band_hard:
                            data[hard_mask] = median[hard_mask]
                            repaired_count += band_hard

                        # 修复后转回原始 dtype（比如 uint8），避免写入时类型不匹配
                        repaired_bands.append(data.astype(profile["dtype"]))

                    # ---------- 写出修复后栅格 ----------
                    output_path = output_path_for_asset(ctx, "raster_outlier_fixed", asset)
                    # 沿用源文件的 profile，保证 CRS/分辨率/压缩等元数据一致
                    with rasterio.open(output_path, "w", **profile) as dst:
                        for band_index, band in enumerate(repaired_bands, start=1):
                            dst.write(band, band_index)

                    # ---------- 写出异常值掩膜 ----------
                    # 掩膜固定为单波段 uint8：
                    #   0 = 正常像元 或 nodata
                    #   1 = 硬异常 或 软异常
                    # 多波段栅格取各波段掩膜的最大值（任何一个波段异常就标记）
                    mask_path = mask_dir / f"{asset['asset_id']}_{Path(asset['relative_path']).stem}_outlier_mask.tif"
                    mask_profile = profile.copy()
                    mask_profile.update(count=1, dtype="uint8", nodata=0)
                    with rasterio.open(mask_path, "w", **mask_profile) as dst:
                        dst.write(mask_bands[0] if mask_bands else np.zeros((src.height, src.width), dtype=np.uint8), 1)

                # ================================================================
                # 质量检查记录（逐资产）
                # ================================================================
                # 以下为该资产逐条写入 qc_rows，供 qc.csv 汇总：

                # 1. 栅格类型未识别警告（unknown_type=True 说明没匹配到 dem/dom/classification）
                if unknown_type:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "RASTER_TYPE_UNKNOWN", "warn", "栅格类型未识别", "已识别", "unknown"))

                # 2. 粗差计数 → FAIL 级（残差超过硬阈值）
                if hard_count:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "OUTLIER_HARD", "fail", "粗差超过硬阈值", f"残差 ≤ {hard_threshold}", f"{hard_count} 个像元"))

                # 3. 软异常计数 → WARN 级（残差在软/硬阈值之间）
                if soft_count:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "OUTLIER_SOFT", "warn", "异常值超过软阈值", f"残差 ≤ {soft_threshold}", f"{soft_count} 个像元"))

                # 4. 实际修复的粗差像元 → PASS 级（正面反馈）
                if repaired_count:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "OUTLIER_REPAIRED", "pass", "粗差像元已修复", f"残差 ≤ {hard_threshold}", f"{repaired_count} 个像元"))

                # 5. 分类栅格特殊标记：异常值仅标记不修复，因为类别标签无法"插值"
                if raster_type == "classification" and (hard_count or soft_count):
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "OUTLIER_MARKED", "warn", "分类栅格异常值仅标记", "保持原值", f"{hard_count + soft_count} 个像元"))

                # ---------- 资产级状态汇总 ----------
                # 三级状态降级：hard 存在则 fail，只有 soft 则 warn，都没有则 pass
                status = "fail" if hard_count else ("warn" if soft_count else "pass")
                items.append(ItemResult(asset_id=asset_id, level=QCLevel(status), message=status))

                manifest_rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        str(item["input_path"]),
                        str(output_path),
                        str(mask_path),
                        raster_type,
                        hard_count,
                        soft_count,
                        repaired_count,
                        item["source_stage"],
                        status,
                        "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                # 捕获所有异常（文件损坏、内存不足等），写入 error 清单不中断流水线
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), "", "", raster_type, 0, 0, 0, item["source_stage"], "error", message))

        # 汇总输出：写入 CSV 清单文件 + 统计 ToolResult
        manifest_path = ctx.outputs_dir / "raster_outlier_manifest.csv"
        qc_path = ctx.outputs_dir / "raster_outlier_qc.csv"
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        write_csv(qc_path, QC_FIELDS, qc_rows)

        # counts 用于前端展示和后续工具判断
        counts = {"raster_assets": len(resolved), "pass": sum(1 for r in manifest_rows if r["status"] == "pass"), "warn": sum(1 for r in manifest_rows if r["status"] == "warn"), "fail": sum(1 for r in manifest_rows if r["status"] == "fail"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
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

    def _manifest_row(self, run_id, asset, source_path, output_path, mask_path, raster_type, hard_count, soft_count, repaired_count, source_stage, status, message):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "output_path": output_path,
            "mask_path": mask_path,
            "raster_type": raster_type,
            "hard_count": hard_count,
            "soft_count": soft_count,
            "repaired_count": repaired_count,
            "source_stage": source_stage,
            "status": status,
            "message": message,
        }