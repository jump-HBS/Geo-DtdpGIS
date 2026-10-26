"""Register M5 tools."""

from dtdpgis.modules.m5.ground import PointcloudBatchGroundFilter
from dtdpgis.modules.m5.oblique import ObliqueBatchQualityCheck
from dtdpgis.modules.m5.thin import PointcloudBatchThin

__all__ = ["ObliqueBatchQualityCheck", "PointcloudBatchGroundFilter", "PointcloudBatchThin"]
