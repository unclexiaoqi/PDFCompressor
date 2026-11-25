"""生成应用图标（assets/icon.ico / icon.png）。

画一个圆角蓝底 + 白色文档 + 向下箭头的图标，和界面配色一致。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

TOP = (37, 99, 235)
BOTTOM = (29, 78, 216)
WHITE = (255, 255, 255)
SOFT = (219, 234, 254)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def make(size: int = 256) -> Image.Image:
    scale = size / 256.0

    def s(value: float) -> float:
        return value * scale

    background = Image.new("RGB", (size, size), TOP)
    draw = ImageDraw.Draw(background)
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * ratio) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)

    # 高光
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(highlight)
    hdraw.ellipse((-s(40), -s(120), size + s(40), s(120)), fill=(255, 255, 255, 26))
    background = Image.alpha_composite(background.convert("RGBA"), highlight).convert("RGB")

    icon = background.convert("RGBA")
    draw = ImageDraw.Draw(icon)

    # 文档
    left, top, right, bottom = s(70), s(52), s(186), s(196)
    fold = s(32)
    draw.rounded_rectangle((left, top, right, bottom), radius=s(12), fill=WHITE)
    draw.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)],
                 fill=SOFT)
    # 文本线
    line_color = (203, 213, 225)
    for index in range(2):
        y = top + s(32) + index * s(15)
        draw.rounded_rectangle((left + s(20), y, right - s(20), y + s(6)),
                               radius=s(3), fill=line_color)
    # 向下箭头
    cx = s(128)
    draw.rounded_rectangle((cx - s(7), top + s(64), cx + s(7), top + s(106)),
                           radius=s(3), fill=TOP)
    draw.polygon([(cx - s(22), top + s(100)), (cx + s(22), top + s(100)),
                  (cx, top + s(128))], fill=TOP)

    icon.putalpha(rounded_mask(size, int(s(56))))
    return icon


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [make(size) for size in sizes]
    big = make(1024)
    big.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.png")
    big.save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes])
    # macOS 的 .app 需要 .icns
    big.save(ASSETS / "icon.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128),
                                           (256, 256), (512, 512), (1024, 1024)])
    print("已生成", ASSETS / "icon.ico", ASSETS / "icon.png", ASSETS / "icon.icns")


if __name__ == "__main__":
    main()
