"""Headless GUI smoke test for the data quality flow."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QMessageBox

from dtdpgis.gui.main_window import MainWindow


def main() -> int:
    app = QApplication([])
    QMessageBox.information = lambda *args, **kwargs: QMessageBox.Ok
    QMessageBox.warning = lambda *args, **kwargs: QMessageBox.Ok

    window = MainWindow()
    window.show()
    project = Path(tempfile.mkdtemp()) / "project"
    window.project_edit.setText(str(project))
    assert window._ensure_context() is True

    m1_tab = window.module_tabs.widget(0)
    tool_list = m1_tab.findChild(QListWidget)
    selected = None
    for index in range(tool_list.count()):
        item = tool_list.item(index)
        if item.data(Qt.UserRole) == "ingest.batch_qc":
            tool_list.setCurrentItem(item)
            window._on_tool_clicked(item)
            selected = item
            break
    assert selected is not None

    input_widget = window.param_form.widgets["input_dirs"]
    input_edit = input_widget.findChild(QLineEdit)
    assert input_edit is not None
    input_edit.setText(r"C:\Users\jump\Desktop\DtdoGIS试验\数据源\测试")

    window._execute()
    if window.worker:
        window.worker.wait(15000)
    for _ in range(20):
        QTest.qWait(50)

    assert window.queue.rowCount() > 0
    status = window.queue.item(window.queue.rowCount() - 1, 1).text()
    message = window.queue.item(window.queue.rowCount() - 1, 2).text()
    print("GUI_SMOKE_STATUS", status)
    print("GUI_SMOKE_MESSAGE", message)
    window.close()
    return 0 if status == "完成" else 1


if __name__ == "__main__":
    raise SystemExit(main())
