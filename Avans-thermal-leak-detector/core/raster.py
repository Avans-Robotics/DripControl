import numpy as np
import rasterio
from PySide6.QtGui import QImage
from skimage.transform import resize
from skimage import measure
from matplotlib import cm
from PIL import Image, ImageDraw, ImageFont

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

def detect_leaks(path: str, threshold: float, max_size=800):
    """
    Detect leaks in the raster by thresholding and connected components analysis.
    
    Args:
        path: Path to the GeoTIFF file
        threshold: Intensity threshold value (pixels below this are leaks)
        max_size: Maximum size for downsampling (same as display)
    
    Returns:
        List of tuples (x, y) representing leak centroids in downsampled coordinates
    """
    with rasterio.open(path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
        intensity = rgb.mean(axis=0)
        
        # Downsample to match display resolution
        h, w = intensity.shape
        scale = min(max_size / h, max_size / w, 1.0)
        if scale < 1.0:
            intensity = resize(
                intensity,
                (int(h * scale), int(w * scale)),
                preserve_range=True,
                anti_aliasing=True
            )
        
        # Threshold: pixels below threshold are potential leaks
        leak_mask = intensity < threshold
        
        # Connected components analysis
        labeled = measure.label(leak_mask)
        regions = measure.regionprops(labeled)
        
        # Extract centroids (one point per leak)
        centroids = []
        for region in regions:
            # region.centroid returns (row, col), we need (x, y) = (col, row)
            y, x = region.centroid
            centroids.append((int(x), int(y)))
        
        return centroids

def raster_to_qimage(path: str, sensitivity: float, max_size=800, leaks=None) -> QImage:
    with rasterio.open(path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
        intensity = rgb.mean(axis=0)

        # Downsample
        h, w = intensity.shape
        scale = min(max_size / h, max_size / w, 1.0)
        if scale < 1.0:
            intensity = resize(
                intensity,
                (int(h * scale), int(w * scale)),
                preserve_range=True,
                anti_aliasing=True
            )
            # Update dimensions after downsampling
            h, w = intensity.shape

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
            
            # Convert back to numpy array
            colored = np.array(pil_img)

        h, w, _ = colored.shape
        return QImage(
            colored.data, w, h, 3 * w, QImage.Format_RGB888
        ).copy()