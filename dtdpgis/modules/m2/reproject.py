"""Vector and raster batch reprojection tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any
# 项目结果、质检等级、运行上下文、工具结果、工具规格模型
from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register# 注册工具
from dtdpgis.core.tools import BaseTool# 基础工具类
from dtdpgis.modules.m2._common import asset_source_path,ensure_assets_file,load_assets,read_csv_rows,write_csv


VECTOR_MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",# 相对路径
    "source_path",# 源路径
    "output_path",# 输出路径
    "layer",# 图层名称
    "source_crs",# 源坐标系
    "target_crs",# 目标坐标系
    "feature_count",# 特征数量
    "invalid_after",# 无效时间
    "status",# 状态
    "message",# 消息
]

RASTER_MANIFEST_FIELDS = [
    "run_id",# 运行ID
    "asset_id",# 资产ID
    "relative_path",# 相对路径
    "source_path",# 源路径
    "output_path",# 输出路径
    "source_crs",# 源坐标系
    "target_crs",# 目标坐标系
    "src_width",# 源宽度
    "src_height",# 源高度
    "dst_width",# 目标宽度
    "dst_height",# 目标高度
    "src_res",# 源分辨率
    "dst_res",# 目标分辨率
    "resampling",# 重采样方法
    "nodata",# 无数据值
    "status",# 状态
    "message",# 消息
]


def _make_crs(value: Any):
    """将多种形式的 CRS 输入统一转换为 pyproj.CRS 对象。

    支持以下输入格式:
    - None: 直接返回 None
    - dict 含 "wkt" 键: 从 WKT 字符串构建 CRS
    - dict 含 "authority" + "code": 从权威机构代码构建 (如 EPSG:4326)
    - 其他任意可被 pyproj 解析的形式 (字符串、整数等)

    转换失败时返回 None,不抛出异常。
    """
    from pyproj import CRS

    # 空值直接透传，调用方据此判断是否缺少 CRS 配置
    if value is None:
        return None
    try:
        # dict 形式: 优先识别 wkt，其次识别 authority + code 组合
        if isinstance(value, dict):
            if value.get("wkt"):
                return CRS.from_wkt(value["wkt"])
            if value.get("authority") and value.get("code") is not None:
                return CRS.from_authority(value["authority"], int(value["code"]))
        # 兜底: 交由 pyproj 自动识别 (字符串 "EPSG:4326"、整数 4326、URL 等)
        return CRS.from_user_input(value)
    except Exception:
        # 任何解析异常均吞掉，返回 None 让上游决定如何处理
        return None


def _crs_label(crs: Any) -> str:
    """将 pyproj.CRS 对象转换为标签字符串。"""
    if crs is None:
        return ""
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def _audit_fail_rules(ctx: RunContext, params: dict[str, Any]) -> dict[str, set[str]]:
    """从审核文件中提取失败的重投影规则。"""
    audit_file = Path(params.get("audit_file") or ctx.outputs_dir / "crs_audit.csv")
    result: dict[str, set[str]] = {}
    for row in read_csv_rows(audit_file):
        if row.get("level") == "fail":
            result.setdefault(row["asset_id"], set()).add(row["rule_id"])
    return result


def _assumed_crs_for(asset: dict[str, Any], config: dict[str, Any]) -> str | None:
    """根据资产ID或相对路径获取假设的CRS标签。"""
    assumed = config.get("audit", {}).get("assumed_crs", {}) or {}
    return assumed.get(asset.get("asset_id")) or assumed.get(asset.get("relative_path"))


@register# 注册工具
class CrsBatchReprojectVector(BaseTool):
    spec = ToolSpec(
        id="crs.batch_reproject_vector",
        name="矢量批量重投影",
        module="m2",
        description="批量重投影源 CRS 明确的矢量资产。",
        params={
            "input_dirs": {"type": "list[path]", "title": "输入数据目录", "required": False},
            "target_epsg": {"type": "integer", "title": "目标 EPSG 代码", "required": False},
        },
        inputs=["input_dirs"],
        outputs=["vector/", "vector_reproject_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        assets = [a for a in load_assets(ctx, params) if a.get("category") == "vector"]
        return {"vector_assets": len(assets), "note": "dry-run does not write outputs"}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行批量重投影。"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import geopandas as gpd
        import pyogrio

        config = ctx.profile.get("m2", {})# 获取配置
        target_epsg = params.get("target_epsg") or config.get("project_crs", {}).get("target_epsg")# 获取目标EPSG代码
        target_crs = _make_crs({"authority": "EPSG", "code": int(target_epsg)}) if target_epsg else None# 创建目标CRS
        if target_crs is None:
            return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, status="failed", errors=["target_epsg is not configured"])
        from dtdpgis.modules.m2.audit import CrsAudit# 导入CRS审核工具
        # 构建审核参数
        audit_params = {"input_dirs": params["input_dirs"]} if params.get("input_dirs") else {}
        # 执行CRS审核
        CrsAudit().execute(ctx, {**audit_params, "target_epsg": target_epsg})
        assets_file, inventory_result = ensure_assets_file(ctx, params)# 确保资产文件存在
        if inventory_result is not None:
            return inventory_result
        params = dict(params)
        params["assets_file"] = str(assets_file)
        fail_rules = _audit_fail_rules(ctx, params)# 从审核文件中提取失败的重投影规则
        output_dir = ctx.outputs_dir / "vector"## 输出输出目录
        output_dir.mkdir(parents=True, exist_ok=True)# 创建输出目录
        rows: list[dict[str, Any]] = []
        items: list[ItemResult] = []

        for asset in load_assets(ctx, params):
            # 过滤出矢量资产
            if asset.get("category") != "vector":
                continue
            asset_id = asset["asset_id"]
            source_path = asset_source_path(ctx, asset)# 获取资产源路径
            assumed = _assumed_crs_for(asset, config)# 获取假设的CRS标签
            # 构建源CRS
            source_crs = _make_crs(asset.get("crs")) if asset.get("crs") else (_make_crs(assumed) if assumed else None)
            fails = fail_rules.get(asset_id, set())# 获取失败的重投影规则
            if fails and not (fails == {"CRS_MISSING"} and assumed):# 检查是否需要跳过
                message = f"跳过，审计失败: {sorted(fails)}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))# 记录失败的重投影规则
                rows.append(self._manifest_row(ctx.run_id, asset, source_path, "", "", 0, 0, "", "", "skip", message))# 记录跳过的重投影规则
                continue
            if source_crs is None:
                message = "跳过，无源 CRS 且未配置 assumed_crs"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))# 记录失败的重投影规则
                rows.append(self._manifest_row(ctx.run_id, asset, source_path, "", "", 0, 0, "", "", "skip", message))# 记录跳过的重投影规则
                continue

            try:
                layers_data = pyogrio.list_layers(str(source_path))# 获取资产源路径中的图层
                if hasattr(layers_data, "iloc"):# 检查是否为DataFrame
                    layers = list(layers_data.iloc[:, 0])# 提取图层名称
                else:
                    layers = [str(row[0]) for row in layers_data]# 提取图层名称
                layer = layers[0] if layers else None# 获取第一个图层名称
                gdf = gpd.read_file(source_path, layer=layer)# 读取资产源路径中的图层
                if gdf.crs is None:# 检查是否缺少CRS
                    gdf = gdf.set_crs(source_crs, allow_override=True)# 设置源CRS
                reprojected = gdf.to_crs(target_crs)# 重投影
                feature_count = int(len(reprojected))# 计算重投影后的特征数量
                invalid_after = int((~reprojected.geometry.is_valid).sum())# 计算无效几何数量
                stem = Path(asset["relative_path"]).stem# 提取资产相对路径的文件名
                output_path = output_dir / f"{asset_id}_{stem}.gpkg"# 构建输出路径
                reprojected.to_file(output_path, driver="GPKG", layer=layer or "layer")# 写入重投影后的矢量数据
                message = "reprojected"
                if gdf.crs is None:# 检查是否缺少CRS
                    message = "reprojected; file CRS missing, asset CRS applied"# 记录重投影后的消息
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.PASS, message=message))# 记录成功重投影的资产
                rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        source_path,
                        str(output_path),
                        layer or "",
                        feature_count,
                        invalid_after,
                        _crs_label(source_crs),
                        _crs_label(target_crs),
                        "success",
                        message,
                    )
                )
            except Exception as exc:  # 捕获所有异常
                message = str(exc)## 提取异常信息
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))# 记录失败的重投影规则
                rows.append(self._manifest_row(ctx.run_id, asset, source_path, "", "", 0, 0, "", "", "error", message))

        output_path = ctx.outputs_dir / "vector_reproject_manifest.csv"
        write_csv(output_path, VECTOR_MANIFEST_FIELDS, rows)
        counts = {"vector_assets": len(rows), "success": sum(1 for r in rows if r["status"] == "success"), "skip": sum(1 for r in rows if r["status"] == "skip"), "error": sum(1 for r in rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(output_path)], summary=counts)

    def _manifest_row(self, run_id, asset, source_path, output_path, layer, feature_count, invalid_after, source_crs, target_crs, status, message):
        """构建重投影清单行"""
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": str(source_path),
            "output_path": output_path,
            "layer": layer,
            "source_crs": source_crs,
            "target_crs": target_crs,
            "feature_count": feature_count,
            "invalid_after": invalid_after,
            "status": status,
            "message": message,
        }


@register
class CrsBatchReprojectRaster(BaseTool):
    spec = ToolSpec(
        id="crs.batch_reproject_raster",
        name="栅格批量重投影",
        module="m2",
        description="批量重投影源 CRS 明确的栅格资产。",
        params={
            "input_dirs": {"type": "list[path]", "title": "输入数据目录", "required": False},
            "target_epsg": {"type": "integer", "title": "目标 EPSG 代码", "required": False},
        },
        inputs=["input_dirs"],
        outputs=["raster/", "raster_reproject_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        assets = [a for a in load_assets(ctx, params) if a.get("category") == "raster"]
        return {"raster_assets": len(assets), "note": "dry-run does not write outputs"}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行栅格批量重投影工具"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import rasterio
        #Resampling重采样栅格数据，calculate_default_transform计算默认变换矩阵，reproject重投影栅格数据
        from rasterio.warp import Resampling, calculate_default_transform, reproject

        config = ctx.profile.get("m2", {})
        target_epsg = params.get("target_epsg") or config.get("project_crs", {}).get("target_epsg")
        target_crs = _make_crs({"authority": "EPSG", "code": int(target_epsg)}) if target_epsg else None# 构建目标CRS
        if target_crs is None:# 检查是否缺少目标CRS
            return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, status="failed", errors=["target_epsg is not configured"])
        from dtdpgis.modules.m2.audit import CrsAudit

        audit_params = {"input_dirs": params["input_dirs"]} if params.get("input_dirs") else {}# 构建审计参数
        CrsAudit().execute(ctx, {**audit_params, "target_epsg": target_epsg})# 执行CRS审计
        assets_file, inventory_result = ensure_assets_file(ctx, params)# 确保资产文件存在
        if inventory_result is not None:# 检查是否缺少资产文件
            return inventory_result
        params = dict(params)
        params["assets_file"] = str(assets_file)# 设置资产文件路径
        raster_cfg = config.get("raster_reproject", {})# 获取栅格重投影配置
        fail_rules = _audit_fail_rules(ctx, params)# 获取审计失败规则
        output_dir = ctx.outputs_dir / "raster"# 构建输出目录
        output_dir.mkdir(parents=True, exist_ok=True)# 创建输出目录
        rows: list[dict[str, Any]] = []# 重投影清单行
        items: list[ItemResult] = []# 重投影清单项

        for asset in load_assets(ctx, params):# 遍历资产
            if asset.get("category") != "raster":# 检查是否为栅格资产
                continue
            asset_id = asset["asset_id"]
            source_path = asset_source_path(ctx, asset)# 获取资产路径
            assumed = _assumed_crs_for(asset, config)# 获取假设CRS
            source_crs = _make_crs(asset.get("crs")) if asset.get("crs") else (_make_crs(assumed) if assumed else None)# 构建源CRS
            fails = fail_rules.get(asset_id, set())# 获取审计失败规则
            if fails and not (fails == {"CRS_MISSING"} and assumed):# 检查是否缺少CRS
                message = f"跳过，审计失败: {sorted(fails)}"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))# 添加失败项
                rows.append(self._manifest_row(ctx.run_id,asset,source_path,"","","",0,0,0,0,"","","","","skip",message))# 添加失败行
                continue
            if source_crs is None:
                message = "跳过，无源 CRS 且未配置 assumed_crs"
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                rows.append(self._manifest_row(ctx.run_id,asset,source_path,"","","",0,0,0,0,"","","","","skip",message,))
                continue

            try:
                with rasterio.open(source_path) as src:
                    resampling_name = self._resampling_name(asset["relative_path"], raster_cfg)# 获取重采样名称
                    resampling = getattr(Resampling, resampling_name, Resampling.nearest)# 获取重采样方法
                    target_resolution = raster_cfg.get("target_resolution")# 获取目标分辨率
                    resolution = tuple(target_resolution) if target_resolution else None# 构建分辨率元组
                    dst_transform, dst_width, dst_height = calculate_default_transform(# 计算默认变换矩阵
                        source_crs,
                        target_crs,
                        src.width,
                        src.height,
                        left=src.bounds.left,
                        bottom=src.bounds.bottom,
                        right=src.bounds.right,
                        top=src.bounds.top,
                        resolution=resolution,
                    )
                    nodata = raster_cfg.get("nodata", src.nodata)# 获取目标Nodata值
                    profile = {
                        "driver": "GTiff",
                        "height": dst_height,
                        "width": dst_width,
                        "count": src.count,
                        "dtype": src.dtypes[0],
                        "crs": target_crs,
                        "transform": dst_transform,
                        "compress": raster_cfg.get("compression", "LZW"),
                        "tiled": raster_cfg.get("tiled", True),
                        "nodata": nodata,
                    }
                    stem = Path(asset["relative_path"]).stem# 获取资产文件名
                    output_path = output_dir / f"{asset_id}_{stem}.tif"# 构建输出路径
                    with rasterio.open(output_path, "w", **profile) as dst:# 打开输出文件
                        for band_index in range(1, src.count + 1):# 遍历波段
                            reproject(
                                source=rasterio.band(src, band_index),# 获取源波段
                                destination=rasterio.band(dst, band_index),# 获取目标波段
                                src_transform=src.transform,# 获取源变换矩阵
                                src_crs=source_crs,# 获取源CRS
                                src_nodata=src.nodata,# 获取源Nodata值
                                dst_transform=dst_transform,# 获取目标变换矩阵
                                dst_crs=target_crs,# 获取目标CRS
                                dst_nodata=nodata,# 获取目标Nodata值
                                resampling=resampling,# 获取重采样方法
                            )
                    src_res = tuple(round(v, 9) for v in src.res)# 获取源分辨率
                    dst_res = tuple(round(v, 9) for v in (dst_transform.a, -dst_transform.e))# 获取目标分辨率
                    message = "reprojected"
                    items.append(ItemResult(asset_id=asset_id, level=QCLevel.PASS, message=message))
                    rows.append(
                        self._manifest_row(
                            ctx.run_id,# 获取运行ID
                            asset,# 获取资产信息
                            source_path,# 获取源路径
                            str(output_path),# 获取输出路径
                            _crs_label(source_crs),# 获取源CRS标签
                            _crs_label(target_crs),# 获取目标CRS标签
                            src.width,# 获取源宽度
                            src.height,# 获取源高度
                            dst_width,# 获取目标宽度
                            dst_height,# 获取目标高度
                            str(src_res),# 获取源分辨率
                            str(dst_res),# 获取目标分辨率
                            resampling_name,# 获取重采样名称
                            nodata,# 获取目标Nodata值
                            "success",# 获取状态
                            message,# 获取消息
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))
                rows.append(self._manifest_row(ctx.run_id,asset,source_path,"","","",0,0,0,0,"","","","","error",message,))

        output_path = ctx.outputs_dir / "raster_reproject_manifest.csv"
        write_csv(output_path, RASTER_MANIFEST_FIELDS, rows)
        counts = {"raster_assets": len(rows), "success": sum(1 for r in rows if r["status"] == "success"), "skip": sum(1 for r in rows if r["status"] == "skip"), "error": sum(1 for r in rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(output_path)], summary=counts)

    def _resampling_name(self, relative_path: str, config: dict[str, Any]) -> str:
        """根据资产路径和配置文件，获取重采样名称"""
        name = relative_path.lower()
        mapping = config.get("resampling", {})
        if "dem" in name:
            return str(mapping.get("DEM", "bilinear")).lower()
        if "dom" in name:
            return str(mapping.get("DOM", "bilinear")).lower()
        return str(mapping.get("thematic", "nearest")).lower()

    def _manifest_row(
        self,
        run_id,
        asset,
        source_path,
        output_path,
        source_crs,
        target_crs,
        src_width,
        src_height,
        dst_width,
        dst_height,
        src_res,
        dst_res,
        resampling,
        nodata,
        status,
        message,
    ):
        """根据资产信息，生成重投影清单行"""
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": str(source_path),
            "output_path": output_path,
            "source_crs": source_crs,
            "target_crs": target_crs,
            "src_width": src_width,
            "src_height": src_height,
            "dst_width": dst_width,
            "dst_height": dst_height,
            "src_res": src_res,
            "dst_res": dst_res,
            "resampling": resampling,
            "nodata": nodata,
            "status": status,
            "message": message,
        }