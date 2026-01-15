import numpy as np
import rasterio
from PySide6.QtGui import QImage
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

def detect_leaks(path: str, rgb_threshold: float, min_size_percent: float, min_inertia_ratio: float = 0.1, max_size=4000):
    """
    Blob-based leak detection using OpenCV's SimpleBlobDetector.
    
    A "leak" is treated as a dark blob detected by OpenCV's blob detector,
    filtered by area and shape characteristics.
    
    Args:
        path: Path to the GeoTIFF file
        rgb_threshold: Maximum intensity threshold value (0-255) for blob detection.
                      OpenCV searches from minThreshold (30% of this) to this value.
                      Lower = more restrictive = fewer leaks (darker threshold)
        min_size_percent: Minimum blob area as percentage of image area (e.g. 0.1 = 0.1%)
        min_inertia_ratio: Minimum inertia ratio (0.0-1.0) to filter elongated shapes.
                          Lower values allow more elongated shapes (lines, ellipses).
                          Higher values only allow rounder shapes (circles).
                          Default: 0.1
        max_size: Maximum size for downsampling (same as display)
    
    Returns:
        Tuple of (list of (x, y) blob centroids, dict with detection parameters used)
        Centroids are sorted by mean intensity (darkest first)
    """
    intensity, valid_mask = _load_intensity(path, max_size=max_size)
    if not np.any(valid_mask):
        return [], {}

    intensity_scaled, valid_pixels = _normalize_intensity(intensity, valid_mask)
    if valid_pixels.size == 0:
        return [], {}

    h, w = intensity.shape

    # Clamp inputs defensively
    threshold_value = float(np.clip(rgb_threshold, 0.0, 255.0))
    min_size_percent = float(max(min_size_percent, 0.0))
    min_inertia_ratio = float(np.clip(min_inertia_ratio, 0.0001, 1.0))

    # Convert to uint8 for OpenCV (already in 0-255 range)
    intensity_uint8 = np.clip(intensity_scaled, 0, 255).astype(np.uint8)
    
    # Apply valid mask: set invalid pixels to white (255) so they won't be detected as dark blobs
    intensity_uint8[~valid_mask] = 255

    # Calculate minimum area in pixels
    min_area_px = int((min_size_percent / 100.0) * float(h * w))
    # Set a reasonable maximum area (e.g., 50% of image)
    max_area_px = int(0.5 * float(h * w))

    # Configure OpenCV SimpleBlobDetector parameters
    params = cv2.SimpleBlobDetector_Params()
    
    # Threshold parameters: use the rgb_threshold as maxThreshold
    # OpenCV's detector uses multiple thresholds internally
    # We set minThreshold lower and maxThreshold to our threshold value
    min_threshold = max(10, int(threshold_value * 0.3))  # Start at 30% of max
    max_threshold = int(threshold_value)  # Use our threshold as max
    threshold_step = max(5, int(threshold_value / 20))  # Adaptive step size
    
    params.minThreshold = min_threshold
    params.maxThreshold = max_threshold
    params.thresholdStep = threshold_step
    
    # Filter by color: detect dark blobs (blobColor = 0)
    params.filterByColor = True
    params.blobColor = 0
    
    # Filter by area
    params.filterByArea = True
    params.minArea = max(1, min_area_px)
    params.maxArea = max_area_px
    
    # Filter by circularity: exclude very elongated shapes (likely irrigation lines)
    params.filterByCircularity = True
    params.minCircularity = 0.1  # Allow somewhat elongated shapes
    params.maxCircularity = 1.0
    
    # Filter by convexity: ensure blobs are reasonably convex
    params.filterByConvexity = True
    params.minConvexity = 0.5  # Allow some concavity
    params.maxConvexity = 1.0
    
    # Filter by inertia ratio: exclude very elongated shapes
    params.filterByInertia = True
    params.minInertiaRatio = min_inertia_ratio  # User-controlled: lower = allow more elongation
    params.maxInertiaRatio = 1.0

    # Create detector and detect blobs
    detector = cv2.SimpleBlobDetector_create(params)
    keypoints = detector.detect(intensity_uint8)

    # Convert keypoints to (x, y) tuples and sort by intensity (darkest first)
    leaks_with_score = []
    for kp in keypoints:
        x, y = int(round(kp.pt[0])), int(round(kp.pt[1]))
        if 0 <= x < w and 0 <= y < h:
            # Get intensity at this point for sorting
            intensity_at_point = float(intensity_scaled[y, x])
            leaks_with_score.append((intensity_at_point, (x, y)))

    # Sort by intensity (darkest first)
    leaks_with_score.sort(key=lambda t: t[0])
    
    # Return detection info for UI display
    detection_info = {
        'min_threshold': min_threshold,
        'max_threshold': max_threshold,
        'threshold_step': threshold_step,
        'min_area_px': min_area_px,
        'max_area_px': max_area_px,
        'min_circularity': params.minCircularity,
        'min_convexity': params.minConvexity,
        'min_inertia': params.minInertiaRatio
    }
    
    return [xy for _, xy in leaks_with_score], detection_info

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