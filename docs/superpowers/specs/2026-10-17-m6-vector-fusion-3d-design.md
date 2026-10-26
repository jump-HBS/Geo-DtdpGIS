# M6 矢量融合与三维化 功能配置设计

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`、`2026-10-17-m1-data-ingest-design.md`、`2026-10-17-m2-crs-design.md`、`2026-10-17-m3-vector-topology-design.md`
- 范围：仅跨图层空间关系检查和建筑规则化三维升维

## 1. 定位与边界

M6 是矢量进入数字孪生场景前的融合关卡。空间关系检查只检测、只标记，不自动修复；建筑白模负责按规则挤出三维表达。

M6 做：

- 检查道路与水系重叠、水系面重叠、建筑是否在边界内、POI 行政区归属、通用包含关系。
- 把问题要素输出到独立 GeoPackage 供人工复核。
- 按 `height` 或 `floors` 把建筑 footprint 挤出为三维白模。

M6 不做：

- 不自动裁剪、吸附或修正空间关系。
- 不做道路附属设施、BIM 轻量化和 3D Tiles 属性映射。
- 不处理点云、栅格和 OSGB。
- 不修改 M3 产物和原始输入。

## 2. 业务映射

M6 对应岗位职责第 1 条中的矢量融合，以及 51WORLD 城市数字底座、园区数字孪生和建筑白模场景：

- 道路、水系、行政区、POI、建筑在进入全要素场景前必须空间关系清晰。
- 建筑白模是数字孪生场景的基础三维要素。

## 3. 与 M1-M3、M5 的衔接

M6 不重新扫描目录，输入选择优先级：

```text
对 assets.json 中 category = vector 的每个 asset_id：
  1. attribute_normalize_manifest.csv 成功 -> vector_normalized/
  2. topology_repair_manifest.csv 成功 -> vector_repaired/
  3. vector_reproject_manifest.csv 成功 -> M2 vector/
  4. 无 M2/M3 产物 -> M1 原始 source_path，标记 m1_raw
  5. 已隔离、M2 审计 fail 或 M3 失败 -> 跳过
```

图层角色通过 YAML 映射，不靠文件名猜测：

```yaml
m6:
  layer_roles:
    building: {asset_id: vec_0001}
    road: {asset_id: vec_0002}
    water: {asset_id: vec_0003}
    boundary: {asset_id: vec_0004}
    poi: {asset_id: vec_0005}
    admin: {asset_id: vec_0007}
```

角色缺失时，依赖该角色的检查项标记 `ROLE_MISSING` 并跳过，不阻断其他检查。

## 4. M6 工具

### 4.1 `vector.batch_spatial_relation`

职责：跨图层空间关系检查，只检测、只标记。

检查项：

| rule_id | 参与角色 | 默认等级 |
|---|---|---|
| `ROAD_WATER_OVERLAP` | road, water | warn |
| `WATER_WATER_OVERLAP` | water | warn |
| `BUILDING_OUTSIDE_BOUNDARY` | building, boundary | warn |
| `POI_OUTSIDE_ADMIN` | poi, admin | fail |
| `POI_ADMIN_MISMATCH` | poi, admin | warn |
| `WATER_OUTSIDE_ADMIN` | water, admin | warn |
| `CONTAINMENT_VIOLATION` | 任意父子角色 | 按 YAML |
| `CRS_MISMATCH` | 参与图层 | fail |

处理流程：

1. 读取 `layer_roles` 并加载图层。
2. 执行 CRS 一致性前置检查。
3. 按检查项计算相交、重叠面积、包含关系和属性归属。
4. 将 warn/fail 写入 QC 长表。
5. 若启用，输出问题要素到 `spatial_relation_issues.gpkg`。

输出：

- `outputs/<run_id>/vector_spatial_relation/`
- `outputs/<run_id>/spatial_relation_qc.csv`
- `outputs/<run_id>/spatial_relation_manifest.csv`

### 4.2 `building.batch_extrude`

职责：按高度或层数把建筑 footprint 挤出为三维白模。

高度计算优先级：

1. `height` 字段有效，直接使用。
2. `floors` 字段有效，`height = floors * floor_height`。
3. 两者都无效，标记 `HEIGHT_MISSING`，不挤出。

异常处理：

- `floors` 超过 `max_floors` 时标记 `FLOORS_SUSPICIOUS`，按配置 `mark_only` 或 `skip`。
- 单建筑几何无效时标记 `EXTRUDE_FAILED`，不进入输出。

三维表达：

- GeoPackage：输出 `MULTIPOLYGON Z` 面集合白模，保留 `id/floors/height/function` 属性。
- GeoJSON：1.0 默认输出 2D footprint + `height/floors` 属性；显式配置 `3d` 时输出带 Z 坐标。

输出：

- `outputs/<run_id>/building_extruded/`
- `outputs/<run_id>/building_extrude_qc.csv`
- `outputs/<run_id>/building_extrude_manifest.csv`

## 5. 输出物

```text
outputs/<run_id>/
  vector_spatial_relation/
  building_extruded/
  spatial_relation_qc.csv
  spatial_relation_manifest.csv
  building_extrude_qc.csv
  building_extrude_manifest.csv
```

所有 QC 长表沿用统一字段：

```csv
run_id,asset_id,category,format,relative_path,rule_id,level,message,expected,actual
```

## 6. 配置

```yaml
m6:
  layer_roles: {}
  spatial_relation:
    tolerance: 0.01
    checks:
      ROAD_WATER_OVERLAP: {enabled: true, level: warn}
      WATER_WATER_OVERLAP: {enabled: true, level: warn, min_overlap_area: 1.0}
      BUILDING_OUTSIDE_BOUNDARY: {enabled: true, level: warn}
      POI_OUTSIDE_ADMIN: {enabled: true, level: fail}
      POI_ADMIN_MISMATCH: {enabled: true, level: warn, poi_admin_field: admin_name, admin_name_field: name}
      WATER_OUTSIDE_ADMIN: {enabled: true, level: warn}
    containment_rules: []
    output_issue_geometry: true
  building_extrude:
    building_role: building
    floors_field: floors
    height_field: height
    floor_height: 3.0
    max_floors: 100
    suspicious_action: mark_only
    output:
      gpkg: true
      geojson: true
      geojson_geometry: 2d_footprint
```

说明：以上参数为示例，实际以项目规范配置。

## 7. 与后续模块的衔接

- M6 输出的 `building_extruded/` 可作为后续 3D Tiles 转换或三维场景发布的输入。
- 空间关系问题清单供人工复核，不自动进入修复流程。
- L0 使用 `asset_id`、`source_stage` 和 manifest 串联处理链。

## 8. 执行与错误处理

- 空间关系检查可独立运行，建筑挤出依赖建筑角色成功加载。
- 单个建筑失败不中断批处理，写入 manifest 和 QC 长表。
- 角色缺失或字段缺失只跳过相关检查，不阻断其他检查。
- 输出不覆盖 M2/M3 产物和原始输入。

## 9. M6 验收标准

1. 能从 M3 标准化产物中选择建筑、道路、水系、边界、POI、行政区图层。
2. 图层角色缺失时输出 `ROLE_MISSING` 并跳过相关检查。
3. CRS 不一致时输出 `CRS_MISMATCH`，不执行相关空间检查。
4. 空间关系问题写入长表，可选输出问题要素 GeoPackage。
5. 建筑按 `height/floors` 成功挤出，高度缺失建筑不进入输出。
6. GeoPackage 输出三维白模，GeoJSON 默认输出 2D footprint + 高度属性。
