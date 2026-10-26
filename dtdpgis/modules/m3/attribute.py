"""属性批量规范化工具"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register# 注册工具类
from dtdpgis.core.tools import BaseTool# 导入基础工具类
from dtdpgis.modules.m3._common import QC_FIELDS, output_path_for_asset, read_csv_rows, resolve_vector_inputs, write_csv

# 属性规范化工具的清单字段
MANIFEST_FIELDS = [
    "run_id",               # 运行 ID
    "asset_id",             # 资产 ID
    "relative_path",        # 相对路径
    "source_path",          # 源路径
    "output_path",          # 输出路径
    "layer",                # 图层名
    "feature_count",        # 要素数量
    "renamed_fields",       # 重命名字段
    "null_fixed",           # 空值修复
    "required_missing",     # 缺失必填字段
    "domain_violations",    # 值域违规
    "alias_mapped",         # 别名映射
    "status",               # 状态
]


def _schema_for_asset(schemas: dict[str, Any], asset_id: str, layer: str | None) -> dict[str, Any]:
    """按优先级查找资产/图层对应的属性规范化 schema。

    查找顺序 (从精确到模糊):
    1. by_asset_id[asset_id]       —— 针对某个具体资产的专属 schema
    2. by_layer[layer]             —— 同一图层名复用的通用 schema
    3. {}                          —— 未配置时返回空字典，后续各步骤自然跳过

    一个 schema 内部通常包含 field_map / required / value_domain / value_alias / null_policy 等键。
    """
    # 优先级 1: 按资产 ID 精确匹配 (最具体的覆盖)
    by_asset = schemas.get("by_asset_id", {})
    if asset_id in by_asset:
        return by_asset[asset_id]
    # 优先级 2: 按图层名匹配 (多资产共享同一图层 schema)
    by_layer = schemas.get("by_layer", {})
    if layer and layer in by_layer:
        return by_layer[layer]
    # 兜底: 空 schema → 调用方后续对 field_map / required 等的 .get() 也会安全返回空
    return {}


def _null_count(series: Any) -> int:
    """计算序列中空值的数量"""
    return int(series.isna().sum())


@register
class AttributeBatchNormalize(BaseTool):# 属性批量规范化工具类
    spec = ToolSpec(
        id="attribute.batch_normalize",
        name="属性字段规范化",
        module="m3",
        description="按项目 schema 重命名字段、统一空值、校验必填和值域。",
        params={"input_dirs": {"type": "list[path]", "title": "输入矢量数据目录", "required": False}},
        inputs=["assets.json", "topology_repair_manifest.csv"],
        outputs=["vector_normalized/", "attribute_normalize_manifest.csv", "attribute_qc.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        """干运行，不实际写入输出文件"""
        resolved = self._resolve_inputs(ctx, params)
        return {"vector_assets": len(resolved), "note": "dry-run does not write outputs"}

    def _resolve_inputs(self, ctx: RunContext, params: dict[str, Any]) -> list[dict[str, Any]]:
        """解析输入矢量数据目录"""
        topology_rows = read_csv_rows(Path(params.get("topology_manifest") or ctx.outputs_dir / "topology_repair_manifest.csv"))
        topology_by_asset = {
            row["asset_id"]: row
            for row in topology_rows
            if row.get("asset_id") and row.get("status") in {"pass", "partial_success"} and row.get("output_path")
        }# 按资产 ID 紛选出已修复的拓扑数据
        resolved = resolve_vector_inputs(ctx, params)# 解析输入矢量数据目录
        result = []
        for item in resolved:
            asset_id = item["asset"]["asset_id"]
            topology = topology_by_asset.get(asset_id)# 查找资产对应的拓扑数据
            if topology:
                item["input_path"] = Path(topology["output_path"])
                item["source_stage"] = "m3_repaired"
                item["layer"] = topology.get("layer") or item.get("layer")
                item["skip_reason"] = ""
            result.append(item)
        return result

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行属性批量规范化"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import geopandas as gpd

        config = ctx.profile.get("m3", {}).get("attribute_normalize", {})# 获取属性规范化配置
        schemas = config.get("schemas", {})# 获取属性规范化 schema
        default_null_policy = config.get("default_null_policy", "empty_string")# 获取默认空值策略
        output_dir = ctx.outputs_dir / "vector_normalized"# 输出目录
        output_dir.mkdir(parents=True, exist_ok=True)# 创建输出目录
        resolved = self._resolve_inputs(ctx, params)# 解析输入矢量数据目录
        manifest_rows: list[dict[str, Any]] = []# 表格数据
        qc_rows: list[dict[str, Any]] = []# 校验结果数据
        items: list[ItemResult] = []# 项目结果数据

        for item in resolved:
            asset = item["asset"]# 获取资产信息
            asset_id = asset["asset_id"]# 获取资产 ID
            if item["input_path"] is None:# 如果输入路径为空
                message = f"跳过，{item['skip_reason']}"# 构建跳过消息
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))# 添加跳过结果
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, "", "", item.get("layer"), 0, 0, 0, 0, 0, 0, "skip", message))# 添加跳过表格数据
                continue

            try:
                gdf = gpd.read_file(item["input_path"], layer=item.get("layer"))# 读取矢量数据
                layer = item.get("layer") or Path(item["input_path"]).stem# 获取图层名
                schema = _schema_for_asset(schemas, asset_id, layer)# 获取属性规范化 schema
                field_map = schema.get("field_map", {})# 获取字段映射
                required = schema.get("required", [])# 获取必填字段
                value_domain = schema.get("value_domain", {})# 获取值域
                value_alias = schema.get("value_alias", {})# 获取值别名
                null_policy = schema.get("null_policy", default_null_policy)# 获取空值策略

                renamed_fields = 0# 重命名字段计数
                for old_name, new_name in field_map.items():
                    if old_name in gdf.columns and new_name not in gdf.columns:# 如果旧字段存在且新字段不存在
                        gdf = gdf.rename(columns={old_name: new_name})# 重命名字段
                        renamed_fields += 1# 重命名字段计数增加
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "FIELD_RENAMED", "pass", "字段已重命名", old_name, new_name))

                null_fixed = 0# 空值统一计数
                if null_policy == "empty_string":# 如果空值策略为统一为空字符串
                    for column in gdf.select_dtypes(include="object").columns:# 遍历所有对象类型字段
                        before = _null_count(gdf[column])# 统计空值数量
                        if before:# 如果有空值
                            gdf[column] = gdf[column].fillna("")# 填充为空字符串
                            null_fixed += before# 空值统一计数增加
                            qc_rows.append(self._qc_row(ctx.run_id, asset, "NULL_UNIFIED", "pass", "空值已统一为空字符串", before, 0))
                elif isinstance(null_policy, dict):# 如果空值策略为字典
                    for column, policy in null_policy.items():# 遍历空值策略字典
                        if column not in gdf.columns or policy == "keep":# 如果字段不存在或策略为保持
                            continue
                        before = _null_count(gdf[column])# 统计空值数量
                        if policy == "empty_string" and before:# 如果策略为统一为空字符串且有空值
                            gdf[column] = gdf[column].fillna("")# 填充为空字符串
                            null_fixed += before# 空值统一计数增加
                            qc_rows.append(self._qc_row(ctx.run_id, asset, "NULL_UNIFIED", "pass", "空值已统一为空字符串", before, 0))

                required_missing = 0# 必填字段缺失计数
                for field in required:# 遍历必填字段
                    if field not in gdf.columns:# 如果字段不存在
                        required_missing += len(gdf)# 必填字段缺失计数增加
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "REQUIRED_FIELD", "fail", "必填字段缺失", "存在", "字段不存在"))
                    else:
                        missing = _null_count(gdf[field]) + int(gdf[field].astype(str).str.strip().eq("").sum())# 统计空值数量
                        if missing:# 如果有空值
                            required_missing += missing# 必填字段缺失计数增加
                            qc_rows.append(self._qc_row(ctx.run_id, asset, "REQUIRED_FIELD", "fail", "必填字段缺失", "非空", f"{missing} 条为空"))
                        else:# 如果没有空值
                            qc_rows.append(self._qc_row(ctx.run_id, asset, "REQUIRED_FIELD", "pass", "必填字段完整", "非空", "非空"))

                domain_violations = 0# 值域校验失败计数
                for field, domain in value_domain.items():# 遍历值域字典
                    if field not in gdf.columns:# 如果字段不存在
                        continue
                    valid_mask = gdf[field].isin(domain)# 创建有效掩码
                    count = int((~valid_mask & gdf[field].notna() & gdf[field].astype(str).str.strip().ne("")).sum())# 统计无效值数量
                    domain_violations += count# 值域校验失败计数增加
                    if count:# 如果有无效值
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "VALUE_DOMAIN", "warn", "值域越界", domain, f"{count} 条"))
                    else:# 如果没有无效值
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "VALUE_DOMAIN", "pass", "值域校验通过", domain, "全部匹配"))

                alias_mapped = 0# 别名映射计数
                for field, aliases in value_alias.items():# 遍历值别名字典
                    if field not in gdf.columns:# 如果字段不存在
                        continue
                    mapped = gdf[field].map(aliases)# 映射别名
                    count = int(mapped.notna().sum())# 统计映射后的非空值数量
                    alias_mapped += count# 别名映射计数增加
                    gdf[field] = mapped.where(mapped.notna(), gdf[field])# 替换原始字段
                    if count:# 如果有映射后的非空值
                        qc_rows.append(self._qc_row(ctx.run_id, asset, "VALUE_ALIAS", "pass", "别名已映射", "原值", f"{count} 条"))

                output_path = output_path_for_asset(ctx, "vector_normalized", asset)
                gdf.to_file(output_path, driver="GPKG")# 写入处理后的矢量数据
                status = "pass" if required_missing == 0 and domain_violations == 0 else "warn"# 如果没有必填字段缺失和值域校验失败
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.PASS if status == "pass" else QCLevel.WARN, message=status))# 添加处理结果
                manifest_rows.append(
                    self._manifest_row(
                        ctx.run_id,
                        asset,
                        str(item["input_path"]),
                        str(output_path),
                        layer,
                        len(gdf),
                        renamed_fields,
                        null_fixed,
                        required_missing,
                        domain_violations,
                        alias_mapped,
                        status,
                        "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                items.append(ItemResult(asset_id=asset_id, level=QCLevel.FAIL, message=message))# 添加处理结果
                manifest_rows.append(self._manifest_row(ctx.run_id, asset, str(item["input_path"]), "", item.get("layer"), 0, 0, 0, 0, 0, 0, "error", message))

        manifest_path = ctx.outputs_dir / "attribute_normalize_manifest.csv"# 输出处理后的矢量数据清单
        qc_path = ctx.outputs_dir / "attribute_qc.csv"# 输出质量检查结果
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)# 写入处理后的矢量数据清单
        write_csv(qc_path, QC_FIELDS, qc_rows)# 写入质量检查结果
        counts = {"vector_assets": len(resolved), "pass": sum(1 for r in manifest_rows if r["status"] == "pass"), "warn": sum(1 for r in manifest_rows if r["status"] == "warn"), "skip": sum(1 for r in manifest_rows if r["status"] == "skip"), "error": sum(1 for r in manifest_rows if r["status"] == "error")}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(manifest_path), str(qc_path)], summary=counts)

    def _qc_row(self, run_id, asset, rule_id, level, message, expected, actual):
        """创建质量检查结果行"""
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

    def _manifest_row(self, run_id, asset, source_path, output_path, layer, feature_count, renamed_fields, null_fixed, required_missing, domain_violations, alias_mapped, status, message):
        """创建处理后的矢量数据清单行"""
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "relative_path": asset["relative_path"],
            "source_path": source_path,
            "output_path": output_path,
            "layer": layer or "",
            "feature_count": feature_count,
            "renamed_fields": renamed_fields,
            "null_fixed": null_fixed,
            "required_missing": required_missing,
            "domain_violations": domain_violations,
            "alias_mapped": alias_mapped,
            "status": status,
            "message": message,
        }