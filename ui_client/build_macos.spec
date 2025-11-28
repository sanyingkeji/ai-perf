# -*- mode: python ; coding: utf-8 -*-
# macOS 打包配置

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('themes', 'themes'),
        ('resources', 'resources'),
        ('config.json', '.'),
        ('google_client_secret.json', '.'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'httpx',
        'google.oauth2',
        'google.auth',
        # PyObjC 相关模块（用于隐藏/显示 Dock 图标）
        'AppKit',
        'AppKit.NSApplication',
        'objc',
        'objc._objc',
        'Foundation',
    ],
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
    name='Ai Perf Client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Ai Perf Client',
)

app = BUNDLE(
    coll,
    name='Ai Perf Client.app',
    icon='resources/app_icon.icns' if os.path.exists('resources/app_icon.icns') else None,
    bundle_identifier='site.sanying.aiperf.client',
    version='1.0.0',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion': '10.13',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        # 不设置 LSUIElement，让应用在 Dock 显示
        # 关闭窗口后通过代码动态隐藏 Dock 图标
    },
)

