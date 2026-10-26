"""Shared synthetic data fixtures for tests."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon

from dtdpgis._env import ensure_runtime_env


def make_m2_inputs(project: Path) -> Path:
    ensure_runtime_env()
    inputs = project / "inputs"
    vector_dir = inputs / "02_矢量"
    raster_dir = inputs / "03_栅格"
    vector_dir.mkdir(parents=True)
    raster_dir.mkdir(parents=True)

    gdf = gpd.GeoDataFrame(
        {"id": [1], "name": ["building"]},
        geometry=[Polygon([(116.0, 39.8), (116.01, 39.8), (116.01, 39.81), (116.0, 39.81)])],
        crs="EPSG:4326",
    )
    gdf.to_file(vector_dir / "buildings.geojson", driver="GeoJSON")

    profile = {
        "driver": "GTiff",
        "height": 10,
        "width": 10,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": from_origin(116.0, 39.81, 0.001, 0.001),
    }
    with rasterio.open(raster_dir / "DOM_2026.tif", "w", **profile) as dataset:
        dataset.write(np.arange(100, dtype=np.float32).reshape(1, 10, 10))
    return inputs


def make_m3_inputs(project: Path) -> Path:
    ensure_runtime_env()
    inputs = project / "inputs"
    vector_dir = inputs / "02_矢量"
    vector_dir.mkdir(parents=True)

    geometries = [
        Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]),
        Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)]),
        Polygon([(0, 0), (1, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
        Polygon([(3, 0), (3.1, 0.1), (3.1, 0.12), (3, 0)]),
    ]
    gdf = gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C", "D"],
            "floor": [3, 5, 2, 1],
            "func": ["residential", "commercial", "commercial", None],
        },
        geometry=geometries,
        crs="EPSG:4490",
    )
    gdf.to_file(vector_dir / "buildings.geojson", driver="GeoJSON")
    return inputs


def make_m4_inputs(project: Path) -> Path:
    ensure_runtime_env()
    inputs = project / "inputs"
    raster_dir = inputs / "03_栅格"
    raster_dir.mkdir(parents=True)

    profile = {
        "driver": "GTiff",
        "height": 10,
        "width": 10,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4490",
        "transform": from_origin(116.0, 39.8, 0.001, 0.001),
        "nodata": -9999.0,
    }
    dem_a = np.full((10, 10), 50.0, dtype=np.float32)
    dem_a[0, 0] = -9999.0
    dem_a[5, 5] = 999.0
    with rasterio.open(raster_dir / "DEM_A.tif", "w", **profile) as dataset:
        dataset.write(dem_a, 1)

    profile_b = profile.copy()
    profile_b["transform"] = from_origin(116.005, 39.795, 0.001, 0.001)
    dem_b = np.full((10, 10), 52.0, dtype=np.float32)
    with rasterio.open(raster_dir / "DEM_B.tif", "w", **profile_b) as dataset:
        dataset.write(dem_b, 1)
    return inputs


def make_m5_inputs(project: Path) -> Path:
    ensure_runtime_env()
    import json

    import pdal

    inputs = project / "inputs"
    pointcloud_dir = inputs / "01_点云"
    oblique_root = inputs / "04_倾斜摄影" / "Block_A"
    pointcloud_dir.mkdir(parents=True)
    tile_dir = oblique_root / "Data" / "Tile_+000_+000"
    tile_dir.mkdir(parents=True)

    rng = np.random.default_rng(7)
    count = 300
    array = np.zeros(count, dtype=[("X", "f8"), ("Y", "f8"), ("Z", "f8"), ("Classification", "u1")])
    array["X"] = rng.uniform(116.0, 116.01, count)
    array["Y"] = rng.uniform(39.8, 39.81, count)
    array["Z"] = rng.uniform(20, 80, count)
    array["Classification"] = 0
    las_path = pointcloud_dir / "园区_A.las"
    writer = pdal.Pipeline(
        json=json.dumps([{"type": "writers.las", "filename": str(las_path), "a_srs": "EPSG:4490"}]),
        arrays=[array],
    )
    writer.execute()

    (tile_dir / "tile.osgb").write_bytes(b"tile")
    (tile_dir / "empty.osgb").write_bytes(b"")
    (oblique_root / "metadata.xml").write_text("<metadata><SRS>EPSG:4490</SRS></metadata>", encoding="utf-8")
    return inputs


def make_m6_inputs(project: Path) -> Path:
    ensure_runtime_env()
    inputs = project / "inputs"
    vector_dir = inputs / "02_矢量"
    vector_dir.mkdir(parents=True)

    buildings = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4],
            "floors": [3, 5, 0, None],
            "function": ["residential", "commercial", "commercial", None],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Polygon([(1.2, 0), (2.2, 0), (2.2, 1), (1.2, 1), (1.2, 0)]),
            Polygon([(0, 1.2), (1, 1.2), (1, 2.2), (0, 2.2), (0, 1.2)]),
            Polygon([(10, 10), (11, 10), (11, 11), (10, 11), (10, 10)]),
        ],
        crs="EPSG:4490",
    )
    roads = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString([(0, 0.5), (3, 0.5)])],
        crs="EPSG:4490",
    )
    water = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Polygon([(1.5, -1), (2.5, -1), (2.5, 1), (1.5, 1), (1.5, -1)])],
        crs="EPSG:4490",
    )
    boundary = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Polygon([(-1, -1), (4, -1), (4, 4), (-1, 4), (-1, -1)])],
        crs="EPSG:4490",
    )
    poi = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(0.5, 0.5), Point(20, 20)],
        crs="EPSG:4490",
    )
    admin = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Polygon([(-2, -2), (5, -2), (5, 5), (-2, 5), (-2, -2)])],
        crs="EPSG:4490",
    )

    for name, frame in [("buildings", buildings), ("roads", roads), ("water", water), ("boundary", boundary), ("poi", poi), ("admin", admin)]:
        frame.to_file(vector_dir / f"{name}.geojson", driver="GeoJSON")
    return inputs
