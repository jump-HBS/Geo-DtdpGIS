"""Result and output-file display panel."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
    QVBoxLayout,
)


CSV_TITLES = {
    "assets.csv": "资产清单",
    "unknown_files.csv": "未识别文件",
    "qc_summary.csv": "质检明细",
    "pointcloud_stats.csv": "点云统计",
    "quarantine_manifest.csv": "隔离清单",
    "crs_audit.csv": "坐标审计",
    "vector_reproject_manifest.csv": "矢量重投影清单",
    "raster_reproject_manifest.csv": "栅格重投影清单",
    "topology_qc.csv": "拓扑检查",
    "topology_repair_manifest.csv": "拓扑修复清单",
    "attribute_qc.csv": "属性检查",
    "attribute_normalize_manifest.csv": "属性规范化清单",
    "raster_hole_qc.csv": "空洞检查",
    "raster_fill_manifest.csv": "空洞填充清单",
    "raster_outlier_qc.csv": "异常值检查",
    "raster_outlier_manifest.csv": "异常值修复清单",
    "raster_mosaic_qc.csv": "镶嵌检查",
    "raster_mosaic_manifest.csv": "镶嵌清单",
    "pointcloud_ground_filter_qc.csv": "地面滤波检查",
    "pointcloud_ground_filter_manifest.csv": "地面滤波清单",
    "pointcloud_thin_qc.csv": "抽稀检查",
    "pointcloud_thin_manifest.csv": "抽稀清单",
    "oblique_qc.csv": "倾斜摄影检查",
    "oblique_qc_manifest.csv": "倾斜摄影清单",
    "spatial_relation_qc.csv": "空间关系检查",
    "spatial_relation_manifest.csv": "空间关系清单",
    "building_extrude_qc.csv": "建筑挤出检查",
    "building_extrude_manifest.csv": "建筑挤出清单",
    "ai_plugin_qc.csv": "AI 插件检查",
    "ai_plugin_manifest.csv": "AI 插件清单",
}

HEADER_LABELS = {
    "run_id": "运行ID",
    "asset_id": "资产ID",
    "category": "类别",
    "format": "格式",
    "relative_path": "文件路径",
    "source_path": "源路径",
    "output_path": "输出路径",
    "rule_id": "检查项",
    "level": "结论",
    "message": "信息",
    "expected": "期望值",
    "actual": "实际值",
    "status": "状态",
    "feature_count": "要素数",
    "point_count": "点数",
    "z_min": "Z最小值",
    "z_max": "Z最大值",
    "crs": "坐标系",
    "tile_count": "瓦片数",
    "source_stage": "来源阶段",
    "duration_seconds": "耗时(秒)",
    "mosaic_id": "镶嵌ID",
    "raster_type": "栅格类型",
}

LONG_COLUMNS = {"message", "relative_path", "source_path", "output_path", "actual", "expected", "class_distribution"}


class ResultPanel(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result_table = self._new_table(["资产名称", "结论", "信息"])
        self.files_table = self._new_table(["文件", "类型", "路径", "大小"])
        self.addTab(self.result_table, "处理结果")
        self.addTab(self.files_table, "文件产出")
        self._dynamic_tabs: list[int] = []

    def set_result_items(self, items) -> None:
        self.result_table.setRowCount(0)
        for item in items:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            details = getattr(item, "details", {}) or {}
            name = details.get("relative_path") or getattr(item, "asset_id", "")
            level = getattr(item, "level", "")
            level_text = level.value if hasattr(level, "value") else str(level)
            message = getattr(item, "message", "")
            self._set_cell(self.result_table, row, 0, str(name), long=False)
            self._set_cell(self.result_table, row, 1, level_text, long=False)
            self._set_cell(self.result_table, row, 2, message, long=True)

    def set_output_files(self, outputs) -> None:
        self._clear_dynamic_tabs()
        self.files_table.setRowCount(0)
        for output in outputs:
            path = Path(str(output))
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)
            self._set_cell(self.files_table, row, 0, path.name, long=False)
            self._set_cell(self.files_table, row, 1, path.suffix or "目录", long=False)
            self._set_cell(self.files_table, row, 2, str(path), long=True)
            self._set_cell(self.files_table, row, 3, self._format_size(path), long=False)
            if path.suffix.lower() == ".csv" and path.exists():
                self._add_csv_tab(path)

    def _add_csv_tab(self, path: Path) -> None:
        table = self._new_table([])
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(["信息"])
            table.setRowCount(1)
            self._set_cell(table, 0, 0, "CSV 文件为空", long=False)
        else:
            headers = list(rows[0].keys())
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels([HEADER_LABELS.get(h, h) for h in headers])
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for col_index, header in enumerate(headers):
                    value = row.get(header, "")
                    self._set_cell(table, row_index, col_index, value, long=header in LONG_COLUMNS)
        title = CSV_TITLES.get(path.name, path.name)
        self.addTab(table, title)
        self._dynamic_tabs.append(self.count() - 1)

    def _new_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers) if headers else 1)
        if headers:
            table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSortingEnabled(False)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.itemDoubleClicked.connect(self._open_output_location)
        return table

    def _set_cell(self, table: QTableWidget, row: int, column: int, text: str, long: bool) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft if long else Qt.AlignCenter)
        if text:
            item.setToolTip(text)
        table.setItem(row, column, item)

    def _format_size(self, path: Path) -> str:
        if not path.exists():
            return ""
        if path.is_dir():
            return "目录"
        size = path.stat().st_size
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _open_output_location(self, item) -> None:
        table = item.tableWidget()
        row = item.row()
        for col in range(table.columnCount()):
            header = table.horizontalHeaderItem(col).text() if table.horizontalHeaderItem(col) else ""
            if header in {"路径", "输出路径", "源路径", "文件路径"}:
                path_text = table.item(row, col).text() if table.item(row, col) else ""
                path = Path(path_text)
                if path.is_file():
                    QFileDialog.getOpenFileName(self, "输出文件", str(path.parent))
                elif path.exists():
                    QFileDialog.getExistingDirectory(self, "输出目录", str(path))
                return

    def _clear_dynamic_tabs(self) -> None:
        while self.count() > 2:
            self.removeTab(self.count() - 1)
        self._dynamic_tabs.clear()
