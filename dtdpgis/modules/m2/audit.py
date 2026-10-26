"""CRS 审计工具"""

from __future__ import annotations

from pathlib import Path
from typing import Any
# 项目结果、质检等级、运行上下文、工具结果、工具规格模型
from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register# 注册工具
from dtdpgis.core.tools import BaseTool# 基础工具类
# 导入通用工具函数
from dtdpgis.modules.m2._common import aggregate_level, ensure_assets_file, load_assets, write_csv

# 审计字段
AUDIT_FIELDS = [
    "run_id",          # 运行ID
    "asset_id",        # 资产ID
    "category",        # 类别
    "format",          # 格式
    "relative_path",   # 相对路径
    "declared_crs",    # 声明的CRS
    "target_crs",      # 目标CRS
    "rule_id",         # 规则ID
    "level",           # 等级
    "message",         # 消息
    "expected",        # 期望值
    "actual",          # 实际值
]


def _make_crs(value: Any):
    """创建CRS对象"""
    from pyproj import CRS

    if value is None:
        return None
    try:
        if isinstance(value, dict):
            if value.get("wkt"):
                return CRS.from_wkt(value["wkt"])
            if value.get("authority") and value.get("code") is not None:
                return CRS.from_authority(value["authority"], int(value["code"]))
        return CRS.from_user_input(value)# 从用户输入创建CRS对象
    except Exception:
        return None


def _crs_label(value: Any) -> str:
    """创建CRS标签"""
    crs = _make_crs(value)
    if crs is None:
        return ""
    epsg = crs.to_epsg()
    if epsg:
        return f"EPSG:{epsg}"
    return crs.name


def _rule_level(rules: dict[str, Any], rule_id: str, default: str = "warn") -> str:
    """获取规则等级"""
    rule = rules.get(rule_id, {})
    level = rule.get("level", default) if isinstance(rule, dict) else rule
    return str(level or default).lower()


def _check(rule_id: str, level: str, message: str, expected: Any, actual: Any) -> dict[str, Any]:
    """创建检查项"""
    return {"rule_id": rule_id, "level": level, "message": message, "expected": expected, "actual": actual}


def _audit_asset(
    asset: dict[str, Any],                    # 待审计的资产字典，包含 asset_id、crs、bounds、category 等字段
    target_crs: Any,                          # 项目目标 CRS 对象（pyproj.CRS），用于对比基准一致性
    target_epsg: int | None,                  # 项目目标 EPSG 代码，仅用于上下文传递
    target_central_meridian: float | None,    # 目标中央经线（度），用于检查投影分带是否匹配
    target_unit: str | None,                  # 目标单位（如 degree、metre），用于检查单位一致性
    vertical_datum: str | None,               # 项目要求的垂直基准名称，用于检查高程基准声明
    rules: dict[str, Any],                    # 审计规则配置字典，可覆盖各检查项的默认等级
    assumed_crs: dict[str, str],              # 假定的 CRS 映射表，键为 asset_id 或 relative_path
) -> list[dict[str, Any]]:
    """审计单个资产的 CRS 声明及相关属性，返回检查结果列表。

    检查流程：
      1. CRS 是否声明 → 缺失时尝试 assumed_crs 兜底
      2. CRS 是否可被识别(pyproj 能否解析）
      3. 地理坐标系下坐标范围是否合理（经度 ≤360,纬度 ≤90)
      4. 坐标单位是否与项目目标一致
      5. 投影坐标系的中央经线是否与项目目标分带匹配
      6. 水平基准(datum)是否与项目目标一致
      7. 栅格/点云资产是否声明了垂直基准
    """
    checks: list[dict[str, Any]] = []          # 收集所有检查项的列表
    asset_id = asset.get("asset_id")           # 资产唯一标识
    declared = asset.get("crs")                # 资产声明的 CRS（原始值，可能是 dict、字符串或 None）
    crs = _make_crs(declared)                  # 将声明值转换为 pyproj.CRS 对象，解析失败返回 None
    declared_label = _crs_label(declared) if declared else ""  # 声明 CRS 的可读标签（如 EPSG:4326）

    # ---------- 分支一：资产未声明 CRS ----------
    if not declared:
        # 先按 asset_id 查假定 CRS，再按 relative_path 查
        override = assumed_crs.get(asset_id) or assumed_crs.get(asset.get("relative_path", ""))
        if override:
            # 存在配置兜底：记录警告级检查项，说明使用了假定的 CRS
            checks.append(
                _check(
                    "CRS_ASSUMED_BY_CONFIG",
                    _rule_level(rules, "CRS_ASSUMED_BY_CONFIG", "warn"),
                    "缺失 CRS,已使用项目配置显式指定",
                    "已声明",
                    f"缺失，假定 {override}",
                )
            )
        else:
            # 无兜底：记录失败级检查项，CRS 完全缺失
            checks.append(
                _check(
                    "CRS_MISSING",
                    _rule_level(rules, "CRS_MISSING", "fail"),
                    "无 CRS 声明",
                    "已声明",
                    "未声明",
                )
            )
    # ---------- 分支二：资产声明了 CRS 但无法解析 ----------
    elif crs is None:
        checks.append(
            _check(
                "CRS_DECLARED_UNKNOWN",
                _rule_level(rules, "CRS_DECLARED_UNKNOWN", "fail"),
                "CRS 声明无法识别",
                "可识别",
                declared,
            )
        )
    # ---------- 分支三：资产声明了 CRS 且解析成功 ----------
    else:
        # 先记录一条通过的检查项，表明 CRS 可识别
        checks.append(
            _check(
                "CRS_DECLARED_UNKNOWN",
                "pass",
                "CRS 声明可识别",
                "可识别",
                declared_label,
            )
        )
        # --- 检查 1：地理坐标系下坐标范围是否合理 ---
        bounds = asset.get("bounds")
        if crs.is_geographic and bounds and all(v is not None for v in bounds):# 检查地理坐标系下是否有范围
            xmin, ymin, xmax, ymax = bounds
            # 地理坐标系经纬度有效值：经度 [-360, 360]，纬度 [-90, 90]
            inconsistent = abs(xmin) > 360 or abs(xmax) > 360 or abs(ymin) > 90 or abs(ymax) > 90
            checks.append(
                _check(
                    "CRS_VALUE_INCONSISTENT",
                    _rule_level(rules, "CRS_VALUE_INCONSISTENT", "fail") if inconsistent else "pass",
                    "CRS 与坐标数值单位不一致" if inconsistent else "CRS 与坐标数值一致",
                    "经纬度范围",
                    bounds,
                )
            )

        # --- 检查 2：坐标单位与项目目标是否一致 ---
        unit = crs.axis_info[0].unit_name if crs.axis_info else None  # 取第一个坐标轴的单位名
        if target_unit and unit and unit != target_unit:# 检查单位是否与项目目标一致
            checks.append(
                _check(
                    "UNIT_MISMATCH",
                    _rule_level(rules, "UNIT_MISMATCH", "warn"),
                    f"单位为 {unit}，与目标单位 {target_unit} 不一致",
                    target_unit,
                    unit,
                )
            )

        # --- 检查 3：投影坐标系的中央经线是否匹配项目分带 ---
        if crs.is_projected and target_central_meridian is not None:# 检查投影坐标系是否有中央经线
            lon_0 = crs.to_dict().get("lon_0")  # 从投影 CRS 参数字典提取中央经线
            # 使用 1e-9 作为浮点比较容差
            if lon_0 is not None and abs(float(lon_0) - float(target_central_meridian)) > 1e-9:# 检查中央经线是否与项目目标分带匹配
                checks.append(
                    _check(
                        "CM_OUT_OF_PROJECT_BAND",
                        _rule_level(rules, "CM_OUT_OF_PROJECT_BAND", "warn"),
                        "中央经线与项目目标分带不一致",
                        target_central_meridian,
                        lon_0,
                    )
                )

        # --- 检查 4：水平基准是否与项目目标一致 ---
        if target_crs is not None:
            source_datum = getattr(getattr(crs, "datum", None), "name", "")        # 资产 CRS 的基准名称
            target_datum = getattr(getattr(target_crs, "datum", None), "name", "")  # 目标 CRS 的基准名称
            if source_datum and target_datum and source_datum != target_datum:# 检查基准是否与项目目标一致
                checks.append(
                    _check(
                        "DATUM_TRANSFORM_REQUIRED",
                        _rule_level(rules, "DATUM_TRANSFORM_REQUIRED", "warn"),
                        "基准与项目目标不一致，需要基准转换参数",
                        target_datum,
                        source_datum,
                    )
                )

    # ---------- 独立检查：栅格/点云资产是否声明垂直基准 ----------
    # 无论 CRS 声明状态如何，只要项目要求了垂直基准且资产类别为 raster 或 pointcloud，就记录未声明警告
    if vertical_datum and asset.get("category") in {"raster", "pointcloud"}:
        checks.append(
            _check(
                "VERTICAL_DATUM_UNDECLARED",
                _rule_level(rules, "VERTICAL_DATUM_UNDECLARED", "warn"),
                "资产未携带高程基准声明",
                vertical_datum,
                "未声明",
            )
        )

    return checks  # 返回所有检查结果（包含 pass/warn/fail 三种等级）


@register
class CrsAudit(BaseTool):
    spec = ToolSpec(
        id="crs.audit",
        name="坐标基准审计",
        module="m2",
        description="审计资产 CRS 声明、单位、中央经线、范围一致性和基准。",
        params={
            "input_dirs": {"type": "list[path]", "title": "输入数据目录", "required": False},
            "target_epsg": {"type": "integer", "title": "目标 EPSG 代码", "required": False},
        },
        inputs=["input_dirs"],
        outputs=["crs_audit.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        """干运行，不写入输出文件"""
        assets = load_assets(ctx, params)
        return {"assets": len(assets), "note": "dry-run does not write outputs"}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行审计"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        from pyproj import CRS

        assets_file, inventory_result = ensure_assets_file(ctx, params)# 确保资产文件存在
        if inventory_result is not None:# 如果资产文件不存在，返回错误结果
            return inventory_result# 返回错误结果
        params = dict(params)# 复制参数字典，避免修改原始参数
        params["assets_file"] = str(assets_file)# 设置资产文件路径
        assets = load_assets(ctx, params)# 加载资产
        config = ctx.profile.get("m2", {})# 获取配置参数
        project_crs = config.get("project_crs", {})# 获取项目坐标系参数
        audit_cfg = config.get("audit", {})# 获取审计配置
        target_epsg = params.get("target_epsg") or project_crs.get("target_epsg")# 获取目标 EPSG 代码
        target_crs = CRS.from_epsg(int(target_epsg)) if target_epsg else None# 创建目标坐标系
        target_cm = project_crs.get("target_central_meridian")# 获取目标中央经线
        target_unit = project_crs.get("target_unit")# 获取目标单位
        vertical_datum = project_crs.get("vertical_datum")# 获取目标垂直基准
        rules = audit_cfg.get("checks", {})# 获取审计规则
        assumed_crs = audit_cfg.get("assumed_crs", {}) or {}# 获取假设坐标系

        rows: list[dict[str, Any]] = []# 存储审计结果的列表
        item_results: list[ItemResult] = []# 存储每个资产的审计结果
        target_label = _crs_label({"authority": "EPSG", "code": target_epsg}) if target_epsg else ""# 获取目标坐标系标签

        for asset in assets:
            checks = _audit_asset(
                asset,
                target_crs,
                target_epsg,
                target_cm,
                target_unit,
                vertical_datum,
                rules,
                assumed_crs,
            )
            for check in checks:
                rows.append(
                    {
                        "run_id": ctx.run_id,
                        "asset_id": asset["asset_id"],
                        "category": asset["category"],
                        "format": asset["format"],
                        "relative_path": asset["relative_path"],
                        "declared_crs": _crs_label(asset.get("crs")) if asset.get("crs") else "",
                        "target_crs": target_label,
                        **check,
                    }
                )
            levels = [item["level"] for item in checks]# 提取所有检查等级
            item_results.append(
                ItemResult(
                    asset_id=asset["asset_id"],
                    level=QCLevel(aggregate_level(levels)),
                    message="; ".join(item["message"] for item in checks if item["level"] != "pass"),
                    rule_ids=[item["rule_id"] for item in checks if item["level"] != "pass"],
                )
            )

        output_path = ctx.outputs_dir / "crs_audit.csv"
        write_csv(output_path, AUDIT_FIELDS, rows)
        counts = {"assets": len(assets), "pass": 0, "warn": 0, "fail": 0}# 初始化统计字典
        for item in item_results:
            counts[item.level.value] += 1# 统计每个等级的资产数量
        return ToolResult(
            tool_id=self.spec.id,
            run_id=ctx.run_id,
            items=item_results,
            outputs=[str(output_path)],
            summary=counts,
        )