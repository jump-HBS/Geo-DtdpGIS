"""GUI entry point."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from dtdpgis.gui.main_window import MainWindow
from dtdpgis.gui.theme import apply_theme


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DtdpGIS")
    icon_path = r"D:\DtdpGIS\assets\DtdpGIS_circle.png"
    app.setWindowIcon(QIcon(icon_path))
    apply_theme(app, True)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_app())
