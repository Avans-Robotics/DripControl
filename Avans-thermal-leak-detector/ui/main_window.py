from PySide6.QtWidgets import (
    QMainWindow, QPushButton, QFileDialog,
    QLabel, QVBoxLayout, QWidget,
    QSlider, QCheckBox, QDialog, QHBoxLayout, QGridLayout,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QRubberBand, QSplitter, QScrollArea, QTableWidget, QTableWidgetItem,
    QGroupBox, QMessageBox,
)

from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, QTimer, QEvent
from PySide6.QtGui import QPixmap, QImage, QWheelEvent, QPainter, QKeyEvent
from PySide6.QtSvg import QSvgRenderer
from pathlib import Path
import sys

from core.raster import load_raster, raster_to_qimage, detect_leaks, get_intensity_stats, export_leaks_to_kml


def _ui_dir() -> Path:
    """Base directory for ui package (works when run from source or from PyInstaller .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ui"
    return Path(__file__).resolve().parent


def _is_development() -> bool:
    """True when run as python app.py; False when run as built executable (e.g. PyInstaller)."""
    return not getattr(sys, "frozen", False)
import math
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

        self.status = QLabel("")
        self.status.setWordWrap(True)
        # File info: container for structured rows (text + "?" with tooltip when loaded)
        self.file_info_container = QWidget()
        self.file_info_layout = QVBoxLayout(self.file_info_container)
        self.file_info_layout.setContentsMargins(0, 0, 0, 0)
        self._file_info_placeholder = QLabel("No file loaded")
        self._file_info_placeholder.setWordWrap(True)
        self.file_info_layout.addWidget(self._file_info_placeholder)
        self.image_scene = QGraphicsScene()
        self.image_pixmap_item = QGraphicsPixmapItem()
        self.image_scene.addItem(self.image_pixmap_item)
        self.image_view = DebugGraphicsView()
        self.image_view.setScene(self.image_scene)

        # Create load and export buttons (small, side by side at top)
        self.load_btn = QPushButton("Load GeoTIFF")
        self.load_btn.clicked.connect(self.load_file)
        self.load_btn.setMaximumWidth(140)

        # Toggle switch: Original vs Thermal view (sensitivity applies only in thermal mode)
        self.view_toggle = QSlider(Qt.Orientation.Horizontal)
        self.view_toggle.setMinimum(0)
        self.view_toggle.setMaximum(1)
        self.view_toggle.setValue(0)
        self.view_toggle.setPageStep(1)
        self.view_toggle.setSingleStep(1)
        self.view_toggle.setFixedSize(52, 28)
        self.view_toggle.setEnabled(False)
        self.view_toggle.valueChanged.connect(self._on_view_toggle)
        self._apply_view_toggle_style()

        # Create sensitivity slider (contrast for thermal colormap)
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

        # Table of detected leaks (sorted by size, small → large); columns: Leak (index + size), Source (Auto/User)
        self.leak_list = QTableWidget()
        self.leak_list.setColumnCount(2)
        self.leak_list.setHorizontalHeaderLabels(["Leak", "Source"])
        self.leak_list.setEnabled(False)
        self.leak_list.setMinimumHeight(120)
        self.leak_list.setMaximumWidth(240)
        self.leak_list.setAlternatingRowColors(True)
        self.leak_list.horizontalHeader().setStretchLastSection(False)
        self.leak_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.leak_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.leak_list.currentCellChanged.connect(self._on_leak_list_selection_changed)

        self.clear_user_leaks_btn = QPushButton("Clear user-defined leaks")
        self.clear_user_leaks_btn.setEnabled(False)
        self.clear_user_leaks_btn.clicked.connect(self._clear_user_leaks)
        self.clear_user_leaks_btn.setMaximumWidth(200)

        self.export_kml_btn = QPushButton("Export to KML")
        self.export_kml_btn.setEnabled(False)
        self.export_kml_btn.clicked.connect(self.export_leaks_kml)
        self.export_kml_btn.setMaximumWidth(140)
        
        # Debug mode checkbox
        self.debug_checkbox = QCheckBox("Show detection steps")
        self.debug_checkbox.setEnabled(False)
        self.debug_checkbox.stateChanged.connect(self.update_image)

        # Left panel: controls in logical groups
        left_layout = QVBoxLayout()

        # --- File group: load, export, file info ---
        file_group = QGroupBox("File")
        file_layout = QVBoxLayout()
        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.load_btn)
        top_buttons.addWidget(self.export_kml_btn)
        top_buttons.addStretch()
        top_buttons.addWidget(self._make_help_button(
            "Load GeoTIFF: open a thermal GeoTIFF image. Export to KML: save the current leak "
            "locations to a KML file for use in GIS or mapping applications (e.g. Google Earth)."
        ))
        file_layout.addLayout(top_buttons)
        file_layout.addWidget(self.file_info_container)
        file_layout.addWidget(self.status)
        file_group.setLayout(file_layout)
        left_layout.addWidget(file_group)

        # --- View group: Original/Thermal toggle; sensitivity only visible in thermal mode ---
        view_group = QGroupBox("View")
        view_layout = QVBoxLayout()
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("Original"))
        view_row.addWidget(self.view_toggle)
        view_row.addWidget(QLabel("Thermal"))
        view_row.addStretch()
        self.sens_label = QLabel("Color Gradient")
        self.sens_label.setWordWrap(True)
        view_row.addWidget(self.sens_label)
        view_row.addWidget(self.slider)
        view_row.addWidget(self._make_help_button(
            "Original: show the image as captured. Thermal: apply a color gradient to highlight "
            "temperature (darker = cooler). The Color Gradient slider (in Thermal mode) adjusts "
            "the contrast of the colormap so leaks stand out better."
        ))
        view_layout.addLayout(view_row)
        view_group.setLayout(view_layout)
        left_layout.addWidget(view_group)
        # Sensitivity only visible when Thermal is selected
        self._update_sensitivity_visibility()

        # --- Leak detection settings: thresholds, debug ---
        leak_settings_group = QGroupBox("Leak detection settings")
        leak_settings_layout = QVBoxLayout()
        thresh_row = QHBoxLayout()
        thresh_row.setContentsMargins(0, 0, 0, 0)
        self.thresh_label = QLabel("Intensity threshold (0–255): pixels above — are ignored")
        self.thresh_label.setWordWrap(True)
        thresh_row.addWidget(self.thresh_label, 1)
        thresh_row.addWidget(self._make_help_button(
            "Pixels with intensity above this value are ignored; only darker (colder) pixels are "
            "considered as possible leaks. Lower = stricter (fewer leaks); higher = more candidates."
        ))
        leak_settings_layout.addLayout(thresh_row)
        leak_settings_layout.addWidget(self.threshold_slider)
        if _is_development():
            leak_settings_layout.addWidget(self.threshold_info_label)
        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        self.size_label = QLabel("Min size of leak (— — —): — pixels")
        self.size_label.setWordWrap(True)
        size_row.addWidget(self.size_label, 1)
        size_row.addWidget(self._make_help_button(
            "Minimum area (in pixels) for a region to count as a leak. Increase to filter out small "
            "spots; decrease to catch smaller leaks."
        ))
        leak_settings_layout.addLayout(size_row)
        leak_settings_layout.addWidget(self.size_slider)
        if _is_development():
            leak_settings_layout.addWidget(self.debug_checkbox)
        leak_settings_group.setLayout(leak_settings_layout)
        left_layout.addWidget(leak_settings_group)

        # --- Leak detection results: count and table ---
        leak_results_group = QGroupBox("Leak detection results")
        leak_results_layout = QVBoxLayout()
        leak_count_row = QHBoxLayout()
        leak_count_row.setContentsMargins(0, 0, 0, 0)
        leak_count_row.addWidget(self.leak_count_label, 0)
        leak_count_row.addWidget(self._make_help_button(
            "List of detected leaks, sorted by size (small to large). Click a row to highlight the "
            "leak on the map. Click on the map to add a leak at that location; select a leak and "
            "press Delete to remove it. 'Clear user-defined leaks' removes only leaks you added manually."
        ), 0)
        leak_results_layout.addLayout(leak_count_row)
        leak_results_layout.addWidget(self.leak_list)
        leak_results_layout.addWidget(self.clear_user_leaks_btn)
        leak_results_group.setLayout(leak_results_layout)
        left_layout.addWidget(leak_results_group)

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
        logos_dir = _ui_dir() / "logos"

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
        self._last_leaks: list[tuple[int, int, int]] = []  # (x, y, area_px)
        self._leaks_sorted_by_size: list[tuple[int, int, int]] = []  # same, sorted small→large (list index = row)
        self._user_added_leaks: set[tuple[int, int]] = set()  # (x, y) of leaks added by user via map click
        self._selected_leak_xy: tuple[int, int] | None = None
        self._last_image_width = 0
        self._last_image_height = 0
        self._initial_splitter_set = False
        self._image_press_scene: QPointF | None = None  # for map-click detection
        self._image_press_viewport: QPoint | None = None  # viewport pos at press (to distinguish click vs drag)

        # Install on viewport for mouse; on view for keyboard (view gets focus when map is clicked)
        self.image_view.viewport().installEventFilter(self)
        self.image_view.installEventFilter(self)
        self.leak_list.installEventFilter(self)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_splitter_set:
            QTimer.singleShot(50, self._set_initial_splitter_sizes)

    def keyPressEvent(self, event: QKeyEvent):
        """Delete/Backspace removes selected leak regardless of which widget has focus."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.leak_list.currentRow() >= 0:
            self._delete_selected_leak()
            event.accept()
            return
        super().keyPressEvent(event)

    def _apply_view_toggle_style(self):
        """Style the view toggle as a pill switch (light blue track, white thumb)."""
        is_thermal = self.view_toggle.value() == 1
        groove_bg = "#b0c4de" if is_thermal else "#c0c0c0"
        self.view_toggle.setStyleSheet(
            """
            QSlider::groove:horizontal {
                height: 20px;
                background: %s;
                border: 1px solid #87a7c9;
                border-radius: 10px;
            }
            QSlider::handle:horizontal {
                width: 18px;
                height: 18px;
                margin: 1px 1px 1px 1px;
                background: white;
                border: 1px solid #ccc;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: #f8f8f8;
            }
            QSlider::sub-page:horizontal {
                background: transparent;
            }
            QSlider::add-page:horizontal {
                background: transparent;
            }
            """
            % groove_bg
        )

    def _update_sensitivity_visibility(self):
        """Show sensitivity slider and label only when Thermal view is selected."""
        visible = self.view_toggle.value() == 1
        self.sens_label.setVisible(visible)
        self.slider.setVisible(visible)

    def _on_view_toggle(self, value: int):
        """Toggle switch: update groove color, sensitivity visibility, and redraw."""
        self._apply_view_toggle_style()
        self._update_sensitivity_visibility()
        self.update_image()

    def _update_file_info(self, info: list[dict]):
        """Build file info from load_raster() result: each row has text and optional '?' with tooltip."""
        # Clear current content
        while self.file_info_layout.count():
            item = self.file_info_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Build rows
        for row in info:
            text = row["text"]
            tooltip = row.get("tooltip")
            text_label = QLabel(text)
            text_label.setWordWrap(True)
            if tooltip is None:
                self.file_info_layout.addWidget(text_label)
            else:
                row_layout = QHBoxLayout()
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(text_label, 1)
                help_btn = QPushButton("?")
                help_btn.setToolTip(tooltip)
                help_btn.setFlat(True)
                help_btn.setFixedSize(22, 22)
                help_btn.setStyleSheet(
                    "QPushButton { color: #666; font-size: 12px; font-weight: bold; border: none; background: transparent; }"
                    "QPushButton:hover { color: #333; background: #eee; border-radius: 11px; }"
                )
                help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                help_btn.clicked.connect(
                    (lambda t: lambda: QMessageBox.information(self, "Explanation", t))(tooltip)
                )
                row_layout.addWidget(help_btn, 0)
                self.file_info_layout.addLayout(row_layout)

    def _make_help_button(self, tooltip: str) -> QPushButton:
        """Return a styled '?' button that shows tooltip in a message box on click."""
        btn = QPushButton("?")
        btn.setToolTip(tooltip)
        btn.setFlat(True)
        btn.setFixedSize(22, 22)
        btn.setStyleSheet(
            "QPushButton { color: #666; font-size: 12px; font-weight: bold; border: none; background: transparent; }"
            "QPushButton:hover { color: #333; background: #eee; border-radius: 11px; }"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(
            (lambda t: lambda: QMessageBox.information(self, "Explanation", t))(tooltip)
        )
        return btn

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
        self._update_file_info(info)
        self.status.setText("")

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
        self.view_toggle.setEnabled(True)
        self.threshold_slider.setEnabled(True)
        self.size_slider.setEnabled(True)
        self.leak_count_label.setEnabled(True)
        self.leak_list.setEnabled(True)
        if _is_development():
            self.threshold_info_label.setEnabled(True)
            self.debug_checkbox.setEnabled(True)
        self.update_image()

    def update_image(self):
        if not self.current_path:
            return

        # Preserve user-added leaks across new detection runs
        prev_user_coords = set(self._user_added_leaks)
        prev_last_leaks = list(self._last_leaks)

        sensitivity = self.slider.value()
        rgb_threshold = self.threshold_slider.value() / 10.0  # Convert to float (0.0-255.0)
        self.thresh_label.setText(f"Intensity threshold (0–255): pixels above {rgb_threshold} are ignored")
        # Convert slider value to percentage using the configured step size
        min_size_percent = self.size_slider.value() * SIZE_STEP_PERCENT

        # Detect leaks (with or without debug steps; debug only in development)
        if _is_development() and self.debug_checkbox.isChecked():
            leaks, detection_info, steps = detect_leaks(
                self.current_path, rgb_threshold, min_size_percent, return_steps=True
            )
            self._show_debug_window(steps, detection_info)
        else:
            leaks, detection_info = detect_leaks(
                self.current_path, rgb_threshold, min_size_percent
            )

        # Merge new automatic leaks with existing user-added leaks
        user_leaks = [
            (x, y, area)
            for (x, y, area) in prev_last_leaks
            if (x, y) in prev_user_coords
        ]
        auto_leaks = [
            (x, y, area)
            for (x, y, area) in leaks
            if (x, y) not in prev_user_coords
        ]
        self._last_leaks = user_leaks + auto_leaks
        self._leaks_sorted_by_size = sorted(self._last_leaks, key=lambda t: t[2])
        # Keep _user_added_leaks as-is so user leaks survive slider changes
        self.export_kml_btn.setEnabled(len(self._last_leaks) > 0)
        self._update_clear_user_leaks_button()

        # Clear list selection when detection changes (avoid stale highlight)
        self._selected_leak_xy = None
        self.leak_list.blockSignals(True)
        self.leak_list.setCurrentCell(-1, -1)
        self.leak_list.blockSignals(False)

        # Update leak count (automatic + user-added)
        self.leak_count_label.setText(f"Leaks detected: {len(self._last_leaks)}")

        # Update leak table: sort by area (small to large), columns Leak and Source
        self._repopulate_leak_table()
        
        # Update threshold info display (development only)
        if _is_development():
            before_count = detection_info.get('blobs_before_filtering', 'N/A')
            after_count = detection_info.get('blobs_after_filtering', detection_info.get('blobs_after_filtering', 'N/A'))
            self.threshold_info_label.setText(
                f"Blobs: {before_count} → {after_count} (after filtering)"
            )

        # Render image with leak markers (same order as table: by size, so map numbers match table rows)
        saved_sizes = self.splitter.sizes()
        centroids_xy = [(x, y) for x, y, _ in self._leaks_sorted_by_size]
        use_original = self.view_toggle.value() == 0
        qimg = raster_to_qimage(
            self.current_path, sensitivity, leaks=centroids_xy, use_original_colors=use_original,
            highlight_xy=self._selected_leak_xy, user_added_xy=self._user_added_leaks,
        )
        pixmap = QPixmap.fromImage(qimg)
        self.image_pixmap_item.setPixmap(pixmap)
        self.image_pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        new_rect = self.image_pixmap_item.boundingRect()
        size_changed = (
            new_rect.width() != self._last_image_width or new_rect.height() != self._last_image_height
        )
        self._last_image_width = new_rect.width()
        self._last_image_height = new_rect.height()
        # Update size label now we have dimensions (min/max/current in pixels)
        total_px = self._last_image_width * self._last_image_height
        if total_px > 0:
            slider_max = int((MAX_SIZE_PERCENT - MIN_SIZE_PERCENT) / SIZE_STEP_PERCENT)
            a_px = max(1, int((0 * SIZE_STEP_PERCENT / 100.0) * total_px))
            b_px = max(1, int((slider_max * SIZE_STEP_PERCENT / 100.0) * total_px))
            x_px = max(1, int((self.size_slider.value() * SIZE_STEP_PERCENT / 100.0) * total_px))
            self.size_label.setText(f"Min size of leak ({a_px} - {b_px}): {x_px} pixels.")
        else:
            self.size_label.setText("Min size of leak (— — —): — pixels")
        self.image_scene.setSceneRect(new_rect)
        if size_changed:
            self.image_view.fitInView(new_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.splitter.setSizes(saved_sizes)

    def _repopulate_leak_table(self):
        """Fill the leak table from _leaks_sorted_by_size and _user_added_leaks."""
        n = len(self._leaks_sorted_by_size)
        self.leak_list.setRowCount(n)
        for row, (x, y, area) in enumerate(self._leaks_sorted_by_size):
            source = "User" if (x, y) in self._user_added_leaks else "Auto"
            self.leak_list.setItem(row, 0, QTableWidgetItem(f"{area} px"))
            self.leak_list.setItem(row, 1, QTableWidgetItem(source))

    def _on_leak_list_selection_changed(self, row: int, col: int, _prev_row: int, _prev_col: int):
        """When user selects a leak in the table, highlight that leak on the map."""
        if not self._leaks_sorted_by_size or row < 0 or row >= len(self._leaks_sorted_by_size):
            self._selected_leak_xy = None
        else:
            x, y, _ = self._leaks_sorted_by_size[row]
            self._selected_leak_xy = (x, y)
        self._refresh_display()

    def _delete_selected_leak(self):
        """Remove the currently selected leak from the list and update data/display."""
        row = self.leak_list.currentRow()
        if row < 0 or not self._leaks_sorted_by_size or row >= len(self._leaks_sorted_by_size):
            return
        x, y, _ = self._leaks_sorted_by_size[row]
        self._user_added_leaks.discard((x, y))
        # Remove this leak from _last_leaks (match by (x, y))
        self._last_leaks = [(ax, ay, aa) for (ax, ay, aa) in self._last_leaks if (ax, ay) != (x, y)]
        self._leaks_sorted_by_size = sorted(self._last_leaks, key=lambda t: t[2])
        self._selected_leak_xy = None
        self.leak_list.blockSignals(True)
        self._repopulate_leak_table()
        self.leak_list.setCurrentCell(-1, -1)
        self.leak_list.blockSignals(False)
        self.leak_count_label.setText(f"Leaks detected: {len(self._last_leaks)}")
        self.export_kml_btn.setEnabled(len(self._last_leaks) > 0)
        self._update_clear_user_leaks_button()
        self._refresh_display()

    def _clear_user_leaks(self):
        """Remove all user-defined leaks; keep only auto-detected leaks."""
        if not self._user_added_leaks:
            return
        self._last_leaks = [(x, y, a) for (x, y, a) in self._last_leaks if (x, y) not in self._user_added_leaks]
        self._user_added_leaks.clear()
        self._leaks_sorted_by_size = sorted(self._last_leaks, key=lambda t: t[2])
        self._selected_leak_xy = None
        self.leak_list.blockSignals(True)
        self._repopulate_leak_table()
        self.leak_list.setCurrentCell(-1, -1)
        self.leak_list.blockSignals(False)
        self.leak_count_label.setText(f"Leaks detected: {len(self._last_leaks)}")
        self.export_kml_btn.setEnabled(len(self._last_leaks) > 0)
        self._update_clear_user_leaks_button()
        self._refresh_display()

    def _update_clear_user_leaks_button(self):
        """Enable 'Clear user-defined leaks' only when there are user-added leaks."""
        self.clear_user_leaks_btn.setEnabled(len(self._user_added_leaks) > 0)

    def _refresh_display(self):
        """Redraw the image with current leaks and selection highlight (no re-detection)."""
        if not self.current_path or not self._last_leaks:
            return
        sensitivity = self.slider.value()
        centroids_xy = [(x, y) for x, y, _ in self._leaks_sorted_by_size]
        use_original = self.view_toggle.value() == 0
        qimg = raster_to_qimage(
            self.current_path, sensitivity, leaks=centroids_xy, use_original_colors=use_original,
            highlight_xy=self._selected_leak_xy, user_added_xy=self._user_added_leaks,
        )
        self.image_pixmap_item.setPixmap(QPixmap.fromImage(qimg))

    def _viewport_to_scene(self, viewport_pos) -> QPointF:
        """Convert viewport coordinates to scene coordinates."""
        view_pos = self.image_view.viewport().mapTo(self.image_view, viewport_pos)
        return self.image_view.mapToScene(view_pos)

    def eventFilter(self, obj, event):
        """Detect click on map: select nearest leak in list; Delete key removes selected leak."""
        if obj is self.leak_list:
            if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._delete_selected_leak()
                return True
            return super().eventFilter(obj, event)
        # View gets keyboard focus when user clicks map; viewport gets mouse events
        if obj is self.image_view:
            if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.leak_list.currentRow() >= 0:
                    self._delete_selected_leak()
                    return True
            return super().eventFilter(obj, event)
        if obj is not self.image_view.viewport():
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.leak_list.currentRow() >= 0:
                self._delete_selected_leak()
                return True
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pt = event.position().toPoint()
                self._image_press_scene = self._viewport_to_scene(pt)
                self._image_press_viewport = pt
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and self._image_press_scene is not None and self._image_press_viewport is not None:
                release_viewport = event.position().toPoint()
                move_px = math.hypot(
                    release_viewport.x() - self._image_press_viewport.x(),
                    release_viewport.y() - self._image_press_viewport.y(),
                )
                # Only treat as click if mouse barely moved (short click); dragging the view = moved a lot
                if move_px < 6 and self.current_path and self._last_image_width > 0 and self._last_image_height > 0:
                    release_scene = self._viewport_to_scene(release_viewport)
                    px, py = release_scene.x(), release_scene.y()
                    best_i = -1
                    best_d = 1e9
                    for i, (x, y, _) in enumerate(self._leaks_sorted_by_size):
                        d = math.hypot(px - x, py - y)
                        if d < best_d:
                            best_d = d
                            best_i = i
                    if best_i >= 0 and best_d < 30:
                        # Click near existing leak: select it
                        self.leak_list.blockSignals(True)
                        self.leak_list.setCurrentCell(best_i, 0)
                        self.leak_list.selectRow(best_i)
                        self.leak_list.blockSignals(False)
                        x, y, _ = self._leaks_sorted_by_size[best_i]
                        self._selected_leak_xy = (x, y)
                        self._refresh_display()
                        self.image_view.setFocus(Qt.FocusReason.MouseFocusReason)
                    else:
                        # Click away from leaks: add new leak at this location
                        ix = max(0, min(int(round(px)), self._last_image_width - 1))
                        iy = max(0, min(int(round(py)), self._last_image_height - 1))
                        self._last_leaks.append((ix, iy, 0))
                        self._user_added_leaks.add((ix, iy))
                        self._leaks_sorted_by_size = sorted(self._last_leaks, key=lambda t: t[2])
                        self._selected_leak_xy = (ix, iy)
                        self.leak_list.blockSignals(True)
                        self._repopulate_leak_table()
                        new_row = next(i for i, (x, y, _) in enumerate(self._leaks_sorted_by_size) if (x, y) == (ix, iy))
                        self.leak_list.setCurrentCell(new_row, 0)
                        self.leak_list.selectRow(new_row)
                        self.leak_list.blockSignals(False)
                        self.leak_count_label.setText(f"Leaks detected: {len(self._last_leaks)}")
                        self.export_kml_btn.setEnabled(True)
                        self._update_clear_user_leaks_button()
                        self._refresh_display()
                        self.image_view.setFocus(Qt.FocusReason.MouseFocusReason)
            self._image_press_scene = None
            self._image_press_viewport = None
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

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
            centroids_xy = [(x, y) for x, y, _ in self._last_leaks]
            export_leaks_to_kml(self.current_path, centroids_xy, path)
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