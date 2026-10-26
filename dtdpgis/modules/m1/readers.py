"""Format detection and lightweight metadata readers."""

from __future__ import annotations

import hashlib   # 导入哈希库，用于文件内容哈希
from pathlib import Path
from typing import Any      # 用于类型提示，允许任意类型

EXTENSION_FORMATS: dict[str, tuple[str, str]] = {
    ".las": ("pointcloud", "LAS"),# 点云格式
    ".laz": ("pointcloud", "LAZ"),# 压缩点云格式
    ".shp": ("vector", "ESRI Shapefile"),# 矢量格式
    ".geojson": ("vector", "GeoJSON"),# 矢量格式
    ".json": ("vector", "GeoJSON"),# 矢量格式
    ".gpkg": ("vector", "GeoPackage"),# 矢量格式
    ".tif": ("raster", "GeoTIFF"),# 栅格格式
    ".tiff": ("raster", "GeoTIFF"),# 栅格格式
    ".img": ("raster", "ERDAS IMAGINE"),# 栅格格式
    ".dwg": ("cad", "AutoCAD DWG"),# CAD格式
    ".dxf": ("cad", "AutoCAD DXF"),# CAD格式
    ".ifc": ("bim", "IFC"),# BIM格式（带语义的建筑构件对象）
}

# 矢量主文件 -> 旁系文件（sidecar）扩展名映射。
# 例如 .shp 主文件配套 .shx 索引、.dbf 属性、.prj 坐标参考、.cpg 编码等。
SIDECAR_EXTENSIONS: dict[str, list[str]] = {
    ".shp": [".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"],
}
# 所有已知旁系文件扩展名的集合，供快速判断文件是否属于旁系。(有两层列表，所以要两个列表推导式)
SIDECAR_SUFFIXES: set[str] = {suffix for suffixes in SIDECAR_EXTENSIONS.values() for suffix in suffixes}


def detect_format(path: Path) -> tuple[str, str]:
    # 获取文件后缀
    ext = path.suffix.lower()
    # 返回对应的文件类型与格式名称，或是未知类型与格式名称元组
    return EXTENSION_FORMATS.get(ext, ("unknown", ext.lstrip(".").upper() or "UNKNOWN"))


def sidecar_info(primary: Path) -> dict[str, Any]:
    """检查主文件的旁系文件完整性（针对 ESRI Shapefile 等多文件格式）。"""
    expected = SIDECAR_EXTENSIONS.get(primary.suffix.lower(), [])
    present = []
    missing = []
    for suffix in expected:
        candidate = primary.with_suffix(suffix)     # 生成旁系文件路径（更换拓展名）
        if candidate.exists():
            present.append(candidate.name)
        else:
            missing.append(candidate.name)
    return {
        "primary": primary.name,
        "expected": [primary.with_suffix(s).name for s in expected],  # 生成全部旁系文件名列表 
        "present": present,     # 实际存在的旁系文件列表
        "missing": missing,     # 缺失的旁系文件列表
    }


def iter_sidecars(primary: Path) -> list[Path]:
    """返回主文件旁系文件中实际存在的文件路径列表。
    与sidecar_info不同，这里只返回实际存在于[".shx", 
    ".dbf", ".prj", ".cpg", ".sbn", ".sbx"]中的文件路径。"""
    expected = SIDECAR_EXTENSIONS.get(primary.suffix.lower(), [])
    return [primary.with_suffix(suffix) for suffix in expected if primary.with_suffix(suffix).exists()]


def find_oblique_roots(root: Path) -> list[Path]:
    """查找倾斜摄影数据的根目录。适配51WORLD三中布局
    循环1——标准布局(4级)、循环2——metadata.xml布局(3级)、循环3——直接放在块目录布局(3级)。。"""
    candidates: set[Path] = set()
    # 遍历所有子目录，寻找名为 "data" 且包含 OSGB 文件的目录
    for directory in root.rglob("*"):   #.rglob遍历root目录下所有层的文件和子文件夹
        # 跳过非目录项
        if not directory.is_dir():      #.is_dir()判断是否为目录
            continue
        # 如果目录名为 "data" 且包含任何 OSGB 文件，则将其父目录添加到候选集合
        if directory.name.lower() == "data" and any(directory.rglob("*.osgb")):
            candidates.add(directory.parent)
    for directory in root.rglob("*"):
        if not directory.is_dir():
            continue
        # 如果目录下有 metadata.xml 文件且包含任何 OSGB 文件，则将其添加到候选集合
        if (directory / "metadata.xml").exists() and any(directory.rglob("*.osgb")):
            candidates.add(directory)
    # 如果候选集合为空，则遍历所有 OSGB 文件，将其父目录添加到候选集合
    if not candidates:
        for osgb_file in root.rglob("*.osgb"):
            if osgb_file.is_file():
                candidates.add(osgb_file.parent.parent)
    return sorted(candidates)


def _crs_from_pyproj(crs: Any) -> dict[str, Any] | None:        #鸭子类型检查，判断对象是否具有 to_epsg 和 to_wkt 方法
    """把 pyproj 风格的 CRS 对象或字符串标准化为统一字典结构。
    输入可以是“pyproj.CRS”、”rasterio"的"CRS"、"OGR CRS"、
    WKT 字符串、PROJ 字符串等任意 pyproj 能解析的形式。

    优先返回 EPSG 字典 ``{"authority": "EPSG", "code": 4326}``；
    若无 EPSG 编码则退化为 ``{"wkt": "..."}``；
    完全无法识别时返回 ``{"value": "<原始字符串>"}``"""
    if crs is None:
        return None
    if isinstance(crs, str):
        from pyproj import CRS

        try:
            # .from_user_input 可以解析各种 CRS 表示形式，包括 EPSG、WKT、PROJ 字符串等
            parsed = CRS.from_user_input(crs)
            epsg = parsed.to_epsg()
            if epsg:
                return {"authority": "EPSG", "code": epsg}
            return {"wkt": parsed.to_wkt()}
        except Exception:
            return {"value": crs}
    #getattr()函数用于获取对象的属性值，若属性不存在则返回默认值
    epsg = getattr(crs, "to_epsg", None)
    # 如果 epsg 方法存在且可调用，则调用它获取 EPSG 编码
    if callable(epsg):
        code = epsg()
        if code:
            return {"authority": "EPSG", "code": code}
    wkt = getattr(crs, "to_wkt", None)
    if callable(wkt):
        return {"wkt": wkt()}
    return {"wkt": str(crs)}


def read_vector_metadata(path: Path) -> dict[str, Any]:
    """读取矢量图层元数据。

    使用 pyogrio 打开指定路径的矢量数据（如 .shp、.geojson、.gpkg 等），
    提取坐标系、空间范围、几何类型以及要素总数等基本信息。

    Args:
        path: 矢量数据文件路径。

    Returns:
        包含以下字段的字典:
        - crs (dict | None): 坐标系信息，经 _crs_from_pyproj 标准化，
          形如 ``{"authority": "EPSG", "code": 4326}`` 或 ``{"wkt": "..."}``；
          若数据未定义坐标系则为 ``None``。
        - bounds (list[float] | None): 图层整体空间范围
          ``[xmin, ymin, xmax, ymax]``，若无法计算则为 ``None``。
        - geom_type (str): 图层几何类型（如 "Point"、"Polygon" 等）。
        - feature_count (int): 图层要素总数。
    """
    from dtdpgis._env import ensure_runtime_env

    ensure_runtime_env()
    import pyogrio
    # 读取矢量数据元数据，force_feature_count=True 确保获取到总数，force_total_bounds=True 确保获取到空间范围信息
    info = pyogrio.read_info(str(path), force_feature_count=True, force_total_bounds=True)
    # 从元数据中提取坐标系
    crs_value = info["crs"] if isinstance(info, dict) else getattr(info, "crs", None)
    # 从元数据中提取空间范围
    bounds_value = info.get("total_bounds") if isinstance(info, dict) else getattr(info, "total_bounds", None)
    # 转换为范围列表，若无法计算则为 None
    bounds = [float(v) for v in bounds_value] if bounds_value is not None else None     #float(v)将元素转换为浮点数
    return {
        "crs": _crs_from_pyproj(crs_value),
        "bounds": bounds,
        # 从元数据中提取几何类型
        "geom_type": info.get("geometry_type") if isinstance(info, dict) else getattr(info, "geometry_type", None),
        # 从元数据中提取要素总数
        "feature_count": info.get("features") if isinstance(info, dict) else getattr(info, "features", None),
    }


def read_raster_metadata(path: Path) -> dict[str, Any]:
    """读取栅格影像元数据。

    使用 rasterio 打开指定路径的栅格数据（如 .tif、.img），
    提取坐标系、空间范围、尺寸、波段数、数据类型及 nodata 值。

    Args:
        path: 栅格文件路径。

    Returns:
        包含以下字段的字典:
        - crs (dict | None): 坐标系信息，经 _crs_from_pyproj 标准化。
        - bounds (list[float]): 空间范围
          ``[xmin, ymin, xmax, ymax]``。
        - raster (dict): 栅格特有字段:
          - width / height (int): 像元宽高。
          - bands (int): 波段数。
          - dtype (str): 第一波段的 numpy 数据类型名。
          - nodata (list[float | None]): 每个波段的 nodata 值。
    """
    from dtdpgis._env import ensure_runtime_env

    ensure_runtime_env()
    import rasterio

    with rasterio.open(path) as dataset:
        # 从栅格数据中提取空间范围
        bounds = [dataset.bounds.left, dataset.bounds.bottom, dataset.bounds.right, dataset.bounds.top]
        # 从栅格数据中提取 nodata 值
        nodata = [dataset.nodatavals[i] for i in range(dataset.count)]
        return {
            "crs": _crs_from_pyproj(dataset.crs),
            "bounds": bounds,
            "raster": {
                "width": dataset.width,
                "height": dataset.height,
                "bands": dataset.count,
                "dtype": str(dataset.dtypes[0]),
                "nodata": nodata,
            },
        }


def read_pointcloud_metadata(path: Path) -> dict[str, Any]:
    """读取点云文件元数据（LAS/LAZ 等）。

    通过 PDAL 管道 ``readers.las`` 快速获取点云的坐标系、包围盒和点数。

    Args:
        path: 点云文件路径（支持 .las / .laz 等 PDAL readers.las 可识别的格式）。

    Returns:
        包含以下字段的字典:
        - crs (dict | None): 坐标系信息，从 PDAL 返回的 SRS JSON 中解析，
          形如 ``{"authority": "...", "code": 4326}``。
        - bounds (list[float | None] | None): 空间范围
          ``[minx, miny, maxx, maxy]``，各分量可能为 ``None``。
        - pointcloud (dict): 点云特有字段:
          - point_count (int | None): 总点数。
    """
    from dtdpgis._env import ensure_runtime_env

    ensure_runtime_env()
    import json

    import pdal
    # 创建 PDAL 管道，读取点云数据元数据
    pipeline = pdal.Pipeline(json=json.dumps([{"type": "readers.las", "filename": str(path)}]))
    # 执行管道,.quickinfo 获取点云数据元数据,只读元数据（坐标系、包围盒、点数等），不读点
    info = pipeline.quickinfo.get("readers.las", {})
    # 从元数据中提取空间范围
    bounds = info.get("bounds") or {}
    # 从元数据中提取坐标系
    srs_json = (info.get("srs") or {}).get("json") or {}
    # 从坐标系 JSON 中提取 authority 和 code
    crs_id = srs_json.get("id") or {}
    crs = None
    if crs_id.get("authority") and crs_id.get("code") is not None:
        crs = {"authority": crs_id["authority"], "code": int(crs_id["code"])}
    return {
        "crs": crs,
        "bounds": [bounds.get("minx"), bounds.get("miny"), bounds.get("maxx"), bounds.get("maxy")] if bounds else None,#三元表达式
        "pointcloud": {"point_count": info.get("num_points")},#提取点数
    }


def read_oblique_metadata(path: Path) -> dict[str, Any]:
    """倾斜摄影 OSGB 瓦片目录的轻量元数据。

    统计目录树下 ``*.osgb`` 瓦片总数，并检查是否存在
    ``metadata.xml``（51WORLD 等倾斜摄影平台的标准元数据文件）。

    Args:
        path: 倾斜摄影数据根目录。

    Returns:
        包含 oblique 字段的字典:
        - tile_count (int): 根目录下递归匹配到的 OSGB 文件总数。
        - has_metadata (bool): 是否找到 metadata.xml。
    """
    tiles = [p for p in path.rglob("*.osgb") if p.is_file()]#.rglob() 递归匹配所有符合条件子目录
    has_metadata = (path / "metadata.xml").exists() or any(p.name.lower() == "metadata.xml" for p in path.rglob("*.xml"))
    return {
        "oblique": {
            "tile_count": len(tiles),# 统计瓦片总数
            "has_metadata": has_metadata,# 是否找到 metadata.xml
        }
    }


def read_asset_metadata(path: Path, category: str) -> dict[str, Any]:
    """按数据类别分发到对应的元数据读取函数。"""
    if category == "vector":
        return read_vector_metadata(path)
    if category == "raster":
        return read_raster_metadata(path)
    if category == "pointcloud":
        return read_pointcloud_metadata(path)
    if category == "oblique":
        return read_oblique_metadata(path)
    return {}


def sha256_file(path: Path) -> str | None:
    """分块计算文件的 SHA-256 哈希值。
    作用：用于快速判断文件内容是否发生变化，避免重复处理大文件。"""
    if not path.is_file():
        return None
    digest = hashlib.sha256()# 初始化 SHA-256 哈希对象
    with path.open("rb") as handle:# 以二进制模式打开文件
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):# 以 1 MiB 为单位分块读取
            digest.update(chunk)
    return digest.hexdigest()# 返回十六进制哈希值