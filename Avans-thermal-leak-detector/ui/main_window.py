from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget, QScrollArea,
    QSlider, QCheckBox, QDialog, QHBoxLayout, QGridLayout
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage
from core.raster import load_raster, raster_to_qimage, detect_leaks, get_intensity_stats, detect_leaks_with_steps
import numpy as np
import cv2

# Size slider configuration
MAX_SIZE_PERCENT = 1.0  # Maximum size as percentage of image
SIZE_STEP_PERCENT = 0.001  # Step size in percentage (0.001% increments)
MIN_SIZE_PERCENT = SIZE_STEP_PERCENT  # Minimum size as percentage of image

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

        # Create threshold slider for leak detection (single threshold)
        # Intensity > threshold is ignored; only darker pixels (<= threshold) are candidate leaks
        # Slider: 0-2550 internally, divided by 10.0 for float (0.0-255.0)
        # Lower = more restrictive (fewer leaks); higher = less restrictive (more leaks)
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(0)
        self.threshold_slider.setMaximum(2550)  # 0-255.0 with 0.1 steps
        self.threshold_slider.setValue(0)  # will be set from image stats on load
        self.threshold_slider.setEnabled(False)
        self.threshold_slider.valueChanged.connect(self.update_image)
        
        # Label to show threshold and detection stats
        self.threshold_info_label = QLabel("Threshold: -")
        self.threshold_info_label.setEnabled(False)

        # Create minimum size slider for leak detection
        # Controls minimum leak area as percentage of image
        # Higher values = only larger leaks detected
        # Range: MIN_SIZE_PERCENT to MAX_SIZE_PERCENT with SIZE_STEP_PERCENT increments
        slider_max = int((MAX_SIZE_PERCENT - MIN_SIZE_PERCENT) / SIZE_STEP_PERCENT)
        default_slider_value = int(1.0 / SIZE_STEP_PERCENT)  # Default 1% of image area
        
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setMinimum(0)
        self.size_slider.setMaximum(slider_max)
        self.size_slider.setValue(default_slider_value)
        self.size_slider.setEnabled(False)
        self.size_slider.valueChanged.connect(self.update_image)

        # Create inertia ratio slider for leak detection
        # Controls minimum inertia ratio (0.0-1.0) to filter elongated shapes
        # Lower values = allow more elongated shapes (lines, ellipses)
        # Higher values = only allow rounder shapes (circles)
        # Slider uses integers 0-100 internally, divided by 100.0 for float values
        self.inertia_slider = QSlider(Qt.Horizontal)
        self.inertia_slider.setMinimum(0)
        self.inertia_slider.setMaximum(100)  # 0.0-1.0 with 0.01 steps
        self.inertia_slider.setValue(10)  # Default 0.1 (allow some elongation)
        self.inertia_slider.setEnabled(False)
        self.inertia_slider.valueChanged.connect(self.update_image)

        # Leak count display
        self.leak_count_label = QLabel("Leaks detected: 0")
        self.leak_count_label.setEnabled(False)
        
        # Debug mode checkbox
        self.debug_checkbox = QCheckBox("Show detection steps")
        self.debug_checkbox.setEnabled(False)
        self.debug_checkbox.stateChanged.connect(self.update_image)

        layout = QVBoxLayout()
        layout.addWidget(self.load_btn)
        layout.addWidget(QLabel("Sensitivity (display contrast)"))
        layout.addWidget(self.slider)
        layout.addWidget(QLabel("Intensity Threshold (0-255) — pixels above this are ignored"))
        layout.addWidget(self.threshold_slider)
        layout.addWidget(self.threshold_info_label)
        layout.addWidget(QLabel(f"Min Size (% of image) - Range: {MIN_SIZE_PERCENT}% to {MAX_SIZE_PERCENT}%"))
        layout.addWidget(self.size_slider)
        layout.addWidget(QLabel("Min Inertia Ratio (0.0-1.0) - lower = allow elongated shapes"))
        layout.addWidget(self.inertia_slider)
        layout.addWidget(self.leak_count_label)
        layout.addWidget(self.debug_checkbox)
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
        self.inertia_slider.blockSignals(True)
        try:
            if default_threshold is not None:
                # Slider is 0.1 precision (0-255.0 -> 0-2550)
                slider_val = int(round(float(default_threshold) * 10.0))
                slider_val = max(self.threshold_slider.minimum(), min(self.threshold_slider.maximum(), slider_val))
                self.threshold_slider.setValue(slider_val)
        finally:
            self.threshold_slider.blockSignals(False)
            self.size_slider.blockSignals(False)
            self.inertia_slider.blockSignals(False)

        self.slider.setEnabled(True)
        self.threshold_slider.setEnabled(True)
        self.size_slider.setEnabled(True)
        self.inertia_slider.setEnabled(True)
        self.leak_count_label.setEnabled(True)
        self.threshold_info_label.setEnabled(True)
        self.debug_checkbox.setEnabled(True)
        self.update_image()

    def update_image(self):
        if not self.current_path:
            return

        sensitivity = self.slider.value()
        rgb_threshold = self.threshold_slider.value() / 10.0  # Convert to float (0.0-255.0)
        # Convert slider value to percentage using the configured step size
        min_size_percent = self.size_slider.value() * SIZE_STEP_PERCENT
        min_inertia_ratio = self.inertia_slider.value() / 100.0  # Convert to float (0.0-1.0)
        
        # Detect leaks (with or without debug steps)
        if self.debug_checkbox.isChecked():
            leaks, detection_info, steps = detect_leaks_with_steps(
                self.current_path, rgb_threshold, min_size_percent, min_inertia_ratio
            )
            self._show_debug_window(steps, detection_info)
        else:
            leaks, detection_info = detect_leaks(
                self.current_path, rgb_threshold, min_size_percent, min_inertia_ratio
            )
        
        # Update leak count
        self.leak_count_label.setText(f"Leaks detected: {len(leaks)}")
        
        # Update threshold info display (single threshold)
        thresh = detection_info.get('threshold', detection_info.get('max_threshold', 'N/A'))
        inertia = detection_info['min_inertia']
        before_count = detection_info.get('blobs_before_filtering', 'N/A')
        after_count = detection_info.get('blobs_after_filtering', len(leaks))
        self.threshold_info_label.setText(
            f"Threshold: {thresh} (intensity > {thresh} ignored) | "
            f"Min area: {detection_info['min_area_px']} px | "
            f"Inertia ratio: {inertia:.2f} | "
            f"Blobs: {before_count} → {after_count} (after filtering)"
        )
        
        # Render image with leak markers (use original colors for debugging)
        qimg = raster_to_qimage(self.current_path, sensitivity, leaks=leaks, use_original_colors=True)
        pixmap = QPixmap.fromImage(qimg)
        # Use smooth transformation for high-quality scaling when fitting to viewport
        self.image_label.setPixmap(pixmap)
    
    def _numpy_to_qimage(self, img: np.ndarray) -> QImage:
        """Convert numpy array to QImage for display."""
        if len(img.shape) == 2:  # Grayscale
            h, w = img.shape
            qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        elif len(img.shape) == 3:  # Color (BGR from OpenCV)
            h, w, ch = img.shape
            # Convert BGR to RGB
            rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb_img.data, w, h, 3 * w, QImage.Format_RGB888)
        else:
            return QImage()
        return qimg.copy()
    
    def _show_debug_window(self, steps: dict, detection_info: dict):
        """Show a debug window with all detection steps."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Blob Detection Steps")
        dialog.setMinimumSize(1200, 800)
        
        layout = QGridLayout()
        row = 0

        # Preprocessing: Valid mask
        if 'step0_valid_mask' in steps:
            label = QLabel("Preprocessing: Valid Mask (white = valid, black = invalid)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 0)
            img_label = QLabel()
            qimg = self._numpy_to_qimage(steps['step0_valid_mask'])
            img_label.setPixmap(QPixmap.fromImage(qimg).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img_label, row + 1, 0)

        # Preprocessing: Intensity scaled (valid pixels)
        if 'step0_intensity_scaled' in steps:
            label = QLabel("Preprocessing: Intensity Scaled (valid pixels; invalid = black)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 1)
            img_label = QLabel()
            qimg = self._numpy_to_qimage(steps['step0_intensity_scaled'])
            img_label.setPixmap(QPixmap.fromImage(qimg).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img_label, row + 1, 1)

        # Preprocessing: Intensity uint8 corrected for valid mask
        if 'step0_intensity_uint8' in steps:
            label = QLabel("Preprocessing: Intensity uint8 (invalid pixels → 255, input to blob detector)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 2)
            img_label = QLabel()
            qimg = self._numpy_to_qimage(steps['step0_intensity_uint8'])
            img_label.setPixmap(QPixmap.fromImage(qimg).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img_label, row + 1, 2)

        row += 2

        # Step 1: Original image (input to blob detector)
        if 'step1_original' in steps:
            label = QLabel("Step 1: Original Intensity Image (input to blob detector)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 0)
            img_label = QLabel()
            qimg = self._numpy_to_qimage(steps['step1_original'])
            img_label.setPixmap(QPixmap.fromImage(qimg).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img_label, row + 1, 0)
        
        # Step 2: Single-threshold binary BEFORE opening
        if 'step2_binary_raw' in steps:
            thresh_val = steps.get('step2_threshold_value', 'N/A')
            label = QLabel(f"Step 2: Single Threshold Binary (before opening)\n"
                           f"(threshold = {thresh_val}; intensity > {thresh_val} ignored)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 1)
            img_label = QLabel()
            qimg_raw = self._numpy_to_qimage(steps['step2_binary_raw'])
            img_label.setPixmap(
                QPixmap.fromImage(qimg_raw).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            layout.addWidget(img_label, row + 1, 1)

        # Step 3: Single-threshold binary AFTER opening (used for detection)
        if 'step2_binary' in steps:
            thresh_val = steps.get('step2_threshold_value', 'N/A')
            label = QLabel("Step 3: Single Threshold Binary After Opening (used for detection)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 2)
            img_label = QLabel()
            qimg_opened = self._numpy_to_qimage(steps['step2_binary'])
            img_label.setPixmap(
                QPixmap.fromImage(qimg_opened).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            layout.addWidget(img_label, row + 1, 2)
        
        row += 2

        # Step 4: Connected components before area/inertia filter
        if 'step3_before_filtering' in steps and steps['step3_before_filtering'] is not None:
            blob_count_before = detection_info.get('blobs_before_filtering', 'N/A')
            label = QLabel(f"Step 4: Connected Components ({blob_count_before} blobs)\n(Before min area / inertia filter)")
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            layout.addWidget(label, row, 0)
            img_label = QLabel()
            qimg = self._numpy_to_qimage(steps['step3_before_filtering'])
            img_label.setPixmap(QPixmap.fromImage(qimg).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img_label, row + 1, 0)
        
        # Step 5: After filtering
        if 'step4_after_filtering' in steps and steps['step4_after_filtering'] is not None:
            label = QLabel(f"Step 5: Blobs After Filtering ({detection_info.get('blobs_after_filtering', 'N/A')} blobs)")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, row, 1)
            img_label = QLabel()
            qimg = self._numpy_to_qimage(steps['step4_after_filtering'])
            img_label.setPixmap(QPixmap.fromImage(qimg).scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(img_label, row + 1, 1)
        
        dialog.setLayout(layout)
        dialog.exec()