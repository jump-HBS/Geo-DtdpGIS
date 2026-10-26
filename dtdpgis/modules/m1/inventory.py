"""批量资产扫描工具。"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any
# 导入yaml模块，用于解析yaml文件
import yaml
# 导入资产、项目结果、质检等级、运行上下文、工具结果、工具规格模型
from dtdpgis.core.models import Asset, ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register# 导入工具注册装饰器
from dtdpgis.core.tools import BaseTool# 导入基础工具类，定义了工具接口
from dtdpgis.core.encoding import read_text_auto# 导入自动识别编码读取文本文件的函数
from dtdpgis.modules.m1 import readers# 导入读取器模块，用于读取数据资产


def _as_posix(path: Path, root: Path) -> str:
    """将路径转换为相对于根目录的 POSIX 风格路径。"""
    return path.relative_to(root).as_posix()#把 \ 全部换成 /


def _walk_files(root: Path, ignore_patterns: list[str], follow_symlinks: bool) -> list[Path]:
    """递归遍历目录树，返回所有文件路径。"""
    files: list[Path] = []# 存储所有文件路径的列表
    for current, dirs, names in os.walk(root, followlinks=follow_symlinks):
        #current, dirs, names 分别表示当前目录、子目录、文件名列表
        current_path = Path(current)
        dirs[:] = [
            d
            for d in dirs
            if not any((current_path / d).match(pattern) or d == "__pycache__" for pattern in ignore_patterns)
        ]# 过滤掉所有匹配忽略模式的目录，以及__pycache__目录（Python 编译后的字节码目录）,.match()方法用于匹配路径是否符合模式
        for name in names:
            path = current_path / name# 构建当前文件的完整路径
            if any(path.match(pattern) for pattern in ignore_patterns):# 如果当前文件匹配忽略模式
                continue
            if path.is_file() or (follow_symlinks and path.is_symlink() and path.exists()):# 如果当前文件是普通文件或符号链接且存在
                files.append(path)
    return files

def _load_source_manifest(project_root: Path, params: dict[str, Any]) -> dict[str, Any]:
    """加载资产源清单文件。数据来源("source_manifest.yaml"):配置文件、项目根目录下的资产清单文件、项目根目录下的profiles目录下的资产清单文件。"""
    configured = params.get("source_manifest")# 从参数中获取配置的资产清单文件路径
    candidates = []# 存储所有候选文件路径的列表
    if configured:# 如果配置了资产清单文件路径
        candidates.append(Path(configured))# 添加配置的资产清单文件路径到候选列表   
    candidates.extend(
        [
            project_root / "source_manifest.yaml",# 项目根目录下的资产清单文件
            project_root / "profiles" / "source_manifest.yaml",# 项目根目录下的profiles目录下的资产清单文件
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            data = yaml.safe_load(read_text_auto(candidate)) or {}# 读取并解析候选文件内容，返回空字典
            return data
    return {}


def _match_source(relative_path: str, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    """根据资产路径匹配资产源。"""
    normalized = relative_path.replace("\\", "/")# 把 \ 全部换成 /
    for source in sources:
        # 遍历所有资产源
        # 从资产源中获取目录路径，去掉 \ 并去掉首尾的 /
        directory = str(source.get("directory", "")).replace("\\", "/").strip("/")
        if directory and normalized.startswith(directory):# 如果资产路径以资产源目录路径开头
            return source
    return None


def _asset_id(category: str, index: int) -> str:
    """根据资产分类和索引生成资产ID。"""
    return f"{category}_{index:04d}"


def _dir_size(path: Path) -> int:
    """计算目录大小（字节）。"""
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _read_metadata_safely(path: Path, category: str) -> dict[str, Any]:
    """安全读取资产元数据。调用readers模块函数。"""
    try:
        return readers.read_asset_metadata(path, category)
    except Exception as exc:  
        return {"errors": [str(exc)]}


def _scan_assets(input_dirs: list[Path], config: dict[str, Any], source_manifest: dict[str, Any]) -> dict[str, Any]:
    """扫描资产目录，生成资产列表。"""
    # 从配置中获取忽略模式
    ignore_patterns = config.get("ignore_patterns", [])
    # 从配置中获取是否跟随符号链接
    follow_symlinks = bool(config.get("follow_symlinks", False))
    # 从资产清单文件中获取资产源
    sources = source_manifest.get("sources", []) if isinstance(source_manifest, dict) else []
    
    all_files: list[Path] = []
    for root in input_dirs:
        if root.is_file():# 如果当前路径是普通文件
            all_files.append(root)
        else:# 如果当前路径是目录
            # 递归遍历目录树，返回所有文件路径
            all_files.extend(_walk_files(root, ignore_patterns, follow_symlinks))

    oblique_roots: list[Path] = []# 存储所有倾斜摄影目录路径的列表
    oblique_files: set[Path] = set()# 存储所有倾斜摄影文件路径的集合
    for root in input_dirs:
        if root.is_dir():
            for oblique_root in readers.find_oblique_roots(root):# 倾斜摄影目录路径
                oblique_roots.append(oblique_root)
                oblique_files.update(p for p in oblique_root.rglob("*") if p.is_file())

    sidecar_files: set[Path] = set()# 存储所有侧车文件路径的集合
    primary_files: list[Path] = []# 存储所有主文件路径的列表
    for path in all_files:
        #剔除倾斜摄影文件，矢量旁系文件
        if path in oblique_files:
            continue
        if path.suffix.lower() in readers.SIDECAR_SUFFIXES:
            continue
        if path.suffix.lower() in readers.SIDECAR_EXTENSIONS:
            sidecar_files.update(readers.iter_sidecars(path))
        primary_files.append(path)

    assets: list[Asset] = []# 存储所有资产的列表
    unknown_files: list[dict[str, Any]] = []# 存储所有未知文件的列表
    category_counters: dict[str, int] = {}# 存储每个资产分类的文件数量的字典

    for root in input_dirs:
        if root.is_dir():
            for oblique_root in sorted(oblique_roots):
                if not oblique_root.is_relative_to(root):# 如果倾斜摄影目录路径不是根目录的子路径
                    continue
                relative = _as_posix(oblique_root, root)# 转换为POSIX路径
                metadata = _read_metadata_safely(oblique_root, "oblique")# 调用readers模块指定方法，读取倾斜摄影元数据
                source = _match_source(relative, sources)# 匹配资产源
                index = category_counters.get("oblique", 0)# 获取当前摄影文件数量索引
                category_counters["oblique"] = index + 1    # 增加当前摄影文件数量索引
                tile_files = [p for p in oblique_root.rglob("*.osgb") if p.is_file()]# 递归遍历目录树，返回所有 OSGB 文件路径
                assets.append(
                    Asset(
                        asset_id=_asset_id("osgb", index),
                        category="oblique",
                        format="OSGB",
                        relative_path=relative,
                        source_path=str(oblique_root),# 转换为字符串路径
                        source=source.get("supplier") if source else None,# 从资产源中获取供应商名称
                        batch=source.get("batch") if source else None,# 从资产源中获取批次号
                        source_name=source.get("name") if source else None,# 从资产源中获取资产源名称
                        purpose=source.get("purpose") if source else None,# 从资产源中获取资产用途
                        file_count=len(tile_files),# 计算倾斜摄影文件数量
                        size_bytes=_dir_size(oblique_root),# 计算倾斜摄影目录大小（字节）
                        oblique=metadata.get("oblique"),# 从倾斜摄影元数据中获取倾斜摄影参数
                        status="error" if metadata.get("errors") else "scanned",# 如果有错误信息，状态为错误；否则为已扫描
                        errors=metadata.get("errors", []),# 从倾斜摄影元数据中获取错误信息
                    )
                )

    for path in sorted(primary_files):
        # 剔除侧车文件（旁系文件）
        root = next((r for r in input_dirs if path.is_relative_to(r) or path == r), input_dirs[0])# 找到包含当前文件的根目录
        relative = _as_posix(path, root)# 转换为POSIX路径
        category, fmt = readers.detect_format(path)# 检测文件格式
        if category == "unknown":
            unknown_files.append({"path": relative, "suffix": path.suffix, "size_bytes": path.stat().st_size})# 存储未知文件信息
            continue
        metadata = _read_metadata_safely(path, category)# 调用readers模块指定方法，读取文件元数据
        source = _match_source(relative, sources)# 匹配资产源
        index = category_counters.get(category, 0)# 获取当前分类文件数量索引
        category_counters[category] = index + 1    # 增加当前分类文件数量索引
        sidecars = readers.sidecar_info(path) if path.suffix.lower() == ".shp" else None# 读取侧车文件（旁系文件）信息
        asset_size = path.stat().st_size + sum(p.stat().st_size for p in readers.iter_sidecars(path))# 计算资产大小（字节）
        assets.append(
            Asset(
                asset_id=_asset_id(category, index),# 生成资产ID
                category=category,# 资产分类
                format=fmt,# 文件格式
                relative_path=relative,# 相对路径
                source_path=str(path),# 转换为字符串路径
                source=source.get("supplier") if source else None,# 从资产源中获取供应商名称
                batch=source.get("batch") if source else None,# 从资产源中获取批次号
                source_name=source.get("name") if source else None,# 从资产源中获取资产源名称
                purpose=source.get("purpose") if source else None,# 从资产源中获取资产用途
                file_count=1 + (len(sidecars["present"]) if sidecars else 0),# 计算资产文件数量
                size_bytes=asset_size,# 资产大小（字节）
                sha256=readers.sha256_file(path) if config.get("hash_algorithm") == "sha256" else None,# 计算文件SHA-256哈希值
                mtime=str(path.stat().st_mtime),# 文件最后修改时间
                crs=metadata.get("crs"),# 从文件元数据中获取投影坐标系统
                bounds=metadata.get("bounds"),# 从文件元数据中获取边界框
                geom_type=metadata.get("geom_type"),# 从文件元数据中获取几何类型
                feature_count=metadata.get("feature_count"),# 从文件元数据中获取特征数量
                raster=metadata.get("raster"),# 从文件元数据中获取栅格信息
                pointcloud=metadata.get("pointcloud"),# 从文件元数据中获取点云信息
                sidecars=sidecars,# 从文件元数据中获取侧车文件（旁系文件）信息
                status="error" if metadata.get("errors") else "scanned",# 如果有错误信息，状态为错误；否则为已扫描
                errors=metadata.get("errors", []),# 从文件元数据中获取错误信息
            )
        )

    assets.sort(key=lambda item: (item.category, item.relative_path))# 按资产分类和相对路径排序
    return {
        "assets": assets,# 资产清单
        "unknown_files": unknown_files,# 未知文件清单
        "counts": {
            "scanned_files": len(all_files),# 扫描文件数量
            "assets": len(assets),# 资产数量
            "unknown_files": len(unknown_files),# 未知文件数量
        },
    }


@register
class InventoryBatchScan(BaseTool):
    spec = ToolSpec(
        id="inventory.batch_scan",
        name="数据资产扫描",
        module="m1",
        description="递归扫描输入目录，识别格式、聚合目录型资产并生成资产清单。",
        params={
            "input_dirs": {"type": "list[path]", "title": "输入数据目录", "required": True},
        },
        inputs=["input_dirs"],
        outputs=["assets.json", "assets.csv", "unknown_files.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        """作用：模拟运行工具，返回结果。"""
        input_dirs = [Path(p) for p in params.get("input_dirs", [])]# 输入数据目录
        config = ctx.profile.get("m1", {}).get("inventory", {})# 配置参数
        if not input_dirs:
            return {"error": "input_dirs is required"}# 输入数据目录不能为空
        preview = {"input_dirs": [str(p) for p in input_dirs], "estimated_files": 0, "note": "dry-run does not read metadata"}# 预览结果
        for root in input_dirs:
            if root.is_dir():# 如果是目录
                preview["estimated_files"] += len(_walk_files(root, config.get("ignore_patterns", []), bool(config.get("follow_symlinks", False))))# 计算目录下文件数量
            elif root.is_file():# 如果是文件
                preview["estimated_files"] += 1# 增加文件数量
        return preview# 返回预览结果

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """作用：执行工具，返回结果。"""
        input_dirs = [Path(p) for p in params.get("input_dirs", [])]# 输入数据目录
        if not input_dirs:
            return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, status="failed", errors=["input_dirs is required"])# 输入数据目录不能为空
        config = ctx.profile.get("m1", {}).get("inventory", {})# 配置参数
        source_manifest = _load_source_manifest(ctx.project_root, params)# 加载资产源清单
        scanned = _scan_assets(input_dirs, config, source_manifest)# 扫描资产
        assets = scanned["assets"]# 资产清单
        unknown_files = scanned["unknown_files"]# 未知文件清单

        assets_path = ctx.outputs_dir / "assets.json"# 资产清单路径
        assets_csv_path = ctx.outputs_dir / "assets.csv"# 资产清单CSV路径
        unknown_csv_path = ctx.outputs_dir / "unknown_files.csv"# 未知文件清单CSV路径

        assets_path.write_text(
            json.dumps(
                # 序列化资产清单为JSON格式
                {
                    "run_id": ctx.run_id,
                    "root": str(input_dirs[0]) if len(input_dirs) == 1 else [str(p) for p in input_dirs],# 输入数据目录
                    "counts": scanned["counts"],# 扫描统计
                    "assets": [asset.model_dump(mode="json") for asset in assets],# 资产清单
                },
                ensure_ascii=False,# 不使用ASCII编码
                indent=2,# 缩进2个空格
            ),
            encoding="utf-8",# 编码为UTF-8
        )

        with assets_csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            # 写入资产清单CSV文件（assets.csv）
            writer = csv.writer(handle)
            writer.writerow(["asset_id", "category", "format", "source", "batch", "relative_path", "size_bytes", "crs", "feature_count"])
            for asset in assets:
                crs = asset.crs.get("authority") if asset.crs else None# 获取CRS
                if crs and asset.crs.get("code") is not None:# 如果有EPSG代码
                    crs = f"{crs}:{asset.crs['code']}"
                writer.writerow(
                    #逐行写入
                    [asset.asset_id, asset.category, asset.format, asset.source, asset.batch, asset.relative_path, asset.size_bytes, crs, asset.feature_count]
                )

        with unknown_csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            # 写入未知文件清单CSV文件（比如栅格旁系文件）（unknown_files.csv）
            writer = csv.DictWriter(handle, fieldnames=["path", "suffix", "size_bytes"])
            writer.writeheader()
            writer.writerows(unknown_files)

        items = [
            ItemResult(
                asset_id=asset.asset_id,# 资产ID
                level=QCLevel.WARN if asset.errors else QCLevel.PASS,# 质量控制等级
                message=", ".join(asset.errors) if asset.errors else f"扫描完成，格式：{asset.format}",# 消息
                details={"relative_path": asset.relative_path, "format": asset.format, "size_bytes": asset.size_bytes},# 详情
            )
            for asset in assets
        ]
        return ToolResult(
            tool_id=self.spec.id,
            run_id=ctx.run_id,
            items=items,
            outputs=[str(assets_path), str(assets_csv_path), str(unknown_csv_path)],
            summary=scanned["counts"],
        )
