"""Project profile loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dtdpgis.core.encoding import read_text_auto


DEFAULT_PROFILE: dict[str, Any] = {
    "m1": {
        "inventory": {
            "hash_algorithm": "none",
            "follow_symlinks": False,
            "ignore_patterns": ["**/__pycache__/**", "**/.git/**"],
        },
        "ingest_qc": {
            "quarantine_on": ["fail"],
            "project_extent": None,
            "extent_buffer_m": 500,
            "rules": {
                "common.readable": {"level": "fail"},
                "common.non_empty": {"level": "fail"},
                "common.sidecar_complete": {"level": "fail"},
                "common.crs_declared": {"level": "fail", "allow_missing_for": ["cad", "unknown"]},
                "common.bounds_within_project": {"level": "warn"},
                "common.hash_match": {"level": "fail"},
                "common.duplicate": {"level": "warn"},
                "vector.feature_count_positive": {"level": "fail"},
                "raster.dimensions_positive": {"level": "fail"},
                "oblique.tile_count_positive": {"level": "fail"},
                "oblique.has_metadata": {"level": "warn"},
            },
        },
        "pointcloud_stats": {
            "sample_size": 200000,
            "spacing_method": "median",
            "z_unit": "meter",
            "sentinel_z": [-9999, -99999, 9999, 99999],
            "project_z_range": None,
        },
    },
    "m2": {
        "project_crs": {
            "target_epsg": None,
            "target_central_meridian": None,
            "target_unit": "metre",
            "vertical_datum": None,
        },
        "audit": {
            "assumed_crs": {},
            "checks": {
                "CRS_MISSING": {"level": "fail"},
                "CRS_DECLARED_UNKNOWN": {"level": "fail"},
                "CRS_VALUE_INCONSISTENT": {"level": "fail"},
                "CRS_ASSUMED_BY_CONFIG": {"level": "warn"},
                "UNIT_MISMATCH": {"level": "warn"},
                "CM_OUT_OF_PROJECT_BAND": {"level": "warn"},
                "DATUM_TRANSFORM_REQUIRED": {"level": "warn"},
                "VERTICAL_DATUM_UNDECLARED": {"level": "warn"},
            },
        },
        "vector_reproject": {
            "output_format": "GPKG",
            "always_xy": True,
            "allow_ballpark": False,
        },
        "raster_reproject": {
            "resampling": {"DEM": "bilinear", "DOM": "bilinear", "thematic": "nearest"},
            "target_resolution": None,
            "grid_align": "project",
            "compression": "LZW",
            "tiled": True,
            "nodata": None,
        },
    },
    "m3": {
        "topology_repair": {
            "snap_tolerance": 0.001,
            "sliver_area_threshold": 1.0,
            "sliver_ratio_threshold": 10.0,
            "rules": {
                "SELF_INTERSECTION": {"enabled": True, "level": "fail", "action": "repair"},
                "INVALID_RING": {"enabled": True, "level": "fail", "action": "repair"},
                "REPEATED_VERTICES": {"enabled": True, "level": "warn", "action": "repair"},
                "SLIVER_POLYGON": {"enabled": True, "level": "warn", "action": "mark"},
            },
            "output_format": "GPKG",
        },
        "attribute_normalize": {
            "default_null_policy": "empty_string",
            "schemas": {"by_asset_id": {}, "by_layer": {}},
        },
    },
    "m4": {
        "raster_types": {"by_asset_id": {}, "patterns": {}},
        "fill_holes": {
            "dem": {"max_hole_pixels": 100, "max_dist": 20, "smooth_iterations": 1},
            "dom": {"max_hole_pixels": 50, "max_dist": 10, "smooth_iterations": 0},
            "classification": {"action": "mark_only"},
            "mark_filled_mask": True,
        },
        "outlier_fix": {
            "dem": {"window_size": 5, "method": "median_residual", "soft_threshold": 30, "hard_threshold": 80, "action": "repair_hard_only"},
            "dom": {"window_size": 3, "method": "median_residual", "soft_threshold": 20, "hard_threshold": 50, "action": "repair_hard_only"},
            "classification": {"action": "mark_only"},
        },
        "mosaic": {
            "dem": {"overlap_strategy": "mean", "seam_tolerance_pixels": 2, "seam_elevation_threshold": 0.5, "resampling": "bilinear"},
            "dom": {"overlap_strategy": "mean", "seam_tolerance_pixels": 2, "color_balance": False, "resampling": "bilinear"},
            "classification": {"overlap_strategy": "first", "resampling": "nearest", "seam_tolerance_pixels": 2},
        },
    },
    "m5": {
        "ground_filter": {
            "csf": {
                "resolution": 1.0,
                "threshold": 0.5,
                "hdiff": 0.3,
                "rigidness": 3,
                "step": 0.65,
                "iterations": 500,
                "ground_class": 2,
            },
            "dem": {"resolution": 1.0, "output_nodata": -9999},
            "chunk_size": 5000000,
            "ground_ratio_warn": 0.7,
        },
        "thin": {
            "mode": "random",
            "random": {"keep_ratio": 0.3, "seed": 42},
            "voxel": {"cell_size": 0.1},
            "preserve_ground": True,
            "ground_class": 2,
        },
        "oblique_qc": {
            "float_object": {"max_ground_clearance": 2.0, "max_float_tile_ratio": 0.1},
            "texture_distortion": {"sample_tiles": 20},
            "hole": {"max_hole_area_ratio": 0.2},
            "coord": {"expected_crs": None, "bounds_tolerance": 1000},
            "metadata": {"require_srs": True},
        },
        "oblique_convert": {"enabled": False, "command": "", "args_template": "-i {input} -o {output}", "timeout": 3600},
    },
    "m6": {
        "layer_roles": {},
        "spatial_relation": {
            "tolerance": 0.01,
            "checks": {
                "ROAD_WATER_OVERLAP": {"enabled": True, "level": "warn"},
                "WATER_WATER_OVERLAP": {"enabled": True, "level": "warn", "min_overlap_area": 1.0},
                "BUILDING_OUTSIDE_BOUNDARY": {"enabled": True, "level": "warn"},
                "POI_OUTSIDE_ADMIN": {"enabled": True, "level": "fail"},
                "POI_ADMIN_MISMATCH": {"enabled": True, "level": "warn"},
                "WATER_OUTSIDE_ADMIN": {"enabled": True, "level": "warn"},
            },
            "containment_rules": [],
            "output_issue_geometry": True,
        },
        "building_extrude": {
            "building_role": "building",
            "floors_field": "floors",
            "height_field": "height",
            "floor_height": 3.0,
            "max_floors": 100,
            "suspicious_action": "mark_only",
            "output": {"gpkg": True, "geojson": True, "geojson_geometry": "2d_footprint"},
        },
    },
    "m7": {
        "plugins_dir": None,
    }
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_profile(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    profile = deep_merge(DEFAULT_PROFILE, {})
    profiles_dir = root / "profiles"
    candidates = []
    if profiles_dir.exists():
        candidates.extend([profiles_dir / "default.yaml", profiles_dir / f"{root.name}.yaml"])
    for candidate in candidates:
        if candidate.exists():
            data = yaml.safe_load(read_text_auto(candidate)) or {}
            profile = deep_merge(profile, data)
    return profile
