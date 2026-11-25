# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：python -m PyInstaller PDFCompressor.spec --noconfirm

顺手从 engine/__init__.py 读版本号，生成 Windows 文件属性里的版本资源。
"""

import re
from pathlib import Path

_version_text = Path("engine/__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _version_text).group(1)
_parts = [int(part) for part in VERSION.split(".")][:4]
_parts += [0] * (4 - len(_parts))
_ver_tuple = ", ".join(str(part) for part in _parts)

_version_file = Path("build/version_info.txt")
_version_file.parent.mkdir(parents=True, exist_ok=True)
_version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({_ver_tuple}),
    prodvers=({_ver_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404B0',
          [StringStruct('CompanyName', 'PDFCompressor'),
           StringStruct('FileDescription', 'PDF Compressor'),
           StringStruct('FileVersion', '{VERSION}'),
           StringStruct('InternalName', 'PDFCompressor'),
           StringStruct('LegalCopyright', 'MIT License'),
           StringStruct('OriginalFilename', 'PDFCompressor.exe'),
           StringStruct('ProductName', 'PDF Compressor'),
           StringStruct('ProductVersion', '{VERSION}')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
    encoding="utf-8",
)

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
    name="PDFCompressor",
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
    icon="assets/icon.ico",
    version=str(_version_file),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDFCompressor",
)
