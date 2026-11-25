# -*- mode: python ; coding: utf-8 -*-
"""macOS 打包配置：bash build-macos.sh 会调用它。

产物：dist/PDFCompressor.app
"""

from pathlib import Path
import re

APP_NAME = "PDFCompressor"
BUNDLE_ID = "com.pdfcompressor.app"

_version_source = Path("engine/__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _version_source).group(1)

hiddenimports = [
    "fontTools",
    "fontTools.subset",
    "fontTools.ttLib",
    "fontTools.pens",
    "fontTools.pens.pointPen",
    "fontTools.misc",
    "tkinterdnd2",
    "pikepdf",
    "pymupdf",
]

datas = [("assets", "assets"), ("DISCLAIMER.md", ".")]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon="assets/icon.icns",
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": "PDF 压缩器",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleGetInfoString": f"PDF 压缩器 {VERSION}",
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "MIT License",
        "LSUIElement": False,
    },
)
