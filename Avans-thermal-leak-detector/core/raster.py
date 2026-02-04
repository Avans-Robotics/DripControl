import numpy as np
import rasterio
from rasterio import warp as rasterio_warp
from PySide6.QtGui import QImage
import simplekml
from skimage.transform import resize
from skimage import measure
from matplotlib import cm
from PIL import Image, ImageDraw, ImageFont
import cv2

def load_raster(path: str) -> str:
    with rasterio.open(path) as ds:
        if ds.count < 3:
            raise ValueError("Raster must have at least 3 bands (RGB)")

        rgb = ds.read([1, 2, 3]).astype(np.float32)
        intensity = rgb.mean(axis=0)

        return (
            f"Loaded file:\n{path}\n\n"
            f"Size: {ds.width} x {ds.height}\n"
            f"CRS: {ds.crs}\n"
            f"Intensity min/max: "
            f"{intensity.min():.2f} / {intensity.max():.2f}"
        )

def _load_intensity(path: str, max_size=4000):
    with rasterio.open(path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
        intensity = rgb.mean(axis=0)

        # Downsample to reduce cost while staying consistent with display resolution.
        h, w = intensity.shape
        scale = min(max_size / h, max_size / w, 1.0)
        if scale < 1.0:
            intensity = resize(
                intensity,
                (int(h * scale), int(w * scale)),
                preserve_range=True,
                anti_aliasing=True
            )
            h, w = intensity.shape

        # Start with finite pixels, then suppress border-connected black padding.
        valid_mask = np.isfinite(intensity)
        if not np.any(valid_mask):
            return intensity, valid_mask

        border_black = (intensity <= 1.0) & valid_mask
        if np.any(border_black):
            bg_labels = measure.label(border_black, connectivity=2)
            border_ids = np.unique(
                np.concatenate(
                    [
                        bg_labels[0, :],
                        bg_labels[-1, :],
                        bg_labels[:, 0],
                        bg_labels[:, -1],
                    ]
                )
            )
            border_ids = border_ids[border_ids != 0]
            if border_ids.size:
                border_connected_bg = np.isin(bg_labels, border_ids)
                valid_mask = valid_mask & (~border_connected_bg)

        return intensity, valid_mask

def _normalize_intensity(intensity: np.ndarray, valid_mask: np.ndarray):
    valid_pixels = intensity[valid_mask]
    if valid_pixels.size == 0:
        return intensity, valid_pixels

    # Robust normalization to reduce the impact of outliers and striping.
    p2, p98 = np.percentile(valid_pixels, [2, 98])
    if p98 <= p2:
        return intensity, valid_pixels

    norm = (intensity - p2) / (p98 - p2)
    norm = np.clip(norm, 0.0, 1.0)
    scaled = norm * 255.0
    return scaled, valid_pixels

def get_intensity_stats(path: str, max_size=4000) -> dict:
    """
    Compute robust intensity stats for a raster (used to set sensible defaults).
    Returns a dict with percentiles in the valid (non-padding) area.
    """
    intensity, valid_mask = _load_intensity(path, max_size=max_size)
    if not np.any(valid_mask):
        return {}

    intensity_scaled, valid_pixels = _normalize_intensity(intensity, valid_mask)
    if valid_pixels.size == 0:
        return {}

    percentiles = [5, 10, 25, 50, 75, 90, 95]
    values = np.percentile(intensity_scaled[valid_mask], percentiles)
    stats = {f"p{p}": float(v) for p, v in zip(percentiles, values)}
    stats["min"] = float(intensity_scaled[valid_mask].min())
    stats["max"] = float(intensity_scaled[valid_mask].max())
    return stats

def _single_threshold_detection(
    intensity_uint8: np.ndarray,
    intensity_scaled: np.ndarray,
    valid_mask: np.ndarray,
    threshold_value: float,
    min_area_px: int,
    max_area_px: int,
    apply_filters: bool,
) -> tuple:
    """
    Detect dark blobs using a single intensity threshold.
    Pixels with intensity > threshold are ignored (background).
    Returns (list of (x, y, area_px), components_before for debug,
             raw binary image, opened binary image, detection_info dict).
    """
    h, w = intensity_uint8.shape
    thresh_val = float(np.clip(threshold_value, 0.0, 255.0))
    # Raw binary: foreground (intensity <= thresh) = 0, background = 255
    _, binary_raw = cv2.threshold(intensity_uint8, thresh_val, 255, cv2.THRESH_BINARY_INV)

    # Morphological smoothing: opening on the binary image to remove tiny specks
    # (work purely in the binary domain, after thresholding)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary_opened = cv2.morphologyEx(binary_raw, cv2.MORPH_OPEN, kernel)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_opened, connectivity=4)
    components_before = []
    components_after = []

    for i in range(1, num_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        cx, cy = centroids[i, 0], centroids[i, 1]
        if area <= 0:
            continue
        x_int, y_int = int(round(cx)), int(round(cy))
        intensity_at_point = float(intensity_scaled[y_int, x_int]) if 0 <= y_int < h and 0 <= x_int < w else 0.0
        components_before.append((area, intensity_at_point, (x_int, y_int)))
        if not apply_filters:
            components_after.append((area, intensity_at_point, (x_int, y_int)))
            continue
        if area < min_area_px or area > max_area_px:
            continue
        components_after.append((area, intensity_at_point, (x_int, y_int)))

    components_after.sort(key=lambda t: t[1])  # sort by intensity (darkest first)
    centroids_sorted = [(xy[0], xy[1], area) for area, _, xy in components_after]
    detection_info = {
        'threshold': thresh_val,
        'min_area_px': min_area_px,
        'max_area_px': max_area_px,
        'blobs_before_filtering': len(components_before),
        'blobs_after_filtering': len(centroids_sorted),
    }
    return centroids_sorted, components_before, binary_raw, binary_opened, detection_info


def _threshold_and_area_params(rgb_threshold: float, min_size_percent: float,
                               image_height: int, image_width: int) -> tuple:
    """Single threshold and area limits. Returns (threshold, min_area_px, max_area_px, info_dict)."""
    threshold_value = float(np.clip(rgb_threshold, 0.0, 255.0))
    min_size_percent = float(max(min_size_percent, 0.0))
    min_area_px = max(1, int((min_size_percent / 100.0) * float(image_height * image_width)))
    max_area_px = int(0.5 * float(image_height * image_width))
    info = {'threshold': threshold_value, 'min_area_px': min_area_px, 'max_area_px': max_area_px}
    return threshold_value, min_area_px, max_area_px, info


def detect_leaks(path: str, rgb_threshold: float, min_size_percent: float, max_size=4000, return_steps: bool = False):
    """
    Leak detection using a single intensity threshold.
    Pixels with intensity > threshold are ignored; dark blobs (intensity <= threshold) are
    found as connected components and filtered by area.

    Args:
        path: Path to the GeoTIFF file
        rgb_threshold: Single intensity threshold (0-255). Anything above this is ignored.
        min_size_percent: Minimum blob area as percentage of image area
        max_size: Maximum size for downsampling
        return_steps: If True, return (centroids, detection_info, steps_dict) for visualization.

    Returns:
        If return_steps is False: (list of (x, y, area_px) per leak, detection_info dict).
        If return_steps is True: (same list, detection_info, steps_dict with intermediate images).
        List is sorted by intensity (darkest first). Use area_px for size-based sorting in the UI.
    """
    intensity, valid_mask = _load_intensity(path, max_size=max_size)
    if not np.any(valid_mask):
        return ([], {}) if not return_steps else ([], {}, {})

    intensity_scaled, valid_pixels = _normalize_intensity(intensity, valid_mask)
    if valid_pixels.size == 0:
        return ([], {}) if not return_steps else ([], {}, {})

    h, w = intensity.shape
    intensity_uint8 = np.clip(intensity_scaled, 0, 255).astype(np.uint8)
    intensity_uint8[~valid_mask] = 255

    # Gaussian blur on intensity before thresholding (reduces noise/specks)
    intensity_blurred = cv2.GaussianBlur(intensity_uint8, (3, 3), 0)

    thresh, min_area_px, max_area_px, _ = _threshold_and_area_params(
        rgb_threshold, min_size_percent, h, w
    )
    centroids, components_before, binary_raw, binary_opened, detection_info = _single_threshold_detection(
        intensity_blurred, intensity_scaled, valid_mask,
        thresh, min_area_px, max_area_px, apply_filters=True
    )

    if not return_steps:
        return centroids, detection_info

    # Build steps dict for visualization
    step0_valid_mask = (valid_mask.astype(np.uint8) * 255)
    step0_intensity_scaled = np.clip(intensity_scaled, 0, 255).astype(np.uint8).copy()
    step0_intensity_scaled[~valid_mask] = 0
    steps = {
        'step0_valid_mask': step0_valid_mask,
        'step0_intensity_scaled': step0_intensity_scaled,
        'step0_intensity_uint8': intensity_uint8.copy(),
        'step1_original': intensity_uint8.copy(),
        'step1_blurred': intensity_blurred.copy(),
        'step2_binary_raw': binary_raw,
        'step2_binary': binary_opened,
        'step2_threshold_value': thresh,
        'step3_before_filtering': None,
        'step4_after_filtering': None,
    }
    img_before = cv2.cvtColor(intensity_uint8, cv2.COLOR_GRAY2BGR)
    for _area, _intensity, (x, y) in components_before:
        radius = max(2, int(np.sqrt(_area / np.pi)))
        cv2.circle(img_before, (x, y), min(radius, 50), (0, 255, 0), 1)
    steps['step3_before_filtering'] = img_before
    img_after = cv2.cvtColor(intensity_uint8, cv2.COLOR_GRAY2BGR)
    for x, y, _ in centroids:
        cv2.circle(img_after, (x, y), 5, (0, 0, 255), 2)
    steps['step4_after_filtering'] = img_after

    return centroids, detection_info, steps

def raster_to_qimage(path: str, sensitivity: float, max_size=4000, leaks=None, use_original_colors=False) -> QImage:
    with rasterio.open(path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
        intensity = rgb.mean(axis=0)

        # Downsample
        h, w = intensity.shape
        scale = min(max_size / h, max_size / w, 1.0)
        if scale < 1.0:
            # Downsample RGB bands
            rgb_downsampled = np.zeros((3, int(h * scale), int(w * scale)), dtype=np.float32)
            for i in range(3):
                rgb_downsampled[i] = resize(
                    rgb[i],
                    (int(h * scale), int(w * scale)),
                    preserve_range=True,
                    anti_aliasing=True
                )
            rgb = rgb_downsampled
            intensity = resize(
                intensity,
                (int(h * scale), int(w * scale)),
                preserve_range=True,
                anti_aliasing=True
            )
            # Update dimensions after downsampling
            h, w = intensity.shape

        if use_original_colors:
            # Show original RGB colors (for debugging)
            # Since it's grayscale thermal data, all bands are the same, so we can use any band
            # Normalize to 0-255 range for display
            rgb_min = rgb.min()
            rgb_max = rgb.max()
            if rgb_max > rgb_min:
                rgb_normalized = ((rgb - rgb_min) / (rgb_max - rgb_min) * 255).astype(np.uint8)
            else:
                rgb_normalized = rgb.astype(np.uint8)
            # Convert from (3, h, w) to (h, w, 3) and ensure C-contiguous
            colored = np.transpose(rgb_normalized, (1, 2, 0)).copy()
        else:
            # Percentile stretch controlled by slider
            low = np.percentile(intensity, sensitivity)
            high = np.percentile(intensity, 100 - sensitivity)
            norm = np.clip((intensity - low) / (high - low + 1e-6), 0, 1)

            # Apply thermal colormap
            cmap = cm.get_cmap("inferno")
            colored = (cmap(norm)[:, :, :3] * 255).astype(np.uint8)
        
        # Draw leak markers if provided
        if leaks:
            # Convert to PIL Image for better drawing capabilities
            pil_img = Image.fromarray(colored)
            draw = ImageDraw.Draw(pil_img)
            
            # Calculate marker size based on image dimensions (smaller markers)
            marker_radius = max(5, min(h, w) // 100)
            font_size = max(8, marker_radius + 2)
            
            # Try to load a font, fallback to default if not available
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                try:
                    font = ImageFont.load_default()
                except:
                    font = None
            
            for idx, (x, y) in enumerate(leaks, 1):
                # Ensure coordinates are within bounds
                if 0 <= y < h and 0 <= x < w:
                    # Draw white circle (background)
                    draw.ellipse(
                        [x - marker_radius - 2, y - marker_radius - 2,
                         x + marker_radius + 2, y + marker_radius + 2],
                        fill=(255, 255, 255), outline=(0, 0, 0), width=2
                    )
                    
                    # Draw red circle
                    draw.ellipse(
                        [x - marker_radius, y - marker_radius,
                         x + marker_radius, y + marker_radius],
                        fill=(255, 0, 0), outline=(255, 255, 255), width=1
                    )
                    
                    # Draw number text
                    text = str(idx)
                    if font:
                        # Get text bounding box for centering
                        bbox = draw.textbbox((0, 0), text, font=font)
                        text_width = bbox[2] - bbox[0]
                        text_height = bbox[3] - bbox[1]
                        text_x = x - text_width // 2
                        text_y = y - text_height // 2
                    else:
                        # Fallback positioning
                        text_x = x - 5
                        text_y = y - 6
                    
                    # Draw white text
                    draw.text((text_x, text_y), text, fill=(255, 255, 255), font=font)
            
            # Convert back to numpy array and ensure C-contiguous
            colored = np.ascontiguousarray(pil_img)

        h, w, _ = colored.shape
        # Ensure array is C-contiguous before creating QImage
        colored = np.ascontiguousarray(colored)
        return QImage(
            colored.data, w, h, 3 * w, QImage.Format_RGB888
        ).copy()


def export_leaks_to_kml(
    raster_path: str,
    centroids: list[tuple[int, int]],
    output_path: str,
    max_size: int = 4000,
) -> None:
    """
    Export leak pixel centroids to a KML file with correct geographic coordinates.
    Uses the raster's transform and CRS; reprojects to WGS84 (lon, lat) for KML.
    Centroids must be in the same downsampled pixel space as detect_leaks (same max_size).
    """
    with rasterio.open(raster_path) as ds:
        if ds.crs is None:
            raise ValueError("Raster has no CRS; cannot export to geographic KML.")
        src_crs = ds.crs
        height, width = int(ds.height), int(ds.width)
        scale = min(max_size / height, max_size / width, 1.0)

        map_xs: list[float] = []
        map_ys: list[float] = []
        for col_px, row_px in centroids:
            col_orig = col_px / scale
            row_orig = row_px / scale
            x, y = ds.xy(row_orig, col_orig)
            map_xs.append(x)
            map_ys.append(y)

    if not map_xs:
        kml = simplekml.Kml()
        kml.save(output_path)
        return

    wgs84 = "EPSG:4326"
    if str(src_crs).upper() == wgs84 or str(src_crs) == "4326":
        lons, lats = map_xs, map_ys
    else:
        lons, lats = rasterio_warp.transform(src_crs, wgs84, map_xs, map_ys)

    kml = simplekml.Kml()
    for i, (lon, lat) in enumerate(zip(lons, lats)):
        kml.newpoint(name=f"Leak {i + 1}", coords=[(lon, lat)])
    kml.save(output_path)