from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget, QScrollArea
)
from PySide6.QtGui import QPixmap
from core.raster import load_raster, raster_to_qimage

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Thermal Leak Detector")

        self.status = QLabel("No file loaded")
        self.image_label = QLabel()
        self.image_label.setScaledContents(True)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image_label)

        self.load_btn = QPushButton("Load GeoTIFF")
        self.load_btn.clicked.connect(self.load_file)

        layout = QVBoxLayout()
        layout.addWidget(self.load_btn)
        layout.addWidget(self.status)
        layout.addWidget(scroll)

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

        qimg = raster_to_qimage(path)
        self.image_label.setPixmap(QPixmap.fromImage(qimg))
