from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
import sys

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.showMaximized)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()