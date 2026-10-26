from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QMessageBox

from dtdpgis.gui.main_window import MainWindow


app = QApplication([])
QMessageBox.information = lambda *a, **k: QMessageBox.Ok
QMessageBox.warning = lambda *a, **k: QMessageBox.Ok

w = MainWindow()
project = Path(tempfile.mkdtemp()) / "project"
w.project_edit.setText(str(project))
w._ensure_context()

m1 = w.module_tabs.widget(0)
lst = m1.findChild(QListWidget)
for i in range(lst.count()):
    item = lst.item(i)
    if item.data(Qt.UserRole) == "ingest.batch_qc":
        w._on_tool_clicked(item)
        break

edit = w.param_form.widgets["input_dirs"].findChild(QLineEdit)
edit.setText(r"C:\Users\jump\Desktop\DtdoGIS试验\数据源\测试")
w._execute()

print("worker running", w.worker.isRunning() if w.worker else None)
if w.worker:
    ok = w.worker.wait(15000)
    print("wait", ok, "finished", w.worker.isFinished())
    print("queue rows", w.queue.rowCount())
    if w.queue.rowCount():
        print([w.queue.item(0, c).text() for c in range(3)])
