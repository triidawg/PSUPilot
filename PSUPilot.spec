# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('drivers', 'drivers'), ('C:/Users/Janne/AppData/Local/Programs/Python/Python314/Lib/site-packages/customtkinter', 'customtkinter')],
    hiddenimports=['pyvisa_py', 'pyvisa_py.highlevel', 'pyvisa_py.sessions', 'pyvisa_py.serial', 'pyvisa_py.tcpip', 'pyvisa_py.usb', 'pyvisa_py.gpib', 'pyvisa_py.protocols', 'serial', 'serial.serialwin32'],
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
    name='PSUPilot',
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
)
