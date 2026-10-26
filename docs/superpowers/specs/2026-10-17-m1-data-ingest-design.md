# M1 数据接入与质检 功能配置设计

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`
- 范围：仅 M1 数据接入与质检，不包含修复、重投影、格式转换和 AI 推理

## 1. 定位与边界

M1 是所有后续处理的入口关卡。它要回答的问题是：

- 这批数据里有什么，格式、数量、大小、来源是什么。
- 每个文件或资产能否被读取，是否完整，CRS 是否声明。
- 哪些数据可以直接进入后续流程，哪些需要人工核验，哪些应当隔离。

M1 只做普查、入口质检、标记和隔离，不做修复、重投影、转换、拓扑治理、栅格修复或三维模型处理。

## 2. 业务映射

M1 直接对应岗位职责第 1 条和第 4 条：

- 客户数据接收核验：记录供应商、批次、接收时间、用途、校验和，判断数据是否完整。
- 公开数据源筛选：识别格式、范围、坐标系，判断是否适合进入项目底板。
- 数据能不能用：对每个资产给出 pass、warn、fail，并形成可追溯清单。

M1 必须覆盖 51WORLD 业务中常见的交付数据类型：

| 类型 | 常见格式 | M1 处理深度 |
|---|---|---|
| 点云 | LAS、LAZ | 普查 + 轻量统计质检 |
| 倾斜摄影 | OSGB 目录 | 目录型资产普查 + 完整性检查 |
| 矢量 | Shapefile、GeoJSON、GeoPackage | 普查 + 可读性、要素数、CRS、附属文件检查 |
| 栅格 | GeoTIFF、IMG | 普查 + 可读性、尺寸、波段、CRS 检查 |
| CAD/BIM | DWG、DXF、IFC | 只登记来源和基本文件信息，不做解析 |
| 其他 | TXT、TMP 等 | 识别并标记为 unknown，不进入处理链 |

## 3. M1 工具

M1 1.0 最终配置为三个工具：

### 3.1 `inventory.batch_scan`

职责：递归扫描输入目录，识别格式，聚合目录型资产，读取基础元数据，生成资产清单。

输入：

- 一个或多个输入目录。
- 可选的 `source_manifest.yaml`，用于记录供应商、批次和接收信息。
- 参数：是否计算哈希、是否跟随符号链接、忽略目录、哈希算法。

处理流程：

1. 递归遍历目录，记录路径、扩展名、大小、修改时间。
2. 使用扩展名和文件头识别格式；无法识别进入 unknown。
3. 对 Shapefile 等主文件，检查并聚合 `.shx`、`.dbf`、`.prj` 等附属文件。
4. 将 OSGB 等目录型资产聚合为一个资产，统计内部 tile 文件数。
5. 按类型分发到读取器：
   - VectorReader：CRS、范围、几何类型、要素数。
   - RasterReader：CRS、范围、宽度、高度、波段、数据类型。
   - PointCloudReader：LAS/LAZ 版本、点数、范围、CRS。
   - ObliqueReader：metadata、tile 数、根节点文件。
   - CadReader：只登记，不解析。
   - UnknownReader：记录文件，不进入后续处理。
6. 计算文件哈希，写入 `assets.json` 和 `assets.csv`。

输出：

- `outputs/<run_id>/assets.json`
- `outputs/<run_id>/assets.csv`
- `outputs/<run_id>/unknown_files.csv`

### 3.2 `ingest.batch_qc`

职责：对资产清单执行入口级可接受性检查，给出 pass、warn、fail。

输入：

- `assets.json`
- `profiles/<project>.yaml` 中的 `m1.ingest_qc` 规则。

通用检查项：

- 可读性：读取器能否正常打开。
- 非空：文件或资产是否为空。
- 附属文件完整：Shapefile 的 `.shx`、`.dbf`、`.prj` 是否齐全。
- CRS 声明：是否存在坐标参考。
- 范围检查：范围是否落在项目配置范围内，或至少位于合理地理范围。
- 哈希匹配：与供应商提供的校验和是否一致。
- 重复检测：同一资产是否重复提交。

按类型检查项：

- 矢量：要素数大于 0。
- 栅格：宽度、高度、波段数大于 0。
- 倾斜摄影：tile 数大于 0，metadata 是否存在。
- 点云：基础可读性和 CRS 检查，详细统计交给 `pointcloud.batch_stats`。

规则等级：

- `pass`：进入后续处理。
- `warn`：可进入后续处理，但在报告中高亮。
- `fail`：默认复制到隔离区，不进入后续处理。

输出：

- `outputs/<run_id>/qc_summary.csv`
- `outputs/<run_id>/quarantine_manifest.csv`
- `quarantine/<run_id>/` 下的隔离文件副本

### 3.3 `pointcloud.batch_stats`

职责：对 LAS/LAZ 做轻量统计，为后续 M2 坐标处理、M5 点云处理提供依据。

输入：

- `assets.json` 中 `category=pointcloud` 且格式为 LAS/LAZ 的资产。
- 采样参数和项目基准配置。

统计内容：

- 点数。
- Z 最小值、最大值和分位数。
- 分类分布。
- 平均点间距，按抽样计算。
- CRS，来自 LAS VLR 或 WKT。
- 范围。

输出：

- `outputs/<run_id>/pointcloud_stats.csv`

命名说明：不叫 `batch_header_qc`。分类分布、点间距和 Z 分位数需要读取或抽样点数据，不是单纯读取头部，因此使用 `pointcloud.batch_stats` 更准确。

## 4. 输出物与职责

一次 M1 运行结束后的标准输出：

```text
outputs/<run_id>/
  assets.json
  assets.csv
  unknown_files.csv
  qc_summary.csv
  pointcloud_stats.csv
  quarantine_manifest.csv

quarantine/<run_id>/
  <fail 文件副本>

reports/<run_id>.html
state.db
```

去掉了 DeepSeek 方案中的 `qc_exceptions.csv` 和 `m1_report_section.json`：

- `qc_summary.csv` 已包含所有检查和等级，异常文件可直接过滤，不必再生成派生文件。
- M1 报告片段由报告服务直接从 `qc_summary.csv`、`pointcloud_stats.csv` 和运行状态生成，不需要中间 JSON。

## 5. 资产数据模型

`assets.json` 结构：

```json
{
  "run_id": "20261017_143000",
  "root": "inputs",
  "scanned_at": "2026-10-17T14:30:00+08:00",
  "source_manifest_ref": "profiles/source_manifest.yaml",
  "counts": {
    "scanned_files": 128,
    "assets": 96,
    "unknown_files": 5
  },
  "assets": [
    {
      "asset_id": "pc_0001",
      "category": "pointcloud",
      "format": "LAS",
      "source": "某测绘院",
      "batch": "BATCH-2026-1017-01",
      "relative_path": "01_点云/园区_A.las",
      "file_count": 1,
      "size_bytes": 123456789,
      "sha256": "abc...",
      "mtime": "2026-10-17T10:00:00+08:00",
      "crs": {
        "authority": "EPSG",
        "code": 4490
      },
      "bounds": [116.1, 39.8, 116.2, 39.9],
      "pointcloud": {
        "version": "1.4",
        "point_count": 15234000
      }
    },
    {
      "asset_id": "osgb_0001",
      "category": "oblique",
      "format": "OSGB",
      "source": "无人机作业队",
      "batch": "BATCH-2026-1017-02",
      "relative_path": "04_倾斜摄影/Block_A",
      "file_count": 256,
      "size_bytes": 456789012,
      "crs": null,
      "oblique": {
        "tile_count": 256,
        "has_metadata": true
      }
    }
  ]
}
```

计数字段规则：

- `scanned_files`：实际扫描到的物理文件数。
- `assets`：按业务聚合后的资产数。一个 OSGB Block 目录算一个资产，但其物理 tile 文件计入 `file_count`。
- `unknown_files`：无法识别且未聚合为资产的物理文件数。

`qc_summary.csv` 采用长表结构，一行一个检查项：

```csv
run_id,asset_id,category,format,relative_path,rule_id,level,message,expected,actual
20261017_143000,vec_0001,vector,Shapefile,02_矢量/建筑.shp,sidecar_complete,pass,附属文件齐全,4,4
20261017_143000,pc_0003,pointcloud,LAS,01_点云/建筑_C.las,crs_declared,warn,缺少CRS,已声明,未声明
20261017_143000,pc_0005,pointcloud,LAS,01_点云/空_D.las,non_empty,fail,点数为0,大于0,0
```

`quarantine_manifest.csv`：

```csv
run_id,asset_id,source_path,quarantine_path,rule_id,reason,sha256
20261017_143000,pc_0005,inputs/01_点云/空_D.las,quarantine/20261017_143000/空_D.las,non_empty,点数为0,abc...
```

## 6. 来源档案

客户数据接收核验必须记录数据来源。新增 `source_manifest.yaml`：

```yaml
project: 51World_北京园区_数字孪生_20261017
sources:
  - name: 园区机载点云
    supplier: 某测绘院
    batch: BATCH-2026-1017-01
    received_at: 2026-10-17
    purpose: 园区实景三维单体化
    directory: inputs/01_点云
    expected_crs: EPSG:4490
    contact: 张三
  - name: 建筑轮廓矢量
    supplier: 规划局
    batch: BATCH-2026-1017-03
    received_at: 2026-10-17
    purpose: 建筑白模生成
    directory: inputs/02_矢量
    expected_crs: EPSG:4490
```

`inventory.batch_scan` 将来源信息合并到对应资产，解决“同一批数据来自哪里、谁提交、用于什么”的溯源问题。

## 7. 质检规则配置

规则不写死在代码中，放在 `profiles/<project>.yaml`：

```yaml
m1:
  inventory:
    hash_algorithm: sha256
    follow_symlinks: false
    ignore_patterns:
      - "**/__pycache__/**"
      - "**/.git/**"
  ingest_qc:
    quarantine_on: [fail]
    project_extent: [116.0, 39.7, 116.5, 40.0]
    extent_buffer_m: 500
    rules:
      common.readable: {level: fail}
      common.non_empty: {level: fail}
      common.sidecar_complete: {level: fail}
      common.crs_declared: {level: fail, allow_missing_for: [cad, unknown]}
      common.bounds_within_project: {level: warn}
      common.hash_match: {level: fail}
      common.duplicate: {level: warn}
      vector.feature_count_positive: {level: fail}
      raster.dimensions_positive: {level: fail}
      oblique.tile_count_positive: {level: fail}
      oblique.has_metadata: {level: warn}
  pointcloud_stats:
    sample_size: 200000
    spacing_method: median
    z_unit: meter
    sentinel_z: [-9999, -99999, 9999, 99999]
    project_z_range: [20, 120]
```

说明：以上阈值是示例，实际以项目配置为准。Z 范围必须由项目基准定义，不使用全国或全球固定值。

## 8. 与后续模块的衔接

M1 是后续流程的唯一数据发现入口。后续模块不重新扫描磁盘，而是读取 `assets.json`：

- M2 读取 `category` 和 `crs`，筛选需要重投影的资产。
- M3 读取 `category=vector` 且 `level != fail` 的资产。
- M4 读取 `category=raster` 且 `level != fail` 的资产。
- M5 读取 `category=pointcloud` 或 `category=oblique` 的资产，并使用 `pointcloud_stats.csv` 作为参考。
- L0 使用 `asset_id`、`run_id` 和 `level` 做任务依赖和报告汇总。

下游工具的输入参数使用 `asset_id` 列表或资产清单文件，而不是再次扫描原始目录。这样避免重复解析，也保证每个模块的处理范围一致。

## 9. 执行与错误处理

- 每个工具先 dry-run，展示文件数、资产数、预计检查数和预计隔离数。
- 单文件读取失败不中断整个批处理，记录到该资产的 `ingest_errors`。
- fail 资产复制到 `quarantine/<run_id>/`，原始 `inputs/` 文件不移动、不删除。
- warn 资产保留在原位置，但在报告和清单中标记。
- 任务状态写入 SQLite，支持断点续跑。

## 10. M1 验收标准

1. 给定混合数据目录，可生成资产清单、来源信息、质检结论和隔离清单。
2. 资产清单中的 `asset_id` 稳定，后续模块可基于它传递数据。
3. Shapefile 缺失 `.prj`、`.dbf`、`.shx` 时能正确标记。
4. OSGB 目录聚合为一个资产，且 tile 数统计正确。
5. fail 资产进入隔离区，warn 资产保留并标记。
6. 所有原始输入文件未被修改。
7. 质检规则可通过项目 YAML 修改，不重新发布程序。
