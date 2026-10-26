"""运行时环境变量配置模块。在每次用 GIS 库之前，
先把 PROJ_LIB 和 GDAL_DATA 这两个环境变量指向
 rasterio 自带的正确数据库目录，防止 GDAL/PROJ
 因为找不到底层数据文件而崩溃或算错坐标。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _site_packages() -> Path:
    """返回当前解释器的 site-packages 目录路径"""
    return Path(sys.prefix) / "Lib" / "site-packages"


def configure_runtime() -> None:
    """配置运行时环境变量，确保 GDAL/PROJ 指向捆绑的 GIS 数据。"""

    for dll_dir in (Path(sys.prefix) / "Library" / "bin", Path(sys.prefix) / "DLLs"):
        if dll_dir.exists():
            try:
                os.add_dll_directory(str(dll_dir))
            except OSError:
                pass

    proj_data = _site_packages() / "rasterio" / "proj_data"
    if (proj_data / "proj.db").exists():
        os.environ["PROJ_LIB"] = str(proj_data)

    gdal_data = _site_packages() / "rasterio" / "gdal_data"
    if (gdal_data / "gdalvrt.xsd").exists():
        os.environ["GDAL_DATA"] = str(gdal_data)


def ensure_runtime_env() -> None:
    """确保运行时环境变量已正确配置。"""

    configure_runtime()
