"""Register M4 tools."""

from dtdpgis.modules.m4.fill import RasterBatchFillHoles
from dtdpgis.modules.m4.mosaic import RasterBatchMosaic
from dtdpgis.modules.m4.outlier import RasterBatchOutlierFix

__all__ = ["RasterBatchFillHoles", "RasterBatchMosaic", "RasterBatchOutlierFix"]
