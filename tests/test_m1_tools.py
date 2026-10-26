from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace
from dtdpgis._env import ensure_runtime_env


def _make_las(path: Path, *, points: int = 200, broken: bool = False, empty: bool = False) -> None:
    if empty:
        path.write_bytes(b"")
        return
    if broken:
        path.write_text("not a las file", encoding="utf-8")
        return

    ensure_runtime_env()
    import pdal

    rng = np.random.default_rng(42)
    array = np.zeros(points, dtype=[("X", "f8"), ("Y", "f8"), ("Z", "f8"), ("Classification", "u1")])
    array["X"] = rng.uniform(116.0, 116.01, points)
    array["Y"] = rng.uniform(39.8, 39.81, points)
    array["Z"] = rng.uniform(20, 80, points)
    array["Classification"] = rng.integers(1, 5, points)
    pipeline = pdal.Pipeline(
        json=json.dumps([{"type": "writers.las", "filename": str(path), "a_srs": "EPSG:4490"}]),
        arrays=[array],
    )
    pipeline.execute()


def _make_vector(path: Path) -> None:
    ensure_runtime_env()
    gdf = gpd.GeoDataFrame(
        {"id": [1], "name": ["building"]},
        geometry=[Polygon([(116.0, 39.8), (116.01, 39.8), (116.01, 39.81), (116.0, 39.81)])],
        crs="EPSG:4490",
    )
    gdf.to_file(path, driver="ESRI Shapefile")


def _make_raster(path: Path) -> None:
    ensure_runtime_env()
    profile = {
        "driver": "GTiff",
        "height": 10,
        "width": 10,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4490",
        "transform": from_origin(116.0, 39.81, 0.001, 0.001),
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(np.arange(100, dtype=np.float32).reshape(1, 10, 10))


def _make_osgb(root: Path, name: str, *, has_metadata: bool) -> None:
    block = root / name
    tile = block / "Data" / "Tile_+000_+000"
    tile.mkdir(parents=True)
    (tile / "tile.osgb").write_bytes(b"\x00\x01")
    if has_metadata:
        (block / "metadata.xml").write_text("<metadata/>", encoding="utf-8")


def _make_fixture(project: Path) -> Path:
    inputs = project / "inputs"
    (inputs / "01_点云").mkdir(parents=True)
    (inputs / "02_矢量").mkdir(parents=True)
    (inputs / "03_栅格").mkdir(parents=True)
    (inputs / "04_倾斜摄影").mkdir(parents=True)
    (inputs / "05_CAD").mkdir(parents=True)
    (inputs / "99_其他").mkdir(parents=True)

    _make_las(inputs / "01_点云" / "园区_A.las")
    _make_las(inputs / "01_点云" / "空_D.las", empty=True)
    _make_las(inputs / "01_点云" / "损坏_E.las", broken=True)
    _make_vector(inputs / "02_矢量" / "建筑.shp")
    _make_raster(inputs / "03_栅格" / "DOM_2026.tif")
    _make_osgb(inputs / "04_倾斜摄影", "Block_A", has_metadata=True)
    _make_osgb(inputs / "04_倾斜摄影", "Block_B", has_metadata=False)
    (inputs / "05_CAD" / "规划总图.dwg").write_bytes(b"dwg")
    (inputs / "99_其他" / "readme.txt").write_text("readme", encoding="utf-8")
    (inputs / "99_其他" / "temp.tmp").write_bytes(b"tmp")
    return inputs


def test_m1_pipeline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    inputs = _make_fixture(project)
    ctx = ProjectWorkspace(project).new_run("run_001")

    inventory = get_tool("inventory.batch_scan")()
    result = inventory.execute(ctx, {"input_dirs": [str(inputs)]})
    assert result.status == "success"
    assert (ctx.outputs_dir / "assets.json").exists()

    assets = json.loads((ctx.outputs_dir / "assets.json").read_text(encoding="utf-8"))
    categories = {asset["category"] for asset in assets["assets"]}
    assert categories == {"pointcloud", "vector", "raster", "oblique", "cad"}
    assert assets["counts"]["unknown_files"] == 2
    assert len(assets["assets"]) == 8

    ingest = get_tool("ingest.batch_qc")()
    ingest_result = ingest.execute(ctx, {})
    assert ingest_result.status == "success"
    assert (ctx.outputs_dir / "qc_summary.csv").exists()
    assert (ctx.outputs_dir / "quarantine_manifest.csv").exists()
    assert ingest_result.summary["fail"] >= 2

    pointcloud = get_tool("pointcloud.batch_stats")()
    pointcloud_result = pointcloud.execute(ctx, {})
    assert pointcloud_result.status == "success"
    assert (ctx.outputs_dir / "pointcloud_stats.csv").exists()
    assert pointcloud_result.summary["pointcloud_assets"] == 3
