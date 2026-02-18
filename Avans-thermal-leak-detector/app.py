import sys
import os

# When frozen (PyInstaller), point PROJ to bundled proj.db so CRS/export works
if getattr(sys, 'frozen', False):
    _proj_data = os.path.join(sys._MEIPASS, 'rasterio', 'proj_data')
    if os.path.isdir(_proj_data):
        os.environ['PROJ_DATA'] = _proj_data

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow, QWidget { background-color: #f5f5f5; }
        QGroupBox {
            font-weight: 600;
            border: 1px solid #c5c5c5;
            border-radius: 6px;
            margin-top: 10px;
            padding: 10px 10px 10px 10px;
            background-color: white;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 6px;
            background-color: white;
            color: #333;
        }
        QPushButton {
            min-width: 60px;
            padding: 6px 12px;
            border: 1px solid #aaa;
            border-radius: 4px;
            background-color: #fafafa;
        }
        QPushButton:hover { background-color: #e8e8e8; }
        QPushButton:pressed { background-color: #ddd; }
        QPushButton:disabled { background-color: #eee; color: #999; }
        QSlider::groove:horizontal {
            height: 6px;
            background: #ddd;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            width: 14px;
            height: 14px;
            margin: -4px 0;
            background: #fff;
            border: 1px solid #aaa;
            border-radius: 7px;
        }
        QSlider::handle:horizontal:hover { background: #f0f0f0; }
        QScrollArea { border: none; background: transparent; }
        QTableWidget { background: white; border: 1px solid #c5c5c5; border-radius: 4px; }
    """)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.showMaximized)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()