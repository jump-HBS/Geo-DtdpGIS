# M5 点云与倾斜摄影 功能配置设计

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`、`2026-10-17-m1-data-ingest-design.md`、`2026-10-17-m2-crs-design.md`
- 范围：仅点云地面滤波、抽稀和 OSGB Block 级质量检测

## 1. 定位与边界

M5 是点云与实景三维数据发布前的轻量处理和质量检测模块。点云工具执行处理，OSGB 工具只检测不修复。

M5 做：

- 使用 PDAL `filters.csf` 提取地面点，输出地面点 LAS 和 DEM。
- 对点云做随机或体素抽稀，默认保护地面点。
- 对 OSGB Block 做悬浮、拉花、空洞、坐标和接边的粗筛与标记。

M5 不做：

- 不做点云语义分割和精细分类。
- 不做 OSGB 内部三角面修复和纹理修补。
- 不做 OSGB 转 3D Tiles，1.0 只保留外部命令配置。
- 不做单体化。
- 不修改 LAS/LAZ 头部 CRS，不重投影点云。

## 2. 业务映射

M5 对应岗位职责第 3 条，服务以下 51WORLD 场景：

- 水利数字孪生：机载 LiDAR 地面点生成 DEM，用于淹没分析和地形融合。
- 城市实景三维：倾斜摄影 Block 发布前筛出悬浮、拉花和空洞问题。
- 自动驾驶仿真：点云抽稀后用于 51Sim 场景构建和传感器仿真。

## 3. 与 M1、M2 的衔接

M5 不重新扫描目录，消费 M1 和 M2 已有产物：

```text
assets.json                     -> 资产来源
pointcloud_stats.csv            -> 点云基础统计，避免重复读取
crs_audit.csv                   -> 坐标审计结论
quarantine_manifest.csv         -> 排除已隔离资产
```

输入过滤规则：

```text
对 category ∈ {pointcloud, oblique} 的每个 asset_id：
  1. M1 已隔离或 M2 审计 fail -> 跳过
  2. 点云：
       - 输入为 M1 原始 source_path
       - 缺失 CRS 且未配置 assumed_crs -> 跳过
       - 使用 assumed_crs -> 标记 source_stage=m2_assumed_crs
  3. OSGB：
       - 输入为 M1 聚合后的 Block 目录
       - 解析 metadata.xml，失败则标记，不阻断其他 Block
```

## 4. M5 工具

### 4.1 `pointcloud.batch_ground_filter`

职责：使用 PDAL `filters.csf` 提取地面点，输出地面点 LAS 和 DEM。

输入：

- 点云资产的 M1 原始路径。
- `pointcloud_stats.csv` 中的点数和 Z 范围。
- `m5.ground_filter` 配置。

处理流程：

1. 读取 stats 中的点数、Z 范围和分类分布。
2. 点数超过 `chunk_size` 时标记 `CHUNKED_PROCESSING`；1.0 先按全量处理，分块策略作为后续优化。
3. 构建 PDAL pipeline：
   - `readers.las`
   - `filters.csf`
   - 筛选 `Classification == ground_class`
   - 输出地面点 LAS，并使用 `writers.gdal` 输出 DEM。
4. 记录地面点数量、比例、CSF 参数和 `source_stage`。

输出：

- `outputs/<run_id>/pointcloud_ground/`
- `outputs/<run_id>/pointcloud_ground_filter_manifest.csv`
- `outputs/<run_id>/pointcloud_ground_filter_qc.csv`

### 4.2 `pointcloud.batch_thin`

职责：按随机或体素方式抽稀点云，默认保护地面点。

输入：

- 点云资产的 M1 原始路径。
- `pointcloud_stats.csv`。
- `m5.thin` 配置。

处理流程：

1. 若 `preserve_ground=true`，先分离地面点和非地面点。
2. 只对非地面点执行抽稀。
3. random 模式使用 `filters.randomize` 加 `filters.decimation`。
4. voxel 模式使用 `filters.voxelcentroidnearestneighbor`。
5. 合并地面点和抽稀后的非地面点。
6. 记录抽稀前后点数、保留比例和地面点是否被抽稀。

输出：

- `outputs/<run_id>/pointcloud_thinned/`
- `outputs/<run_id>/pointcloud_thin_manifest.csv`
- `outputs/<run_id>/pointcloud_thin_qc.csv`

说明：`batch_thin` 的输入是原始完整点云，不是地面点提取结果。

### 4.3 `oblique.batch_quality_check`

职责：对 OSGB Block 做发布前粗筛和标记。

检测项：

| rule_id | 检测内容 | 1.0 能力 |
|---|---|---|
| `OBLIQUE_SRS` | metadata SRS 与项目基准是否一致 | 解析 metadata 并与 M2 目标 CRS 对比 |
| `OBLIQUE_METADATA` | metadata.xml 是否可解析 | 可解析性检查 |
| `OBLIQUE_TILE_COUNT` | Block 内 tile 数量是否合理 | 目录结构统计 |
| `FLOAT_TILE_RATIO` | 可疑悬浮瓦片比例 | 包围盒底面与邻域地面高程粗筛 |
| `HOLE_TILE` | 可疑空洞瓦片 | 包围盒和抽样检测 |
| `TEXTURE_DISTORTION` | 可疑拉花 | 抽样纹理/法向一致性粗筛 |
| `SEAM_BBOX_OVERLAP` | 相邻 Block 包围盒重叠 | 相邻 Block 包围盒比较 |
| `COORD_ANOMALY` | 根节点坐标异常 | 包围盒与项目范围比较 |

输出：

- `outputs/<run_id>/oblique_qc.csv`
- `outputs/<run_id>/oblique_qc_manifest.csv`

说明：1.0 只做 Block 级粗筛，不承诺定位到单个三角面或像素。

## 5. 输出物

```text
outputs/<run_id>/
  pointcloud_ground/
  pointcloud_thinned/
  pointcloud_ground_filter_manifest.csv
  pointcloud_ground_filter_qc.csv
  pointcloud_thin_manifest.csv
  pointcloud_thin_qc.csv
  oblique_qc.csv
  oblique_qc_manifest.csv
```

所有 QC 长表沿用统一字段：

```csv
run_id,asset_id,category,format,relative_path,rule_id,level,message,expected,actual
```

## 6. 配置

```yaml
m5:
  ground_filter:
    csf:
      resolution: 1.0
      threshold: 0.5
      hdiff: 0.3
      rigidness: 3
      step: 0.65
      iterations: 500
      ground_class: 2
    dem:
      resolution: 1.0
      output_nodata: -9999
    chunk_size: 5000000
    ground_ratio_warn: 0.7
  thin:
    mode: random
    random: {keep_ratio: 0.3, seed: 42}
    voxel: {cell_size: 0.1}
    preserve_ground: true
    ground_class: 2
  oblique_qc:
    float_object: {max_ground_clearance: 2.0, max_float_tile_ratio: 0.1}
    texture_distortion: {sample_tiles: 20}
    hole: {max_hole_area_ratio: 0.2}
    coord: {expected_crs: null, bounds_tolerance: 1000}
    metadata: {require_srs: true}
  oblique_convert:
    enabled: false
    command: ""
    args_template: "-i {input} -o {output}"
    timeout: 3600
```

说明：以上参数为示例，实际以项目规范配置。CSF 参数必须根据点云密度、地形类型和项目目标调整。

## 7. 与后续模块的衔接

- M5 输出的地面点 LAS 和 DEM 可进入水利淹没分析、地形融合或 M6 三维场景构建。
- M5 的 OSGB 质量结论作为后续单体化和 3D Tiles 转换的输入参考。
- L0 使用 `asset_id`、`source_stage` 和 manifest 串联处理链。

## 8. 执行与错误处理

- 点云工具可独立运行，OSGB 工具独立于点云流程。
- 单个点云或 Block 失败不中断批处理，写入 manifest 并继续。
- 缺失 CRS 且未配置 `assumed_crs` 的点云跳过并记录。
- OSGB metadata 不可解析时只标记，不阻断其他 Block。
- 输出不覆盖 M1 原始数据和 M2 产物。

## 9. M5 验收标准

1. 能从 M1 资产清单筛选点云和 OSGB 资产，并跳过已隔离或审计失败资产。
2. CSF 地面滤波输出地面点 LAS 和 DEM。
3. 抽稀默认保护地面点，关闭保护时明确标记。
4. OSGB 质量检测输出 Block 级粗筛结论。
5. OSGB 转换接口默认关闭，不执行任何转换。
6. 所有检查项写入统一长表。
