from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget, QScrollArea,
    QSlider
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from core.raster import load_raster, raster_to_qimage, detect_leaks

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

        # Create threshold slider for leak detection
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(0)
        self.threshold_slider.setMaximum(255)
        self.threshold_slider.setValue(50)
        self.threshold_slider.setEnabled(False)
        self.threshold_slider.valueChanged.connect(self.update_image)

        # Leak count display
        self.leak_count_label = QLabel("Leaks detected: 0")
        self.leak_count_label.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(self.load_btn)
        layout.addWidget(QLabel("Sensitivity"))
        layout.addWidget(self.slider)
        layout.addWidget(QLabel("Threshold"))
        layout.addWidget(self.threshold_slider)
        layout.addWidget(self.leak_count_label)
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
        self.threshold_slider.setEnabled(True)
        self.leak_count_label.setEnabled(True)
        self.update_image()

    def update_image(self):
        if not self.current_path:
            return

        sensitivity = self.slider.value()
        threshold = self.threshold_slider.value()
        
        # Detect leaks
        leaks = detect_leaks(self.current_path, threshold)
        
        # Update leak count
        self.leak_count_label.setText(f"Leaks detected: {len(leaks)}")
        
        # Render image with leak markers
        qimg = raster_to_qimage(self.current_path, sensitivity, leaks=leaks)
        self.image_label.setPixmap(QPixmap.fromImage(qimg))