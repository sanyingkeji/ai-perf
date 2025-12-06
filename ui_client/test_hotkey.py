#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 macOS 全局快捷键功能
"""
import sys
import platform
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

if platform.system() != "Darwin":
    print("此脚本仅适用于 macOS")
    sys.exit(1)

print("=" * 60)
print("测试 macOS 全局快捷键")
print("=" * 60)

# 1. 检查权限
print("\n1. 检查辅助功能权限...")
from utils.mac_hotkey import check_accessibility_permission
permission = check_accessibility_permission()
print(f"   权限状态: {permission}")
if permission is False:
    print("   ❌ 未授权，请在系统设置中授予辅助功能权限")
    print("   路径: 系统偏好设置 > 安全性与隐私 > 隐私 > 辅助功能")
    sys.exit(1)
elif permission is True:
    print("   ✅ 已授权")
else:
    print("   ⚠️  无法确定权限状态")

# 2. 测试注册快捷键
print("\n2. 测试注册全局快捷键...")
try:
    from utils.mac_hotkey import MacGlobalHotkey
    from PySide6.QtCore import QCoreApplication, QTimer
    
    # 创建应用实例
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
    
    callback_called = False
    
    def test_callback():
        global callback_called
        callback_called = True
        print("\n   ✅ 快捷键触发成功！回调函数被调用")
        app.quit()
    
    print("   正在注册快捷键: Control + A")
    hotkey = MacGlobalHotkey(test_callback)
    print("   ✅ 快捷键注册成功")
    
    print("\n" + "=" * 60)
    print("请按下 Control + A 来测试快捷键")
    print("等待 30 秒后自动退出...")
    print("=" * 60)
    
    # 30秒后自动退出
    QTimer.singleShot(30000, app.quit)
    
    # 运行事件循环
    app.exec()
    
    if callback_called:
        print("\n✅ 测试成功：快捷键正常工作")
    else:
        print("\n❌ 测试失败：快捷键未触发")
        print("   可能的原因：")
        print("   1. 权限未正确授予")
        print("   2. 快捷键被其他应用占用")
        print("   3. 事件处理逻辑有问题")
    
except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
