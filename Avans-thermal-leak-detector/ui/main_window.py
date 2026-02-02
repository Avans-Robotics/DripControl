from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget,
    QSlider, QCheckBox, QDialog, QHBoxLayout, QGridLayout,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QRubberBand, QSplitter, QScrollArea,
)

from PySide6.QtCore import Qt, QRect, QRectF, QTimer
from PySide6.QtGui import QPixmap, QImage, QWheelEvent, QPainter
from PySide6.QtSvg import QSvgRenderer
from pathlib import Path

from core.raster import load_raster, raster_to_qimage, detect_leaks, get_intensity_stats, export_leaks_to_kml
import numpy as np
import cv2

# Size slider configuration
MAX_SIZE_PERCENT = 0.1  # Maximum size as percentage of image
SIZE_STEP_PERCENT = 0.001  # Step size in percentage (0.001% increments)
MIN_SIZE_PERCENT = SIZE_STEP_PERCENT  # Minimum size as percentage of image

# Copyright logos: both use this width; height scales to keep aspect ratio
LOGO_WIDTH_PX = 360


class DebugGraphicsView(QGraphicsView):
    """
    Zoomable/pannable view for debug window. Pan via left-drag (ScrollHandDrag);
    zoom via mouse wheel; zoom to rect via right-drag rubber band.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            self.renderHints() | QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rubber_origin = None
        self._rubber_band = None

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._rubber_origin = event.position().toPoint()
            if self._rubber_band is None:
                self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
            self._rubber_band.setGeometry(QRect(self._rubber_origin, self._rubber_origin))
            self._rubber_band.show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber_band is not None and self._rubber_origin is not None:
            r = QRect(self._rubber_origin, event.position().toPoint()).normalized()
            self._rubber_band.setGeometry(r)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self._rubber_band is not None:
            r = self._rubber_band.geometry()
            self._rubber_band.hide()
            self._rubber_origin = None
            if r.width() > 4 and r.height() > 4:
                rect_scene = QRectF(self.mapToScene(r.topLeft()), self.mapToScene(r.bottomRight()))
                self.fitInView(rect_scene, Qt.AspectRatioMode.KeepAspectRatio)
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Thermal Leak Detector")

        self.status = QLabel("No file loaded")
        self.status.setWordWrap(True)
        self.image_scene = QGraphicsScene()
        self.image_pixmap_item = QGraphicsPixmapItem()
        self.image_scene.addItem(self.image_pixmap_item)
        self.image_view = DebugGraphicsView()
        self.image_view.setScene(self.image_scene)

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
        self.threshold_info_label.setWordWrap(True)

        # Create minimum size slider for leak detection
        # Controls minimum leak area as percentage of image
        # Higher values = only larger leaks detected
        # Range: MIN_SIZE_PERCENT to MAX_SIZE_PERCENT with SIZE_STEP_PERCENT increments
        slider_max = int((MAX_SIZE_PERCENT - MIN_SIZE_PERCENT) / SIZE_STEP_PERCENT)
        default_slider_value = int(slider_max / 10.0)  # Default MAX_SIZE_PERCENT% of image area
        
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setMinimum(0)
        self.size_slider.setMaximum(slider_max)
        self.size_slider.setValue(default_slider_value)
        self.size_slider.setEnabled(False)
        self.size_slider.valueChanged.connect(self.update_image)

        # Leak count display
        self.leak_count_label = QLabel("Leaks detected: 0")
        self.leak_count_label.setEnabled(False)

        # Export to KML
        self.export_kml_btn = QPushButton("Export to KML")
        self.export_kml_btn.setEnabled(False)
        self.export_kml_btn.clicked.connect(self.export_leaks_kml)
        
        # Debug mode checkbox
        self.debug_checkbox = QCheckBox("Show detection steps")
        self.debug_checkbox.setEnabled(False)
        self.debug_checkbox.stateChanged.connect(self.update_image)

        # Left panel: controls (labels wrap to fit panel width)
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.load_btn)
        sens_label = QLabel("Sensitivity (display contrast)")
        sens_label.setWordWrap(True)
        left_layout.addWidget(sens_label)
        left_layout.addWidget(self.slider)
        thresh_label = QLabel("Intensity Threshold (0-255) — pixels above this are ignored")
        thresh_label.setWordWrap(True)
        left_layout.addWidget(thresh_label)
        left_layout.addWidget(self.threshold_slider)
        left_layout.addWidget(self.threshold_info_label)
        size_label = QLabel(f"Min Size (% of image) - Range: {MIN_SIZE_PERCENT}% to {MAX_SIZE_PERCENT}%")
        size_label.setWordWrap(True)
        left_layout.addWidget(size_label)
        left_layout.addWidget(self.size_slider)
        left_layout.addWidget(self.leak_count_label)
        left_layout.addWidget(self.export_kml_btn)
        left_layout.addWidget(self.debug_checkbox)
        left_layout.addWidget(self.status)
        left_layout.addStretch()

        # Copyright and logos at bottom
        copyright_text = (
            "© Alex Andrien\n"
            "--------------------------------\n"
            "Avans University of Applied Sciences\n"
            "--------------------------------\n"
            "Centre of Expertise Veiligheid en Veerkracht\n"
            "--------------------------------\n"
            "Lectoraat Robotisering en Sensoring"
        )
        copyright_label = QLabel(copyright_text)
        copyright_label.setWordWrap(True)
        copyright_label.setStyleSheet("color: #666; font-size: 20px;")
        logo_w = LOGO_WIDTH_PX
        logos_dir = Path(__file__).parent / "logos"

        # COE logo (above) – SVG: width = logo_w, height from aspect ratio
        coe_logo_path = logos_dir / "coe_vv_logo.svg"
        coe_logo = QLabel()
        if coe_logo_path.exists():
            renderer = QSvgRenderer(str(coe_logo_path))
            if renderer.isValid():
                size = renderer.defaultSize()
                h = int(logo_w * size.height() / size.width()) if size.width() else logo_w
                pix = QPixmap(logo_w, h)
                pix.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pix)
                renderer.render(painter, QRectF(0, 0, logo_w, h))
                painter.end()
                coe_logo.setPixmap(pix)
                coe_logo.setFixedSize(logo_w, h)
            else:
                coe_logo.setStyleSheet("background-color: #e8e8e8; border: 1px solid #ccc;")
                coe_logo.setFixedSize(logo_w, logo_w)
                coe_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
                coe_logo.setText("COE")
        else:
            coe_logo.setStyleSheet("background-color: #e8e8e8; border: 1px solid #ccc;")
            coe_logo.setFixedSize(logo_w, logo_w)
            coe_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            coe_logo.setText("COE")

        # Interreg logo (below) – PNG: width = logo_w, height from aspect ratio
        interreg_path = logos_dir / "Logo interreg smart farming en food processing.png"
        interreg_logo = QLabel()
        if interreg_path.exists():
            pix = QPixmap(str(interreg_path))
            if not pix.isNull():
                h = int(logo_w * pix.height() / pix.width()) if pix.width() else logo_w
                pix = pix.scaled(
                    logo_w,
                    h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                interreg_logo.setPixmap(pix)
                interreg_logo.setFixedSize(pix.size())
            else:
                interreg_logo.setStyleSheet("background-color: #e8e8e8; border: 1px solid #ccc;")
                interreg_logo.setFixedSize(logo_w, logo_w)
                interreg_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
                interreg_logo.setText("Interreg")
        else:
            interreg_logo.setStyleSheet("background-color: #e8e8e8; border: 1px solid #ccc;")
            interreg_logo.setFixedSize(logo_w, logo_w)
            interreg_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            interreg_logo.setText("Interreg")

        logos_column = QVBoxLayout()
        logos_column.addWidget(coe_logo)
        logos_column.addWidget(interreg_logo)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(copyright_label, 1)
        bottom_row.addLayout(logos_column)
        left_layout.addLayout(bottom_row)

        left_panel = QWidget()
        left_panel.setLayout(left_layout)
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_panel)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(180)

        # Right: image view
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left_scroll)
        self.splitter.addWidget(self.image_view)
        self.splitter.setSizes([280, 720])  # Initial widths: left 280px, right gets the rest
        self.splitter.setStretchFactor(0, 1)  # Left panel scales with window
        self.splitter.setStretchFactor(1, 3)  # Right (image) gets more of the extra space

        self.setCentralWidget(self.splitter)

        self.current_path = None
        self._last_leaks: list[tuple[int, int]] = []
        self._last_image_width = 0
        self._last_image_height = 0
        self._initial_splitter_set = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_splitter_set:
            QTimer.singleShot(50, self._set_initial_splitter_sizes)

    def _set_initial_splitter_sizes(self):
        """Set left panel to at least half the window width on first show."""
        if self._initial_splitter_set:
            return
        total = self.splitter.width()
        if total > 0:
            left = max(total // 2, self.splitter.widget(0).minimumWidth())
            self.splitter.setSizes([left, total - left])
            self._initial_splitter_set = True

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
        self.debug_checkbox.setEnabled(True)
        self.update_image()

    def update_image(self):
        if not self.current_path:
            return

        sensitivity = self.slider.value()
        rgb_threshold = self.threshold_slider.value() / 10.0  # Convert to float (0.0-255.0)
        # Convert slider value to percentage using the configured step size
        min_size_percent = self.size_slider.value() * SIZE_STEP_PERCENT
        
        # Detect leaks (with or without debug steps)
        if self.debug_checkbox.isChecked():
            leaks, detection_info, steps = detect_leaks(
                self.current_path, rgb_threshold, min_size_percent, return_steps=True
            )
            self._show_debug_window(steps, detection_info)
        else:
            leaks, detection_info = detect_leaks(
                self.current_path, rgb_threshold, min_size_percent
            )

        self._last_leaks = leaks
        self.export_kml_btn.setEnabled(len(leaks) > 0)

        # Update leak count
        self.leak_count_label.setText(f"Leaks detected: {len(leaks)}")
        
        # Update threshold info display (single threshold)
        thresh = detection_info.get('threshold', detection_info.get('max_threshold', 'N/A'))
        before_count = detection_info.get('blobs_before_filtering', 'N/A')
        after_count = detection_info.get('blobs_after_filtering', len(leaks))
        self.threshold_info_label.setText(
            f"Threshold: {thresh} (intensity > {thresh} ignored) | "
            f"Min area: {detection_info['min_area_px']} px | "
            f"Blobs: {before_count} → {after_count} (after filtering)"
        )
        
        # Render image with leak markers (use original colors for debugging)
        saved_sizes = self.splitter.sizes()
        qimg = raster_to_qimage(self.current_path, sensitivity, leaks=leaks, use_original_colors=True)
        pixmap = QPixmap.fromImage(qimg)
        self.image_pixmap_item.setPixmap(pixmap)
        self.image_pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        new_rect = self.image_pixmap_item.boundingRect()
        size_changed = (
            new_rect.width() != self._last_image_width or new_rect.height() != self._last_image_height
        )
        self._last_image_width = new_rect.width()
        self._last_image_height = new_rect.height()
        self.image_scene.setSceneRect(new_rect)
        if size_changed:
            self.image_view.fitInView(new_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.splitter.setSizes(saved_sizes)

    def export_leaks_kml(self):
        """Open save dialog and export current leak centroids to a KML file."""
        if not self.current_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export leak locations",
            "",
            "KML (*.kml)",
        )
        if not path:
            return
        path = str(Path(path).with_suffix(".kml"))
        try:
            export_leaks_to_kml(self.current_path, self._last_leaks, path)
            n = len(self._last_leaks)
            self.status.setText(f"Exported {n} leak(s) to {path}")
        except Exception as e:
            self.status.setText(f"Export failed: {e}")

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
    
    def _add_debug_image(self, layout, views: list, row: int, col: int,
                         title: str, img_data, detection_info: dict = None, extra: str = None) -> DebugGraphicsView | None:
        """Add a label and a zoomable view for an image; append view to views. Returns the view or None."""
        if img_data is None:
            return None
        qimg = self._numpy_to_qimage(img_data)
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            return None
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, pix.width(), pix.height())
        item = QGraphicsPixmapItem(pix)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        scene.addItem(item)
        view = DebugGraphicsView()
        view.setScene(scene)
        view.setFixedSize(400, 300)
        label_text = title
        if extra:
            label_text = f"{title}\n{extra}"
        label = QLabel(label_text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label, row, col)
        layout.addWidget(view, row + 1, col)
        views.append(view)
        return view

    def _show_debug_window(self, steps: dict, detection_info: dict):
        """Show a debug window with all detection steps (zoomable views, independent)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Blob Detection Steps")
        dialog.setMinimumSize(1200, 800)

        layout = QGridLayout()
        views: list[DebugGraphicsView] = []
        row = 0

        # Preprocessing row
        if 'step0_valid_mask' in steps:
            self._add_debug_image(
                layout, views, row, 0,
                "Preprocessing: Valid Mask (white = valid, black = invalid)",
                steps['step0_valid_mask']
            )
        if 'step0_intensity_scaled' in steps:
            self._add_debug_image(
                layout, views, row, 1,
                "Preprocessing: Intensity Scaled (valid pixels; invalid = black)",
                steps['step0_intensity_scaled']
            )
        if 'step0_intensity_uint8' in steps:
            self._add_debug_image(
                layout, views, row, 2,
                "Preprocessing: Intensity uint8 (invalid → 255, input to blob detector)",
                steps['step0_intensity_uint8']
            )
        row += 2

        # Step 1, Gaussian blur, Step 2
        if 'step1_original' in steps:
            self._add_debug_image(
                layout, views, row, 0,
                "Step 1: Original Intensity Image (input to blob detector)",
                steps['step1_original']
            )
        if 'step1_blurred' in steps:
            self._add_debug_image(
                layout, views, row, 1,
                "After Gaussian Blur (input to threshold)",
                steps['step1_blurred']
            )
        if 'step2_binary_raw' in steps:
            thresh_val = steps.get('step2_threshold_value', 'N/A')
            self._add_debug_image(
                layout, views, row, 2,
                "Step 2: Single Threshold Binary (before opening)",
                steps['step2_binary_raw'],
                extra=f"threshold = {thresh_val}; intensity > {thresh_val} ignored"
            )
        row += 2

        # Step 3: opened binary
        if 'step2_binary' in steps:
            self._add_debug_image(
                layout, views, row, 0,
                "Step 3: Single Threshold Binary After Opening (used for detection)",
                steps['step2_binary']
            )
        row += 2

        # Step 4 & 5
        if 'step3_before_filtering' in steps and steps['step3_before_filtering'] is not None:
            blob_count_before = detection_info.get('blobs_before_filtering', 'N/A')
            self._add_debug_image(
                layout, views, row, 0,
                f"Step 4: Connected Components ({blob_count_before} blobs)",
                steps['step3_before_filtering'],
                extra="Before min area filter"
            )
        if 'step4_after_filtering' in steps and steps['step4_after_filtering'] is not None:
            n_after = detection_info.get('blobs_after_filtering', 'N/A')
            self._add_debug_image(
                layout, views, row, 1,
                f"Step 5: Blobs After Filtering ({n_after} blobs)",
                steps['step4_after_filtering']
            )

        if views:
            views[0].fitInView(views[0].sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        hint = QLabel("Zoom: mouse wheel  |  Pan: left-drag  |  Zoom to rect: right-drag, then release")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint, row + 2, 0, 1, 3)

        dialog.setLayout(layout)
        dialog.exec()