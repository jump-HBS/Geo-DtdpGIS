# M2 坐标与基准 功能配置设计

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`、`2026-10-17-m1-data-ingest-design.md`
- 范围：仅坐标审计与矢量/栅格批量重投影，不包含点云重投影和 OSGB 内部坐标转换

## 1. 定位与边界

M2 是 M1 之后的第一道处理模块。它的核心不是“会转换坐标”，而是把多源数据统一到项目指定的时空基准，并留下可复核的审计证据。

M2 做：

- 审计 CRS 声明、单位、中央经线、范围一致性和高程基准声明。
- 对源 CRS 明确的矢量与栅格执行批量重投影。
- 对跨带、跨投影边界、基准转换缺失给出显式告警。
- 输出审计清单和重投影清单。

M2 不做：

- 不自动猜测缺失 CRS，不静默执行 `set_crs`。
- 不做七参数/四参数完整求解与基准转换，仅标记 `DATUM_TRANSFORM_REQUIRED`。
- 不重投影 LAS/LAZ，不修改点云头部。
- 不深入 OSGB 内部 tile 坐标，只审计 metadata 中的 CRS 声明。
- 不覆盖 `inputs/`。

## 2. 业务映射

M2 对应岗位职责第 2 条，并服务以下 51WORLD 场景：

- 城市数字底座：多来源矢量/栅格统一到 CGCS2000 高斯克吕格 3 度带。
- 水利数字孪生：流域、库区、工程级多层级数据统一基准。
- 实景三维交付：DOM、DEM、矢量与倾斜摄影在发布前保持同一平面和高程基准。

业务上的默认参考基准是 2000 国家大地坐标系、3 度分带高斯克吕格投影和 1985 国家高程基准。具体 EPSG、中央经线和目标分辨率必须按项目配置，不写死在代码中。

## 3. 与 M1 的衔接

M2 不重新扫描磁盘，消费 M1 的标准输出：

```text
outputs/<run_id>/assets.json        -> M2 的资产来源
outputs/<run_id>/qc_summary.csv     -> M1 坐标相关异常的过滤线索
```

衔接规则：

1. M2 只读取 `assets.json`，使用其中的 `asset_id`、`source_path`、`category`、`format`、`crs`、`bounds`。
2. M1 的 `qc_summary.csv` 中 `rule_id` 包含 `common.crs_declared` 或 `common.bounds_within_project` 的记录优先进入 M2 审计。
3. M1 结论为 fail 的资产默认不进入 M2 重投影，只有审计通过或用户显式覆盖后才处理。
4. M2 不使用已经删除的 `qc_exceptions.csv`，统一使用 M1 的 `qc_summary.csv`。

## 4. M2 工具

M2 1.0 最终配置为三个工具。

### 4.1 `crs.audit`

职责：不修改数据，对每个资产生成一行或多行审计结论。

输入：

- `assets.json`
- 可选的 `qc_summary.csv`
- `profiles/<project>.yaml` 中的 `m2.audit` 规则

审计项：

| rule_id | 含义 | 默认等级 |
|---|---|---|
| `CRS_MISSING` | 无 CRS 声明 | fail |
| `CRS_ASSUMED_BY_CONFIG` | 缺失 CRS，但项目 YAML 已显式指定源 CRS | warn |
| `CRS_DECLARED_UNKNOWN` | 有 CRS，但无法从 pyproj/GDAL 识别 | fail |
| `CRS_VALUE_INCONSISTENT` | 声明 CRS 与 bounds 数值单位明显矛盾 | fail |
| `UNIT_MISMATCH` | 单位为度，但进入米制项目流程 | warn |
| `CM_OUT_OF_PROJECT_BAND` | 中央经线与项目目标分带不一致 | warn |
| `DATUM_TRANSFORM_REQUIRED` | 基准不是项目目标基准，需要七参数/四参数或格网改正 | warn |
| `VERTICAL_DATUM_UNDECLARED` | 高程基准未声明 | warn |

补充规则：

- `CRS_MISSING` 不允许自动 `set_crs` 后转换。只有用户在项目 YAML 中显式配置 `assumed_crs` 时，才把该资产标记为 `CRS_ASSUMED_BY_CONFIG`，并在后续重投影中使用该显式指定的源 CRS。
- `CRS_VALUE_INCONSISTENT` 使用声明 CRS 的轴单位和 bounds 数值范围判断，不能仅凭数值猜 EPSG。
- `UNIT_MISMATCH` 和 `CM_OUT_OF_PROJECT_BAND` 是重投影候选，不应误判为文件损坏。

输出：

- `outputs/<run_id>/crs_audit.csv`

### 4.2 `crs.batch_reproject_vector`

职责：批量重投影源 CRS 已明确的矢量资产。

输入：

- `assets.json`
- `crs_audit.csv`
- 目标 CRS、输出格式和坐标操作配置

处理流程：

1. 筛选 `category=vector` 且 `crs` 非空、审计结论不为 fail 的资产。
2. 对 GeoPackage 等多图层数据，1.0 处理默认图层；存在多图层时在清单中标记 `layer`。
3. 使用 `pyproj.Transformer`，`always_xy=True`，并记录使用的坐标操作精度。
4. 对跨带或跨投影边界资产，dry-run 中明确提示，不静默处理。
5. 调用 `geopandas.to_crs` 重投影。
6. 校验重投影后几何有效性和要素数量。
7. 写入 `outputs/<run_id>/vector/`，文件名保留 `asset_id` 前缀和原始主文件名。
8. 记录源 CRS、目标 CRS、要素数变化、bounds 变化、坐标操作。

输出：

- `outputs/<run_id>/vector/`
- `outputs/<run_id>/vector_reproject_manifest.csv`

### 4.3 `crs.batch_reproject_raster`

职责：批量重投影源 CRS 已明确的栅格资产。

处理流程：

1. 筛选 `category=raster` 且 `crs` 非空、审计结论不为 fail 的资产。
2. 读取源 CRS、transform、尺寸、分辨率和 Nodata。
3. 使用 `rasterio.warp.calculate_default_transform` 计算目标网格。
4. 按项目配置设置目标分辨率、网格对齐、压缩和分块。
5. 按数据类型配置重采样：DEM 使用双线性或三次卷积，DOM 按项目配置，分类栅格必须使用最近邻。
6. 显式设置目标 Nodata，避免空洞被填成 0。
7. 逐波段 `reproject`，输出到 `outputs/<run_id>/raster/`。
8. 记录尺寸变化、分辨率变化、Nodata 和重采样方法。

输出：

- `outputs/<run_id>/raster/`
- `outputs/<run_id>/raster_reproject_manifest.csv`

## 5. 输出物

一次 M2 运行结束后，`outputs/<run_id>/` 新增：

```text
outputs/<run_id>/
  crs_audit.csv
  vector_reproject_manifest.csv
  raster_reproject_manifest.csv
  vector/
  raster/
```

不生成 `crs_audit_summary.json` 和 `m2_report_section.json`。汇总和 HTML 报告由 L0 报告服务直接从 CSV 和运行状态生成，避免中间 JSON 冗余。

`crs_audit.csv` 采用与 M1 `qc_summary.csv` 一致的长表结构：

```csv
run_id,asset_id,category,format,relative_path,declared_crs,target_crs,rule_id,level,message,expected,actual
20261017_143000,vec_0002,vector,GeoJSON,02_矢量/道路.geojson,,EPSG:4548,CRS_MISSING,fail,已声明,已声明,未声明
20261017_143000,vec_0004,vector,Shapefile,02_矢量/园区边界.shp,EPSG:4549,EPSG:4548,CM_OUT_OF_PROJECT_BAND,warn,中央经线 117,中央经线 117,中央经线 120
```

## 6. 配置

```yaml
m2:
  project_crs:
    target_epsg: 4548
    target_central_meridian: 117
    target_unit: metre
    vertical_datum: "1985国家高程基准"
  audit:
    assumed_crs: {}
    checks:
      CRS_MISSING: {level: fail}
      CRS_ASSUMED_BY_CONFIG: {level: warn}
      CRS_DECLARED_UNKNOWN: {level: fail}
      CRS_VALUE_INCONSISTENT: {level: fail}
      UNIT_MISMATCH: {level: warn}
      CM_OUT_OF_PROJECT_BAND: {level: warn}
      DATUM_TRANSFORM_REQUIRED: {level: warn}
      VERTICAL_DATUM_UNDECLARED: {level: warn}
  vector_reproject:
    output_format: GPKG
    always_xy: true
    allow_ballpark: false
  raster_reproject:
    resampling:
      DEM: bilinear
      DOM: bilinear
      thematic: nearest
    target_resolution: null
    grid_align: project
    compression: LZW
    tiled: true
    nodata: null
```

说明：`EPSG:4548` 和中央经线 117 是示例，实际由项目规范确定。`assumed_crs` 只接受用户显式配置，不接受自动推断。

## 7. 与后续模块的衔接

- M3 读取 `vector_reproject_manifest.csv` 中重投影后的矢量路径，进行拓扑治理。
- M4 读取 `raster_reproject_manifest.csv` 中重投影后的栅格路径，进行空洞和异常值修复。
- M5 使用 M2 的 CRS 审计结果确认点云/倾斜摄影的坐标状态，但点云和 OSGB 的重投影不在 M2 范围内。
- L0 使用 `asset_id` 和 manifest 中的 `source_asset_id`、`output_path` 串联处理链。

## 8. 执行与错误处理

- 每个工具先 dry-run，展示待审计/待重投影资产数量和预计异常。
- 单个资产失败不中断批处理，写入 manifest 并继续。
- 跨带、跨边界和基准转换缺失不静默处理，全部进入审计或清单。
- 输出不覆盖 M1 输出，也不覆盖输入数据。
- 任务状态写入 SQLite，支持断点续跑。

## 9. M2 验收标准

1. 能从 M1 `assets.json` 生成 CRS 审计清单。
2. 缺失 CRS 的资产不自动重投影，除非项目 YAML 显式指定 `assumed_crs`。
3. 声明为经纬度但 bounds 为米制投影值的资产被标记为不一致。
4. 跨带资产被标记，不静默转换。
5. 栅格重投影生成新的网格、transform 和 CRS，不只是修改元数据。
6. 分类栅格使用最近邻，DEM/DOM 使用配置的重采样方法。
7. 重投影结果写入独立 `vector/` 和 `raster/` 目录，不覆盖输入。
