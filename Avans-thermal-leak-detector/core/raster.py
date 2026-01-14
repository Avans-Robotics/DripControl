import rasterio
import numpy as np

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
