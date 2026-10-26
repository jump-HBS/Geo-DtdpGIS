# 输出文件信息栏美化方案

## 1. 目标

工具执行结束后，在底部输出信息区直接展示 CSV 表格数据，而不是只显示文件路径。

## 2. 总体布局

底部信息区改为可动态添加标签页的容器：

```text
┌─────────────────────────────────────────────────────────────┐
│ [处理结果] [文件产出] [质检明细] [资产清单] [坐标审计] ...      │
├─────────────────────────────────────────────────────────────┤
│ 当前标签页内容                                               │
└─────────────────────────────────────────────────────────────┘
```

固定标签页：

- `处理结果`：当前工具返回的 ItemResult 汇总表。
- `文件产出`：本次生成的所有文件列表。

动态标签页：

- 每个 CSV 文件自动生成一个标签页。
- 标签名称使用中文业务含义，不直接显示文件名。

## 3. CSV 中文标签映射

### M1

| 文件 | 中文标签 |
|---|---|
| `assets.csv` | 资产清单 |
| `unknown_files.csv` | 未识别文件 |
| `qc_summary.csv` | 质检明细 |
| `pointcloud_stats.csv` | 点云统计 |
| `quarantine_manifest.csv` | 隔离清单 |

### M2

| 文件 | 中文标签 |
|---|---|
| `crs_audit.csv` | 坐标审计 |
| `vector_reproject_manifest.csv` | 矢量重投影清单 |
| `raster_reproject_manifest.csv` | 栅格重投影清单 |

### M3

| 文件 | 中文标签 |
|---|---|
| `topology_qc.csv` | 拓扑检查 |
| `topology_repair_manifest.csv` | 拓扑修复清单 |
| `attribute_qc.csv` | 属性检查 |
| `attribute_normalize_manifest.csv` | 属性规范化清单 |

### M4

| 文件 | 中文标签 |
|---|---|
| `raster_hole_qc.csv` | 空洞检查 |
| `raster_fill_manifest.csv` | 空洞填充清单 |
| `raster_outlier_qc.csv` | 异常值检查 |
| `raster_outlier_manifest.csv` | 异常值修复清单 |
| `raster_mosaic_qc.csv` | 镶嵌检查 |
| `raster_mosaic_manifest.csv` | 镶嵌清单 |

### M5

| 文件 | 中文标签 |
|---|---|
| `pointcloud_ground_filter_qc.csv` | 地面滤波检查 |
| `pointcloud_ground_filter_manifest.csv` | 地面滤波清单 |
| `pointcloud_thin_qc.csv` | 抽稀检查 |
| `pointcloud_thin_manifest.csv` | 抽稀清单 |
| `oblique_qc.csv` | 倾斜摄影检查 |
| `oblique_qc_manifest.csv` | 倾斜摄影清单 |

### M6

| 文件 | 中文标签 |
|---|---|
| `spatial_relation_qc.csv` | 空间关系检查 |
| `spatial_relation_manifest.csv` | 空间关系清单 |
| `building_extrude_qc.csv` | 建筑挤出检查 |
| `building_extrude_manifest.csv` | 建筑挤出清单 |

### M7

| 文件 | 中文标签 |
|---|---|
| `ai_plugin_qc.csv` | AI 插件检查 |
| `ai_plugin_manifest.csv` | AI 插件清单 |

## 4. 表格展示规范

1. 使用 `QTableWidget`，只读模式。
2. 表头居中，背景使用浅色或主题色。
3. 短文本列居中。
4. 长文本列如 `message`、`relative_path` 左对齐，并设置 tooltip。
5. 行高统一为 28px。
6. 奇偶行使用极浅底色区分。
7. 自动根据内容设置最小列宽，但限制最大宽度。
8. 支持点击表头排序。
9. 支持按 `level` 或 `status` 快速筛选。
10. 所有表头使用中文列名映射，例如：
    - `asset_id` -> 资产 ID
    - `relative_path` -> 文件路径
    - `rule_id` -> 检查项
    - `level` -> 结论
    - `message` -> 信息
    - `status` -> 状态

## 5. 实现步骤

1. 新建 `dtdpgis/gui/result_panel.py`。
2. 实现 `ResultPanel(QTabWidget)`：
   - `set_result_items(items)`
   - `set_output_files(outputs)`
   - `_add_csv_tab(path, title)`
   - `_format_header(header)`
   - `_make_table(rows)`
3. 在 `MainWindow` 中替换当前底部输出区。
4. 执行结束后调用 `ResultPanel.set_output_files(result.outputs)`。
5. CSV 读取统一使用 `csv.DictReader` 和 `utf-8-sig`。
6. 对 `assets.json` 只生成简要摘要标签，不展开完整 JSON。

## 6. 验收标准

1. 运行 M1 数据质检后，能自动出现“质检明细”和“资产清单”标签。
2. CSV 表格字段显示为中文，数据居中。
3. 多个 CSV 时出现多个中文标签。
4. 输出文件页仍保留，可查看文件路径和大小。
5. 表格排序、筛选、tooltip 正常工作。
