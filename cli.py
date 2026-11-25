"""命令行入口：批量压缩 PDF。

示例：
    python cli.py 报告.pdf
    python cli.py 扫描件1.pdf 扫描件2.pdf -o D:\\out --preset smallest
    python cli.py 资料/ --recursive -o out --preset standard --strip metadata,thumbnails
    python cli.py 报告.pdf --dry-run          # 只体检，不写文件
    python cli.py 报告.pdf --json             # 机器可读摘要
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine import (  # noqa: E402
    PRESET_LABELS, PRESET_ORDER, __version__, analyze, compress_file,
    default_output_path, get_preset, human_size,
)
from engine.settings import Settings  # noqa: E402
from engine.utils import DISCLAIMER_LINE, CancelToken, unique_output_path  # noqa: E402

STRIP_KEYS = {
    "metadata": "strip_metadata",
    "thumbnails": "strip_thumbnails",
    "attachments": "strip_attachments",
    "outlines": "strip_outlines",
    "annotations": "strip_annotations",
    "structure": "strip_structure",
    "page-labels": "strip_page_labels",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfcomp",
        description="PDF 压缩器（本地批量压缩）",
    )
    parser.add_argument("inputs", nargs="+", help="PDF 文件或目录")
    parser.add_argument("-o", "--output", help="输出文件或输出目录（默认与源文件同目录）")
    parser.add_argument("--preset", choices=PRESET_ORDER, default="standard",
                        help="压缩预设（默认 standard）")
    parser.add_argument("--suffix", help="输出文件名后缀（默认取预设，标准为 -压缩）")
    parser.add_argument("--recursive", action="store_true", help="目录递归查找 PDF")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出文件")

    g = parser.add_argument_group("图像")
    g.add_argument("--dpi", type=int, help="同时设置彩色和灰度图目标 DPI")
    g.add_argument("--color-dpi", type=int)
    g.add_argument("--gray-dpi", type=int)
    g.add_argument("--mono-dpi", type=int)
    g.add_argument("--quality", type=int, help="JPEG 品质 1-100（彩色/灰度）")
    g.add_argument("--gray", action="store_true", help="彩色图像转灰度")
    g.add_argument("--to-rgb", action="store_true", help="CMYK 图像转 RGB（颜色可能轻微偏移）")
    g.add_argument("--downsample", choices=("bicubic", "average", "subsample"))

    g = parser.add_argument_group("字体与结构")
    g.add_argument("--no-fonts", action="store_true", help="关闭字体子集化")
    g.add_argument("--unembed-base14", action="store_true", help="取消嵌入标准 14 字体")
    g.add_argument("--no-object-streams", action="store_true", help="不使用对象流压缩")
    g.add_argument("--linearize", action="store_true", help="线性化（快速 Web 查看）")
    g.add_argument("--keep-everything", action="store_true",
                   help="保留书签/注释/附件/元数据/缩略图/结构树")
    g.add_argument("--strip", default="", help="要放弃的对象，逗号分隔："
                   + ",".join(STRIP_KEYS))
    g.add_argument("--decrypt", action="store_true", help="允许对受保护的 PDF 解密后压缩")

    parser.add_argument("--dry-run", action="store_true", help="只体检并给出预估，不写文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出结果表格")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出每个文件的进度")
    parser.add_argument("--version", action="version", version=f"PDFCompressor {__version__}")
    return parser


def collect_inputs(paths: list[str], recursive: bool) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            found.extend(sorted(p for p in path.glob(pattern) if p.is_file()))
        elif path.is_file():
            found.append(path)
    # 去重且保持顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = get_preset(args.preset)
    if args.color_dpi:
        settings.color_dpi = args.color_dpi
    if args.gray_dpi:
        settings.gray_dpi = args.gray_dpi
    if args.dpi:
        settings.color_dpi = settings.gray_dpi = args.dpi
    if args.mono_dpi:
        settings.mono_dpi = args.mono_dpi
    if args.quality:
        settings.color_quality = settings.gray_quality = max(1, min(100, args.quality))
    if args.downsample:
        settings.downsample = args.downsample
    if args.gray:
        settings.convert_gray = True
    if args.to_rgb:
        settings.cmyk_mode = "rgb"
    if args.no_fonts:
        settings.subset_fonts = False
    if args.unembed_base14:
        settings.unembed_base14 = True
    if args.no_object_streams:
        settings.compress_object_streams = False
    if args.linearize:
        settings.linearize = True
    if args.keep_everything:
        settings.strip_metadata = False
        settings.strip_thumbnails = False
        settings.strip_attachments = False
        settings.strip_outlines = False
        settings.strip_annotations = False
        settings.strip_structure = False
        settings.strip_page_labels = False
    if args.strip:
        names = [n.strip().lower() for n in args.strip.split(",") if n.strip()]
        if "all" in names:
            for key in STRIP_KEYS.values():
                setattr(settings, key, True)
        for name in names:
            key = STRIP_KEYS.get(name)
            if key:
                setattr(settings, key, True)
            elif name:
                raise SystemExit(f"未知的 --strip 项：{name}")
    if args.decrypt:
        settings.allow_decrypt = True
    if args.suffix:
        settings.output_suffix = args.suffix
    return settings


def output_for(src: Path, settings: Settings, args: argparse.Namespace) -> Path:
    if args.output:
        target = Path(args.output)
        if target.is_dir() or str(args.output).endswith(("/", "\\")):
            target.mkdir(parents=True, exist_ok=True)
            candidate = target / default_output_path(src, settings).name
        else:
            candidate = target
    else:
        candidate = default_output_path(src, settings)
    if candidate.exists() and not args.overwrite:
        candidate = unique_output_path(candidate)
    if candidate.resolve() == src.resolve():
        candidate = unique_output_path(candidate)
    return candidate


def print_profile(path: Path, settings: Settings) -> None:
    profile = analyze(path)
    print(f"\n{path.name}")
    print(f"  大小 {human_size(profile.size)} · {profile.pages} 页 · "
          f"{profile.version or '未知版本'} · "
          f"{'像扫描件' if profile.scanned else '文字/矢量为主'}")
    for label, size, note in profile.breakdown():
        share = size / profile.size * 100 if profile.size else 0
        print(f"  {label:<10}{human_size(size):>10}  {share:5.1f}%  {note}")
    if profile.images:
        print("  最大的图：")
        for image in profile.biggest_images[:5]:
            print(f"    {image.width}×{image.height} {image.kind:<5} "
                  f"{human_size(image.size):>9}  有效 {image.dpi:.0f} DPI  {image.filter}")
    print(f"  预计：彩色/灰度 {settings.color_dpi} DPI · JPEG {settings.color_quality} · "
          f"单色 {settings.mono_dpi} DPI")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(DISCLAIMER_LINE, file=sys.stderr)
    except Exception:
        pass
    try:
        settings = settings_from_args(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    inputs = collect_inputs(args.inputs, args.recursive)
    if not inputs:
        print("没有找到 PDF 文件", file=sys.stderr)
        return 2

    if args.dry_run:
        for path in inputs:
            print_profile(path, settings)
            if len(inputs) > 1:
                print()
        return 0

    if not args.quiet:
        print(f"预设 {PRESET_LABELS[args.preset]} · 共 {len(inputs)} 个文件\n")

    cancel = CancelToken()
    results = []
    started = time.time()
    try:
        for index, src in enumerate(inputs, 1):
            dst = output_for(src, settings, args)
            prefix = f"[{index}/{len(inputs)}] {src.name}"
            if not args.quiet:
                print(prefix, flush=True)

            def progress(fraction: float, text: str = "", _prefix=prefix) -> None:
                if args.verbose:
                    print(f"\r  {fraction * 100:5.1f}%  {text:<40}", end="", flush=True)

            result = compress_file(src, dst, settings, progress=progress, cancel=cancel)
            if args.verbose:
                print("\r" + " " * 70 + "\r", end="")
            results.append(result)
            if args.quiet:
                continue
            if result.ok:
                print(f"    {human_size(result.orig_size)} → {human_size(result.new_size)}"
                      f"  ({result.ratio * 100:.1f}% 更小)"
                      f"{'  [已是原文件副本]' if result.kept_original else ''}")
                if args.verbose:
                    for row in result.detail_rows():
                        print(f"      {row[0]}：{row[1]}")
            else:
                print(f"    失败：{result.error}")
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n已中断")

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    total_before = sum(r.orig_size for r in ok)
    total_after = sum(r.new_size for r in ok)
    elapsed = time.time() - started

    print("\n" + "-" * 62)
    if ok and total_before:
        print(f"合计：{human_size(total_before)} → {human_size(total_after)}"
              f"  减少 {(1 - total_after / total_before) * 100:.1f}%"
              f"  用时 {elapsed:.1f} 秒")
        if len(ok) == 1:
            print(f"输出：{ok[0].dst}")
        else:
            print(f"输出目录：{Path(ok[0].dst).parent}")
    if failed:
        print(f"失败 {len(failed)} 个：")
        for result in failed:
            print(f"  - {result.src.name}: {result.error}")

    if args.json:
        payload = {
            "ok": len(ok),
            "failed": len(failed),
            "elapsed": round(elapsed, 2),
            "total_before": total_before,
            "total_after": total_after,
            "files": [
                {
                    "src": str(r.src),
                    "dst": str(r.dst) if r.dst else None,
                    "ok": r.ok,
                    "error": r.error,
                    "orig_size": r.orig_size,
                    "new_size": r.new_size,
                    "ratio": round(r.ratio, 4),
                    "kept_original": r.kept_original,
                    "psnr": round(r.verify.psnr, 2) if r.verify else None,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
