from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget, QScrollArea,
    QSlider
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from core.raster import load_raster, raster_to_qimage, detect_leaks, get_intensity_stats

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Thermal Leak Detector")

        self.status = QLabel("No file loaded")
        self.image_label = QLabel()
        self.image_label.setScaledContents(True)
        self.image_label.setAlignment(Qt.AlignCenter)

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
        # Using direct intensity values (0.0-255.0) with 0.1 precision
        # Slider uses integers 0-2550 internally, divided by 10.0 for float values
        # Lower values = more restrictive = fewer leaks (darker threshold)
        # Higher values = less restrictive = more leaks (brighter threshold)
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(0)
        self.threshold_slider.setMaximum(2550)  # 0-255.0 with 0.1 steps
        self.threshold_slider.setValue(0)  # will be set from image stats on load
        self.threshold_slider.setEnabled(False)
        self.threshold_slider.valueChanged.connect(self.update_image)
        
        # Label to show actual threshold range being used
        self.threshold_info_label = QLabel("Threshold range: -")
        self.threshold_info_label.setEnabled(False)

        # Create minimum size slider for leak detection
        # Controls minimum leak area as percentage of image (0-1%)
        # Higher values = only larger leaks detected
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setMinimum(0)
        self.size_slider.setMaximum(100)  # 0-1% of image area (scaled by 10 for precision)
        self.size_slider.setValue(1)  # Default 0.1% of image area (0.1% == slider value 1)
        self.size_slider.setEnabled(False)
        self.size_slider.valueChanged.connect(self.update_image)

        # Leak count display
        self.leak_count_label = QLabel("Leaks detected: 0")
        self.leak_count_label.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(self.load_btn)
        layout.addWidget(QLabel("Sensitivity (display contrast)"))
        layout.addWidget(self.slider)
        layout.addWidget(QLabel("Max Intensity Threshold (0-255)"))
        layout.addWidget(self.threshold_slider)
        layout.addWidget(self.threshold_info_label)
        layout.addWidget(QLabel("Min Size (% of image)"))
        layout.addWidget(self.size_slider)
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

        # Set robust defaults from image statistics so the first render is useful.
        # We pick a "dark" percentile as the initial threshold so leaks appear immediately.
        try:
            stats = get_intensity_stats(path)
            default_threshold = stats.get("p5") if stats else None
        except Exception:
            default_threshold = None

        # Avoid multiple redraws while setting defaults/enabling widgets.
        self.threshold_slider.blockSignals(True)
        self.size_slider.blockSignals(True)
        try:
            if default_threshold is not None:
                # Slider is 0.1 precision (0-255.0 -> 0-2550)
                slider_val = int(round(float(default_threshold) * 10.0))
                slider_val = max(self.threshold_slider.minimum(), min(self.threshold_slider.maximum(), slider_val))
                self.threshold_slider.setValue(slider_val)
        finally:
            self.threshold_slider.blockSignals(False)
            self.size_slider.blockSignals(False)

        self.slider.setEnabled(True)
        self.threshold_slider.setEnabled(True)
        self.size_slider.setEnabled(True)
        self.leak_count_label.setEnabled(True)
        self.threshold_info_label.setEnabled(True)
        self.update_image()

    def update_image(self):
        if not self.current_path:
            return

        sensitivity = self.slider.value()
        rgb_threshold = self.threshold_slider.value() / 10.0  # Convert to float (0.0-255.0)
        min_size_percent = self.size_slider.value() / 10.0  # Convert to percentage (0-10%)
        
        # Detect leaks
        leaks, detection_info = detect_leaks(self.current_path, rgb_threshold, min_size_percent)
        
        # Update leak count
        self.leak_count_label.setText(f"Leaks detected: {len(leaks)}")
        
        # Update threshold info display
        min_thresh = detection_info['min_threshold']
        max_thresh = detection_info['max_threshold']
        step = detection_info['threshold_step']
        self.threshold_info_label.setText(
            f"Threshold range: {min_thresh}-{max_thresh} (step: {step}) | "
            f"Min area: {detection_info['min_area_px']} px"
        )
        
        # Render image with leak markers (use original colors for debugging)
        qimg = raster_to_qimage(self.current_path, sensitivity, leaks=leaks, use_original_colors=True)
        pixmap = QPixmap.fromImage(qimg)
        # Use smooth transformation for high-quality scaling when fitting to viewport
        self.image_label.setPixmap(pixmap)