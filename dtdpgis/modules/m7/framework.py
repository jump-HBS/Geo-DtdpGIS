"""AI插件框架"""

from __future__ import annotations

import json
import os
import subprocess# 导入子进程模块
import time
from pathlib import Path
from typing import Any

import yaml# 导入 YAML 解析库

from dtdpgis.core.encoding import read_text_auto
from dtdpgis.modules.m7.models import PluginExecutionResult, PluginSpec


QC_FIELDS = ["run_id", "asset_id", "category", "format", "relative_path", "rule_id", "level", "message", "expected", "actual"]
MANIFEST_FIELDS = ["run_id", "plugin_id", "plugin_name", "backend_type", "output_dir", "status", "duration_seconds", "message"]


def plugin_root(ctx: Any) -> Path:
    """获取插件根目录"""
    configured = ctx.profile.get("m7", {}).get("plugins_dir")
    if configured:
        return Path(configured)
    project_plugins = ctx.project_root / "ai_plugins"
    if project_plugins.exists():
        return project_plugins
    repo_plugins = Path(__file__).resolve().parents[3] / "ai_plugins"
    return repo_plugins


def discover_plugins(root: Path) -> list[PluginSpec]:
    """发现插件"""
    """从插件根目录发现插件"""
    """返回插件清单"""
    plugins = []
    if not root.exists():
        return plugins
    for directory in sorted(root.iterdir()):
        manifest = directory / "plugin.yaml"
        if not manifest.exists():
            continue
        data = yaml.safe_load(read_text_auto(manifest)) or {}
        data.setdefault("id", directory.name)
        plugins.append(PluginSpec.model_validate(data))
    return plugins


def plugin_directory(root: Path, plugin_id: str) -> Path:
    """获取插件目录"""
    return root / plugin_id


def resolve_python(plugin_dir: Path, spec: PluginSpec) -> str:
    """解析插件 Python 解释器路径"""
    if spec.python_executable:
        candidate = Path(spec.python_executable)
        if candidate.is_absolute():
            return str(candidate)
        return str((plugin_dir / candidate).resolve())
    return "python"


def health_check(plugin_dir: Path, spec: PluginSpec) -> tuple[bool, str]:
    """检查插件健康状态"""
    """返回插件是否可用、插件输出信息"""
    if spec.backend_type in {"cloud_api", "local_api"}:
        env_name = spec.connection.get("api_key_env")
        if env_name and not os.environ.get(env_name):
            return False, f"missing environment variable {env_name}"
        return True, "available"
    command = spec.health_check.get("command")
    if not command:
        return True, "available"
    python = resolve_python(plugin_dir, spec)
    full_command = [python] + [str(item) for item in command]
    try:
        result = subprocess.run(full_command, cwd=plugin_dir, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def run_plugin(ctx: Any, root: Path, spec: PluginSpec, params: dict[str, Any]) -> PluginExecutionResult:
    """执行插件"""
    """返回插件执行结果"""
    plugin_dir = plugin_directory(root, spec.id)# 获取插件目录
    output_dir = ctx.outputs_dir / "ai" / spec.id# 获取输出目录
    output_dir.mkdir(parents=True, exist_ok=True)# 创建输出目录
    python = resolve_python(plugin_dir, spec)# 解析插件 Python 解释器路径
    replacements = {
        "{input_path}": str(params.get("input_path") or ""),# 替换输入路径占位符
        "{output_dir}": str(output_dir),# 替换输出目录占位符
        "{params_json}": json.dumps(params.get("params", {}), ensure_ascii=False),# 替换参数 JSON 字符串占位符
    }
    command = [python]# 构建命令列表
    for item in spec.entry_command:# 遍历插件入口命令
        value = str(item)# 转换为字符串
        for token, replacement in replacements.items():# 替换占位符
            value = value.replace(token, replacement)# 替换占位符
        command.append(value)# 添加到命令列表

    env = os.environ.copy()
    env_name = spec.connection.get("api_key_env")# 获取 API 密钥环境变量名
    if env_name:# 如果有 API 密钥环境变量名
        value = os.environ.get(env_name)# 获取环境变量值
        if value:
            env[env_name] = value# 添加到环境变量字典

    started = time.time()# 记录开始时间
    try:
        result = subprocess.run(command, cwd=plugin_dir, env=env, capture_output=True, text=True, timeout=spec.timeout_seconds)# 执行插件命令
        duration = round(time.time() - started, 3)# 计算执行时间
        missing = [name for name in spec.output_contract.get("files", []) if not (output_dir / name).exists()]# 查找缺失的输出文件
        if result.returncode != 0:
            return PluginExecutionResult(plugin_id=spec.id, status="error", output_dir=output_dir, message=result.stderr.strip() or f"exit code {result.returncode}", duration_seconds=duration, stdout=result.stdout, stderr=result.stderr)
        if missing:# 如果有缺失的输出文件
            return PluginExecutionResult(plugin_id=spec.id, status="invalid", output_dir=output_dir, message=f"missing output files: {missing}", duration_seconds=duration, stdout=result.stdout, stderr=result.stderr)
        return PluginExecutionResult(plugin_id=spec.id, status="success", output_dir=output_dir, message="completed", duration_seconds=duration, stdout=result.stdout, stderr=result.stderr)
    except subprocess.TimeoutExpired:# 如果超时
        duration = round(time.time() - started, 3)# 计算执行时间
        return PluginExecutionResult(plugin_id=spec.id, status="timeout", output_dir=output_dir, message="timeout", duration_seconds=duration)
    except Exception as exc:  # noqa: BLE001# 如果发生其他异常
        duration = round(time.time() - started, 3)# 计算执行时间
        return PluginExecutionResult(plugin_id=spec.id, status="error", output_dir=output_dir, message=str(exc), duration_seconds=duration)


def run_available_plugins(ctx: Any, plugin_ids: list[str] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行可用插件"""
    """返回插件执行结果"""
    from dtdpgis.core.models import ItemResult, QCLevel

    root = plugin_root(ctx)# 获取插件根目录
    specs = discover_plugins(root)# 发现插件
    if plugin_ids:
        wanted = set(plugin_ids)# 转换为集合
        specs = [spec for spec in specs if spec.id in wanted]# 过滤插件
    params = params or {}# 处理参数
    results = []# 插件执行结果列表
    manifest_rows = []# 插件执行结果清单行列表
    qc_rows = []# 插件执行结果清单行列表
    items = []# 插件执行结果清单行列表
    for spec in specs:# 遍历插件
        plugin_dir = plugin_directory(root, spec.id)# 获取插件目录
        available, health_message = health_check(plugin_dir, spec)# 检查插件健康状态a
        if not available:# 如果插件不可用
            manifest_rows.append({"run_id": ctx.run_id, "plugin_id": spec.id, "plugin_name": spec.name, "backend_type": spec.backend_type, "output_dir": "", "status": "unavailable", "duration_seconds": 0, "message": health_message})# 添加插件执行结果清单行
            items.append(ItemResult(asset_id=spec.id, level=QCLevel.WARN, message=f"unavailable: {health_message}"))# 添加插件执行结果清单行
            continue
        result = run_plugin(ctx, root, spec, params)# 执行插件
        results.append(result)# 添加插件执行结果
        manifest_rows.append({"run_id": ctx.run_id, "plugin_id": spec.id, "plugin_name": spec.name, "backend_type": spec.backend_type, "output_dir": str(result.output_dir), "status": result.status, "duration_seconds": result.duration_seconds, "message": result.message})# 添加插件执行结果清单行
        level = "pass" if result.status == "success" else "fail"
        items.append(ItemResult(asset_id=spec.id, level=QCLevel(level), message=result.message))
        qc_rows.append(
            {
                "run_id": ctx.run_id,
                "asset_id": spec.id,
                "category": "ai_plugin",
                "format": spec.backend_type,
                "relative_path": str(result.output_dir),
                "rule_id": "AI_PLUGIN_DONE",
                "level": level,
                "message": result.message,
                "expected": "success",
                "actual": result.status,
            }
        )

    qc_path = ctx.outputs_dir / "ai_plugin_qc.csv"
    manifest_path = ctx.outputs_dir / "ai_plugin_manifest.csv"
    _write_csv(qc_path, QC_FIELDS, qc_rows)
    _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
    return {"plugins": len(specs), "success": sum(1 for r in results if r.status == "success"), "results": [r.model_dump(mode="json") for r in results]}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    """写入 CSV 文件"""
    import csv

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
