# 通知图标设置指南

## 概述

通知功能现在支持自定义图标。如果不提供图标，系统会自动使用应用的默认图标。

## 使用方法

### 1. 使用默认图标（推荐）

不指定 `icon_path` 参数，系统会自动使用应用图标：

```python
from utils.notification import send_notification

# 自动使用应用默认图标
send_notification(
    title="今日评分已生成",
    message="您的今日 AI 绩效评分为 85 分",
    subtitle="高于团队平均 5 分"
)
```

### 2. 使用自定义图标

提供 `icon_path` 参数指定自定义图标：

```python
from utils.notification import send_notification

# 使用自定义图标
send_notification(
    title="系统维护",
    message="系统将在今晚 22:00 进行维护",
    subtitle="预计耗时 30 分钟",
    icon_path="/path/to/custom_icon.png"  # 自定义图标路径
)
```

## 图标格式要求

### macOS

- **推荐格式**: `.icns` (macOS 图标格式)
- **支持格式**: `.png`, `.jpg`, `.jpeg`
- **图标尺寸**: 建议 512x512 或更高分辨率

### Windows

- **推荐格式**: `.ico` (Windows 图标格式)
- **支持格式**: `.png`, `.jpg`, `.jpeg`
- **图标尺寸**: 建议 256x256 或更高分辨率

## 图标路径

### 绝对路径

```python
send_notification(
    title="测试",
    message="这是一条测试通知",
    icon_path="/Users/username/icons/notification_icon.png"  # macOS
    # 或
    icon_path="C:\\Users\\username\\icons\\notification_icon.ico"  # Windows
)
```

### 相对路径（相对于项目根目录）

```python
from pathlib import Path

icon_path = str(Path(__file__).parent.parent / "resources" / "notification_icon.png")
send_notification(
    title="测试",
    message="这是一条测试通知",
    icon_path=icon_path
)
```

## 默认图标查找逻辑

如果不提供 `icon_path`，系统会按以下顺序查找应用默认图标：

1. **打包后的应用**:
   - macOS: `应用包.app/Contents/Resources/app_icon.icns`
   - Windows: `应用目录/app_icon.ico`

2. **开发环境**:
   - `ui_client/resources/app_icon.icns` (macOS)
   - `ui_client/resources/app_icon.ico` (Windows)
   - `ui_client/resources/app_icon.png` (通用)

## 示例代码

### 示例 1: 不同类型的通知使用不同图标

```python
from utils.notification import send_notification
from pathlib import Path

resources_dir = Path(__file__).parent.parent / "resources"

# 评分通知 - 使用评分图标
send_notification(
    title="今日评分已生成",
    message="您的今日 AI 绩效评分为 85 分",
    icon_path=str(resources_dir / "score_icon.png")
)

# 系统通知 - 使用系统图标
send_notification(
    title="系统维护",
    message="系统将在今晚 22:00 进行维护",
    icon_path=str(resources_dir / "system_icon.png")
)

# 消息通知 - 使用消息图标
send_notification(
    title="新消息",
    message="您有一条新的系统消息",
    icon_path=str(resources_dir / "message_icon.png")
)
```

### 示例 2: 根据通知类型动态选择图标

```python
from utils.notification import send_notification
from pathlib import Path

def send_typed_notification(notification_type: str, title: str, message: str):
    """根据通知类型发送带图标的通知"""
    resources_dir = Path(__file__).parent.parent / "resources"
    
    icon_map = {
        "score": "score_icon.png",
        "system": "system_icon.png",
        "message": "message_icon.png",
        "warning": "warning_icon.png",
        "error": "error_icon.png",
    }
    
    icon_path = None
    if notification_type in icon_map:
        icon_file = resources_dir / icon_map[notification_type]
        if icon_file.exists():
            icon_path = str(icon_file)
    
    send_notification(
        title=title,
        message=message,
        icon_path=icon_path  # 如果图标不存在，会使用默认图标
    )

# 使用示例
send_typed_notification("score", "今日评分", "您的评分为 85 分")
send_typed_notification("warning", "数据缺失", "今天缺少 Jira 评论")
```

## 注意事项

1. **图标文件必须存在**: 如果指定的图标路径不存在，会回退到使用默认图标，不会报错

2. **图标文件大小**: 建议图标文件不要太大（< 1MB），以免影响通知显示速度

3. **macOS 限制**: 
   - 使用 `osascript` 方案时（macOS 10.14+），无法自定义图标，会使用应用默认图标
   - 使用 `NSUserNotification` 时，可以设置自定义图标

4. **Windows 限制**:
   - Toast 通知的图标会显示在通知的左侧
   - 图标必须是有效的图片文件

5. **开发环境测试**: 在开发环境中，确保图标文件路径正确，可以使用绝对路径或相对于项目根目录的路径

## 测试图标

可以使用测试脚本测试图标功能：

```python
# test_notification_icon.py
from utils.notification import send_notification
from pathlib import Path

# 测试默认图标
send_notification(
    title="测试默认图标",
    message="这条通知使用应用默认图标"
)

# 测试自定义图标（如果存在）
icon_path = Path(__file__).parent.parent / "resources" / "app_icon.png"
if icon_path.exists():
    send_notification(
        title="测试自定义图标",
        message="这条通知使用自定义图标",
        icon_path=str(icon_path)
    )
```

## 常见问题

### Q: 为什么我的自定义图标没有显示？

**A**: 可能的原因：
1. 图标文件路径不正确
2. 图标文件格式不支持
3. macOS 上使用了 `osascript` 方案（不支持自定义图标）
4. 图标文件损坏或无法读取

**解决方法**:
- 检查图标文件是否存在
- 使用绝对路径
- 在 macOS 上，确保使用 `NSUserNotification` API（需要 PyObjC）

### Q: 如何确保图标在所有平台上都能正常显示？

**A**: 
1. 准备多个格式的图标：`.icns` (macOS), `.ico` (Windows), `.png` (通用)
2. 根据平台选择对应的图标格式
3. 提供回退机制，如果指定图标不存在，使用默认图标

### Q: 图标尺寸有什么要求？

**A**: 
- **macOS**: 建议 512x512 或更高（支持 Retina 显示）
- **Windows**: 建议 256x256 或更高
- 系统会自动缩放图标以适应通知显示


