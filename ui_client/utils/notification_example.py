#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知功能使用示例
"""

from utils.notification import send_notification, SystemNotification
from utils.background_notifier import BackgroundNotifier
from utils.config_manager import ConfigManager


def example_basic_notification():
    """示例：基本通知"""
    # 检查用户是否启用了通知
    config = ConfigManager.load()
    if not config.get("notifications", True):
        print("用户已禁用通知")
        return
    
    # 发送通知
    send_notification(
        title="今日评分已生成",
        message="您的今日 AI 绩效评分为 85 分",
        subtitle="高于团队平均 5 分"  # 仅 macOS
    )


def example_review_result_notification():
    """示例：复评结果通知"""
    config = ConfigManager.load()
    if not config.get("notifications", True):
        return
    
    send_notification(
        title="复评结果",
        message="您的复评已完成：从 72 → 85",
        subtitle="点击查看详情"
    )


def example_background_notification():
    """示例：后台通知（应用未运行时）"""
    notifier = BackgroundNotifier()
    
    # 发送通知（自动检测应用是否运行）
    notifier.send_notification(
        title="数据缺失提醒",
        message="今天缺少 Jira 评论，协作分将偏低",
        subtitle="建议下班前补记录",
        action_url="aiperf://today"  # 点击后打开应用
    )


def example_notification_with_permission_check():
    """示例：带权限检查的通知"""
    # 检查权限（仅 macOS）
    if not SystemNotification.check_permission():
        print("需要通知权限，请在系统设置中授权")
        return
    
    # 检查用户配置
    config = ConfigManager.load()
    if not config.get("notifications", True):
        return
    
    # 发送通知
    send_notification(
        title="系统维护",
        message="系统将在今晚 22:00 进行维护",
        subtitle="预计耗时 30 分钟"
    )


def example_notification_queue():
    """示例：通知队列（应用启动时显示）"""
    notifier = BackgroundNotifier()
    
    # 应用启动时，加载并显示队列中的通知
    queue = notifier.load_notification_queue()
    for notification in queue:
        send_notification(
            title=notification["title"],
            message=notification["message"],
            subtitle=notification.get("subtitle")
        )
    
    # 清空队列
    notifier.clear_notification_queue()


# 在 API 响应后发送通知的示例
def example_api_callback_notification():
    """示例：在 API 响应后发送通知"""
    from utils.api_client import ApiClient
    
    api = ApiClient()
    
    try:
        # 获取今日评分
        data = api.get_today_score()
        
        # 发送通知
        config = ConfigManager.load()
        if config.get("notifications", True):
            send_notification(
                title="今日评分已更新",
                message=f"总分: {data.get('total_ai', 0)} 分",
                subtitle=f"执行力: {data.get('execution', 0)} | 协作: {data.get('collaboration', 0)}"
            )
    except Exception as e:
        print(f"获取评分失败: {e}")


if __name__ == "__main__":
    # 测试通知
    print("测试基本通知...")
    example_basic_notification()
    
    print("\n测试权限检查...")
    example_notification_with_permission_check()

