#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开发模式下测试通知功能
使用方法：
    python test_notification.py [选项]

选项：
    --basic          测试基本系统通知
    --service        测试后台服务安装/启用/禁用
    --background     测试后台服务脚本（模拟应用未运行）
    --api            测试从 API 获取通知（需要登录）
    --all            运行所有测试
"""

import sys
import time
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.notification import send_notification, SystemNotification
from utils.system_notification_service import SystemNotificationService
from utils.config_manager import ConfigManager


def test_basic_notification():
    """测试基本系统通知"""
    print("=" * 60)
    print("测试 1: 基本系统通知")
    print("=" * 60)
    
    # 检查用户配置
    config = ConfigManager.load()
    if not config.get("notifications", True):
        print("⚠️  用户已禁用通知，请在 config.json 中设置 notifications: true")
        return False
    
    # 检查权限（macOS）
    import platform
    if platform.system() == "Darwin":
        permission = SystemNotification.check_permission()
        if permission is False:
            print("⚠️  macOS 通知权限未授权")
            print("   提示：首次发送通知时系统会自动请求权限")
        elif permission is None:
            print("ℹ️  无法确定通知权限状态（macOS 10.14+ 使用 osascript 方案）")
            print("   提示：如果通知未显示，请在系统设置中授权通知权限")
    
    # 发送测试通知
    print("\n📤 发送测试通知...")
    success = send_notification(
        title="测试通知",
        message="这是一条开发模式下的测试通知",
        subtitle="来自 Ai Perf Client 开发测试"
    )
    
    if success:
        print("✅ 通知发送成功！请查看系统通知栏")
    else:
        print("❌ 通知发送失败")
    
    return success


def test_service_management():
    """测试后台服务管理"""
    print("\n" + "=" * 60)
    print("测试 2: 后台服务管理")
    print("=" * 60)
    
    service = SystemNotificationService()
    status = service.get_status()
    
    print(f"\n系统: {status['system']}")
    print(f"服务已安装: {'是' if status['installed'] else '否'}")
    print(f"服务已启用: {'是' if status['enabled'] else '否'}")
    
    # 检查脚本路径
    script_path = service._get_service_script_path()
    if script_path:
        print(f"✅ 找到后台服务脚本: {script_path}")
    else:
        print("❌ 未找到后台服务脚本")
        print("   提示：确保 scripts/notification_background_service.py 存在")
        return False
    
    # 测试安装
    print("\n📦 测试服务安装...")
    if not status['installed']:
        success, msg = service.install()
        if success:
            print("✅ 服务安装成功")
        else:
            print(f"❌ 服务安装失败: {msg}")
            return False
    else:
        print("ℹ️  服务已安装，跳过安装步骤")
    
    # 测试启用
    print("\n▶️  测试服务启用...")
    if not status['enabled']:
        success, msg = service.enable()
        if success:
            print("✅ 服务启用成功")
        else:
            print(f"❌ 服务启用失败: {msg}")
            return False
    else:
        print("ℹ️  服务已启用")
    
    # 验证状态
    print("\n🔍 验证服务状态...")
    time.sleep(1)  # 等待服务状态更新
    new_status = service.get_status()
    if new_status['installed'] and new_status['enabled']:
        print("✅ 服务状态正常")
        return True
    else:
        print("❌ 服务状态异常")
        return False


def test_background_service():
    """测试后台服务脚本"""
    print("\n" + "=" * 60)
    print("测试 3: 后台服务脚本")
    print("=" * 60)
    
    script_path = Path(__file__).parent.parent / "scripts" / "notification_background_service.py"
    
    if not script_path.exists():
        print(f"❌ 后台服务脚本不存在: {script_path}")
        return False
    
    print(f"✅ 找到后台服务脚本: {script_path}")
    
    # 检查配置
    config = ConfigManager.load()
    if not config.get("notifications", True):
        print("⚠️  用户已禁用通知")
        return False
    
    session_token = config.get("session_token", "").strip()
    if not session_token:
        print("⚠️  未登录，无法测试从 API 获取通知")
        print("   提示：请先登录应用，或手动测试脚本")
        print(f"   命令: python {script_path} --once")
        return False
    
    print("\n📤 运行后台服务脚本（单次执行模式）...")
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--once"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            print("✅ 后台服务脚本执行成功")
            if result.stdout:
                print(f"   输出: {result.stdout}")
            return True
        else:
            print(f"❌ 后台服务脚本执行失败（退出码: {result.returncode}）")
            if result.stderr:
                print(f"   错误: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ 后台服务脚本执行超时")
        return False
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        return False


def test_api_notification():
    """测试从 API 获取通知"""
    print("\n" + "=" * 60)
    print("测试 4: 从 API 获取通知")
    print("=" * 60)
    
    # 检查登录状态
    config = ConfigManager.load()
    session_token = config.get("session_token", "").strip()
    if not session_token:
        print("❌ 未登录，无法测试 API 通知")
        print("   提示：请先运行应用并登录")
        return False
    
    api_base = config.get("api_base", "").strip()
    if not api_base:
        print("❌ API 服务器地址未配置")
        return False
    
    print(f"✅ 已登录，API 地址: {api_base}")
    
    try:
        from utils.api_client import ApiClient
        
        api_client = ApiClient.from_config()
        
        print("\n📥 从 API 获取未读通知...")
        response = api_client._get("/api/notifications", params={"unread_only": True, "limit": 10})
        
        if response.get("status") == "success":
            items = response.get("items", [])
            print(f"✅ 获取到 {len(items)} 条未读通知")
            
            if items:
                print("\n通知列表:")
                for i, item in enumerate(items[:5], 1):  # 只显示前5条
                    print(f"  {i}. [{item.get('id')}] {item.get('title')}")
                    print(f"     {item.get('message', '')[:50]}...")
                
                # 发送第一条通知作为测试
                if items:
                    first_item = items[0]
                    print(f"\n📤 发送第一条通知: {first_item.get('title')}")
                    success = send_notification(
                        title=first_item.get("title", "系统通知"),
                        message=first_item.get("message", ""),
                        subtitle=first_item.get("subtitle")
                    )
                    if success:
                        print("✅ 通知发送成功")
                        return True
                    else:
                        print("❌ 通知发送失败")
                        return False
            else:
                print("ℹ️  暂无未读通知")
                print("   提示：可以在管理端创建测试通知")
                return True
        else:
            print(f"❌ API 请求失败: {response.get('message', '未知错误')}")
            return False
            
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def show_help():
    """显示帮助信息"""
    print(__doc__)
    print("\n快速测试命令:")
    print("  python test_notification.py --basic      # 测试基本通知")
    print("  python test_notification.py --service     # 测试服务管理")
    print("  python test_notification.py --background # 测试后台服务")
    print("  python test_notification.py --api         # 测试 API 通知")
    print("  python test_notification.py --all         # 运行所有测试")


def main():
    """主函数"""
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        
        if arg == "--help" or arg == "-h":
            show_help()
            return
        
        results = []
        
        if arg == "--basic" or arg == "--all":
            results.append(("基本通知", test_basic_notification()))
        
        if arg == "--service" or arg == "--all":
            results.append(("服务管理", test_service_management()))
        
        if arg == "--background" or arg == "--all":
            results.append(("后台服务", test_background_service()))
        
        if arg == "--api" or arg == "--all":
            results.append(("API 通知", test_api_notification()))
        
        if not results:
            print("❌ 未知选项，使用 --help 查看帮助")
            return
        
        # 显示测试结果摘要
        print("\n" + "=" * 60)
        print("测试结果摘要")
        print("=" * 60)
        for name, success in results:
            status = "✅ 通过" if success else "❌ 失败"
            print(f"{name}: {status}")
        
        all_passed = all(result[1] for result in results)
        if all_passed:
            print("\n🎉 所有测试通过！")
        else:
            print("\n⚠️  部分测试失败，请检查上述输出")
    else:
        # 默认运行基本测试
        print("开发模式通知测试工具")
        print("=" * 60)
        print("提示：使用 --help 查看所有选项")
        print()
        test_basic_notification()


if __name__ == "__main__":
    main()

