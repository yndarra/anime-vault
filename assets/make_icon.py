"""Иконка anime-vault (окно и .exe): бирюзовый скруглённый квадрат с «AV», как значок в боковой панели окна.

    python assets/make_icon.py      → assets/icon.ico (16…256 px) и assets/icon.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SIZE = 256
TEAL, DARK = (90, 209, 196, 255), (14, 15, 19, 255)

image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=60, fill=TEAL)
try:
    font = ImageFont.truetype("segoeuib.ttf", 112)
except OSError:
    font = ImageFont.load_default(112)
draw.text((SIZE / 2, SIZE / 2 + 4), "AV", font=font, fill=DARK, anchor="mm")
image.save(HERE / "icon.png")
image.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
