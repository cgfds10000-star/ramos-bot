"""Build the shared Ramos canvas; no interface elements are baked into it."""
from pathlib import Path
import shutil
import numpy as np
from PIL import Image

root = Path(__file__).resolve().parents[1]
width, height = 935, 1683
y, x = np.mgrid[0:height, 0:width]
rgb = np.broadcast_to(np.array([10., 9., 16.]), (height, width, 3)).copy()
for cx, cy, rx, ry, color in [
    (-80, 180, 360, 390, (27, 0, 40)),
    (1030, 230, 270, 390, (35, 0, 45)),
    (-70, 1390, 270, 290, (40, 0, 63)),
    (1020, 1390, 260, 310, (43, 0, 67)),
]:
    strength = np.exp(-(((x-cx)/rx)**2 + ((y-cy)/ry)**2))
    rgb += strength[..., None] * color
phase = (x + y) % 6
line = np.exp(-(np.minimum(phase, 6-phase)/.65)**2)
rgb += line[..., None] * np.array([2.6, 2.4, 3.5])
path = root / 'public/ramos-background.png'
Image.fromarray(np.clip(rgb, 0, 255).astype('uint8')).save(path, optimize=True)
shutil.copyfile(path, root / 'mobile/assets/ramos-background.png')
