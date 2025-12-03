# -*- mode: python ; coding: utf-8 -*-
# macOS 打包配置

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 收集所有 encodings 子模块（修复 ModuleNotFoundError）
encodings_modules = collect_submodules('encodings')

# 动态构建 datas 列表，只在文件存在时添加
datas_list = [
    ('themes', 'themes'),
    ('resources', 'resources'),
    ('config.json', '.'),
    ('google_client_secret.json', '.'),
]

# 检查 notification_background_service.py 是否存在
# 使用多种路径尝试，确保在不同环境下都能找到
spec_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
notification_script_paths = [
    os.path.join(spec_dir, '..', 'scripts', 'notification_background_service.py'),
    os.path.normpath(os.path.join(spec_dir, '..', 'scripts', 'notification_background_service.py')),
    '../scripts/notification_background_service.py',
]

notification_script = None
notification_rel_path = None
for path in notification_script_paths:
    # 尝试解析为绝对路径
    if os.path.isabs(path):
        abs_path = path
    else:
        # 相对于 spec 文件所在目录
        abs_path = os.path.normpath(os.path.join(spec_dir, path))
    
    if os.path.exists(abs_path):
        notification_script = abs_path
        # 使用相对路径（相对于当前工作目录，PyInstaller 会从 spec 文件所在目录解析）
        notification_rel_path = '../scripts/notification_background_service.py'
        print(f"找到 notification_background_service.py: {abs_path}")
        break

if notification_script and notification_rel_path:
    datas_list.append((notification_rel_path, 'scripts'))
    print(f"已添加 notification_background_service.py 到打包列表")
else:
    print(f"警告: notification_background_service.py 不存在，跳过")
    print(f"  当前工作目录: {os.getcwd()}")
    print(f"  spec 文件目录: {spec_dir}")
    print(f"  尝试的路径:")
    for path in notification_script_paths:
        abs_path = os.path.normpath(os.path.join(spec_dir, path)) if not os.path.isabs(path) else path
        print(f"    {path} -> {abs_path} (存在: {os.path.exists(abs_path)})")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas_list,
    hiddenimports=[
        # Python 标准库核心模块（确保被包含，修复 encodings 缺失问题）
        'encodings',
        # 使用 collect_submodules 收集所有 encodings 子模块
    ] + encodings_modules + [
        # 其他依赖
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
    target_arch="x86_64",
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
    version='1.0.1',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion': '10.13',
        'CFBundleShortVersionString': '1.0.1',
        'CFBundleVersion': '1.0.1',
        # 不设置 LSUIElement，让应用在 Dock 显示
        # 关闭窗口后通过代码动态隐藏 Dock 图标
    },
)

