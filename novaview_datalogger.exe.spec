# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

streamlit_datas = collect_data_files('streamlit')
streamlit_hiddenimports = collect_submodules('streamlit')
reportlab_datas, reportlab_binaries, reportlab_hiddenimports = collect_all('reportlab')


a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=reportlab_binaries,
    datas=[('app.py', '.'), ('img', 'img'), ('.streamlit', '.streamlit')] + copy_metadata('streamlit') + copy_metadata('reportlab') + streamlit_datas + reportlab_datas,
    hiddenimports=streamlit_hiddenimports + reportlab_hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name='novaview_datalogger.exe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['img/icon.ico'],
)
