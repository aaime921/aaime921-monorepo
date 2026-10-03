# trainiq.spec — Feature 0.1
#
# PyInstaller spec for a windowed macOS .app bundle (Milestone 4 §9,
# Decision Matrix 11.3). Run on a macOS machine with:
#
#     pyinstaller trainiq.spec
#
# IMPORTANT — read this before running it:
#   This spec can only actually be executed on macOS. It cannot be run or
#   verified in this development sandbox (Linux, no PyInstaller macOS
#   toolchain, no Apple code-signing identity). See the "What could not be
#   built or verified here" note in the Epic 0 completion report for the
#   concrete implication: Feature 0.1's own DoD ("a signed, empty .app
#   double-click-launches on a clean macOS machine with no Python
#   installed") is NOT yet verified and requires running this on real
#   hardware.

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["trainiq/app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TrainIQ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # --windowed equivalent
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # set explicitly at build time — see build.sh
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TrainIQ",
)

app = BUNDLE(
    coll,
    name="TrainIQ.app",
    icon=None,  # TODO: add an .icns before any real distribution
    bundle_identifier="com.trainiq.app",
    info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # LSUIElement/background-agent options deliberately left unset here —
        # Epic 0 builds the standard double-click GUI path first, per the
        # roadmap's "double-click -> sync -> recommendation" North Star.
        # The separate LaunchAgent sync-only executable (Tier A Feature 5.2)
        # is out of scope for Epic 0.
    },
)
