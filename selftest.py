"""自测：造一批覆盖典型场景的 PDF，跑压缩，检查体积 / 页数 / 画质 / 文字。

用法：
    python selftest.py              # 跑默认预设（最小、标准、高质量）
    python selftest.py --all        # 跑全部预设
    python selftest.py --keep       # 保留测试文件（_testdata/）
退出码 0 表示全部通过。
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pikepdf
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import PRESET_ORDER, analyze, compress_file, get_preset  # noqa: E402

TESTDATA = HERE / "_testdata"
RNG = np.random.default_rng(20260101)

WINDOWS_FONTS = [
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
]


# ---------------------------------------------------------------------------
# 造测试文件
# ---------------------------------------------------------------------------

def _photo(w: int, h: int, quality: int = 92) -> bytes:
    """有渐变、有噪点、有边缘的“照片”，避免纯色导致压缩率虚高。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    arr = np.zeros((h, w, 3), np.float32)
    arr[..., 0] = 110 + 90 * np.sin(xx / 120.0) * np.cos(yy / 200.0)
    arr[..., 1] = 130 + 80 * np.cos(yy / 90.0) + 25 * np.sin(xx / 33.0)
    arr[..., 2] = 150 + 70 * np.sin((xx + yy) / 160.0)
    arr += RNG.normal(0, 7, (h, w, 3))
    # 加几块“文字样”色块，模拟扫描件上的内容
    for i in range(12):
        y = 200 + i * (h - 400) // 12
        arr[y:y + 26, 220:w - 220, 0] *= 0.35
        arr[y:y + 26, 220:w - 220, 1] *= 0.35
        arr[y:y + 26, 220:w - 220, 2] *= 0.35
    buf = io.BytesIO()
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8")).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def build_scan(path: Path, pages: int = 3) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=_photo(2480, 3508))
        page.insert_text((40, 830), f"scanned page {i + 1}", fontsize=7)
    doc.save(str(path))
    doc.close()


def build_cjk_font(path: Path) -> None:
    font = next((f for f in WINDOWS_FONTS if f.exists()), None)
    doc = pymupdf.open()
    if font is None:
        page = doc.new_page()
        page.insert_text((60, 100), "no system CJK font available", fontsize=14)
    else:
        text = ("这是一段用来验证字体子集化的中文文本。压缩器应当只保留用到的字形，"
                "把十几兆的内嵌字体缩到几十 KB。The quick brown fox jumps over the lazy dog.")
        for i in range(3):
            page = doc.new_page(width=595, height=842)
            page.insert_font(fontname="F0", fontfile=str(font))
            page.insert_text((55, 110 + i * 30), text, fontsize=13, fontname="F0")
            page.insert_text((55, 300), f"第 {i + 1} 页", fontsize=18, fontname="F0")
    doc.set_metadata({"title": "字体子集化测试", "author": "selftest", "producer": "PDFCompressor selftest"})
    doc.set_toc([[1, "第一章", 1], [2, "第一节", 2]])
    doc.save(str(path))
    doc.close()


def _bilevel_bits(dpi: int = 300) -> np.ndarray:
    """渲染一页密排小字，二值化成 1-bit，模拟 300 DPI 单色扫描件。"""
    probe = pymupdf.open()
    page = probe.new_page(width=595, height=842)
    y = 48.0
    for i in range(46):
        page.insert_text((40, y), "The quick brown fox jumps over the lazy dog 0123456789 " * 1,
                         fontsize=8)
        y += 17.0
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    probe.close()
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    bits = gray < 170
    bits ^= RNG.random(bits.shape) < 0.0006
    return bits


def _write_bilevel_pdf(path: Path, ink: np.ndarray, invert_decode: bool = False) -> None:
    """ink=True 表示该像素是黑的。PDF 里 1-bit 约定 1=白，所以要取反后打包。"""
    h, w = ink.shape
    if invert_decode:
        # /Decode [1 0]：1 → 黑，所以墨点直接写 1
        bits = ink.astype(np.uint8)
    else:
        bits = (~ink).astype(np.uint8)
    packed = np.packbits(bits, axis=1).tobytes()
    compressed = zlib.compress(packed, 9)
    pdf = pikepdf.new()
    p = pikepdf.Page(pdf.add_blank_page(page_size=(595, 842)))
    dictionary = {
        "/Type": pikepdf.Name("/XObject"),
        "/Subtype": pikepdf.Name("/Image"),
        "/Width": w, "/Height": h,
        "/ColorSpace": pikepdf.Name("/DeviceGray"),
        "/BitsPerComponent": 1,
        "/Filter": pikepdf.Name("/FlateDecode"),
    }
    if invert_decode:
        dictionary["/Decode"] = pikepdf.Array([1, 0])
    img = pikepdf.Stream(pdf, compressed, pikepdf.Dictionary(dictionary))
    p.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im0": img})})
    p.Contents = pdf.make_stream(b"q 595 0 0 842 0 0 cm /Im0 Do Q")
    pdf.save(str(path))
    pdf.close()


def build_bilevel(path: Path) -> None:
    _write_bilevel_pdf(path, _bilevel_bits())


def build_bilevel_inverted_decode(path: Path) -> None:
    """带 /Decode [1 0] 的二值图：很多扫描仪的 PDF 都这么存（1 表示黑）。"""
    _write_bilevel_pdf(path, _bilevel_bits(), invert_decode=True)


def build_cmyk(path: Path) -> None:
    """CMYK 图片：PDF 里的 CMYK JPEG 按反相存储，最容易压坏颜色。"""
    w, h = 1600, 2200
    arr = np.zeros((h, w, 4), np.uint8)
    arr[:, :] = (0, 0, 0, 0)                        # 白
    arr[:, w // 2:] = (0, 255, 255, 0)              # 红
    arr[: h // 3, : w // 3] = (255, 0, 0, 0)        # 青
    arr[h // 3: 2 * h // 3, : w // 3] = (0, 0, 0, 255)  # 黑
    arr[2 * h // 3:, :, :] = (0, 60, 0, 40)         # 灰蓝
    buf = io.BytesIO()
    # 反相后写 JPEG —— 与真实 PDF 的 CMYK 存储方式一致
    Image.fromarray(255 - arr, "CMYK").save(buf, "JPEG", quality=92)
    jpeg = buf.getvalue()

    pdf = pikepdf.new()
    p = pikepdf.Page(pdf.add_blank_page(page_size=(595, 842)))
    img = pikepdf.Stream(pdf, jpeg, pikepdf.Dictionary({
        "/Type": pikepdf.Name("/XObject"), "/Subtype": pikepdf.Name("/Image"),
        "/Width": w, "/Height": h, "/ColorSpace": pikepdf.Name("/DeviceCMYK"),
        "/BitsPerComponent": 8, "/Filter": pikepdf.Name("/DCTDecode"),
    }))
    p.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im0": img})})
    p.Contents = pdf.make_stream(b"q 595 0 0 842 0 0 cm /Im0 Do Q")
    pdf.save(str(path))
    pdf.close()


def build_mixed(path: Path) -> None:
    """带书签、元数据、注释、附件、缩略图的“正常文档”。"""
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 90), f"Quarterly Report {2026} — page {i + 1}", fontsize=18)
        for line in range(14):
            page.insert_text((60, 140 + line * 26),
                             "The quick brown fox jumps over the lazy dog. " * 2,
                             fontsize=10)
        if i % 2 == 0:
            page.insert_image(pymupdf.Rect(60, 560, 535, 790), stream=_photo(1200, 700, 90))
        page.add_text_annot((120, 300), "这是一条注释")
    doc.set_metadata({
        "title": "季度报告", "author": "Selftest", "subject": "混合内容测试",
        "keywords": "pdf, compression", "creator": "selftest", "producer": "selftest",
    })
    doc.set_toc([[1, "封面", 1], [1, "正文", 2], [2, "附录", 4]])
    doc.embfile_add("readme.txt", b"attachment content " * 500, filename="readme.txt",
                    desc="附件说明")
    doc.save(str(path))
    doc.close()

    # 塞一个缩略图
    with pikepdf.open(str(path)) as pdf:
        thumb = pikepdf.Stream(pdf, _photo(160, 220, 80), pikepdf.Dictionary({
            "/Type": pikepdf.Name("/XObject"), "/Subtype": pikepdf.Name("/Image"),
            "/Width": 160, "/Height": 220, "/ColorSpace": pikepdf.Name("/DeviceRGB"),
            "/BitsPerComponent": 8, "/Filter": pikepdf.Name("/DCTDecode"),
        }))
        pdf.pages[0].obj["/Thumb"] = pdf.make_indirect(thumb)
        pdf.save(str(path.with_suffix(".tmp.pdf")), compress_streams=True)
    path.with_suffix(".tmp.pdf").replace(path)


def build_transparent(path: Path) -> None:
    """带 SMask 的半透明图像（软件截图常见）。"""
    w, h = 1600, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = np.clip(255 - xx / 8, 0, 255)
    arr[..., 1] = np.clip(255 - yy / 6, 0, 255)
    arr[..., 2] = 180
    arr[..., 3] = np.clip(255 - (xx + yy) / 10, 0, 255)
    rgb = io.BytesIO()
    Image.fromarray(arr[..., :3]).save(rgb, "JPEG", quality=90)
    alpha = zlib.compress(arr[..., 3].tobytes(), 6)

    pdf = pikepdf.new()
    p = pikepdf.Page(pdf.add_blank_page(page_size=(595, 842)))
    mask = pikepdf.Stream(pdf, alpha, pikepdf.Dictionary({
        "/Type": pikepdf.Name("/XObject"), "/Subtype": pikepdf.Name("/Image"),
        "/Width": w, "/Height": h, "/ColorSpace": pikepdf.Name("/DeviceGray"),
        "/BitsPerComponent": 8, "/Filter": pikepdf.Name("/FlateDecode"),
    }))
    img = pikepdf.Stream(pdf, rgb.getvalue(), pikepdf.Dictionary({
        "/Type": pikepdf.Name("/XObject"), "/Subtype": pikepdf.Name("/Image"),
        "/Width": w, "/Height": h, "/ColorSpace": pikepdf.Name("/DeviceRGB"),
        "/BitsPerComponent": 8, "/Filter": pikepdf.Name("/DCTDecode"),
        "/SMask": pdf.make_indirect(mask),
    }))
    p.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im0": img})})
    p.Contents = pdf.make_stream(b"q 595 0 0 842 0 0 cm /Im0 Do Q")
    pdf.save(str(path))
    pdf.close()


def build_vector(path: Path, pages: int = 12) -> None:
    """纯文字/矢量文档：没有图像，考察对象流与流压缩。"""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 80), f"Vector text page {i + 1}", fontsize=16)
        y = 120
        for row in range(30):
            page.insert_text((60, y), "The quick brown fox jumps over the lazy dog. " * 2, fontsize=9)
            y += 22
        page.draw_rect(pymupdf.Rect(60, 760, 535, 800), color=(0.2, 0.3, 0.8), width=1.2)
    doc.set_metadata({"title": "纯文字文档", "author": "selftest"})
    doc.save(str(path))
    doc.close()


FIXTURES = {
    "scan": (build_scan, "彩色扫描件（300 DPI JPEG）"),
    "cjk": (build_cjk_font, "内嵌中文字体（全量嵌入）"),
    "bilevel": (build_bilevel, "单色扫描件（1-bit Flate）"),
    "decode": (build_bilevel_inverted_decode, "单色图（/Decode 反相）"),
    "cmyk": (build_cmyk, "CMYK 图片（PDF 反相存储）"),
    "mixed": (build_mixed, "混合文档（书签/注释/附件/缩略图）"),
    "alpha": (build_transparent, "透明图像（SMask）"),
    "vector": (build_vector, "纯文字矢量文档"),
}


# ---------------------------------------------------------------------------
# 跑测试
# ---------------------------------------------------------------------------

def _text_of(path: Path) -> str:
    try:
        with pymupdf.open(str(path)) as doc:
            return "".join(page.get_text("text") for page in doc)
    except Exception:
        return ""


def run_case(fixture: str, preset: str, src: Path, out_dir: Path) -> dict:
    settings = get_preset(preset)
    dst = out_dir / f"{fixture}-{preset}.pdf"
    started = time.time()
    result = compress_file(src, dst, settings)
    elapsed = time.time() - started
    row = {
        "fixture": fixture,
        "preset": preset,
        "ok": result.ok,
        "error": result.error,
        "before": result.orig_size,
        "after": result.new_size,
        "ratio": result.ratio,
        "psnr": result.verify.psnr if result.verify else 0.0,
        "seconds": elapsed,
        "kept_original": result.kept_original,
        "notes": list(result.warnings),
    }
    if not result.ok:
        return row

    # 页数
    try:
        with pymupdf.open(str(dst)) as after, pymupdf.open(str(src)) as before:
            row["pages_ok"] = after.page_count == before.page_count
    except Exception as exc:
        row["pages_ok"] = False
        row["error"] = f"输出无法打开：{exc}"

    # 文字保留（有文字的样例）
    text_before = _text_of(src)
    if len(text_before) > 200:
        text_after = _text_of(dst)
        row["text_ratio"] = len(text_after) / max(1, len(text_before))
    else:
        row["text_ratio"] = 1.0

    # 放弃对象的效果
    if preset == "smallest" and fixture == "mixed":
        with pikepdf.open(str(dst)) as pdf:
            row["outline_gone"] = "/Outlines" not in pdf.Root
            row["thumbs_gone"] = "/Thumb" not in pdf.pages[0].obj
            row["attach_gone"] = len(pdf.attachments.keys()) == 0
    return row


def check_ccitt_roundtrip() -> list[str]:
    """单元检查：CCITT G4 码流必须能被渲染器逐像素还原（含黑白极性）。"""
    from engine.imageopt import _ccitt_encode

    probe = pymupdf.open()
    page = probe.new_page(width=300, height=200)
    for i in range(6):
        page.insert_text((12, 40 + i * 24), "CCITT G4 roundtrip 0123456789 abcdef", fontsize=11)
    pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    probe.close()
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    ink = gray < 170
    im1 = Image.fromarray(np.where(ink, 0, 255).astype("uint8")).convert("1")

    encoded = _ccitt_encode(im1)
    if encoded is None:
        return ["CCITT 编码失败"]
    data, extra, _decoded = encoded

    pdf = pikepdf.new()
    p = pikepdf.Page(pdf.add_blank_page(page_size=(im1.width, im1.height)))
    img = pikepdf.Stream(pdf, data, pikepdf.Dictionary({
        "/Type": pikepdf.Name("/XObject"), "/Subtype": pikepdf.Name("/Image"),
        "/Width": im1.width, "/Height": im1.height,
        "/ColorSpace": pikepdf.Name("/DeviceGray"),
        "/BitsPerComponent": 1,
        "/Filter": pikepdf.Name("/CCITTFaxDecode"),
        "/DecodeParms": extra["decode_parms"],
    }))
    p.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im0": img})})
    p.Contents = pdf.make_stream(
        f"q {im1.width} 0 0 {im1.height} 0 0 cm /Im0 Do Q".encode())
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()

    doc = pymupdf.open("pdf", buf.getvalue())
    render = np.frombuffer(doc[0].get_pixmap(dpi=72, colorspace=pymupdf.csGRAY).samples,
                           np.uint8).reshape(im1.height, im1.width)
    doc.close()
    rendered_ink = render < 128
    if not np.array_equal(rendered_ink, ink):
        return [f"CCITT 渲染不一致（{int((rendered_ink != ink).sum())} 像素）"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF 压缩器自测")
    parser.add_argument("--all", action="store_true", help="跑全部预设")
    parser.add_argument("--keep", action="store_true", help="保留测试文件")
    parser.add_argument("--only", default="", help="只跑指定样例（逗号分隔）")
    args = parser.parse_args()

    if TESTDATA.exists() and not args.keep:
        shutil.rmtree(TESTDATA, ignore_errors=True)
    out_dir = TESTDATA / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    presets = list(PRESET_ORDER) if args.all else ["smallest", "standard", "high"]
    selected = [n for n in FIXTURES if not args.only or n in args.only.split(",")]

    print("生成测试文件…")
    sources: dict[str, Path] = {}
    for name in selected:
        builder, _label = FIXTURES[name]
        path = TESTDATA / f"{name}.pdf"
        if not path.exists() or not args.keep:
            builder(path)
        sources[name] = path
        profile = analyze(path)
        print(f"  {name:9s} {profile.size / 1024 / 1024:7.2f} MB  "
              f"{profile.pages:3d} 页  {len(profile.images):3d} 图  "
              f"{len(profile.fonts):2d} 字体  {FIXTURES[name][1]}")

    print("\n开始压缩…\n")
    header = f"{'样例':<9}{'预设':<11}{'原始':>10}{'压缩后':>10}{'减少':>8}{'PSNR':>7}{'耗时':>7}  备注"
    print(header)
    print("-" * len(header))

    rows = []
    failures = list(check_ccitt_roundtrip())
    print("单元检查：CCITT G4 " + ("❌ " + failures[0] if failures else "码流可逐像素还原 ✓") + "\n")
    for name in selected:
        for preset in presets:
            row = run_case(name, preset, sources[name], out_dir)
            rows.append(row)
            note = ""
            if not row["ok"]:
                note = row["error"]
            elif row.get("kept_original"):
                note = "已是最优（输出原文件）"
            elif not row.get("pages_ok", True):
                note = "页数不一致"
            elif abs(row["ratio"]) < 0.02:
                note = "体积基本不变"
            print(f"{name:<9}{preset:<11}{row['before'] / 1024:>9.0f}K"
                  f"{row['after'] / 1024:>9.0f}K{row['ratio'] * 100:>7.1f}%"
                  f"{row['psnr']:>7.1f}{row['seconds']:>6.1f}s  {note}")

            # ---------------- 断言 ----------------
            if not row["ok"]:
                failures.append(f"{name}/{preset}: {row['error']}")
                continue
            if not row.get("pages_ok", True):
                failures.append(f"{name}/{preset}: 页数不一致")
            if row.get("text_ratio", 1.0) < 0.9:
                failures.append(f"{name}/{preset}: 文字丢失（保留 {row['text_ratio'] * 100:.0f}%）")
            if row["ratio"] < 0.02 and name not in ("bilevel", "decode") and preset != "print":
                failures.append(f"{name}/{preset}: 几乎没有压缩")
            minimum = {"smallest": 22.0, "standard": 27.0, "high": 31.0, "print": 33.0}[preset]
            if row["psnr"] < minimum and not row.get("kept_original"):
                failures.append(f"{name}/{preset}: 画质过低 PSNR {row['psnr']:.1f} < {minimum}")
            if name == "mixed" and preset == "smallest":
                if not (row.get("outline_gone") and row.get("thumbs_gone") and row.get("attach_gone")):
                    failures.append("mixed/smallest: 书签/缩略图/附件未被清理")

    total_before = sum(r["before"] for r in rows if r["ok"])
    total_after = sum(r["after"] for r in rows if r["ok"])
    print("-" * len(header))
    if total_before:
        print(f"{'合计':<9}{'':<11}{total_before / 1024:>9.0f}K{total_after / 1024:>9.0f}K"
              f"{(1 - total_after / total_before) * 100:>7.1f}%")

    print()
    if failures:
        print(f"❌ {len(failures)} 项未通过：")
        for item in failures:
            print("   -", item)
        if not args.keep:
            print(f"\n测试文件保留在 {TESTDATA}（加 --keep 可避免删除）")
        return 1
    print(f"✅ 全部通过（{len(rows)} 个组合）")
    if not args.keep:
        shutil.rmtree(TESTDATA, ignore_errors=True)
        print("测试文件已清理（加 --keep 可保留）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
