"""
从源图自动生成各密度的 launcher 图标。
用法: python gen-icons.py [源图] [输出目录]
默认: 源图 = 仓库根 logo@300.png, 输出目录 = 仓库根 Android/
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).parent
DEFAULT_SOURCE = SCRIPT_DIR.parent / "logo@300.png"
DEFAULT_OUTPUT = SCRIPT_DIR.parent / "Android"

SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def crop_square(img):
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def make_round(img, size):
    """生成圆形图标：先缩放再叠加圆形 alpha 遮罩。"""
    base = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(base, (0, 0), mask)
    return result


def generate(source, output_dir):
    source = Path(source)
    output_dir = Path(output_dir)
    if not source.exists():
        print(f"错误：找不到源图 {source}")
        return False
    img = Image.open(source).convert("RGBA")
    print(f"源图: {source} ({img.size[0]}x{img.size[1]})")
    square = crop_square(img)
    if square.size != img.size:
        print(f"  已裁剪为中心正方形: {square.size[0]}x{square.size[1]}")
    for dpi, size in SIZES.items():
        dpi_dir = output_dir / f"mipmap-{dpi}"
        dpi_dir.mkdir(parents=True, exist_ok=True)
        launcher = square.resize((size, size), Image.LANCZOS)
        launcher_path = dpi_dir / "ic_launcher.png"
        launcher.save(launcher_path, "PNG", optimize=True)
        round_icon = make_round(square, size)
        round_path = dpi_dir / "ic_launcher_round.png"
        round_icon.save(round_path, "PNG", optimize=True)
        print(
            f"  ✓ mipmap-{dpi}: ic_launcher.png + ic_launcher_round.png "
            f"({size}x{size})"
        )
    play_store = output_dir / "play_store_512.png"
    square.resize((512, 512), Image.LANCZOS).save(play_store, "PNG", optimize=True)
    print(f"  ✓ play_store_512.png (512x512)")
    print("完成！")
    return True


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    ok = generate(source, output_dir)
    sys.exit(0 if ok else 1)
