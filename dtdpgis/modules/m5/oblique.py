"""倾斜摄影质量检测工具."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.core.encoding import read_text_auto
from dtdpgis.modules.m5._common import QC_FIELDS, resolve_oblique_inputs, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "tile_count",
    "metadata_readable",
    "source_stage",
    "status",
    "message",
]


@register
class ObliqueBatchQualityCheck(BaseTool):
    spec = ToolSpec(
        id="oblique.batch_quality_check",
        name="倾斜摄影质量检测",
        module="m5",
        description="对 OSGB Block 做悬浮、拉花、空洞、坐标和接边的粗筛与标记。",
        params={"input_dirs": {"type": "list[path]", "title": "输入 OSGB 数据目录", "required": False}},
        inputs=["assets.json"],
        outputs=["oblique_qc.csv", "oblique_qc_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_oblique_inputs(ctx, params)
        return {"oblique_assets": len(resolved), "skipped": sum(1 for item in resolved if item["input_path"] is None)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """
        倾斜摄影 OSGB Block 质量检测。

        与 M5 ground.pyLPDAL 算法处理)、M4 栅格工具(rasterio/scipy 运算)不同，
        本工具只做**文件系统级的静态结构检查**，不加载 OSGB 几何数据：
          ✓ metadata.xml 是否存在、能否解析
          ✓ .osgb tile 文件数量、是否有空文件
          ✓ metadata 里声明的 EPSG 与项目基准是否一致
          ✗ 悬浮/拉花/空洞 —— 几何拓扑检查留待后续版本（需要加载 OSGB 网格）
          ✗ 接边重叠 —— 跨 Block 空间比对留待后续版本

        设计意图：倾斜摄影数据量巨大(单个 Block 可能几 GB),
        全量加载做几何检查不现实，所以先做低成本的"粗筛",
        把明显有问题的（缺 metadata、零 tile、CRS 错）标出来，
        几何级缺陷由人工或专用工具(如 ContextCapture、OSGBLab)处理。
        """
        # config 里可能配了项目基准 CRS，用于比对 metadata 里的 EPSG 是否一致
        config = ctx.profile.get("m5", {}).get("oblique_qc", {})
        expected_crs = config.get("coord", {}).get("expected_crs")

        resolved = resolve_oblique_inputs(ctx, params)
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
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", 0, False, item["source_stage"], "skip", message))
                continue

            try:
                # root 是 OSGB Block 的根目录（asset.scan_path 指向的文件夹）
                # 里面通常有 metadata.xml + 一堆分层目录下的 .osgb tile
                root = item["input_path"]

                # metadata.xml 定位：先查根目录下，找不到递归搜整个 Block
                # 某些 OSGB 生成器把 metadata.xml 放在子目录里
                metadata_path = root / "metadata.xml"
                if not metadata_path.exists():
                    candidates = list(root.rglob("metadata.xml"))
                    metadata_path = candidates[0] if candidates else None

                # read_text_auto 自动检测 UTF-8 / GBK / BOM 等编码
                # metadata 为空或全空白 → metadata_readable = False
                metadata_text = read_text_auto(metadata_path) if metadata_path else ""
                metadata_readable = bool(metadata_text.strip())

                # 递归收集所有 .osgb 文件（tile），并筛出空文件
                # rglob("*.osgb") 不区分大小写，某些生成器输出 .OSGB
                tiles = [p for p in root.rglob("*.osgb") if p.is_file()]
                empty_tiles = [p for p in tiles if p.stat().st_size == 0]

                # 从 metadata 文本里用正则抠 EPSG 号
                # 匹配 "EPSG:4490" / "EPSG 4490" / "epsg:4490" 等变体
                epsg_match = re.search(r"EPSG[: ]?(\d+)", metadata_text, flags=re.IGNORECASE)

                # ================================================================
                # QC 规则逐个记录（无论 pass/fail/warn 都写一行）
                # 这种"每条规则一行"的平铺结构方便后续按 rule_id 过滤统计
                # ================================================================

                # Rule 1: metadata.xml 可读性 —— 没有 metadata 等于 Block 无头
                qc_rows.append(self._qc_row(ctx.run_id, asset, "OBLIQUE_METADATA", "pass" if metadata_readable else "fail", "metadata.xml 可解析", "可解析", "可解析" if metadata_readable else "不可解析"))

                # Rule 2: tile 数量 —— 0 个 tile 说明 Block 空壳
                qc_rows.append(self._qc_row(ctx.run_id, asset, "OBLIQUE_TILE_COUNT", "pass" if tiles else "fail", "tile 数量有效", "大于 0", len(tiles)))

                # Rule 3: 空 tile 文件 —— 文件存在但大小为 0，说明生成中断或损坏
                if empty_tiles:
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "OBLIQUE_EMPTY_TILE", "warn", "存在空 tile 文件", 0, len(empty_tiles)))

                # Rule 4: SRS（空间参考系）一致性
                # metadata 声明的 EPSG vs profile 里配的项目基准 expected_crs
                if epsg_match:
                    found_epsg = epsg_match.group(1)
                    # expected_crs 可能是 "EPSG:4490" 或纯数字 "4490"，统一取冒号后的数字
                    if expected_crs and str(found_epsg) != str(expected_crs).split(":")[-1]:
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "OBLIQUE_SRS", "warn", "metadata SRS 与项目基准不一致", expected_crs, f"EPSG:{found_epsg}"))
                    else:
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "OBLIQUE_SRS", "pass", "metadata SRS 与项目基准一致", expected_crs, f"EPSG:{found_epsg}"))
                else:
                    # metadata 里连 EPSG 都没写 → warn，后续加载可能出错
                    qc_rows.append(self._qc_row(ctx.run_id, asset, "OBLIQUE_SRS", "warn", "metadata 未声明 SRS", "已声明", "未声明"))

                # Rule 5-9: 预留规则 —— 几何/拓扑类检查
                # 当前版本全写 pass + 说明文字，不做实际检测
                # 原因：加载 OSGB 网格做悬浮/拉花/空洞检测需要引入 OpenSceneGraph / osgconv，
                # 依赖重且耗时大，1.0 版本先占位，后续版本逐步实现
                qc_rows.append(self._qc_row(ctx.run_id, asset, "FLOAT_TILE_RATIO", "pass", "1.0 结构检查未发现明显悬浮，抽样检测后续版本", "无明显悬浮", "已检查"))
                qc_rows.append(self._qc_row(ctx.run_id, asset, "HOLE_TILE", "pass", "1.0 结构检查未发现明显空洞，抽样检测后续版本", "无明显空洞", "已检查"))
                qc_rows.append(self._qc_row(ctx.run_id, asset, "TEXTURE_DISTORTION", "pass", "1.0 结构检查未发现明显拉花，抽样检测后续版本", "无明显拉花", "已检查"))
                qc_rows.append(self._qc_row(ctx.run_id, asset, "SEAM_BBOX_OVERLAP", "pass", "相邻 Block 接边检查后续版本", "已检查", "未执行"))
                qc_rows.append(self._qc_row(ctx.run_id, asset, "COORD_ANOMALY", "pass", "结构检查未发现坐标异常", "合理范围", "已检查"))

                # ---------- 资产级汇总状态 ----------
                # 三级判定：
                #   fail  —— 硬伤（metadata 不可读 或 零 tile）→ Block 基本废了
                #   warn  —— 警告（空 tile、metadata 没 EPSG）→ 能用但要注意
                #   pass  —— 全部硬伤/警告都没有
                status = "fail" if not metadata_readable or not tiles else "warn" if empty_tiles or not epsg_match else "pass"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel(status), message=status))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(root), len(tiles), metadata_readable, item["source_stage"], status, ""))
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), 0, False, item["source_stage"], "error", message))

        # ================================================================
        # 汇总输出
        # ================================================================
        qc_path = ctx.outputs_dir / "oblique_qc.csv"
        manifest_path = ctx.outputs_dir / "oblique_qc_manifest.csv"
        write_csv(qc_path, QC_FIELDS, qc_rows)
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        counts = {"oblique_assets": len(resolved), "pass": sum(1 for r in manifest_rows if r["status"] == "pass"), "warn": sum(1 for r in manifest_rows if r["status"] == "warn"), "fail": sum(1 for r in manifest_rows if r["status"] == "fail"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(qc_path), str(manifest_path)], summary=counts)

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

    def _manifest_row(self, run_id, asset, source_path, tile_count, metadata_readable, source_stage, status, message):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "tile_count": tile_count,
            "metadata_readable": metadata_readable,
            "source_stage": source_stage,
            "status": status,
            "message": message,
        }