"""Render xx's locally bundled DynaPuff wordmark to desktop icon formats.

Development-only requirement: Pillow. Run from any directory.
The font's SIL Open Font License is retained in public/fonts.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SIZE = 1024
image = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=225, fill='#f8e8ed')
font = ImageFont.truetype(str(ROOT / 'public/fonts/DynaPuff.ttf'), 650)
box = draw.textbbox((0, 0), 'xx', font=font)
x = (SIZE - (box[2] - box[0])) / 2 - box[0]
y = (SIZE - (box[3] - box[1])) / 2 - box[1] - 15
draw.text((x, y), 'xx', font=font, fill='#773e57')

icons = ROOT / 'src-tauri/icons'
for path in icons.glob('*.png'):
    with Image.open(path) as old:
        dimensions = old.size
    image.resize(dimensions, Image.Resampling.LANCZOS).save(path)
for name in ['icon.ico', 'app_icon.ico']:
    image.save(icons / name, sizes=[(n, n) for n in [16, 24, 32, 48, 64, 128, 256]])
for name in ['icon.icns', 'app_icon.icns']:
    image.save(icons / name)
image.resize((256, 256), Image.Resampling.LANCZOS).save(ROOT / 'public/xx-icon.png')
image.save(ROOT / 'src/app/favicon.ico', sizes=[(n, n) for n in [16, 32, 48, 64, 128, 256]])
for name in ['logo.png', 'logo-collapsed.png']:
    image.resize((256, 256), Image.Resampling.LANCZOS).save(ROOT / 'public' / name)
print('Generated xx desktop, favicon and public icons.')
