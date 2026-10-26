"""Light and dark QSS themes for the desktop app."""

LIGHT_QSS = """
QMainWindow, QWidget { background: #f5f7fa; color: #263142; font-family: "Microsoft YaHei", "Segoe UI"; font-size: 13px; }
QMenuBar { background: #ffffff; color: #263142; border-bottom: 1px solid #e3e8ef; }
QMenuBar::item:selected { background: #edf4fd; color: #3d7fd8; }
QTabWidget::pane { border: 1px solid #e3e8ef; background: #ffffff; }
QTabBar::tab { background: #ffffff; color: #7b8797; padding: 8px 16px; border: 1px solid #e3e8ef; border-bottom: none; }
QTabBar::tab:selected { color: #3d7fd8; border-bottom: 2px solid #3d7fd8; background: #ffffff; }
QPushButton { background: #ffffff; color: #465468; border: 1px solid #e3e8ef; border-radius: 6px; padding: 7px 13px; }
QPushButton:hover { border-color: #3d7fd8; color: #3d7fd8; }
QPushButton:pressed { background: #edf4fd; }
QPushButton#primary { background: #3d7fd8; color: #ffffff; border-color: #3d7fd8; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #fbfcfe; border: 1px solid #e3e8ef; border-radius: 6px; padding: 6px 8px; color: #263142; }
QListWidget, QTableWidget, QTreeWidget { background: #ffffff; border: 1px solid #e3e8ef; border-radius: 6px; color: #263142; }
QHeaderView::section { background: #f5f7fa; color: #7b8797; border: none; border-bottom: 1px solid #e3e8ef; padding: 6px; }
QStatusBar { background: #ffffff; color: #7b8797; border-top: 1px solid #e3e8ef; }
QSplitter::handle { background: #e3e8ef; }
"""

DARK_QSS = """
QMainWindow, QWidget { background: #08090b; color: #d7e0ea; font-family: "Microsoft YaHei", "Segoe UI"; font-size: 13px; }
QMenuBar { background: #111419; color: #d7e0ea; border-bottom: 1px solid #252b34; }
QMenuBar::item:selected { background: #172b38; color: #4a9fe0; }
QTabWidget::pane { border: 1px solid #252b34; background: #111419; }
QTabBar::tab { background: #111419; color: #8b98a9; padding: 8px 16px; border: 1px solid #252b34; border-bottom: none; }
QTabBar::tab:selected { color: #4a9fe0; border-bottom: 2px solid #4a9fe0; background: #111419; }
QPushButton { background: #111419; color: #d7e0ea; border: 1px solid #252b34; border-radius: 6px; padding: 7px 13px; }
QPushButton:hover { border-color: #4a9fe0; color: #4a9fe0; }
QPushButton:pressed { background: #172b38; }
QPushButton#primary { background: #4a9fe0; color: #ffffff; border-color: #4a9fe0; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #08090b; border: 1px solid #252b34; border-radius: 6px; padding: 6px 8px; color: #d7e0ea; }
QListWidget, QTableWidget, QTreeWidget { background: #111419; border: 1px solid #252b34; border-radius: 6px; color: #d7e0ea; }
QHeaderView::section { background: #08090b; color: #8b98a9; border: none; border-bottom: 1px solid #252b34; padding: 6px; }
QStatusBar { background: #111419; color: #8b98a9; border-top: 1px solid #252b34; }
QSplitter::handle { background: #252b34; }
"""


def apply_theme(app, dark: bool) -> None:
    app.setStyleSheet(DARK_QSS if dark else LIGHT_QSS)
