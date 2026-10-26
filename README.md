# DtdpGIS 项目说明

本文档记录当前项目结构、文件职责和已有功能。后续每次完成功能开发后，需要同步更新本文档中的目录树、文件说明和当前功能状态。

## 1. 项目定位

DtdpGIS 是一个面向 51WORLD GIS 数据处理岗位场景的桌面批处理软件，基于 Python 和 Qt，目标是把空间数据接收、质检、清洗、处理、报告输出做成可复用、可追溯的批处理工具。

当前 1.0 开发阶段已完成 M1-M6 数据处理工具，并搭建了 M7 AI 插件框架和 `sample_echo` 示例插件。

## 2. 运行环境

- `Python/`：从 ArcGIS Pro 克隆出的 Python 3.13 运行环境，属于运行依赖，不属于项目源码。
- 已内置：GDAL、rasterio、geopandas、pyogrio、shapely、pyproj、PDAL、pandas、numpy、matplotlib、pytest 等。
- 尚未安装：PySide6、py3dtiles、open3d、torch、cv2。
- M7 插件独立虚拟环境使用 `C:\Python\python.exe` 创建，避免污染 ArcGIS Pro 克隆环境。
- 程序启动时会自动设置 `PROJ_LIB` 和 `GDAL_DATA`，避免本机 PostgreSQL/ArcGIS 路径污染坐标库。

## 3. 目录结构

```text
DtdpGIS/
├─ Python/                         # 运行环境
├─ dtdpgis/                        # 软件源码包
│  ├─ __init__.py                  # 包入口，初始化运行时并加载全部工具
│  ├─ __main__.py                  # 支持 python -m dtdpgis 启动
│  ├─ _env.py                      # 运行时环境变量配置模块
│  ├─ cli.py                       # 命令行入口，提供 M1-M7 子命令
│  ├─ gui/                         # PySide6 桌面界面
│  │  ├─ __init__.py               # GUI 包标识
│  │  ├─ main.py                   # GUI 启动入口，创建 QApplication
│  │  ├─ main_window.py            # 主窗口与模块/工具交互逻辑
│  │  ├─ param_form.py             # 根据 ToolSpec 动态生成参数表单
│  │  └─ theme.py                  # 浅色与深色 QSS 主题
│  ├─ core/                        # 平台级核心能力
│  │  ├─ __init__.py               # 核心包标识
│  │  ├─ config.py                 # 默认配置与项目 YAML 加载
│  │  ├─ models.py                 # 工具和运行状态的核心数据模型。
│  │  ├─ registry.py               # 工具注册与发现
│  │  ├─ tools.py                  # 定义工具接口
│  │  └─ workspace.py              # 项目工作区与 run_id 管理
│  └─ modules/                     # 业务模块
│     ├─ __init__.py               # 业务模块包标识
│     ├─ m1/                       # 数据接入与质检
│     │  ├─ __init__.py            M1 包标识
│     │  ├─ ingest.py              数据质检工具(结果写入到qc_summary.csv)
│     │  │                          规则1:资产能否被成功读取
│     │  │                          规则2:资产是否为空数据
│     │  │                          规则3:矢量附属文件是否齐全
│     │  │                          规则4:是否声明了坐标系(CRS)
│     │  │                          规则5:资产空间范围是否合理(需自行配置)
│     │  │                          规则6:是否包含重复文件
│     │  │                          规则7:按数据类别的专属规则检查
│     │  │                             -矢量：要素数必须 > 0
│     │  │                             -栅格：宽/高/波段数都必须 > 0
│     │  │                             -倾斜摄影：瓦片数 > 0，建议有metadata.xml
│     │  ├─ inventory.py           # 数据资产扫描工具
│     │  │                          1、遍历输入文件目录树，os.walk()函数
│     │  │                          2、分类资产：矢量、倾斜摄影、其他；判定文件格式
│     │  │                          3、按照文件格式，调用readers.py读取数据，生成资产清单（assets.json）
│     │  │                          4、写入资产清单CSV文件（assets.csv）【此外也会统计unknown_files.csv】
│     │  ├─ pointcloud.py          # 点云轻量统计工具,结果写入到pointcloud_stats.csv
│     │  │                          1、快速信息中提取CRS信息（PDAL，numpy）
│     │  │                          2、统计点云文件中的点数、分类数(已标注地物类别数)
│     │  │                          3、最小Z值(最低点高程)、最大Z值（最高点高程）
│     │  │                          4、p01Z(第1分位数Z值:剔除极端低噪后的下限)、
│     │  │                             p05Z(基本可靠的最低高程)、p50Z(典型中位高
│     │  │                             程),p95Z(基本可靠的最高高程)、p99Z(剔除极端高噪后的上限)
│     │  │                         5、哨兵值(标记缺失/无效点的占位值)
│     │  ├─ readers.py             # 格式识别与元数据读取
│     │  │                          1、识别数据给格式（根据文件扩展名）：矢量、栅格、点云、倾斜摄影、CAD
│     │  │                          2、查找倾斜摄影数据的根目录：3中不同数据布局
│     │  │                          3、CRS对象或字符串标准化（pyproj）：先EPSG，再WKT，最后字符串
│     │  │                          4、数据读取
│     │  │                             -矢量（pyogrio）：坐标、范围、几何类型、要素总数
│     │  │                             -栅格（rasterio）：坐标、范围、像元宽高、波段数、nodata
│     │  │                             -点云（pdal）：坐标、范围、点数
│     │  │                             -倾斜摄影：瓦片总数、是否有 metadata.xml
│     │  └─ tools.py               # M1 工具注册
│     ├─ m2/                       # 坐标与基准
│     │  ├─ __init__.py           
│     │  ├─ _common.py             # M2 公共读写与输入解析（资产assets.json路径、CSV写入、读取）
│     │  ├─ audit.py               # CRS 审计工具（结果写入到crs_audit.csv）
│     │  │                          1、CRS 解析：解析EPSG标签或者WKT字符串
│     │  │                          2、规则 & 检查项：
│     │  │                          3、核心审计 ：CRS是否缺失、CRS是否可识别（单位/中央经线/基准）
│     │  │                          4、地理坐标（范围）、投影坐标（中央经线）
│     │  ├─ reproject.py           # 矢量/栅格重投影工具(像素-地理变换矩阵和目标尺寸，逐波段配置的重采样算法)
│     │  │                          1、筛选矢量栅格数据(输出矢量数据为.gpkg格式)
│     │  │                          2、矢量重投影(geopandas)：.to_crs将矢量数据重投影到目标CRS
│     │  │                          3、栅格重投影(rasterio)：reproject将栅格数据重投影到目标CRS(raster_reproject_manifest.csv)
│     │  │                          4、输出重投影清单（vector_reproject_manifest.csv）：包含重投影后的资产ID、源路径、目标路径、CRS等信息
│     │  └─ tools.py               
│     ├─ m3/                       # 矢量清洗与拓扑
│     │  ├─ __init__.py            # M3包标识
│     │  ├─ _common.py             # 共享方法（筛选矢量、配至输出路径），导入M1资产扫描方法
│     │  ├─ attribute.py           # 属性规范化工具（geopandas）：attribute表（字段统一、要素统计、异常值修复）
│     │  ├─ topology.py            # 拓扑修复工具(is_valid判定; make_valid修复;explain_validity解释)
│     │  │                          1、结果放置于topology表
│     │  │                          2、重复（极近）点修复（shapely.geometry）
│     │  │                          3、狭长面标记（计算狭长比、面积）
│     │  │                          4、几何有效性（make_valid）：自相交、无效环、空几何
│     │  └─ tools.py               
│     ├─ m4/                       # 栅格与地形
│     │  ├─ __init__.py           
│     │  ├─ _common.py             # M4 输入解析与栅格类型识别
│     │  ├─ fill.py                # 空洞填充工具（rasterio）
│     │  │                          1、从空洞的边缘往中心填，空洞过大则仅标记(numpy)
│     │  │                             -scipy.ndimage 8邻域分组
│     │  │                             -默认>100像元标记
│     │  │                          2、反距离加权（fillnodata方法快速实现）
│     │  ├─ outlier.py             # 异常值修复工具(rasterio/numpy)
│     │  │                          1、异常值判定：取邻居中值(np.nanmedian)；算残差（np.abs）；分档（两个阈值）
│     │  │                          2、修复粗差（不包含分类栅格）:邻域中值替换
│     │  ├─ mosaic.py              # 栅格镶嵌工具(rasterio)
│     │  │                          1、确保类型、坐标系、分辨率一致(以第一个资产为准)
│     │  │                          2、可配置：重叠方式(均值、第一个重叠);重采样方法
│     │  │                          3、rasterio.merge合并栅格数据集
│     │  │                          4、极粗略接边判定，是否有缝隙像素（可忽略）
│     │  └─ tools.py               
│     ├─ m5/                       # 点云与倾斜摄影
│     │  ├─ __init__.py            
│     │  ├─ _common.py             # M5 点云/OSGB 输入解析
│     │  ├─ ground.py              # 点云地面滤波工具（CSF布料模拟算法）
│     │  │                          1、转数组（pdal）;打分类码(地面为2)
│     │  │                          2、CSF滤波(pdal.Pipeline().execute())
│     │  │                          3、numpy索引分成两组（地面、非地面）
│     │  │                          4、拆除地面点生成DEM：from_origin、numpy
│     │  ├─ thin.py                # 点云抽稀工具
│     │  │                          1、地面点保护不参与抽稀（同上）
│     │  │                          2、随机抽稀(np.random)：给每个点生成随机数，按比例随机挑
│     │  │                          3、体素抽稀：保留距离质心最近的点
│     │  ├─ oblique.py             # OSGB极简检测工具(未做悬浮、空洞、拉花等几何检查)
│     │  │                          1、找metadata.xml并判断是否可读(PDAL)
│     │  │                          2、数tile文件、找空文件
│     │  │                          3、提取EPSG并判断与配置是否一致
│     │  └─ tools.py               
│     ├─ m6/                       # 矢量融合与三维化
│     │  ├─ __init__.py            
│     │  ├─ _common.py             # M6 输入解析与角色匹配
│     │  ├─ spatial.py             # 空间关系检查工具（徐根据实际需求判定）
│     │  │                          1、intersects、union_all、area、within等空间关系方法
│     │  │                          2、判定相交关系、相交面积、包含关系等等
│     │  │                          3、gpd.GeoDataFrame合并所有问题几何,to_file打包为DPKG
│     │  ├─ extrude.py             # 建筑三维挤出工具(未实现，仅提供思路)
│     │  │                          1、二维面几何+height或floors属性列(可配置)
│     │  │                          2、输出一个MultiPolygon（底面、顶面、侧面）。
│     │  └─ tools.py               
│     └─ m7/                       # AI 插件框架(未实现，后续更新方向)
│     │  ├─ __init__.py            # M7 包标识
│     │  ├─ models.py              # 插件清单与执行结果模型
│     │  ├─ framework.py           # 插件发现、健康检查与子进程执行
│     │  └─ tools.py               # M7 框架公共导出
├─ ai_plugins/                     # AI 插件目录
│  └─ sample_echo/                 # 插件生命周期示例
│     ├─ plugin.yaml               # 示例插件清单
│     ├─ runner.py                 # 示例插件运行器
│     └─ venv/
├─ assets/                         # 图标资源
│  ├─ DtdpGIS_circle.png           # 圆形透明 PNG 图标
│  └─ DtdpGIS_circle.ico           # 桌面快捷方式圆形 ICO 图标
├─ DtdpGIS.pyw                     # 双击启动脚本
├─ DtdpGIS.cmd                     # Windows 命令行快捷启动脚本
├─ tests/                          # 自动化测试
│  ├─ conftest.py                  # pytest 运行时初始化
│  ├─ fixtures.py                  # 合成测试数据工厂
│  ├─ test_m1_tools.py             # M1 工具测试
│  ├─ test_m2_tools.py             # M2 工具测试
│  ├─ test_m3_tools.py             # M3 工具测试
│  ├─ test_m4_tools.py             # M4 工具测试
│  ├─ test_m5_tools.py             # M5 工具测试
│  ├─ test_m6_tools.py             # M6 工具测试
│  └─ test_m7_tools.py             # M7 插件测试
├─ profiles/                       # 项目配置
│  └─ default.yaml                 # 默认规则与参数
├─ docs/                           # 设计文档
│  └─ superpowers/specs/
│     ├─ 2026-10-17-dtdpgis-1.0-framework-design.md        # 总体框架设计
│     ├─ 2026-10-17-m1-data-ingest-design.md               # M1 数据接入与质检设计
│     ├─ 2026-10-17-m2-crs-design.md                       # M2 坐标与基准设计
│     ├─ 2026-10-17-m3-vector-topology-design.md           # M3 矢量清洗与拓扑设计
│     ├─ 2026-10-17-m4-raster-terrain-design.md            # M4 栅格与地形设计
│     ├─ 2026-10-17-m5-pointcloud-oblique-design.md        # M5 点云与倾斜摄影设计
│     ├─ 2026-10-17-m6-vector-fusion-3d-design.md          # M6 矢量融合与三维化设计
│     └─ 2026-10-17-m7-ai-plugin-deployment-design.md      # M7 AI 插件部署设计
└─ README.md                       # 本说明文档
```

说明：

- `__pycache__/` 和 `.pytest_cache/` 是 Python 运行缓存，不属于源码。
- 每次批量运行的业务输出写入项目目录下的 `outputs/<run_id>/` 和 `quarantine/<run_id>/`。

## 4. 文件说明

### 4.1 包入口与运行时

| 文件 | 说明 |
|---|---|
| `dtdpgis/__init__.py` | 包入口。先调用运行时环境配置，再加载 M1、M2、M3、M4 工具注册表，保证其他模块导入前 `PROJ_LIB`、`GDAL_DATA` 已指向正确目录 |
| `dtdpgis/__main__.py` | 支持 `python -m dtdpgis` 方式启动 CLI |
| `dtdpgis/_env.py` | GIS 运行时环境修复。提供 `configure_runtime()` 初始化环境变量，`ensure_runtime_env()` 在每次 GIS 调用前恢复干净的 `PROJ_LIB` 和 `GDAL_DATA`，避免 PostgreSQL/ArcGIS 路径污染 |
| `dtdpgis/cli.py` | 命令行入口。提供 `m1`、`m2`、`m3`、`m4`、`m5`、`m6`、`m7` 子命令，按模块执行顺序依次运行对应工具和 AI 插件 |
| `dtdpgis/gui/main.py` | PySide6 GUI 启动入口，创建 `QApplication`、设置图标和主题 |
| `dtdpgis/gui/main_window.py` | 主窗口。实现模块标签、工具列表、参数面板、任务队列、结果表和主题切换 |
| `dtdpgis/gui/param_form.py` | 根据 `ToolSpec.params` 动态生成参数表单 |
| `dtdpgis/gui/theme.py` | 浅色和深色 QSS 主题 |
| `DtdpGIS.pyw` | 双击启动脚本，调用 GUI 入口 |
| `DtdpGIS.cmd` | Windows 命令行快捷启动脚本 |
| `assets/DtdpGIS_circle.png` | 从桌面原图标裁剪的圆形 PNG 图标 |
| `assets/DtdpGIS_circle.ico` | 用于桌面快捷方式的圆形 ICO 图标 |

### 4.2 核心层

| 文件 | 说明 |
|---|---|
| `dtdpgis/core/models.py` | 定义全项目共用数据契约：`ToolSpec` 工具元数据、`BaseTool` 对应的 `ToolResult`/`ItemResult`、`RunContext` 运行上下文、`Asset` 资产模型、`QCLevel` 等级枚举 |
| `dtdpgis/core/tools.py` | 定义 `BaseTool` 抽象接口。每个业务工具必须提供 `spec`、`dry_run()` 和 `execute()`，这是 GUI 和 CLI 与业务逻辑解耦的基础 |
| `dtdpgis/core/registry.py` | 工具注册表。提供 `register()`、`get_tool()` 和 `discover_tools()`，启动时自动导入 M1-M6 工具并注册 |
| `dtdpgis/core/config.py` | 配置加载。保存 `DEFAULT_PROFILE` 默认规则，通过 `deep_merge()` 合并项目 `profiles/default.yaml`，并支持项目级 YAML 覆盖 |
| `dtdpgis/core/workspace.py` | 项目工作区。负责创建 `outputs/<run_id>/`、`quarantine/<run_id>/`、`reports/`，并生成 `RunContext`，每次运行结果独立、可追溯 |

### 4.3 M1 数据接入与质检

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m1/readers.py` | M1 数据读取层。按扩展名识别格式，聚合 Shapefile 附属文件和 OSGB Block 目录，用 `pyogrio`/`rasterio`/`pdal` 读取矢量、栅格、点云、倾斜摄影元数据，并提供 SHA256 计算 |
| `dtdpgis/modules/m1/inventory.py` | 实现 `inventory.batch_scan`。递归扫描输入目录，读取元数据、关联来源档案、生成稳定 `asset_id`，输出 `assets.json`、`assets.csv`、`unknown_files.csv` |
| `dtdpgis/modules/m1/ingest.py` | 实现 `ingest.batch_qc`（界面名“数据质检”）。可直接输入数据目录，自动扫描并生成可读性、非空、附属文件、CRS、范围、重复和分类专项检查；输出长表 `qc_summary.csv` 和 `quarantine_manifest.csv` |
| `dtdpgis/modules/m1/pointcloud.py` | 实现 `pointcloud.batch_stats`。用 PDAL 读取 LAS/LAZ，统计点数、Z 分位、分类分布、点间距和 CRS，输出 `pointcloud_stats.csv` |
| `dtdpgis/modules/m1/tools.py` | 汇总 M1 三个工具类，导入时完成注册 |

### 4.4 M2 坐标与基准

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m2/_common.py` | M2 公共层。提供 `assets.json` 读取、资产源路径解析、统一 CSV 读写和等级聚合 |
| `dtdpgis/modules/m2/audit.py` | 实现 `crs.audit`。审计 CRS 缺失、声明不可识别、数值单位矛盾、跨带、基准转换和高程基准，输出 `crs_audit.csv` |
| `dtdpgis/modules/m2/reproject.py` | 实现 `crs.batch_reproject_vector` 和 `crs.batch_reproject_raster`。矢量用 `geopandas/pyogrio` 重投影；栅格用 `rasterio.warp` 重建目标网格；分别输出 `vector/`、`raster/` 和 manifest |
| `dtdpgis/modules/m2/tools.py` | 汇总 M2 三个工具类，导入时完成注册 |

### 4.5 M3 矢量清洗与拓扑

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m3/_common.py` | M3 公共层。解析 M1 `assets.json`、M2 `vector_reproject_manifest.csv`、`crs_audit.csv` 和隔离清单，按优先级确定 M3 输入路径；提供统一 QC 长表写入 |
| `dtdpgis/modules/m3/topology.py` | 实现 `topology.batch_repair`。修复自相交、无效环、连续重复顶点，标记狭长面，输出 `vector_repaired/`、`topology_repair_manifest.csv`、`topology_qc.csv` |
| `dtdpgis/modules/m3/attribute.py` | 实现 `attribute.batch_normalize`。执行字段重命名、空值统一、必填校验、值域校验和别名映射，输出 `vector_normalized/`、`attribute_normalize_manifest.csv`、`attribute_qc.csv` |
| `dtdpgis/modules/m3/tools.py` | 汇总 M3 两个工具类，导入时完成注册 |

### 4.6 M4 栅格与地形

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m4/_common.py` | M4 公共层。解析 M1 资产、M2 `raster_reproject_manifest.csv`、CRS 审计和隔离清单，确定栅格输入路径；提供栅格类型识别和统一 QC 长表写入 |
| `dtdpgis/modules/m4/fill.py` | 实现 `raster.batch_fill_holes`。按 DEM/DOM/分类栅格分治填充 Nodata 空洞，输出填充结果和填充掩膜 |
| `dtdpgis/modules/m4/outlier.py` | 实现 `raster.batch_outlier_fix`。按邻域中值残差检测 DEM/DOM 异常值，硬阈值修复、软阈值标记，输出修复结果和异常值掩膜 |
| `dtdpgis/modules/m4/mosaic.py` | 实现 `raster.batch_mosaic`。按类型、CRS、分辨率分组镶嵌，检查缝隙和接边质量，输出镶嵌结果和清单 |
| `dtdpgis/modules/m4/tools.py` | 汇总 M4 三个工具类，导入时完成注册 |

### 4.7 M5 点云与倾斜摄影

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m5/_common.py` | M5 公共层。解析 M1 资产、M2 CRS 审计和隔离清单，分别筛选点云和 OSGB 输入；提供统一 QC 长表写入 |
| `dtdpgis/modules/m5/ground.py` | 实现 `pointcloud.batch_ground_filter`。用 PDAL `filters.csf` 提取地面点，输出地面点 LAS 和 DEM |
| `dtdpgis/modules/m5/thin.py` | 实现 `pointcloud.batch_thin`。随机或体素抽稀，默认保护地面点，输出抽稀后 LAS |
| `dtdpgis/modules/m5/oblique.py` | 实现 `oblique.batch_quality_check`。对 OSGB Block 做结构、SRS、空 tile 等粗筛，只检测不修复 |
| `dtdpgis/modules/m5/tools.py` | 汇总 M5 三个工具类，导入时完成注册 |

### 4.8 M6 矢量融合与三维化

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m6/_common.py` | M6 公共层。按 M3 标准化、M3 修复、M2 重投影、M1 原始的优先级解析矢量输入；提供图层角色匹配和统一 QC 长表写入 |
| `dtdpgis/modules/m6/spatial.py` | 实现 `vector.batch_spatial_relation`。检查道路/水系重叠、建筑越界、POI 行政区归属等跨图层空间关系，只检测不修复 |
| `dtdpgis/modules/m6/extrude.py` | 实现 `building.batch_extrude`。按 `height` 或 `floors` 把建筑 footprint 挤出为三维白模，输出 GeoPackage 和 GeoJSON |
| `dtdpgis/modules/m6/tools.py` | 汇总 M6 两个工具类，导入时完成注册 |

### 4.9 M7 AI 插件框架

| 文件 | 说明 |
|---|---|
| `dtdpgis/modules/m7/models.py` | 定义 `PluginSpec` 和 `PluginExecutionResult`，用于插件清单校验和执行结果 |
| `dtdpgis/modules/m7/framework.py` | M7 插件框架。负责插件发现、健康检查、子进程执行、输出契约校验、QC 和 manifest 汇总 |
| `dtdpgis/modules/m7/tools.py` | 汇总并导出 M7 框架的公共函数 |
| `ai_plugins/sample_echo/plugin.yaml` | 示例插件清单，声明 `local_process` 后端和输出契约 |
| `ai_plugins/sample_echo/runner.py` | 示例插件运行器，生成 `result.json` 和 `summary.csv` |
| `ai_plugins/sample_echo/venv/` | 由 `C:\Python\python.exe` 创建的独立虚拟环境，不进入 GIS 主环境 |

### 4.10 配置、测试与文档

| 文件 | 说明 |
|---|---|
| `profiles/default.yaml` | 项目默认配置。包含 M1-M6 的默认规则、处理参数和 M7 插件目录配置 |
| `tests/conftest.py` | pytest 启动钩子。在测试导入 GIS 库前初始化 DtdpGIS 运行时环境 |
| `tests/fixtures.py` | 测试合成数据工厂。生成 M2/M3/M4 所需的矢量、栅格和异常样本 |
| `tests/test_m1_tools.py` | M1 端到端测试。构造混合格式目录，验证资产扫描、入口质检和点云统计 |
| `tests/test_m2_tools.py` | M2 端到端测试。验证 CRS 审计、矢量重投影和栅格重投影 |
| `tests/test_m3_tools.py` | M3 端到端测试。验证拓扑修复和属性字段规范化 |
| `tests/test_m4_tools.py` | M4 端到端测试。验证空洞填充、异常值修复和栅格镶嵌 |
| `tests/test_m5_tools.py` | M5 端到端测试。验证地面滤波、点云抽稀和 OSGB 质量检测 |
| `tests/test_m6_tools.py` | M6 端到端测试。验证空间关系检查和建筑三维挤出 |
| `tests/test_m7_tools.py` | M7 端到端测试。验证插件发现、健康检查和示例插件执行 |
| `docs/superpowers/specs/2026-10-17-dtdpgis-1.0-framework-design.md` | 1.0 总体框架设计，定义平台底座和 M1-M7 模块边界 |
| `docs/superpowers/specs/2026-10-17-m1-data-ingest-design.md` | M1 数据接入与质检功能配置设计 |
| `docs/superpowers/specs/2026-10-17-m2-crs-design.md` | M2 坐标与基准功能配置设计 |
| `docs/superpowers/specs/2026-10-17-m3-vector-topology-design.md` | M3 矢量清洗与拓扑功能配置设计 |
| `docs/superpowers/specs/2026-10-17-m4-raster-terrain-design.md` | M4 栅格与地形功能配置设计 |
| `docs/superpowers/specs/2026-10-17-m5-pointcloud-oblique-design.md` | M5 点云与倾斜摄影功能配置设计 |
| `docs/superpowers/specs/2026-10-17-m6-vector-fusion-3d-design.md` | M6 矢量融合与三维化功能配置设计 |
| `docs/superpowers/specs/2026-10-17-m7-ai-plugin-deployment-design.md` | M7 AI 辅助插件部署方案 |

## 5. 当前功能状态

| 模块 | 状态 | 已实现工具 |
|---|---|---|
| L0 平台底座 | 核心模型、工作区、配置、工具注册表已建立 | 无 |
| M1 数据接入与质检 | 已实现 | `inventory.batch_scan`、`ingest.batch_qc`、`pointcloud.batch_stats` |
| M2 坐标与基准 | 已实现 | `crs.audit`、`crs.batch_reproject_vector`、`crs.batch_reproject_raster` |
| M3 矢量清洗与拓扑 | 已实现 | `topology.batch_repair`、`attribute.batch_normalize` |
| M4 栅格与地形 | 已实现 | `raster.batch_fill_holes`、`raster.batch_outlier_fix`、`raster.batch_mosaic` |
| M5 点云与倾斜摄影 | 已实现 | `pointcloud.batch_ground_filter`、`pointcloud.batch_thin`、`oblique.batch_quality_check` |
| M6 矢量融合与三维化 | 已实现 | `vector.batch_spatial_relation`、`building.batch_extrude` |
| M7 AI 辅助插件 | 插件框架已搭建，`sample_echo` 已跑通 | `ai_plugins/sample_echo` |

## 6. 运行方式

启动桌面 GUI：

```powershell
& D:\DtdpGIS\Python\pythonw.exe D:\DtdpGIS\DtdpGIS.pyw
```

也可以双击桌面快捷方式 `DtdpGIS.lnk`。

运行 M1 全部工具：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m1 --project D:\Projects\<项目目录> --inputs D:\Projects\<项目目录>\inputs
```

运行测试：

```powershell
& D:\DtdpGIS\Python\python.exe -m pytest -q D:\DtdpGIS\tests
```

M1 一次运行后的输出：

```text
<项目目录>/
├─ outputs/<run_id>/
│  ├─ assets.json
│  ├─ assets.csv
│  ├─ unknown_files.csv
│  ├─ qc_summary.csv
│  ├─ pointcloud_stats.csv
│  └─ quarantine_manifest.csv
└─ quarantine/<run_id>/
   └─ <隔离文件副本>
```

运行 M2 全部工具：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m2 --project D:\Projects\<项目目录> --run-id <与 M1 相同的 run_id>
```

M2 一次运行后的输出：

```text
outputs/<run_id>/
├─ crs_audit.csv
├─ vector_reproject_manifest.csv
├─ raster_reproject_manifest.csv
├─ vector/
│  └─ <重投影后的矢量>
└─ raster/
   └─ <重投影后的栅格>
```

运行 M3 全部工具：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m3 --project D:\Projects\<项目目录> --run-id <与 M1、M2 相同的 run_id>
```

M3 一次运行后的输出：

```text
outputs/<run_id>/
├─ vector_repaired/
├─ vector_normalized/
├─ topology_repair_manifest.csv
├─ topology_qc.csv
├─ attribute_normalize_manifest.csv
└─ attribute_qc.csv
```

运行 M4 全部工具：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m4 --project D:\Projects\<项目目录> --run-id <与 M1、M2 相同的 run_id>
```

M4 一次运行后的输出：

```text
outputs/<run_id>/
├─ raster_filled/
├─ raster_filled_masks/
├─ raster_outlier_fixed/
├─ raster_outlier_masks/
├─ raster_mosaic/
├─ raster_hole_qc.csv
├─ raster_fill_manifest.csv
├─ raster_outlier_qc.csv
├─ raster_outlier_manifest.csv
├─ raster_mosaic_qc.csv
└─ raster_mosaic_manifest.csv
```

运行 M5 全部工具：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m5 --project D:\Projects\<项目目录> --run-id <与 M1、M2 相同的 run_id>
```

M5 一次运行后的输出：

```text
outputs/<run_id>/
├─ pointcloud_ground/
├─ pointcloud_thinned/
├─ pointcloud_ground_filter_manifest.csv
├─ pointcloud_ground_filter_qc.csv
├─ pointcloud_thin_manifest.csv
├─ pointcloud_thin_qc.csv
├─ oblique_qc.csv
└─ oblique_qc_manifest.csv
```

运行 M6 全部工具：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m6 --project D:\Projects\<项目目录> --run-id <与 M1、M2、M3 相同的 run_id>
```

M6 一次运行后的输出：

```text
outputs/<run_id>/
├─ vector_spatial_relation/
├─ building_extruded/
├─ spatial_relation_qc.csv
├─ spatial_relation_manifest.csv
├─ building_extrude_qc.csv
└─ building_extrude_manifest.csv
```

运行 M7 示例插件：

```powershell
& D:\DtdpGIS\Python\python.exe -m dtdpgis.cli m7 --project D:\Projects\<项目目录> --run-id <run_id> --plugin sample_echo
```

M7 插件运行后的输出：

```text
outputs/<run_id>/
├─ ai/
│  └─ sample_echo/
│     ├─ result.json
│     └─ summary.csv
├─ ai_plugin_qc.csv
└─ ai_plugin_manifest.csv
```

## 7. 文档维护约定

后续开发完成后，需要更新本文档：

- 新增或删除源码文件时，更新目录树。
- 新增工具、模块或配置时，更新文件说明和当前功能状态。
- 修改运行方式或输出物时，同步更新运行方式和输出结构。
- 设计文档与实现不一致时，优先修正设计文档，再同步本说明文档。
