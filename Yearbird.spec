# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files

# Locate PyQt5 installation
import PyQt5
qt_root = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5")
qt_lib = os.path.join(qt_root, "lib")
qtwe_core = os.path.join(qt_lib, "QtWebEngineCore.framework")

# Paths to QtWebEngine resources
qtwe_resources = os.path.join(qtwe_core, "Resources")
qtwe_locales = os.path.join(qtwe_resources, "qtwebengine_locales")
qtwe_process = os.path.join(qtwe_core, "Helpers", "QtWebEngineProcess.app")

import glob

datas = [
    ("src/guide", "guide"),
    ("src/us-states.json", "."),
    ("src/us-counties-lower48.json", "."),
    ("src/world-countries.json", "."),
    ("src/eBird_BBLCodes.csv", "."),
    ("src/eBird_Taxonomy.csv", "."),
    ("src/ebird_api_ref_location_eBird_list_subnational1.csv", "."),
]

# White toolbar icons (loaded from filesystem at runtime)
for _f in glob.glob("src/icon_*_white.png"):
    datas.append((_f, "."))

# QtWebEngine .pak files and ICU data
for fname in [
    "qtwebengine_resources.pak",
    "qtwebengine_resources_100p.pak",
    "qtwebengine_resources_200p.pak",
    "icudtl.dat",
]:
    datas.append(
        (
            os.path.join(qtwe_resources, fname),
            "PyQt5/Qt5/lib/QtWebEngineCore.framework/Resources",
        )
    )

# Locales directory
datas.append(
    (
        qtwe_locales,
        "PyQt5/Qt5/lib/QtWebEngineCore.framework/Resources/qtwebengine_locales",
    )
)

# QtWebEngineProcess.app
datas.append(
    (
        qtwe_process,
        "PyQt5/Qt5/lib/QtWebEngineCore.framework/Helpers",
    )
)

# PyQt5 data files (plugins, translations, etc.)
datas += collect_data_files("PyQt5")

hiddenimports = [
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtWebEngine",
]

a = Analysis(
    ["src/yearbird.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Yearbird",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Yearbird",
)

app = BUNDLE(
    coll,
    name="Yearbird.app",
    icon="icons/Yearbird.icns",
    bundle_identifier=None,
    codesign_identity=None,
    entitlements_file=None,
)
