"""图像重压缩：降采样 + JPEG/CCITT/ZIP 重编码。

分三个动作：
  1. 缩减像素采样（双立方 / 平均 / 次采样），只有超过目标 1.5 倍才降；
  2. 重新压缩（JPEG / ZIP / CCITT G4），并有体积护栏（不变大才替换）；
  3. 画质闸门：算 PSNR，太差就提高品质重来，仍不达标则保留原图。
"""

from __future__ import annotations

import io
import math
import zlib
from dataclasses import dataclass, field

import numpy as np
import pikepdf
from PIL import Image

from .analyze import ImageEntry, PdfProfile
from .settings import Settings
from .utils import CancelToken, Cancelled

Image.MAX_IMAGE_PIXELS = None  # 扫描件常见超大图，交给内存而不是 Pillow 的警告

RESAMPLE = {
    "bicubic": Image.BICUBIC,
    "average": Image.BOX,
    "subsample": Image.NEAREST,
}


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------

@dataclass
class ImageDecision:
    entry: ImageEntry
    kind: str
    action: str          # "jpeg" | "mono" | "skip"
    new_w: int
    new_h: int
    quality: int
    scale: float = 1.0
    reason: str = ""


def plan_images(profile: PdfProfile, settings: Settings) -> list[ImageDecision]:
    """为每个图像决定：降不降、降到多少、用什么品质。"""
    plans: list[ImageDecision] = []
    for entry in profile.images:
        kind = entry.kind if entry.kind in ("color", "gray", "mono") else "color"
        target = max(24, settings.dpi_for(kind))
        dpi = entry.dpi if entry.dpi > 1 else 72.0
        scale = 1.0
        if dpi > target * max(1.0, settings.downsample_trigger):
            scale = target / dpi
        new_w = max(1, int(round(entry.width * scale)))
        new_h = max(1, int(round(entry.height * scale)))

        if entry.has_mask:
            plans.append(ImageDecision(entry, kind, "skip", entry.width, entry.height,
                                       settings.quality_for(kind), 1.0, "带色键蒙版，跳过"))
            continue
        if entry.filter in ("JBIG2Decode",):
            plans.append(ImageDecision(entry, kind, "skip", entry.width, entry.height,
                                       settings.quality_for(kind), 1.0, "JBIG2 编码，跳过"))
            continue
        if scale >= 1.0 and entry.size < settings.min_image_bytes:
            plans.append(ImageDecision(entry, kind, "skip", entry.width, entry.height,
                                       settings.quality_for(kind), 1.0, "体积过小"))
            continue
        if new_w < 16 or new_h < 16:
            plans.append(ImageDecision(entry, kind, "skip", entry.width, entry.height,
                                       settings.quality_for(kind), 1.0, "尺寸过小"))
            continue

        if kind == "mono":
            action = "mono"
            quality = 100
        else:
            action = "jpeg"
            quality = int(settings.quality_for(kind))
            if settings.convert_gray and kind == "color":
                kind = "gray"
                quality = int(settings.gray_quality)
        plans.append(ImageDecision(entry, kind, action, new_w, new_h, quality, scale,
                                   "降采样" if scale < 1.0 else "重压缩"))
    return plans


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------

@dataclass
class ImageRecord:
    xref: int
    kind: str
    before: int
    after: int
    width: int
    height: int
    new_w: int
    new_h: int
    quality: int
    psnr: float = 0.0
    note: str = ""


@dataclass
class ImageStats:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    before: int = 0
    after: int = 0
    psnr_min: float = 99.0
    psnr_sum: float = 0.0
    psnr_count: int = 0
    records: list[ImageRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return max(0, self.before - self.after)

    @property
    def psnr_avg(self) -> float:
        return self.psnr_sum / self.psnr_count if self.psnr_count else 0.0


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def optimize_images(
    pdf: pikepdf.Pdf,
    plans: list[ImageDecision],
    settings: Settings,
    progress=None,
    cancel: CancelToken | None = None,
) -> ImageStats:
    stats = ImageStats(total=len(plans))
    by_num = _objects_by_number(pdf)
    index = 0
    total = max(1, len(plans))

    for plan in plans:
        index += 1
        if cancel:
            cancel.check()
        if progress:
            progress(index / total, f"图像 {index}/{total}")
        entry = plan.entry
        stats.before += entry.size

        if plan.action == "skip":
            stats.skipped += 1
            stats.after += entry.size
            continue

        obj = by_num.get(entry.xref)
        if obj is None or not isinstance(obj, pikepdf.Stream):
            stats.skipped += 1
            stats.after += entry.size
            continue
        if str(obj.get("/Subtype", "")) != "/Image":
            stats.skipped += 1
            stats.after += entry.size
            continue
        if obj.get("/Mask") is not None and obj.get("/SMask") is None:
            stats.skipped += 1
            stats.after += entry.size
            continue

        try:
            old_raw = len(obj.read_raw_bytes())
        except Exception:
            old_raw = entry.size

        try:
            record, data, extra = _recompress(obj, plan, settings)
        except Cancelled:
            raise
        except Exception as exc:  # 单张图失败不影响整篇
            stats.failed += 1
            stats.skipped += 1
            stats.after += old_raw
            stats.warnings.append(f"图像 #{entry.xref} 处理失败：{exc}")
            continue

        if record is None:  # 放弃替换
            stats.skipped += 1
            stats.after += old_raw
            continue

        new_size = len(data)
        if new_size >= old_raw * settings.size_guard:
            stats.skipped += 1
            stats.after += old_raw
            stats.records.append(ImageRecord(
                entry.xref, plan.kind, old_raw, new_size, plan.entry.width, plan.entry.height,
                record["w"], record["h"], plan.quality, 0.0, "收益不足，保留原图"))
            continue

        try:
            _apply_stream(obj, data, extra, record["w"], record["h"], record["colorspace"], record["bpc"])
            if record.get("smask_bytes"):
                _replace_smask(pdf, obj, record)
        except Exception as exc:
            stats.failed += 1
            stats.skipped += 1
            stats.after += old_raw
            stats.warnings.append(f"图像 #{entry.xref} 写回失败：{exc}")
            continue

        stats.processed += 1
        stats.after += new_size
        psnr = record.get("psnr", 0.0)
        if psnr > 0:
            stats.psnr_min = min(stats.psnr_min, psnr)
            stats.psnr_sum += psnr
            stats.psnr_count += 1
        stats.records.append(ImageRecord(
            entry.xref, plan.kind, old_raw, new_size, plan.entry.width, plan.entry.height,
            record["w"], record["h"], plan.quality, psnr, record.get("note", "")))

    return stats


def _objects_by_number(pdf: pikepdf.Pdf) -> dict[int, pikepdf.Object]:
    mapping: dict[int, pikepdf.Object] = {}
    try:
        for obj in pdf.objects:
            try:
                num, _gen = obj.objgen
            except Exception:
                continue
            if num > 0 and num not in mapping and isinstance(obj, pikepdf.Stream):
                mapping[num] = obj
    except Exception:
        pass
    return mapping


def _apply_stream(obj, data, extra, width, height, colorspace, bpc) -> None:
    obj.write(data, filter=pikepdf.Name(extra["filter"]),
              decode_parms=extra.get("decode_parms"))
    obj.Width = int(width)
    obj.Height = int(height)
    obj.BitsPerComponent = int(bpc)
    if colorspace:
        obj.ColorSpace = pikepdf.Name(colorspace)
    for key in ("/DecodeParms", "/Decode", "/SMaskInData"):
        if key == "/DecodeParms" and extra.get("decode_parms") is not None:
            continue
        if key in obj:
            del obj[key]


def _replace_smask(pdf, obj, record) -> None:
    w, h = record["w"], record["h"]
    mask_bytes = record["smask_bytes"]
    mask = pikepdf.Stream(pdf, mask_bytes, pikepdf.Dictionary({
        "/Type": pikepdf.Name("/XObject"),
        "/Subtype": pikepdf.Name("/Image"),
        "/Width": w,
        "/Height": h,
        "/ColorSpace": pikepdf.Name("/DeviceGray"),
        "/BitsPerComponent": 8,
        "/Filter": pikepdf.Name("/FlateDecode"),
    }))
    obj.SMask = pdf.make_indirect(mask)


# ---------------------------------------------------------------------------
# 单张图：解码 → 降采样 → 编码 → 校验
# ---------------------------------------------------------------------------

def _recompress(obj: pikepdf.Stream, plan: ImageDecision, settings: Settings) -> tuple[dict | None, bytes, dict]:
    pim = pikepdf.PdfImage(obj)
    raw_w, raw_h = pim.width, pim.height
    pil = pim.as_pil_image()
    if pil.mode not in ("1", "L", "RGB", "CMYK", "P", "LA", "RGBA"):
        pil = pil.convert("RGB")

    filter_names = {str(f) for f in (pim.filters or [])}
    cmyk_jpeg = pil.mode == "CMYK" and "/DCTDecode" in filter_names
    if cmyk_jpeg and obj.get("/Decode") is not None:
        return None, b"", {}          # CMYK 反相叠加 /Decode，风险大，跳过

    # 视觉颜色：PDF 里的 CMYK JPEG 是按反相存储的；/Decode 由 pikepdf 解码时已处理
    visual = _invert(pil) if cmyk_jpeg else pil

    kind = plan.kind if plan.kind == "mono" else _refine_kind(visual)
    if plan.kind == "mono" and kind == "color":
        kind = "color"  # 索引彩色图不按单色处理
    if settings.convert_gray and kind == "color":
        kind = "gray"

    # 输出目标：保留 CMYK 时沿用文件里的存储值，颜色不会漂
    keep_cmyk = cmyk_jpeg and settings.cmyk_mode == "keep" and not settings.convert_gray
    if keep_cmyk:
        target_mode, pixels = "cmyk", pil
    elif kind == "mono":
        target_mode, pixels = "mono", visual
    elif kind == "gray":
        target_mode, pixels = "gray", visual
    else:
        target_mode, pixels = "rgb", visual

    # 依据「实际类型」重算目标分辨率与品质
    target = max(24, settings.dpi_for(kind))
    dpi = plan.entry.dpi if plan.entry.dpi > 1 else 72.0
    scale = 1.0
    if dpi > target * max(1.0, settings.downsample_trigger):
        scale = target / dpi
    new_w = max(1, min(raw_w, int(round(raw_w * scale))))
    new_h = max(1, min(raw_h, int(round(raw_h * scale))))
    if scale >= 1.0:
        new_w, new_h = raw_w, raw_h

    quality = int(settings.quality_for(kind)) if target_mode != "mono" else 100

    best: tuple[bytes, dict, dict] | None = None

    for _attempt in range(3):
        work = _downsample(pixels, new_w, new_h, target_mode, settings)
        data, extra, decoded = _encode(work, target_mode, quality, settings)
        psnr = _psnr(work, decoded, target_mode)
        best = (data, extra, {"psnr": psnr, "w": new_w, "h": new_h,
                              "colorspace": extra["colorspace"], "bpc": extra["bpc"],
                              "note": extra.get("note", "")})
        if psnr >= settings.min_psnr or target_mode == "mono":
            break
        if quality >= 95:
            break
        quality = min(95, quality + 15)

    if best is None:
        return None, b"", {}

    data, extra, record_out = best
    if target_mode != "mono" and record_out["psnr"] < settings.min_psnr * 0.92:
        # 画质明显不达标：放弃替换，保留原图
        return None, b"", {}

    if obj.get("/SMask") is not None and (new_w, new_h) != (raw_w, raw_h):
        mask_bytes = _resized_smask(obj, new_w, new_h, settings)
        if mask_bytes is None:
            return None, b"", {}
        record_out["smask_bytes"] = mask_bytes

    return record_out, data, extra


def _downsample(pil: Image.Image, w: int, h: int, mode: str, settings: Settings) -> Image.Image:
    if (pil.width, pil.height) == (w, h):
        return pil
    method = RESAMPLE.get(settings.downsample, Image.BICUBIC)
    if mode == "mono":
        gray = pil.convert("L")
        small = gray.resize((w, h), Image.BOX if method != Image.NEAREST else Image.NEAREST)
        return small.point(lambda v: 255 if v >= 128 else 0, mode="1")
    if mode == "gray":
        return pil.convert("L").resize((w, h), method)
    if mode == "cmyk":
        return pil.convert("CMYK").resize((w, h), method)
    return pil.convert("RGB").resize((w, h), method)


def _encode(pil: Image.Image, mode: str, quality: int, settings: Settings) -> tuple[bytes, dict, Image.Image]:
    if mode == "mono":
        return _encode_mono(pil, settings)
    if mode == "gray":
        buf = io.BytesIO()
        pil.convert("L").save(buf, "JPEG", quality=quality, optimize=True, progressive=False)
        data = buf.getvalue()
        extra = {"filter": "/DCTDecode", "decode_parms": None,
                 "colorspace": "/DeviceGray", "bpc": 8}
        return data, extra, _open_jpeg(data, "L")
    if mode == "cmyk":
        # 按 PDF 约定原样存储（PIL 读写 CMYK JPEG 是自洽的），渲染结果不变
        buf = io.BytesIO()
        pil.convert("CMYK").save(buf, "JPEG", quality=quality, optimize=True,
                                 progressive=False, subsampling=0)
        data = buf.getvalue()
        extra = {"filter": "/DCTDecode", "decode_parms": None,
                 "colorspace": "/DeviceCMYK", "bpc": 8, "note": "CMYK 保留"}
        return data, extra, _open_jpeg(data, "CMYK")
    buf = io.BytesIO()
    subsampling = 0 if quality >= 75 else 2
    pil.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True,
                            progressive=False, subsampling=subsampling)
    data = buf.getvalue()
    extra = {"filter": "/DCTDecode", "decode_parms": None,
             "colorspace": "/DeviceRGB", "bpc": 8}
    return data, extra, _open_jpeg(data, "RGB")


def _encode_mono(pil: Image.Image, settings: Settings) -> tuple[bytes, dict, Image.Image]:
    im1 = pil if pil.mode == "1" else pil.convert("L").point(lambda v: 255 if v >= 128 else 0, mode="1")
    w, h = im1.size

    candidates: list[tuple[bytes, dict, Image.Image]] = []
    if settings.mono_codec in ("auto", "ccitt"):
        ccitt = _ccitt_encode(im1)
        if ccitt is not None:
            data, extra, decoded = ccitt
            candidates.append((data, extra, decoded))
    if settings.mono_codec in ("auto", "zip") or not candidates:
        data = zlib.compress(_pack_bilevel(im1), 9)
        extra = {"filter": "/FlateDecode", "decode_parms": None,
                 "colorspace": "/DeviceGray", "bpc": 1, "note": "ZIP"}
        candidates.append((data, extra, im1.copy()))

    data, extra, decoded = min(candidates, key=lambda item: len(item[0]))
    extra["note"] = "CCITT G4" if extra["filter"] == "/CCITTFaxDecode" else "ZIP"
    return data, extra, decoded


def _ccitt_encode(im1: Image.Image):
    """产出 CCITT G4 码流（PDF 单色扫描件的主流压缩）。

    借 Pillow 自带的 PDF 写出器做 G4 编码：它输出的是标准 CCITT 流，
    再自己核对解码结果，确保黑白极性没弄反。
    """
    try:
        buf = io.BytesIO()
        im1.save(buf, format="PDF", resolution=72.0)
        data = None
        with pikepdf.open(io.BytesIO(buf.getvalue())) as probe:
            for obj in probe.objects:
                if isinstance(obj, pikepdf.Stream) and str(obj.get("/Subtype", "")) == "/Image":
                    data = obj.read_raw_bytes()
                    break
        if not data:
            return None
        expected = np.asarray(im1)
        for black_is_1 in (True, False):
            extra = {
                "filter": "/CCITTFaxDecode",
                "decode_parms": pikepdf.Dictionary({
                    "/K": -1,
                    "/Columns": im1.width,
                    "/Rows": im1.height,
                    "/BlackIs1": black_is_1,
                }),
                "colorspace": "/DeviceGray", "bpc": 1,
            }
            decoded = _decode_ccitt(data, im1, black_is_1)
            if decoded is not None and np.array_equal(decoded, expected):
                return data, extra, im1.copy()
    except Exception:
        return None
    return None


def _decode_ccitt(data: bytes, im1: Image.Image, black_is_1: bool):
    """用 pikepdf 解一遍，验证码流与极性。"""
    try:
        pdf = pikepdf.new()
        stream = pikepdf.Stream(pdf, data, pikepdf.Dictionary({
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": im1.width,
            "/Height": im1.height,
            "/ColorSpace": pikepdf.Name("/DeviceGray"),
            "/BitsPerComponent": 1,
            "/Filter": pikepdf.Name("/CCITTFaxDecode"),
            "/DecodeParms": pikepdf.Dictionary({
                "/K": -1, "/Columns": im1.width, "/Rows": im1.height,
                "/BlackIs1": black_is_1,
            }),
        }))
        return np.asarray(pikepdf.PdfImage(stream).as_pil_image().convert("1"))
    except Exception:
        return None


def _pack_bilevel(im1: Image.Image) -> bytes:
    arr = np.asarray(im1, dtype=np.uint8)
    bits = (arr > 0).astype(np.uint8)
    return np.packbits(bits, axis=1).tobytes()


def _refine_kind(pil: Image.Image) -> str:
    if pil.mode == "1":
        return "mono"
    if pil.mode == "P":
        try:
            colors = pil.convert("RGB").getcolors(maxcolors=64) or []
        except Exception:
            colors = []
        if len(colors) <= 2:
            values = np.array([c for _n, c in colors]) if colors else np.zeros((1, 3))
            if values.min() >= 200 or values.max() <= 40:
                return "mono"
        return "color"
    if pil.mode in ("L", "LA", "I", "I;16", "F"):
        return "gray"
    if pil.mode == "CMYK":
        return "color"
    return "color"


def _invert(pil: Image.Image) -> Image.Image:
    if pil.mode == "1":
        return pil.point(lambda v: 0 if v else 255, mode="1")
    if pil.mode == "L":
        return pil.point(lambda v: 255 - v)
    if pil.mode == "RGB":
        return Image.fromarray(255 - np.asarray(pil, dtype=np.uint8), mode="RGB")
    if pil.mode == "CMYK":
        return Image.fromarray(255 - np.asarray(pil, dtype=np.uint8), mode="CMYK")
    return pil


def _open_jpeg(data: bytes, mode: str) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.convert(mode)


def _psnr(ref: Image.Image, got: Image.Image, mode_name: str) -> float:
    mode = {"gray": "L", "mono": "L", "cmyk": "CMYK"}.get(mode_name, "RGB")
    a = np.asarray(ref.convert(mode), dtype=np.float32)
    b = np.asarray(got.convert(mode), dtype=np.float32)
    if a.shape != b.shape:
        return 0.0
    mse = float(np.mean((a - b) ** 2))
    if mse <= 1e-9:
        return 99.0
    return float(10.0 * math.log10(255.0 ** 2 / mse))


def _resized_smask(obj, w: int, h: int, settings: Settings) -> bytes | None:
    try:
        smask = obj.get("/SMask")
        pil = pikepdf.PdfImage(smask).as_pil_image().convert("L")
        pil = pil.resize((w, h), Image.BOX)
        return zlib.compress(pil.tobytes(), 6)
    except Exception:
        return None


__all__ = ["plan_images", "optimize_images", "ImageDecision", "ImageStats", "ImageRecord"]
