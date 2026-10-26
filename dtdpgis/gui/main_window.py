"""Main desktop window."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dtdpgis.core.registry import TOOL_REGISTRY
from dtdpgis.core.workspace import ProjectWorkspace
from dtdpgis.gui.param_form import ParamForm
from dtdpgis.gui.result_panel import ResultPanel
from dtdpgis.gui.theme import apply_theme


MODULE_NAMES = {
    "m1": "数据接入与质检",
    "m2": "坐标与基准",
    "m3": "矢量清洗与拓扑",
    "m4": "栅格与地形",
    "m5": "点云与倾斜摄影",
    "m6": "矢量融合与三维化",
    "m7": "AI 辅助插件",
}


class ToolWorker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, tool_cls, ctx, params, parent=None) -> None:
        super().__init__(parent)
        self.tool_cls = tool_cls
        self.ctx = ctx
        self.params = params

    def run(self) -> None:
        try:
            tool = self.tool_cls()
            result = tool.execute(self.ctx, self.params)
            self.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DtdpGIS")
        self.resize(1440, 900)
        self.workspace: ProjectWorkspace | None = None
        self.ctx = None
        self.current_tool_id: str | None = None
        self.dark_theme = True
        self.worker: ToolWorker | None = None
        self._build_ui()
        self._load_tools()
        apply_theme(self.app_instance(), True)

    def app_instance(self):
        from PySide6.QtWidgets import QApplication

        return QApplication.instance()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("工作空间(可选)"))
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("可选：选择常用数据目录")
        top.addWidget(self.project_edit, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._choose_project)
        top.addWidget(browse_btn)
        self.run_btn = QPushButton("使用工作空间")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self._init_run)
        top.addWidget(self.run_btn)
        self.theme_btn = QPushButton("切换浅色")
        self.theme_btn.clicked.connect(self._toggle_theme)
        top.addWidget(self.theme_btn)
        root.addLayout(top)

        main_split = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("项目资产")
        main_split.addWidget(self.tree)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.module_tabs = QTabWidget()
        self.module_tabs.currentChanged.connect(self._on_module_changed)
        center_layout.addWidget(self.module_tabs, 1)
        self.desc_label = QLabel("请选择工具")
        self.desc_label.setWordWrap(True)
        center_layout.addWidget(self.desc_label)
        self.param_form = ParamForm()
        center_layout.addWidget(self.param_form, 1)
        actions = QHBoxLayout()
        execute_btn = QPushButton("执行")
        execute_btn.setObjectName("primary")
        execute_btn.clicked.connect(self._execute)
        actions.addWidget(execute_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self._cancel)
        actions.addWidget(cancel_btn)
        center_layout.addLayout(actions)
        main_split.addWidget(center)

        self.queue = QTableWidget(0, 3)
        self.queue.setHorizontalHeaderLabels(["工具", "状态", "信息"])
        self.queue.horizontalHeader().setStretchLastSection(True)
        for column in range(3):
            header = self.queue.horizontalHeaderItem(column)
            if header:
                header.setTextAlignment(Qt.AlignCenter)
        main_split.addWidget(self.queue)
        main_split.setSizes([260, 760, 360])

        self.result_panel = ResultPanel()

        v_split = QSplitter(Qt.Vertical)
        v_split.addWidget(main_split)
        v_split.addWidget(self.result_panel)
        v_split.setSizes([620, 260])
        root.addWidget(v_split, 1)

        self.status = self.statusBar()
        self.status.showMessage("就绪")
        self.setCentralWidget(central)

    def _load_tools(self) -> None:
        for module in MODULE_NAMES:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            tool_list = QListWidget()
            layout.addWidget(tool_list)
            self.module_tabs.addTab(tab, MODULE_NAMES[module])
            if module == "m7":
                item = QListWidgetItem("sample_echo")
                item.setData(Qt.UserRole, "m7.sample_echo")
                tool_list.addItem(item)
                tool_list.itemClicked.connect(self._on_tool_clicked)
                continue
            for tool_id, tool_cls in sorted(TOOL_REGISTRY.items()):
                if tool_cls.spec.module != module:
                    continue
                item = QListWidgetItem(tool_cls.spec.name)
                item.setData(Qt.UserRole, tool_id)
                tool_list.addItem(item)
            tool_list.itemClicked.connect(self._on_tool_clicked)

    def _on_module_changed(self, index: int) -> None:
        self.current_tool_id = None
        self.param_form.clear_form()
        self.desc_label.setText("请选择工具")

    def _on_tool_clicked(self, item: QListWidgetItem) -> None:
        self.current_tool_id = item.data(Qt.UserRole)
        if self.current_tool_id == "m7.sample_echo":
            self.desc_label.setText("M7 示例插件：验证插件发现、子进程执行和输出契约。")
            self.param_form.clear_form()
            return
        tool_cls = TOOL_REGISTRY[self.current_tool_id]
        spec = tool_cls.spec
        self.desc_label.setText(spec.description)
        defaults = self._defaults_for_tool()
        self.param_form.set_defaults(defaults)
        self.param_form.build(spec.params)
        if self.current_tool_id == "ingest.batch_qc":
            if not defaults.get("input_dirs"):
                self.desc_label.setText(spec.description + "\n提示：请填写输入数据目录，将自动扫描并质检。")

    def _defaults_for_tool(self) -> dict:
        if not self.ctx:
            return {}
        out = self.ctx.outputs_dir
        defaults = {
            "assets_file": str(out / "assets.json"),
            "audit_file": str(out / "crs_audit.csv"),
            "vector_manifest": str(out / "vector_reproject_manifest.csv"),
            "raster_manifest": str(out / "raster_reproject_manifest.csv"),
            "topology_manifest": str(out / "topology_repair_manifest.csv"),
            "source_manifest": str(self.ctx.project_root / "profiles" / "source_manifest.yaml"),
        }
        if self.current_tool_id in {"ingest.batch_qc", "pointcloud.batch_stats"} or self.current_tool_id.startswith("crs."):
            inputs = self.ctx.project_root / "inputs"
            defaults["input_dirs"] = str(inputs) if inputs.exists() else ""
            defaults["assets_file"] = ""
            target_epsg = self.ctx.profile.get("m2", {}).get("project_crs", {}).get("target_epsg")
            if target_epsg:
                defaults["target_epsg"] = target_epsg
        return defaults

    def _choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工作空间目录")
        if path:
            self.project_edit.setText(path)

    def _ensure_context(self) -> bool:
        project = self.project_edit.text().strip()
        if not project:
            project = tempfile.mkdtemp(prefix="dtdpgis_")
        if self.workspace is None or (project and str(self.workspace.root) != str(Path(project).resolve())):
            self.workspace = ProjectWorkspace(project)
            self.param_form.start_dir = str(self.workspace.root)
        if self.ctx is None:
            self.ctx = self.workspace.new_run()
            self._refresh_tree()
            self.status.showMessage(f"当前运行：{self.ctx.run_id}")
        return True

    def _init_run(self) -> None:
        self.ctx = None
        if not self._ensure_context():
            return
        QMessageBox.information(self, "运行", f"已创建运行：{self.ctx.run_id}")

    def _refresh_tree(self) -> None:
        self.tree.clear()
        if not self.workspace:
            return
        root_item = QTreeWidgetItem(self.tree, [self.workspace.root.name])
        for entry in sorted(self.workspace.root.iterdir()):
            child = QTreeWidgetItem(root_item, [entry.name])
            if entry.is_dir():
                for sub in sorted(entry.iterdir()):
                    QTreeWidgetItem(child, [sub.name])
        root_item.setExpanded(True)

    def _execute(self) -> None:
        if not self._ensure_context():
            return
        if self.current_tool_id == "m7.sample_echo":
            self._run_sample_echo()
            return
        if not self.current_tool_id or self.current_tool_id not in TOOL_REGISTRY:
            QMessageBox.information(self, "提示", "请先选择工具。")
            return
        tool_cls = TOOL_REGISTRY[self.current_tool_id]
        params = self.param_form.values()
        if self.current_tool_id == "inventory.batch_scan" and not params.get("input_dirs"):
            QMessageBox.warning(self, "缺少输入", "请填写输入数据目录。")
            return
        required_file_params = {"assets_file", "audit_file", "vector_manifest", "raster_manifest", "topology_manifest"}
        if self.current_tool_id == "ingest.batch_qc" and params.get("input_dirs"):
            required_file_params.discard("assets_file")
        for key in required_file_params:
            value = params.get(key)
            if value and not Path(str(value)).exists():
                QMessageBox.warning(self, "输入文件不存在", f"{key} 指向的文件不存在：\n{value}\n请先运行上游工具，或手动浏览选择已有文件。")
                return
        self._add_queue_row(tool_cls.spec.name, "运行中", "")
        self.worker = ToolWorker(tool_cls, self.ctx, params)
        self.worker.result_ready.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _run_sample_echo(self) -> None:
        from dtdpgis.modules.m7.framework import run_available_plugins

        self._add_queue_row("sample_echo", "运行中", "")
        summary = run_available_plugins(self.ctx, plugin_ids=["sample_echo"])
        self._on_result({"tool_id": "m7.sample_echo", "run_id": self.ctx.run_id, "items": [], "summary": summary, "errors": []})

    def _add_queue_row(self, tool: str, status: str, message: str) -> None:
        row = self.queue.rowCount()
        self.queue.insertRow(row)
        self._set_queue_item(row, 0, tool, Qt.AlignCenter)
        self._set_queue_item(row, 1, status, Qt.AlignCenter)
        self._set_queue_item(row, 2, message, Qt.AlignCenter)
        self.queue.scrollToBottom()

    def _on_result(self, result) -> None:
        self.worker = None
        if isinstance(result, dict):
            tool_id = result.get("tool_id", "")
            items = result.get("items", [])
            summary = result.get("summary", {})
            self._update_queue_last(tool_id, "完成", json.dumps(summary, ensure_ascii=False, default=str))
            self._update_results(items)
        else:
            tool_id = getattr(result, "tool_id", "tool")
            self._update_queue_last(tool_id, "完成", getattr(result, "status", ""))
            self._update_results(getattr(result, "items", []))
            self._update_output_files(getattr(result, "outputs", []))
        self.status.showMessage("执行完成")

    def _on_failed(self, message: str) -> None:
        self.worker = None
        self._update_queue_last("", "失败", message)
        QMessageBox.warning(self, "执行失败", message)

    def _update_queue_last(self, tool: str, status: str, message: str) -> None:
        row = self.queue.rowCount() - 1
        if row < 0:
            return
        if tool:
            self._set_queue_item(row, 0, tool, Qt.AlignCenter)
        self._set_queue_item(row, 1, status, Qt.AlignCenter)
        self._set_queue_item(row, 2, message, Qt.AlignCenter)

    def _set_queue_item(self, row: int, column: int, text: str, alignment) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(alignment)
        if text:
            item.setToolTip(text)
        self.queue.setItem(row, column, item)

    def _update_results(self, items) -> None:
        self.result_panel.set_result_items(items)

    def _update_output_files(self, outputs) -> None:
        self.result_panel.set_output_files(outputs)

    def _cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
            self.worker = None
            self._update_queue_last("", "已取消", "")
            self.status.showMessage("已取消")

    def _toggle_theme(self) -> None:
        self.dark_theme = not self.dark_theme
        apply_theme(self.app_instance(), self.dark_theme)
        self.theme_btn.setText("切换深色" if self.dark_theme else "切换浅色")
