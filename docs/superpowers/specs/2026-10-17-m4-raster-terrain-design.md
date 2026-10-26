# M4 栅格与地形 功能配置设计

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`、`2026-10-17-m1-data-ingest-design.md`、`2026-10-17-m2-crs-design.md`
- 范围：仅 DEM、DOM、分类栅格的空洞修复、异常值修复和镶嵌

## 1. 定位与边界

M4 承接 M2 已重投影的栅格，在项目统一基准网格上完成地形和影像修复。它解决的是发布前栅格数据的连续性和可信度问题。

M4 做：

- 区分 DEM、DOM、分类栅格，执行不同的空洞处理策略。
- 按邻域统计检测和修复 DEM/DOM 异常值。
- 对同类栅格批量镶嵌，检查接边误差和高程/色调跳变。
- 输出修复区域和异常值区域掩膜。

M4 不做：

- 不做 CRS 审计和重投影。
- 不修复 M2 已判定 fail 或 M1 已隔离的栅格。
- 不对分类栅格做插值填充或平滑修复。
- 不做 DOM 变化检测、局部坐标纠正和地形衍生指标。
- 不覆盖 M2 产物和原始输入。

## 2. 业务映射

M4 对应岗位职责第 2 条，服务以下 51WORLD 场景：

- 城市数字底座：DEM 连续、DOM 色调一致、无拼接缝。
- 水利数字孪生：地形与水面接边、库区高程连续。
- 实景三维发布前处理：DOM/DEM 作为场景底板的几何和纹理基础。

行业参考要求：DOM 接边误差不大于 2 个像素，色调一致；DEM 接边无偏移、漏洞和高程异常。具体阈值以项目 YAML 为准。

## 3. 与 M1、M2 的衔接

M4 不重新扫描目录，输入选择规则：

```text
对 assets.json 中 category = raster 的每个 asset_id：
  1. 若 raster_reproject_manifest.csv 中存在该 asset_id 的 success 记录
       -> 使用 M2 输出路径 raster/<output_path>
  2. 若 crs_audit.csv 中存在 fail 或资产已隔离
       -> 跳过，写入排除清单
  3. 若用户跳过 M2
       -> 使用 M1 原始 source_path，标记 source_stage=m1_raw
```

M4 不重复 M2 的 CRS 处理。输出继承 M2 的目标 CRS、分辨率和网格。

## 4. 栅格类型识别

每个栅格资产需要确定 `raster_type`，取值 `dem`、`dom`、`classification`。

识别优先级：

1. 项目 YAML 中 `m4.raster_types` 按 `asset_id` 显式指定。
2. YAML 中按文件名模式匹配。
3. 文件名包含 `dem`、`dsm` 判定为 `dem`；包含 `dom`、`tdom` 判定为 `dom`；包含 `classification`、`landuse`、`lulc` 判定为 `classification`。
4. 无法识别时默认 `dem`，但输出 `RASTER_TYPE_UNKNOWN` warn，并只执行保守检查，不自动修复。

## 5. M4 工具

### 5.1 `raster.batch_fill_holes`

职责：填充 Nodata 空洞，输出填充区域掩膜。

按类型策略：

- DEM：小空洞使用反距离加权或邻域插值填充。
- DOM：小空洞可插值，大空洞只标记。
- 分类栅格：只标记，不插值。

处理流程：

1. 读取栅格类型和 Nodata。
2. 统计空洞数量、总面积和最大空洞尺寸。
3. 对每个空洞按阈值决定自动填充或标记。
4. 对允许填充的空洞执行填充。
5. 输出填充后栅格和 0/1 填充掩膜。

输出：

- `outputs/<run_id>/raster_filled/`
- `outputs/<run_id>/raster_filled_masks/`
- `outputs/<run_id>/raster_hole_qc.csv`
- `outputs/<run_id>/raster_fill_manifest.csv`

### 5.2 `raster.batch_outlier_fix`

职责：按邻域中值残差检测并修复异常值。

处理流程：

1. 取待检像元邻域窗口，排除 Nodata。
2. 计算邻域中值或均值，求残差。
3. 按软阈值和硬阈值分级：
   - 硬阈值以上：自动修复并标记。
   - 软阈值以上但低于硬阈值：标记，默认不修复。
4. 分类栅格只标记，不替换。
5. 输出修复后栅格和异常值掩膜。

输出：

- `outputs/<run_id>/raster_outlier_fixed/`
- `outputs/<run_id>/raster_outlier_masks/`
- `outputs/<run_id>/raster_outlier_qc.csv`
- `outputs/<run_id>/raster_outlier_manifest.csv`

### 5.3 `raster.batch_mosaic`

职责：批量镶嵌同类栅格，检查接边质量。

分组条件：`raster_type + CRS + 分辨率` 一致才可进入同一镶嵌任务。

处理流程：

1. 按类型、CRS、分辨率分组。
2. 检查网格对齐，必要时重采样到共同网格。
3. 按类型处理重叠区：
   - DEM：`mean`、`first`、`last` 等。
   - DOM：`mean` 或简单羽化；完整匀色后续版本。
   - 分类栅格：`first`，使用最近邻，不混合类别。
4. 检查接边误差、高程跳变、色调差异和未覆盖缝隙。
5. 输出镶嵌结果和接边检查长表。

输出：

- `outputs/<run_id>/raster_mosaic/`
- `outputs/<run_id>/raster_mosaic_qc.csv`
- `outputs/<run_id>/raster_mosaic_manifest.csv`

## 6. 输出物

```text
outputs/<run_id>/
  raster_filled/
  raster_filled_masks/
  raster_outlier_fixed/
  raster_outlier_masks/
  raster_mosaic/
  raster_hole_qc.csv
  raster_fill_manifest.csv
  raster_outlier_qc.csv
  raster_outlier_manifest.csv
  raster_mosaic_qc.csv
  raster_mosaic_manifest.csv
```

所有 QC 长表沿用统一字段：

```csv
run_id,asset_id,category,format,relative_path,rule_id,level,message,expected,actual
```

等级只使用 `pass`、`warn`、`fail`。填充、修复、镶嵌等已执行动作使用 `pass`，不引入 `info` 等级。

## 7. 配置

```yaml
m4:
  raster_types:
    by_asset_id: {}
    patterns: {}
  fill_holes:
    dem: {max_hole_pixels: 100, max_dist: 20, smooth_iterations: 1}
    dom: {max_hole_pixels: 50, max_dist: 10, smooth_iterations: 0}
    classification: {action: mark_only}
    mark_filled_mask: true
  outlier_fix:
    dem: {window_size: 5, method: median_residual, soft_threshold: 30, hard_threshold: 80, action: repair_hard_only}
    dom: {window_size: 3, method: median_residual, soft_threshold: 20, hard_threshold: 50, action: repair_hard_only}
    classification: {action: mark_only}
  mosaic:
    dem: {overlap_strategy: mean, seam_tolerance_pixels: 2, seam_elevation_threshold: 0.5, resampling: bilinear}
    dom: {overlap_strategy: mean, seam_tolerance_pixels: 2, color_balance: false, resampling: bilinear}
    classification: {overlap_strategy: first, resampling: nearest, seam_tolerance_pixels: 2}
```

说明：阈值均为示例，实际以项目规范配置。

## 8. 与后续模块的衔接

- M4 输出 `raster_mosaic/` 供后续实景三维场景底板、水利淹没分析或 M5 地形融合使用。
- M4 不依赖 M3。
- L0 使用 `asset_id`、`source_stage` 和 manifest 串联处理链。

## 9. 执行与错误处理

- 默认顺序：`fill_holes → outlier_fix → mosaic`。
- `outlier_fix` 优先读取 `raster_filled/`，回退到 M2 `raster/`。
- `mosaic` 优先读取 `raster_outlier_fixed/`，回退到 `raster_filled/`，再回退到 M2 `raster/`。
- 单个栅格失败不中断批处理，写入 manifest 并继续。
- 分类栅格自动跳过插值和修复，只标记。
- 输出不覆盖 M2 产物和原始输入。

## 10. M4 验收标准

1. 能从 M2 `raster_reproject_manifest.csv` 找到重投影后的栅格。
2. 能跳过 M2 审计 fail 和 M1 已隔离的栅格。
3. DEM/DOM 小空洞被填充，并输出掩膜。
4. 大空洞和分类栅格 Nodata 只标记，不填充。
5. DEM/DOM 硬阈值异常值被修复，软阈值异常值只标记。
6. 同类栅格按 CRS 和分辨率分组后完成镶嵌。
7. 接边检查输出到统一长表，不覆盖 M2 产物。
