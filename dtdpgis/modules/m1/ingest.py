"""Ingest 入口级可接受性质检(QC)工具。

对已扫描的资产列表执行 7+ 条规则检查，产出 pass / warn / fail 分级，
并将 fail/warn 资产隔离复制到 quarantine 目录。
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

from dtdpgis.core.encoding import read_text_auto  # 自动识别编码读取文本文件
# 导入资产、项目结果、质检等级、运行上下文、工具结果、工具规格模型
from dtdpgis.core.models import Asset, ItemResult, QCLevel, RunContext, ToolResult, ToolSpec  
from dtdpgis.core.registry import register  # 工具注册装饰器
from dtdpgis.core.tools import BaseTool  # 工具基类
from dtdpgis.modules.m1.inventory import InventoryBatchScan  # 前置的批量资产扫描工具


def _rule_level(rules: dict[str, Any], rule_id: str) -> str:
    """从配置 rules 中取出指定 rule_id 的等级(off / fail / warn)，缺失时默认 warn。"""
    rule = rules.get(rule_id, {})
    return str(rule.get("level", "warn")).lower()


# ============================================================
# 以下每个 _check_* 函数都返回 5 元组：
#   (rule_id, default_level, message, expected, actual)
#   rule_id       — 规则唯一标识，如 "common.readable"
#   default_level — 本次检查的原始判定等级（pass / fail / warn）
#   message       — 人类可读的判定描述
#   expected      — 期望值
#   actual        — 实际值
# ============================================================


def _check_readable(asset: Asset) -> tuple[str, str, str, Any, Any]:
    """规则 1:资产能否被成功读取（上游扫描时是否报错）。"""
    #.status: 上游 inventory 扫描时资产的状态
    if asset.status == "error":  # 上游 inventory 扫描时已标记为错误
        return "common.readable", "fail", "资产无法读取", "可读取", "; ".join(asset.errors)
    return "common.readable", "pass", "资产可读取", "可读取", "可读取"


def _check_non_empty(asset: Asset) -> tuple[str, str, str, Any, Any]:
    """规则 2:资产是否为空数据(要素数 / 尺寸 / 点数 / tile 数是否为 0)。"""
    if asset.category == "vector" and asset.feature_count == 0:#.category: 资产类型
        return "common.non_empty", "fail", "矢量要素数为 0", "大于 0", 0
    if asset.category == "raster" and asset.raster and (asset.raster.get("width", 0) == 0 or asset.raster.get("height", 0) == 0):
        return "common.non_empty", "fail", "栅格尺寸为 0", "大于 0", asset.raster
    if asset.category == "pointcloud" and asset.pointcloud and asset.pointcloud.get("point_count") == 0:
        return "common.non_empty", "fail", "点云点数为 0", "大于 0", 0
    if asset.category == "oblique" and asset.oblique and asset.oblique.get("tile_count", 0) == 0:
        return "common.non_empty", "fail", "倾斜摄影 tile 数为 0", "大于 0", 0
    return "common.non_empty", "pass", "资产非空", "非空", "非空"


def _check_sidecars(asset: Asset) -> tuple[str, str, str, Any, Any]:
    """规则 3:Shapefile 等多文件格式的旁系文件（.shx/.dbf/.prj 等）是否齐全。"""
    if not asset.sidecars:  # 非 Shapefile，无需检查,.sidecars: 旁系文件信
        return "common.sidecar_complete", "pass", "无需附属文件检查", "无", "无"
    missing = asset.sidecars.get("missing", [])
    if missing:
        return "common.sidecar_complete", "fail", "Shapefile 附属文件缺失", "齐全", f"缺失 {missing}"
    return "common.sidecar_complete", "pass", "Shapefile 附属文件齐全", "齐全", "齐全"


def _check_crs(asset: Asset, allow_missing_for: list[str]) -> tuple[str, str, str, Any, Any]:
    """规则 4:资产是否声明了坐标系(CRS)。某些数据类型可配置为允许缺失。"""
    if asset.crs:
        return "common.crs_declared", "pass", "CRS 已声明", "已声明", asset.crs
    if asset.category in allow_missing_for:  # 配置里写了该类型允许无 CRS
        return "common.crs_declared", "pass", "该类型允许 CRS 缺失", "可选", "未声明"
    return "common.crs_declared", "fail", "缺少 CRS", "已声明", "未声明"


def _check_bounds(asset: Asset, extent: list[float] | None, buffer_m: float) -> tuple[str, str, str, Any, Any]:
    """规则 5:资产空间范围是否在项目配置的 extent 范围内（含 buffer 容差）。
    配置项目范围需要在 profiles/default.yaml:10 中配置。"""
    if not extent or not asset.bounds:  # 没配项目范围 或 资产本身无范围
        return "common.bounds_within_project", "pass", "未配置范围或资产无范围", "无检查", "无检查"
    xmin, ymin, xmax, ymax = extent  # 项目范围 [xmin, ymin, xmax, ymax]
    amin, bmin, amax, bmax = asset.bounds  # 资产范围
    # 四个方向都要在 extent ± buffer_m 内才算通过
    inside = amin >= xmin - buffer_m and bmin >= ymin - buffer_m and amax <= xmax + buffer_m and bmax <= ymax + buffer_m
    if inside:
        return "common.bounds_within_project", "pass", "范围在项目范围内", "范围内", asset.bounds
    return "common.bounds_within_project", "warn", "范围超出项目范围", f"{extent}", asset.bounds


def _check_duplicate(asset: Asset, seen: dict[tuple[str, int], str]) -> tuple[str, str, str, Any, Any]:
    """规则 6:是否存在疑似重复资产（相对路径同名且文件大小相同）。

    seen 字典 key = (路径小写, 文件大小),value = 首次遇到的 asset_id。
    """
    key = (asset.relative_path.lower(), asset.size_bytes)   # 键 = (路径小写, 文件大小)
    previous = seen.get(key)                                # 获取首次遇到的 asset_id
    if previous and previous != asset.asset_id:             # key 已存在且不是自己 → 重复
        seen[key] = asset.asset_id                          # 更新为当前 asset_id
        return "common.duplicate", "warn", "发现疑似重复资产", "无重复", previous
    seen[key] = asset.asset_id
    return "common.duplicate", "pass", "未发现重复资产", "无重复", "无重复"


def _check_category(asset: Asset) -> list[tuple[str, str, str, Any, Any]]:
    """规则 7+：按数据类别的专属规则检查（返回列表，每种类别可能多条）。"""
    checks: list[tuple[str, str, str, Any, Any]] = []
    if asset.category == "vector":
        # 矢量专属：要素数必须 > 0
        if asset.feature_count is not None and asset.feature_count <= 0:
            checks.append(("vector.feature_count_positive", "fail", "矢量要素数为 0", "大于 0", asset.feature_count))
        else:
            checks.append(("vector.feature_count_positive", "pass", "矢量要素数有效", "大于 0", asset.feature_count))
    if asset.category == "raster":
        # 栅格专属：宽/高/波段数都必须 > 0
        if asset.raster and asset.raster.get("width", 0) > 0 and asset.raster.get("height", 0) > 0 and asset.raster.get("bands", 0) > 0:
            checks.append(("raster.dimensions_positive", "pass", "栅格尺寸有效", "有效", asset.raster))
        else:
            checks.append(("raster.dimensions_positive", "fail", "栅格尺寸无效", "有效", asset.raster))
    if asset.category == "oblique":
        # 倾斜摄影专属：tile 数 > 0，且建议有 metadata.xml
        tile_count = asset.oblique.get("tile_count", 0) if asset.oblique else 0
        checks.append(
            ("oblique.tile_count_positive", "pass" if tile_count > 0 else "fail", "tile 数有效", "大于 0", tile_count)
        )
        has_metadata = asset.oblique.get("has_metadata", False) if asset.oblique else False
        checks.append(
            ("oblique.has_metadata", "pass" if has_metadata else "warn", "metadata 存在", "存在", has_metadata)
        )
    return checks


def _run_asset_checks(asset: Asset, rules: dict[str, Any], extent: list[float] | None, buffer_m: float, seen: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
    """对单个资产执行全部质检规则，返回规整后的字典列表（供写 CSV 用）。

    流程：先跑所有 _check_* 得到原始 5 元组列表 → 应用配置覆盖等级(off 就跳过) → 产出 dict。
    """
    allow_missing_for = rules.get("common.crs_declared", {}).get("allow_missing_for", [])
    # 按顺序执行 6 条通用规则 + 按类别展开的专属规则
    raw_checks = [
        _check_readable(asset),
        _check_non_empty(asset),
        _check_sidecars(asset),
        _check_crs(asset, allow_missing_for),
        _check_bounds(asset, extent, buffer_m),
        _check_duplicate(asset, seen),
        *_check_category(asset),  # 展开，可能 1~2 条
    ]
    results = []
    for rule_id, default_level, message, expected, actual in raw_checks:
        # 用配置文件里的等级覆盖默认等级；off 表示跳过该规则
        configured_level = _rule_level(rules, rule_id)
        if configured_level == "off":
            continue
        # pass 永远保留 pass，fail/warn 可被配置文件提升/降级
        if default_level == "pass":
            level = "pass"
        else:
            level = configured_level
        results.append(
            {
                "asset_id": asset.asset_id,# 资产 ID
                "category": asset.category,# 数据类别
                "format": asset.format,    # 数据格式
                "relative_path": asset.relative_path,# 相对路径
                "rule_id": rule_id,        # 规则 ID
                "level": level,            # 等级
                "message": message,        # 消息
                "expected": expected,      # 期望值
                "actual": actual,          # 实际值
            }
        )
    return results


def _aggregate_level(levels: list[str]) -> str:
    """聚合一个资产所有单条规则的等级 → 最终等级:fail > warn > pass。"""
    if "fail" in levels:
        return "fail"
    if "warn" in levels:
        return "warn"
    return "pass"


@register# 工具注册装饰器，注册到工具列表中,spec工具规格
class IngestBatchQC(BaseTool):
    spec = ToolSpec(
        id="ingest.batch_qc",
        name="数据质检",
        module="m1",
        description="对输入数据目录直接执行扫描和入口级可接受性检查，输出 pass、warn、fail 和隔离清单。",
        params={
            "input_dirs": {"type": "list[path]", "title": "输入数据目录", "required": False},
        },
        inputs=["input_dirs"],
        outputs=["qc_summary.csv", "quarantine_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        """模拟运行工具，返回结果。"""
        if params.get("input_dirs"):
            inventory_preview = InventoryBatchScan().dry_run(ctx, params)
            return {"mode": "scan_and_qc", "inventory_preview": inventory_preview, "note": "将先扫描输入目录，再对资产执行质检"}
        assets = self._load_assets(ctx, params)
        return {"mode": "qc_existing_assets", "assets": len(assets), "checks_per_asset": 7, "note": "dry-run does not write outputs"}

    def _load_assets(self, ctx: RunContext, params: dict[str, Any]) -> list[Asset]:
        """从 assets.json 读取资产列表，返回 Asset 对象列表。"""
        assets_file = Path(params.get("assets_file") or ctx.outputs_dir / "assets.json")# 资产列表文件路径
        data = json.loads(read_text_auto(assets_file))# 反序列化读取资产列表文件内容
        return [Asset.model_validate(item) for item in data.get("assets", [])]# 从 JSON 数据中提取资产列表

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行工具，返回结果。"""
        if params.get("input_dirs"):# 如果提供了输入目录
            # 执行资产扫描
            inventory_result = InventoryBatchScan().execute(ctx, {"input_dirs": params["input_dirs"], "source_manifest": params.get("source_manifest")})
            # 如果扫描失败，直接返回结果
            if inventory_result.status == "failed":
                return inventory_result
            params = dict(params)# 复制参数字典，避免修改原始参数
            params["assets_file"] = str(ctx.outputs_dir / "assets.json")# 资产列表文件路径
        assets = self._load_assets(ctx, params)# 从资产列表文件中加载资产
        config = ctx.profile.get("m1", {}).get("ingest_qc", {})# 获取配置文件中的质检规则
        rules = config.get("rules", {})# 获取质检规则
        extent = config.get("project_extent")# 获取项目范围
        buffer_m = float(config.get("extent_buffer_m", 500))# 获取缓冲区大小
        quarantine_on = config.get("quarantine_on", ["fail"])# 获取隔离清单
        seen: dict[tuple[str, int], str] = {}# 已检查资产字典，键为资产 ID 和版本号，值为资产 ID
        checks: list[dict[str, Any]] = []# 质检结果列表
        quarantine_rows: list[dict[str, Any]] = []# 隔离清单列表
        item_results: list[ItemResult] = []# 资产质检结果列表

        for asset in assets:
            asset_checks = _run_asset_checks(asset, rules, extent, buffer_m, seen)# 对资产执行质检
            checks.extend(asset_checks)# 合并质检结果
            levels = [item["level"] for item in asset_checks]# 提取所有单条规则的等级
            level = _aggregate_level(levels)# 聚合等级，最终等级:fail > warn > pass
            item_results.append(
                ItemResult(
                    asset_id=asset.asset_id,
                    level=QCLevel(level),# 质检等级
                    message="; ".join(item["message"] for item in asset_checks if item["level"] != "pass") or "质检通过",# 质检消息
                    details={"relative_path": asset.relative_path, "format": asset.format},# 资产详情，文件路径和格式类型
                    rule_ids=[item["rule_id"] for item in asset_checks if item["level"] != "pass"],# 质检失败的规则 ID 列表
                )
            )
            if level in quarantine_on:# 如果资产等级在隔离清单中
                source_path = self._source_path(ctx, asset)# 获取资产源文件路径
                quarantine_path = ctx.quarantine_dir / Path(asset.relative_path).name# 隔离清单文件路径
                copied = False# 是否复制文件到隔离清单目录，默认不复制
                if source_path.is_file():# 如果资产源文件存在
                    quarantine_path.parent.mkdir(parents=True, exist_ok=True)# 创建隔离清单目录，如果不存在
                    shutil.copy2(source_path, quarantine_path)# 复制资产源文件到隔离清单目录
                    copied = True# 标记为已复制到隔离清单目录
                quarantine_rows.append(
                    # 隔离清单列表添加资产信息
                    {
                        "run_id": ctx.run_id,# 运行 ID
                        "asset_id": asset.asset_id,# 资产 ID
                        "source_path": str(source_path),# 资产源文件路径
                        "quarantine_path": str(quarantine_path) if copied else "",# 隔离清单文件路径，如果未复制则为空
                        "copied": copied,# 是否复制文件到隔离清单目录
                        "rule_id": ",".join(item["rule_id"] for item in asset_checks if item["level"] == "fail"),# 质检失败的规则 ID 列表
                        "reason": "; ".join(item["message"] for item in asset_checks if item["level"] == "fail"),# 质检失败的原因
                        "sha256": asset.sha256 or "",# 资产 SHA256 哈希值，如果为空则为空字符串
                    }
                )

        qc_path = ctx.outputs_dir / "qc_summary.csv"# 质检结果文件路径
        with qc_path.open("w", newline="", encoding="utf-8-sig") as handle:
            # 写入质检结果到 CSV 文件
            writer = csv.DictWriter(
                handle,
                fieldnames=["run_id", "asset_id", "category", "format", "relative_path", "rule_id", "level", "message", "expected", "actual"],
            )
            writer.writeheader()
            for check in checks:
                row = {"run_id": ctx.run_id, **check}# 合并运行 ID 和质检结果
                writer.writerow(row)

        quarantine_path = ctx.outputs_dir / "quarantine_manifest.csv"
        with quarantine_path.open("w", newline="", encoding="utf-8-sig") as handle:
            # 写入隔离清单到 CSV 文件
            writer = csv.DictWriter(
                handle,
                fieldnames=["run_id", "asset_id", "source_path", "quarantine_path", "copied", "rule_id", "reason", "sha256"],
            )
            writer.writeheader()
            writer.writerows(quarantine_rows)

        counts = {"assets": len(assets), "pass": 0, "warn": 0, "fail": 0, "quarantined": len(quarantine_rows)}# 统计资产数量和等级等级
        for item in item_results:
            counts[item.level.value] += 1# 统计每个等级的资产数量
        return ToolResult(
            # 返回工具执行结果
            tool_id=self.spec.id,
            run_id=ctx.run_id,
            status="success",
            items=item_results,
            outputs=[str(qc_path), str(quarantine_path)],
            summary=counts,
        )

    def _source_path(self, ctx: RunContext, asset: Asset) -> Path:
        """获取资产源文件路径，优先使用 asset.source_path,其次尝试 ctx.project_root 下的相对路径。"""
        if asset.source_path:
            source = Path(asset.source_path)
            if source.exists():
                return source
        candidates = [
            ctx.project_root / asset.relative_path,# 尝试项目根目录下的相对路径
            ctx.project_root / "inputs" / asset.relative_path,# 尝试 inputs 目录下的相对路径
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]# 如果所有候选路径都不存在，返回第一个候选路径