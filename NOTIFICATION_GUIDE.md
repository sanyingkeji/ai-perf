# 系统通知使用指南

## 功能概述

系统通知功能使用**系统原生 API**（类似 iOS 的 UNUserNotificationCenter），支持在应用运行时和未运行时发送通知，适用于 macOS、Windows 和 Linux 平台。

### 实现方式

- **macOS**: 使用 `NSUserNotificationCenter` / `UserNotifications` framework（系统原生 API）
- **Windows**: 使用 `Windows.UI.Notifications` API（系统原生 API）
- **Linux**: 使用 `notify-send`（基于 D-Bus）或 `plyer` / `dbus-python` 库

### 依赖项

- **macOS**: 使用 `PyObjC`（通常系统 Python 已包含，pyenv 需要安装：`pip install pyobjc`）
- **Windows**: 使用 `winrt`（Windows 10+）或 `win10toast`（回退方案）
- **Linux**: 使用 `notify-send`（系统自带）或 `plyer` / `dbus-python`（可选，`pip install plyer` 或 `pip install dbus-python`）

如果原生 API 不可用，会自动回退到命令行方案（osascript/PowerShell/notify-send）。

## 基本使用

### 1. 在应用内发送通知

```python
from utils.notification import send_notification

# 发送简单通知
send_notification(
    title="今日评分已生成",
    message="您的今日 AI 绩效评分为 85 分",
    subtitle="高于团队平均 5 分"  # 仅 macOS
)
```

### 2. 检查通知权限（macOS）

```python
from utils.notification import SystemNotification

if SystemNotification.check_permission():
    send_notification("标题", "内容")
else:
    print("需要通知权限")
```

### 3. 后台通知（应用未运行时）

```python
from utils.background_notifier import BackgroundNotifier

notifier = BackgroundNotifier()

# 发送通知（如果应用未运行，会自动使用后台脚本）
notifier.send_notification(
    title="复评结果",
    message="您的复评已完成：从 72 → 85",
    action_url="aiperf://today"  # 点击通知后打开应用
)
```

## 在应用未运行时发送通知

### 方法 1: 使用后台脚本（推荐）

创建一个独立的 Python 脚本，通过系统定时任务调用：

**macOS (使用 cron):**
```bash
# 编辑 crontab
crontab -e

# 添加定时任务（每天 10:00 发送通知）
0 10 * * * /usr/bin/python3 /path/to/background_notifier.py "今日评分" "您的评分已生成"
```

**Windows (使用任务计划程序):**
```powershell
# 创建任务
schtasks /create /tn "AiPerfNotification" /tr "python C:\path\to\background_notifier.py \"今日评分\" \"您的评分已生成\"" /sc daily /st 10:00
```

### 方法 2: 使用系统服务

**macOS (LaunchAgent):**
创建 `~/Library/LaunchAgents/site.sanying.aiperf.notification.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>site.sanying.aiperf.notification</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/background_notifier.py</string>
        <string>今日评分</string>
        <string>您的评分已生成</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>10</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
</dict>
</plist>
```

加载服务：
```bash
launchctl load ~/Library/LaunchAgents/site.sanying.aiperf.notification.plist
```

**Windows (系统服务):**
使用 NSSM (Non-Sucking Service Manager) 或 pywin32 创建 Windows 服务。

### 方法 3: 后端 API 触发

在后端 API 中，当需要发送通知时，调用系统命令：

```python
# 后端 Python 代码
import subprocess
import platform

def send_notification_to_client(title: str, message: str):
    """从后端发送通知到客户端"""
    system = platform.system()
    
    if system == "Darwin":
        # macOS
        subprocess.Popen([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"'
        ])
    elif system == "Windows":
        # Windows (需要客户端机器上运行)
        # 可以通过 SSH 或远程执行
        pass
```

## 通知队列

当应用未运行时，通知会被保存到队列中，应用启动时自动显示：

```python
from utils.background_notifier import BackgroundNotifier

notifier = BackgroundNotifier()

# 保存通知到队列
notifier.save_notification_queue(
    title="复评结果",
    message="您的复评已完成",
    action_url="aiperf://review"
)

# 应用启动时加载队列
queue = notifier.load_notification_queue()
for notification in queue:
    send_notification(**notification)

# 清空队列
notifier.clear_notification_queue()
```

## 平台差异

### macOS
- ✅ 支持原生通知
- ✅ 支持副标题
- ✅ 支持自定义声音
- ⚠️ macOS 10.14+ 需要用户授权通知权限
- ✅ 应用未运行时可以通过 `osascript` 发送通知

### Windows
- ✅ 支持 Toast 通知（Windows 10+）
- ❌ 不支持副标题
- ✅ 支持自定义声音
- ✅ 无需权限（Windows 10+）
- ✅ 应用未运行时可以通过 PowerShell 发送通知

### Linux
- ✅ 支持原生通知（基于 D-Bus）
- ❌ 不支持副标题（notify-send 不支持）
- ⚠️ 声音支持取决于桌面环境
- ✅ 通常无需权限（取决于桌面环境设置）
- ✅ 应用未运行时可以通过 `notify-send` 发送通知
- 📦 需要安装：`notify-send`（系统自带）或 `plyer` / `dbus-python`（可选）

## 最佳实践

1. **检查配置**: 发送通知前检查用户是否启用了通知
   ```python
   from utils.config_manager import ConfigManager
   
   config = ConfigManager()
   if config.get("notifications", True):
       send_notification("标题", "内容")
   ```

2. **错误处理**: 通知发送失败时不要中断主流程
   ```python
   try:
       send_notification("标题", "内容")
   except Exception as e:
       print(f"通知发送失败: {e}")
   ```

3. **通知频率**: 避免过于频繁的通知，建议：
   - 每日评分通知：每天 1 次
   - 复评结果通知：立即发送
   - 系统维护通知：按需发送

4. **后台运行**: 如果需要应用在后台运行以接收通知，考虑：
   - macOS: 使用菜单栏应用（系统托盘）
   - Windows: 使用系统托盘应用

## 常见问题

### Q: 应用未运行时如何发送通知？
A: 使用系统定时任务（cron/任务计划程序）或系统服务（LaunchAgent/Windows Service）调用后台脚本。

### Q: macOS 通知权限如何获取？
A: 首次发送通知时，系统会自动弹出权限请求。也可以在"系统偏好设置 > 通知"中手动授权。

### Q: 通知点击后如何打开应用？
A: 使用自定义 URL Scheme（如 `aiperf://today`），在应用中注册 URL 处理器。

### Q: 如何测试通知功能？
A: 运行 `python utils/notification.py` 或 `python utils/background_notifier.py "测试" "这是一条测试通知"`



