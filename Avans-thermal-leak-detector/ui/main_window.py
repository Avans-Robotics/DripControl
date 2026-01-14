from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget
)
from core.raster import load_raster

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Thermal Leak Detector")

        self.status = QLabel("No file loaded")
        self.load_btn = QPushButton("Load GeoTIFF")
        self.load_btn.clicked.connect(self.load_file)

        layout = QVBoxLayout()
        layout.addWidget(self.load_btn)
        layout.addWidget(self.status)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open thermal GeoTIFF",
            "",
            "GeoTIFF (*.tif *.tiff)"
        )

        if not path:
            return

        info = load_raster(path)
        self.status.setText(info)
