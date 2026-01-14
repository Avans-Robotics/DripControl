import numpy as np
import rasterio
from PySide6.QtGui import QImage
from skimage.transform import resize
from matplotlib import cm

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

def raster_to_qimage(path: str, sensitivity: float, max_size=800) -> QImage:
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

        # Percentile stretch controlled by slider
        low = np.percentile(intensity, sensitivity)
        high = np.percentile(intensity, 100 - sensitivity)
        norm = np.clip((intensity - low) / (high - low + 1e-6), 0, 1)

        # Apply thermal colormap
        cmap = cm.get_cmap("inferno")
        colored = (cmap(norm)[:, :, :3] * 255).astype(np.uint8)

        h, w, _ = colored.shape
        return QImage(
            colored.data, w, h, 3 * w, QImage.Format_RGB888
        ).copy()