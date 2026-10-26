"""Topology batch repair tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register# 注册工具
from dtdpgis.core.tools import BaseTool# 基础工具类
from dtdpgis.modules.m3._common import QC_FIELDS, output_path_for_asset, resolve_vector_inputs, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "output_path",
    "layer",
    "feature_count",
    "invalid_before",
    "invalid_after",
    "repaired_count",
    "marked_count",
    "failed_count",
    "source_stage",
    "status",
]


def _remove_repeated_vertices(geom: Any, tolerance: float) -> Any:
    """去除几何中相邻的冗余顶点。

    判定规则: 相邻两点欧氏距离 <= tolerance 时视为重复，后一个被丢弃。
    仅处理 Polygon / MultiPolygon / LineString / MultiLineString；
    其他几何类型 (Point, MultiPoint, GeometryCollection) 原样返回。

    Args:
        geom: 待清理的 Shapely 几何对象。
        tolerance: 距离阈值，单位与几何坐标系一致 (投影坐标为米，地理坐标为度)。

    Returns:
        清理后的几何；若清理后不满足类型的最低顶点数约束，则返回原几何不变。
    """
    from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

    def clean_line(line: LineString) -> LineString:
        """贪心策略去除 LineString 上的相邻重复顶点。

        从头遍历，仅保留与上一个已保留顶点距离 > tolerance 的点。
        若清理后剩余顶点 < 2 (LineString 最低要求)，则放弃清理返回原线。
        """
        coords = list(line.coords)
        if not coords:
            return line
        kept = [coords[0]]                              # 起点必然保留
        for coord in coords[1:]:
            # 仅与上一个已保留点做距离比较 —— 保证"相邻重复"才被去除，
            # 跨段但不相邻的近邻点不会被误伤
            if Point(coord).distance(Point(kept[-1])) > tolerance:
                kept.append(coord)
        if len(kept) < 2:                               # 清理失败: 剩 0-1 个点，无法构成线
            return line
        return LineString(kept)

    # ---- Polygon ---- 外环 + 所有内环分别清理；外环闭合多边形最少需 4 个顶点
    # (首尾同点 + 3 个顶点构成有效面)，不够则放弃清理
    if isinstance(geom, Polygon):
        exterior = clean_line(geom.exterior)# .exterior 外环线
        interiors = [clean_line(ring) for ring in geom.interiors]# .interiors 内环线
        if len(exterior.coords) >= 4:
            return Polygon(exterior, interiors)# 返回清理后的多边形
        return geom
    # ---- MultiPolygon ---- 递归处理每个子 Polygon，保证多级嵌套都能被清理
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([_remove_repeated_vertices(p, tolerance) for p in geom.geoms])
    # ---- LineString / MultiLineString ---- 直接用 clean_line 处理
    if isinstance(geom, (LineString, MultiLineString)):
        if isinstance(geom, LineString):
            return clean_line(geom)
        return MultiLineString([clean_line(line) for line in geom.geoms])
    # ---- 其他类型 ---- 不做处理，原样返回
    return geom


def _explain(geom: Any) -> str:
    """解释几何有效性问题。

    若几何无效，返回无效原因；若有效，返回"valid"。
    """
    from shapely.validation import explain_validity

    try:
        return explain_validity(geom).lower()
    except Exception:
        return "valid"


def _count_vertices(geom: Any) -> int:
    """统计几何中的顶点数。
    递归处理 MultiPolygon / MultiLineString / MultiPoint。
    """
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Point":
        return 1
    if geom.geom_type in {"LineString", "LinearRing"}:
        return len(geom.coords)
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords) + sum(len(r.coords) for r in geom.interiors)
    if geom.geom_type.startswith("Multi"):
        return sum(_count_vertices(part) for part in geom.geoms)
    if geom.geom_type == "GeometryCollection":# 它是一个包含多个几何对象的类型集合
        return sum(_count_vertices(part) for part in geom.geoms)
    return 0


def _ring_count(geom: Any) -> int:
    """统计几何中的环数。
    递归处理 MultiPolygon。
    """
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Polygon":
        return 1 + len(geom.interiors)
    if geom.geom_type == "MultiPolygon":
        return sum(_ring_count(part) for part in geom.geoms)
    return 0


def _slender_ratio(geom: Any) -> float:
    """计算几何的狭长比。"""
    minx, miny, maxx, maxy = geom.bounds
    width = maxx - minx
    height = maxy - miny
    if width == 0 or height == 0:
        return float("inf")
    return max(width, height) / min(width, height)


def _repair_geometry(geom: Any, config: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """修复几何有效性问题。
    若几何无效，返回修复后的几何；若有效，返回原几何不变。
    """
    from shapely.validation import make_valid

    checks: list[dict[str, Any]] = []
    repaired = geom# 复制原几何，避免修改原数据
    explain = _explain(geom)
    if "self-intersection" in explain:
        checks.append({"rule_id": "SELF_INTERSECTION", "before": 1, "after": 0, "message": "自相交已修复"})
        repaired = make_valid(repaired)
    if "ring" in explain or "hole" in explain or "shell" in explain:
        checks.append({"rule_id": "INVALID_RING", "before": 1, "after": 0, "message": "无效环已修复"})
        repaired = make_valid(repaired)
    if not repaired.is_valid:# 空几何
        repaired = make_valid(repaired)

    tolerance = float(config.get("snap_tolerance", 0.001))
    after_vertices = _count_vertices(repaired)
    deduped = _remove_repeated_vertices(repaired, tolerance)# 去除连续重复顶点
    if _count_vertices(deduped) != after_vertices:# 有重复顶点
        checks.append(
            {
                "rule_id": "REPEATED_VERTICES",
                "before": after_vertices - _count_vertices(deduped),
                "after": 0,
                "message": "连续重复顶点已去除",
            }
        )
        repaired = deduped
    return repaired, checks


@register
class TopologyBatchRepair(BaseTool):
    spec = ToolSpec(
        id="topology.batch_repair",
        name="矢量拓扑修复",
        module="m3",
        description="修复自相交、无效环和重复顶点，标记狭长面。",
        params={"input_dirs": {"type": "list[path]", "title": "输入矢量数据目录", "required": False}},
        inputs=["assets.json", "vector_reproject_manifest.csv"],
        outputs=["vector_repaired/", "topology_repair_manifest.csv", "topology_qc.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_vector_inputs(ctx, params)
        return {"vector_assets": len(resolved), "skipped": sum(1 for item in resolved if item["input_path"] is None)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import geopandas as gpd

        config = ctx.profile.get("m3", {}).get("topology_repair", {})# 从配置文件获取拓扑修复参数
        sliver_area = float(config.get("sliver_area_threshold", 1.0))# 狭长面面积阈值
        sliver_ratio = float(config.get("sliver_ratio_threshold", 10.0))# 狭长面阈值
        output_dir = ctx.outputs_dir / "vector_repaired"# 输出目录
        output_dir.mkdir(parents=True, exist_ok=True)# 确保输出目录存在
        resolved = resolve_vector_inputs(ctx, params)# 解析输入矢量数据目录
        manifest_rows: list[dict[str, Any]] = []# 修复后的矢量数据清单
        qc_rows: list[dict[str, Any]] = []# 质量检查结果
        items: list[ItemResult] = []# 质量检查结果

        for item in resolved:
            asset = item["asset"]
            asset_id = asset["asset_id"]
            if item["input_path"] is None:
                message = f"跳过，{item['skip_reason']}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", "", item.get("layer"), 0, 0, 0, 0, 0, 0, item["source_stage"], "skip", item["skip_reason"]))
                continue

            try:
                gdf = gpd.read_file(item["input_path"], layer=item.get("layer"))
                layer = item.get("layer") or Path(item["input_path"]).stem
                feature_count = len(gdf)
                invalid_before = int((~gdf.geometry.is_valid).sum())# 修复前无效几何数量
                repaired_count = 0# 修复的几何数量
                marked_count = 0# 标记的狭长面数量
                failed_count = 0# 失败的几何数量
                asset_checks: list[dict[str, Any]] = []# 检查结果

                for index, geometry in enumerate(gdf.geometry):
                    if geometry is None or geometry.is_empty:
                        failed_count += 1
                        continue
                    repaired, checks = _repair_geometry(geometry, config)# 修复几何
                    gdf.loc[index, gdf.geometry.name] = repaired# 更新矢量数据中的几何
                    if checks:
                        repaired_count += 1# 修复的几何数量增加
                        asset_checks.extend(checks)# 合并检查结果
                    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                        area = geometry.area
                        ratio = _slender_ratio(geometry)
                        if area < sliver_area and ratio > sliver_ratio:
                            marked_count += 1
                            asset_checks.append({"rule_id": "SLIVER_POLYGON", "before": 1, "after": 1, "message": "狭长面已标记未删除"})

                invalid_after = int((~gdf.geometry.is_valid).sum())# 修复后无效几何数量
                failed_count = max(failed_count, invalid_after)# 失败的几何数量增加
                output_path = output_path_for_asset(ctx, "vector_repaired", asset)
                gdf.to_file(output_path, driver="GPKG", layer=layer)# 保存修复后的矢量数据
                status = "pass" if invalid_after == 0 else "partial_success"# 修复结果
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.PASS if status == "pass" else QCLevel.WARN, message=status))
                manifest_rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        str(item["input_path"]),
                        str(output_path),
                        layer,
                        feature_count,
                        invalid_before,
                        invalid_after,
                        repaired_count,
                        marked_count,
                        failed_count,
                        item["source_stage"],
                        status,
                        "",
                    )
                )
                for check in asset_checks:
                    qc_rows.append(
                        {
                            "run_id": ctx.run_id,
                            "asset_id": asset_id,
                            "category": asset["category"],
                            "format": asset["format"],
                            "relative_path": asset["relative_path"],
                            "rule_id": check["rule_id"],
                            "level": "fail" if check["rule_id"] in {"SELF_INTERSECTION", "INVALID_RING"} else "warn",
                            "message": check["message"],
                            "expected": check["before"],
                            "actual": check["after"],
                        }
                    )
            except Exception as exc:
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), "", item.get("layer") or Path(item["input_path"]).stem, 0, 0, 0, 0, 0, 0, item["source_stage"], "error", message))

        manifest_path = ctx.outputs_dir / "topology_repair_manifest.csv"
        qc_path = ctx.outputs_dir / "topology_qc.csv"
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
        write_csv(qc_path, QC_FIELDS, qc_rows)
        counts = {"vector_assets": len(resolved), "success": sum(1 for r in manifest_rows if r["status"] in {"pass", "partial_success"}), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(manifest_path), str(qc_path)], summary=counts)

    def _manifest_row(self, run_id, asset, source_path, output_path, layer, feature_count, invalid_before, invalid_after, repaired_count, marked_count, failed_count, source_stage, status, message):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "output_path": output_path,
            "layer": layer or "",
            "feature_count": feature_count,
            "invalid_before": invalid_before,
            "invalid_after": invalid_after,
            "repaired_count": repaired_count,
            "marked_count": marked_count,
            "failed_count": failed_count,
            "source_stage": source_stage,
            "status": status,
            "message": message,
        }