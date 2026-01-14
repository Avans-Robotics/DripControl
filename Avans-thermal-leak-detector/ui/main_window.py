from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget, QScrollArea,
    QSlider
)

from PySide6.QtCore import Qt
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

        # Create load button
        self.load_btn = QPushButton("Load GeoTIFF")
        self.load_btn.clicked.connect(self.load_file)

        # Create sensitivity slider
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(1)
        self.slider.setMaximum(20)
        self.slider.setValue(5)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self.update_image)

        layout = QVBoxLayout()
        layout.addWidget(self.load_btn)
        layout.addWidget(QLabel("Sensitivity"))
        layout.addWidget(self.slider)
        layout.addWidget(self.status)
        layout.addWidget(scroll)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.current_path = None

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open thermal GeoTIFF",
            "",
            "GeoTIFF (*.tif *.tiff)"
        )

        if not path:
            return

        self.current_path = path
        info = load_raster(path)
        self.status.setText(info)
        self.slider.setEnabled(True)
        self.update_image()

    def update_image(self):
        if not self.current_path:
            return

        sensitivity = self.slider.value()
        qimg = raster_to_qimage(self.current_path, sensitivity)
        self.image_label.setPixmap(QPixmap.fromImage(qimg))