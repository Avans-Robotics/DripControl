import rasterio
import numpy as np
from PySide6.QtGui import QImage
from skimage.transform import resize

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

def raster_to_qimage(path: str, max_size=800) -> QImage:
    with rasterio.open(path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
        intensity = rgb.mean(axis=0)

        # Downsample for display
        h, w = intensity.shape
        scale = min(max_size / h, max_size / w, 1.0)
        if scale < 1.0:
            intensity = resize(
                intensity,
                (int(h * scale), int(w * scale)),
                preserve_range=True,
                anti_aliasing=True
            )

        # Normalize to 8-bit
        imin, imax = intensity.min(), intensity.max()
        norm = (intensity - imin) / (imax - imin + 1e-6)
        gray = (norm * 255).astype(np.uint8)

        # Convert to RGB (placeholder, grayscale for now)
        rgb8 = np.stack([gray, gray, gray], axis=-1)

        h, w, _ = rgb8.shape
        return QImage(
            rgb8.data, w, h, 3 * w, QImage.Format_RGB888
        ).copy()