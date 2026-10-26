# M7 AI 辅助插件部署方案

- 日期：2026-10-17
- 状态：待用户审阅
- 前置文档：`2026-10-17-dtdpgis-1.0-framework-design.md`
- 范围：AI 插件框架、部署布局、调用方式、输出契约和首批插件规划

## 1. 定位与边界

M7 是可选能力层，目标是让 AI 能力以插件形式接入，不污染 GIS 主环境，也不阻塞 M1-M6。

M7 提供：

- 插件发现、校验、执行、超时控制、日志和结果校验。
- 云端 API、本地专用模型、本地推理服务和混合模式四类后端。
- 统一输入输出契约和 QC 长表。

M7 不提供：

- 不在主环境安装 torch、open3d、onnxruntime、CUDA 等 AI 依赖。
- 不内置具体模型权重。
- 不持有 API Key，不把密钥写入 manifest、日志或数据库。

## 2. 51WORLD 业务映射

M7 对应岗位职责第 5 条，服务以下 AI 方向：

| AI 方向 | 推荐后端 | 说明 |
|---|---|---|
| 大模型辅助陌生格式解析与异常排查 | 云端 API | 适合文本、元数据、字段映射建议、异常解释 |
| 点云语义分割 | 本地专用模型 | 需要逐点 LAS 标签输出，几何密集型 |
| 地物自动提取 | 本地模型或混合 | 影像/点云中提取道路、建筑、植被等要素 |
| 影像变化检测 | 本地专用模型 | 两期 DOM 像素级差分，输出变化图斑 |

原则：大模型 API 做“理解、解析、建议”，本地专用模型做“点级/像素级几何计算”。

## 3. 插件目录布局

```text
DtdpGIS/
├─ dtdpgis/modules/m7/           # M7 插件框架
└─ ai_plugins/
   ├─ llm_format_parser/
   │  ├─ plugin.yaml
   │  ├─ runner.py
   │  ├─ venv/                   # 仅 openai/httpx 等轻量依赖
   │  └─ models/                 # 可选
   ├─ pointcloud_segment/
   │  ├─ plugin.yaml
   │  ├─ runner.py
   │  ├─ venv/                   # torch/onnxruntime/cuda
   │  └─ models/
   ├─ feature_extract/
   │  ├─ plugin.yaml
   │  ├─ runner.py
   │  ├─ venv/
   │  └─ models/
   └─ change_detection/
      ├─ plugin.yaml
      ├─ runner.py
      ├─ venv/
      └─ models/
```

## 4. 插件清单

每个插件使用 `plugin.yaml` 声明能力：

```yaml
id: pointcloud_segment
name: 点云语义分割
version: 0.1.0
description: 对 LAS/LAZ 做逐点语义分割
backend_type: local_process
provider: custom
python_executable: venv/Scripts/python.exe
entry_command:
  - runner.py
  - --input
  - "{input_path}"
  - --output
  - "{output_dir}"
  - --params
  - "{params_json}"
input_contract:
  category: pointcloud
  formats: [LAS, LAZ]
output_contract:
  files: [result.las, label_summary.csv, result.json]
timeout_seconds: 3600
health_check:
  type: command
  command: [runner.py, --health]
```

后端类型：

| backend_type | 用途 | python_executable | connection |
|---|---|---|---|
| `local_process` | 本地专用模型 | 必填 | 不填 |
| `cloud_api` | 云端大模型 API | 可选 | 必填 |
| `local_api` | vLLM/Ollama/Triton 本地服务 | 可选 | 必填 |
| `hybrid` | 本地模型 + 云端复核 | 必填 | 可选 |

云端 API 配置示例：

```yaml
backend_type: cloud_api
provider: openai_compatible
connection:
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: doubao-seed-2-1-pro-260915
  api_key_env: M7_VOLC_API_KEY
  timeout_seconds: 300
```

## 5. 后端调用方式

### 5.1 本地专用模型

插件在自己的 `venv` 中运行，M7 只启动子进程：

```text
M7 framework
  -> 解析 plugin.yaml
  -> 构建 command + 环境变量
  -> subprocess.run(runner.py, timeout=...)
  -> 校验 output_contract
  -> 写 ai_qc.csv 和 ai_plugin_manifest.csv
```

主进程不导入 torch、CUDA 或模型库。

### 5.2 云端 API

`cloud_api` 和 `local_api` 都优先使用 OpenAI 兼容协议：

- 云端：DeepSeek、火山方舟等，改 `base_url` 和 `model`。
- 本地：Ollama、vLLM、LM Studio、Triton 等，指向本地 `/v1` 端点。

runner 在子进程内读取 `api_key_env`，不把密钥写入文件或日志。

### 5.3 混合模式

插件内部可以组合本地粗分割和云端复核，M7 只校验最终输出契约，不感知内部流程。

## 6. API Key 安全

1. 不在 `plugin.yaml`、代码、日志、SQLite 或 QC 长表中写真实 Key。
2. manifest 只声明环境变量名，例如 `api_key_env: M7_VOLC_API_KEY`。
3. M7 从操作系统环境变量或 Windows 凭据管理器读取，并作为子进程环境变量注入。
4. 不同插件使用不同环境变量，互不影响。
5. Key 泄露时只需在平台重置，不需要改代码。

## 7. M7 执行流程

```text
discover_plugins()
  -> 扫描 ai_plugins/*/plugin.yaml
  -> 校验 schema
  -> health_check()
  -> 标记 available / unavailable

run_plugin(ctx, plugin_id, params)
  -> dry_run()
  -> 创建 outputs/<run_id>/ai/<plugin_id>/
  -> 构造子进程命令和环境变量
  -> 执行 runner
  -> 校验输出契约
  -> 汇总 result.json / summary.csv
  -> 写 ai_qc.csv / ai_plugin_manifest.csv
```

不可用插件不阻塞 M1-M6，只在报告中标为 `unavailable`。

## 8. 输出契约

每个插件至少输出：

- `result.json`：状态、输出文件列表、指标、错误信息。
- `summary.csv`：逐文件或逐图幅统计。

M7 统一 QC 长表：

```csv
run_id,asset_id,category,format,relative_path,rule_id,level,message,expected,actual
```

## 9. 首批插件规划

| 优先级 | 插件 | 后端 | 目标 |
|---|---|---|---|
| P0 | `ai.sample_echo` | local_process | 验证插件发现、子进程、超时、输出契约 |
| P1 | `llm_format_parser` | cloud_api | 陌生格式元数据解析、字段映射建议、异常解释 |
| P1 | `change_detection` | local_process/local_api | 两期 DOM 变化检测，输出变化图斑 |
| P2 | `pointcloud_segment` | local_process/hybrid | 点云语义分割，输出 LAS 分类标签 |
| P2 | `feature_extract` | local_process/hybrid | 道路、建筑、植被等地物自动提取 |
| P3 | `osgb_anomaly_assist` | hybrid | OSGB 异常解释和修复建议 |

## 10. 部署路径

### 10.1 云端 API 插件

```powershell
python -m venv ai_plugins\llm_format_parser\venv
ai_plugins\llm_format_parser\venv\Scripts\python.exe -m pip install openai pyyaml
```

### 10.2 本地模型插件

```powershell
python -m venv ai_plugins\pointcloud_segment\venv
ai_plugins\pointcloud_segment\venv\Scripts\python.exe -m pip install torch onnxruntime numpy
```

模型权重放入对应 `models/`，不在 Git 中提交大文件。

## 11. 验收标准

1. M7 能发现并校验 `ai_plugins/` 下所有插件。
2. `local_process` 插件能通过子进程执行并校验输出契约。
3. `cloud_api` 和 `local_api` 插件能完成可达性和 API Key 环境变量检查。
4. 插件失败或不可用不阻塞 M1-M6。
5. API Key 不出现在 manifest、日志和结果文件中。
6. 至少一个 echo/sample 插件跑通完整生命周期。
