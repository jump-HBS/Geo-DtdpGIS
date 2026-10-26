"""Schema-driven parameter form."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ParamForm(QWidget):
    def __init__(self, defaults: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.defaults = defaults or {}
        self.layout = QFormLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.widgets: dict[str, QWidget] = {}
        self.param_types: dict[str, str] = {}
        self.start_dir = ""

    def build(self, params: dict[str, Any]) -> None:
        self.clear_form()
        normal_params = {name: schema for name, schema in params.items() if not (isinstance(schema, dict) and schema.get("advanced"))}
        advanced_params = {name: schema for name, schema in params.items() if isinstance(schema, dict) and schema.get("advanced")}

        for name, schema in normal_params.items():
            widget = self._make_widget(name, schema)
            title = schema.get("title") if isinstance(schema, dict) else name
            self.layout.addRow(title, widget)
            self.widgets[name] = widget
            self.param_types[name] = schema.get("type", "string") if isinstance(schema, dict) else "string"

        if advanced_params:
            group = QGroupBox("高级选项（可选）")
            group.setCheckable(True)
            group.setChecked(False)
            advanced_layout = QFormLayout(group)
            for name, schema in advanced_params.items():
                widget = self._make_widget(name, schema)
                title = schema.get("title") if isinstance(schema, dict) else name
                advanced_layout.addRow(title, widget)
                self.widgets[name] = widget
                self.param_types[name] = schema.get("type", "string") if isinstance(schema, dict) else "string"
            self.layout.addRow(group)

    def set_defaults(self, defaults: dict[str, Any]) -> None:
        self.defaults = defaults

    def clear_form(self) -> None:
        while self.layout.rowCount():
            self.layout.removeRow(0)
        self.widgets.clear()
        self.param_types.clear()

    def _make_widget(self, name: str, schema: Any) -> QWidget:
        if not isinstance(schema, dict):
            schema = {"type": "string"}
        schema_type = schema.get("type", "string")
        default = schema.get("default", self.defaults.get(name))

        if schema_type == "list[path]":
            widget = QLineEdit()
            widget.setPlaceholderText("多个路径用分号分隔")
            if default:
                widget.setText(";".join(default) if isinstance(default, list) else str(default))
            browse = QPushButton("浏览")
            browse.clicked.connect(lambda: self._browse_directory(widget))
            box = QWidget()
            layout = QFormLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.addRow(widget)
            layout.addRow(browse)
            return box
        if schema_type == "path":
            widget = QLineEdit()
            if default:
                widget.setText(str(default))
            browse = QPushButton("浏览")
            file_filter = schema.get("file_filter") or _filter_for_param(name)
            browse.clicked.connect(lambda checked=False, w=widget, ft=file_filter: self._browse_path(w, schema.get("path_type", "directory"), ft))
            box = QWidget()
            layout = QFormLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.addRow(widget)
            layout.addRow(browse)
            return box
        if schema_type == "integer":
            widget = QSpinBox()
            widget.setRange(-(2**31), 2**31 - 1)
            if default is not None:
                widget.setValue(int(default))
            return widget
        if schema_type == "number":
            widget = QDoubleSpinBox()
            widget.setRange(-1e9, 1e9)
            widget.setDecimals(6)
            if default is not None:
                widget.setValue(float(default))
            return widget
        if schema_type == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(default))
            return widget
        if schema_type == "enum":
            widget = QComboBox()
            widget.addItems(schema.get("enum", []))
            return widget
        widget = QLineEdit()
        if default:
            widget.setText(str(default))
        return widget

    def _browse_directory(self, widget: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录", self.start_dir)
        if path:
            widget.setText(path)

    def _browse_path(self, widget: QLineEdit, path_type: str, file_filter: str = "") -> None:
        if path_type == "file":
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", self.start_dir, file_filter)
        else:
            path = QFileDialog.getExistingDirectory(self, "选择目录", self.start_dir)
        if path:
            widget.setText(path)

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, widget in self.widgets.items():
            if self.param_types.get(name) == "list[path]":
                child = widget.findChild(QLineEdit)
                text = child.text().strip() if child else ""
                result[name] = [item.strip() for item in text.split(";") if item.strip()]
            elif isinstance(widget, QLineEdit):
                text = widget.text().strip()
                result[name] = text
            elif isinstance(widget, QSpinBox):
                result[name] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                result[name] = widget.value()
            elif isinstance(widget, QCheckBox):
                result[name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[name] = widget.currentText()
            else:
                child = widget.findChild(QLineEdit)
                result[name] = child.text().strip() if child else ""
        return result


def _filter_for_param(name: str) -> str:
    if name == "assets_file":
        return "JSON 文件 (*.json)"
    if name == "source_manifest":
        return "YAML 文件 (*.yaml)"
    if name in {"audit_file", "vector_manifest", "raster_manifest", "topology_manifest"}:
        return "CSV 文件 (*.csv)"
    return "所有文件 (*.*)"
