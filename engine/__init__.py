"""PDF 压缩内核。

对外只需要两个东西：
    from engine import compress_file, Settings, get_preset
"""

from __future__ import annotations

from .analyze import PdfProfile, analyze, quick_info
from .fonts import FontStage
from .imageopt import ImageDecision, ImageRecord, ImageStats, plan_images
from .pipeline import CompressError, CompressResult, compress_file, default_output_path
from .settings import (
    PRESETS, PRESET_HINTS, PRESET_LABELS, PRESET_ORDER, Settings,
    get_preset, preset_for,
)
from .structure import StructureStats
from .utils import Cancelled, CancelToken, format_delta, human_size
from .verify import VerifyReport, verify

__version__ = "1.0.0"

__all__ = [
    "Settings", "PRESETS", "PRESET_ORDER", "PRESET_LABELS", "PRESET_HINTS",
    "get_preset", "preset_for",
    "compress_file", "default_output_path", "CompressResult", "CompressError",
    "analyze", "quick_info", "PdfProfile", "plan_images", "ImageStats", "ImageDecision",
    "ImageRecord", "FontStage", "StructureStats", "VerifyReport", "verify",
    "CancelToken", "Cancelled", "human_size", "format_delta",
    "__version__",
]
