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
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.showMaximized)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()