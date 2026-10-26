# M3 矢量清洗与拓扑 功能配置设计

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`、`2026-10-17-m1-data-ingest-design.md`、`2026-10-17-m2-crs-design.md`
- 范围：仅矢量几何修复、拓扑修复和属性规范化

## 1. 定位与边界

M3 承接 M2 重投影后的矢量，解决几何无效、拓扑缺陷和属性不规范问题。它是进入 M6 空间关系治理和三维升维前的矢量质量关卡。

M3 做：

- 修复自相交、无效环、重复顶点。
- 标记狭长面，默认不删除。
- 按项目 schema 做字段重命名、空值统一、必填校验、值域校验和别名映射。
- 输出修复前后对比和长表质检结论。

M3 不做：

- 不做 CRS 审计与重投影。
- 不处理点云、栅格和 OSGB。
- 不做面重叠、缝隙、悬挂边等跨图层拓扑治理。
- 不自动删除狭长面，不自动填充必填缺失值。
- 不修改 `inputs/` 和 M2 产物。

## 2. 业务映射

M3 对应岗位职责第 2 条，服务以下 51WORLD 场景：

- 城市数字底座：建筑轮廓、道路、水系、POI、园区边界在进入数字孪生前必须几何有效。
- 水利数字孪生：库区边界、水系面和工程范围需要有效环和统一字段。
- 三维升维前置：建筑白模依赖有效底面和规范的层数、功能、名称字段。

## 3. 与 M1、M2 的衔接

M3 不重新扫描目录，输入按以下优先级确定：

```text
对 assets.json 中 category = vector 的每个 asset_id：
  1. 若 vector_reproject_manifest.csv 中存在该 asset_id 的 success 记录
       -> 使用 M2 输出路径 vector/<output_path>
  2. 若 crs_audit.csv 中该资产没有 fail
       -> 使用 M1 原始 source_path，标记 source_stage=m1_raw
  3. 若 crs_audit.csv 中该资产存在 fail
       -> 跳过，写入 M3 排除清单
  4. 若 M1 qc_summary.csv 中该资产已隔离
       -> 跳过
```

说明：

- M3 不处理 CRS，不承担 `assumed_crs`。CRS 缺失或基准问题的处理由 M2 完成。
- 若用户跳过 M2 直接运行 M3，M3 可以读取 M1 原始矢量，但必须在 manifest 中标记 `source_stage=m1_raw`，并提示坐标基准未经 M2 统一。

## 4. M3 工具

### 4.1 `topology.batch_repair`

职责：修复单个矢量资产内部的几何与拓扑问题，输出修复前后对比。

处理流程：

1. 读取输入图层，保留 CRS、字段和图层名。
2. 对每个要素执行检查：
   - `SELF_INTERSECTION`：几何自相交。
   - `INVALID_RING`：外环或内环无效。
   - `REPEATED_VERTICES`：连续重复坐标点。
   - `SLIVER_POLYGON`：面积小于阈值且长宽比异常。
3. 按配置执行动作：
   - `repair`：使用 `shapely.make_valid`、去重顶点、重建环。
   - `mark`：仅记录，不修改。
4. 记录修复前后的要素数、顶点数、环数、面积和无效要素数。
5. 写出到 `outputs/<run_id>/vector_repaired/`。

修复策略：

- 自相交和无效环优先使用 `make_valid`，保持几何类型。
- 修复后变为 `MultiPolygon` 或 `GeometryCollection` 时记录类型变化，不强行合并。
- 重复顶点只移除连续重复点，不进行抽稀。
- 狭长面只标记，不删除。

输出：

- `outputs/<run_id>/vector_repaired/`
- `outputs/<run_id>/topology_repair_manifest.csv`
- `outputs/<run_id>/topology_qc.csv`

### 4.2 `attribute.batch_normalize`

职责：按项目 schema 规范属性字段，不修改几何。

处理流程：

1. 默认从 `vector_repaired/` 读取输入；回退到 M2 `vector/` 或 M1 原始矢量。
2. 按 `field_map` 重命名字段。
3. 按 `null_policy` 统一空值。
4. 按 `required` 校验必填字段。
5. 按 `value_domain` 校验值域。
6. 按 `value_alias` 映射同义值。
7. 写出到 `outputs/<run_id>/vector_normalized/`。

规则：

- 可确定修复项：字段重命名、空值统一、别名映射。
- 必填缺失和值域越界默认只标记，不自动填充。
- 只有 YAML 显式配置 `default_value` 或 `auto_fill` 时才填充。

输出：

- `outputs/<run_id>/vector_normalized/`
- `outputs/<run_id>/attribute_normalize_manifest.csv`
- `outputs/<run_id>/attribute_qc.csv`

## 5. 输出物

```text
outputs/<run_id>/
  vector_repaired/
  vector_normalized/
  topology_repair_manifest.csv
  topology_qc.csv
  attribute_normalize_manifest.csv
  attribute_qc.csv
```

两个长表沿用 M1、M2 的统一字段：

```csv
run_id,asset_id,category,format,relative_path,rule_id,level,message,expected,actual
```

拓扑修复长表示例：

```csv
run_003,vec_0001,vector,GPKG,vector/建筑.gpkg,SELF_INTERSECTION,fail,自相交已修复,3,0
run_003,vec_0001,vector,GPKG,vector/建筑.gpkg,REPEATED_VERTICES,warn,连续重复顶点已去除,40,0
run_003,vec_0001,vector,GPKG,vector/建筑.gpkg,SLIVER_POLYGON,warn,狭长面已标记未删除,8,8
```

属性规范化长表示例：

```csv
run_003,vec_0001,vector,GPKG,vector_repaired/建筑.gpkg,FIELD_RENAMED,pass,字段已重命名,name,id
run_003,vec_0002,vector,GPKG,vector_repaired/道路.gpkg,VALUE_ALIAS,pass,别名已映射,主干路,primary
run_003,vec_0005,vector,GPKG,vector_repaired/POI.gpkg,REQUIRED_FIELD,fail,必填字段缺失,非空,5 条为空
```

## 6. 配置

```yaml
m3:
  topology_repair:
    snap_tolerance: 0.001
    sliver_area_threshold: 1.0
    rules:
      SELF_INTERSECTION: {enabled: true, level: fail, action: repair}
      INVALID_RING: {enabled: true, level: fail, action: repair}
      REPEATED_VERTICES: {enabled: true, level: warn, action: repair}
      SLIVER_POLYGON: {enabled: true, level: warn, action: mark}
    output_format: GPKG
  attribute_normalize:
    default_null_policy: empty_string
    schemas:
      by_asset_id:
        vec_0001:
          field_map: {name: id, floor: floors, func: function}
          required: [id, floors, function]
          value_domain:
            function: [residential, commercial, industrial, public]
        vec_0005:
          required: [category]
          value_domain:
            category: [shopping, education, medical, transport]
      by_layer:
        道路:
          field_map: {level: road_level}
          required: [road_level]
          value_alias:
            road_level:
              主干路: primary
              主干道: primary
              次干路: secondary
```

说明：

- schema 优先按 `asset_id` 匹配，再按图层名匹配。
- 阈值、字段映射和值域均为示例，实际按项目规范配置。

## 7. 与后续模块的衔接

- M3 输出 `vector_repaired/` 和 `vector_normalized/`，下游 M6 优先读取 `vector_normalized/`。
- M4 不依赖 M3。
- M6 使用 M3 输出做道路/水系/POI 空间关系治理和建筑三维化。
- L0 使用 `asset_id`、`source_stage` 和 manifest 串联处理链。

## 8. 执行与错误处理

- 每个工具先 dry-run，展示待检资产、预计修复项和预计标记项。
- 单个资产失败不中断批处理，写入 manifest 并继续。
- 修复后仍无效的要素写入检查项，状态标记为 `unrepaired`。
- 输入路径不存在或 CRS 缺失时跳过并记录原因。
- 输出不覆盖 M2 产物，也不覆盖原始输入。

## 9. M3 验收标准

1. 能从 M2 `vector_reproject_manifest.csv` 找到重投影后的矢量。
2. 能跳过 M2 审计 fail 和 M1 已隔离的资产。
3. 自相交和无效环修复后几何有效。
4. 狭长面只标记，不删除。
5. 字段重命名、别名映射和空值统一按 YAML 执行。
6. 必填缺失和值域越界只标记，不自动填充。
7. 输出写入独立目录，不覆盖 M2 产物。
