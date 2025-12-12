# 系统通知服务架构文档

## 概述

员工端系统通知服务采用**双重保障机制**，确保用户无论应用是否运行都能接收到系统通知：

1. **运行时通知服务**：应用运行时，通过轮询服务定期检查并显示通知
2. **后台通知服务**：应用未运行时，通过系统级后台服务（macOS LaunchAgent / Windows 任务计划程序）检查并发送通知

---

## 架构组件

### 1. 运行时通知服务

#### 1.1 PollingService（统一轮询服务）
**文件**: `ui_client/utils/polling_service.py`

**职责**：
- 统一管理版本检查和通知检查
- 定期轮询服务器获取未读通知
- 发送系统通知并处理点击事件

**关键特性**：
- 通知检查间隔：60秒
- 版本检查间隔：5分钟
- 使用后台线程（QRunnable）执行检查，不阻塞主线程
- 双重登录状态检查，确保未登录时不发起请求
- 去重机制：使用 `_checked_notification_ids` 集合避免重复通知

**工作流程**：
```
启动应用 → 延迟3秒 → 启动轮询服务
    ↓
检查登录状态
    ↓
创建 API 客户端
    ↓
启动定时器（60秒间隔）
    ↓
[每60秒]
    ↓
后台线程检查通知
    ↓
调用 /api/notifications?unread_only=true&limit=10
    ↓
过滤已读和已处理的通知
    ↓
发送系统通知 + 发出信号
```

**信号**：
- `notification_received(dict)`: 收到新通知
- `notification_clicked(dict)`: 通知被点击
- `version_update_available(dict)`: 检测到新版本

#### 1.2 NotificationService（独立通知服务）
**文件**: `ui_client/utils/notification_service.py`

**说明**：
- 功能与 `PollingService` 中的通知检查部分类似
- 目前**未被主窗口使用**（主窗口使用 `PollingService`）
- 保留作为备用或特殊场景使用

**与 PollingService 的区别**：
- `NotificationService`: 只检查通知
- `PollingService`: 同时检查通知和版本更新

---

### 2. 后台通知服务

#### 2.1 SystemNotificationService（系统服务管理器）
**文件**: `ui_client/utils/system_notification_service.py`

**职责**：
- 管理后台通知服务的安装、启用、禁用、卸载
- 支持 macOS LaunchAgent 和 Windows 任务计划程序
- 自动检测服务状态和配置有效性

**macOS 实现**：
- 使用 LaunchAgent（`~/Library/LaunchAgents/site.sanying.aiperf.notification.plist`）
- 每60秒运行一次后台脚本
- 日志输出到用户配置目录的 `logs/` 文件夹

**Windows 实现**：
- 使用任务计划程序（任务名：`AiPerfNotificationService`）
- 每1分钟运行一次后台脚本
- 使用 `pythonw.exe` 避免弹出命令行窗口

**关键方法**：
- `install(force_reinstall=False)`: 安装服务
- `enable()`: 启用服务
- `disable()`: 禁用服务
- `uninstall()`: 卸载服务
- `is_installed()`: 检查是否已安装
- `is_enabled()`: 检查是否已启用
- `is_configuration_valid()`: 检查配置是否正确

#### 2.2 notification_background_service.py（后台服务脚本）
**文件**: `scripts/notification_background_service.py`

**职责**：
- 应用未运行时独立运行的后台脚本
- 定期检查未读通知并发送系统通知
- 持久化已发送通知ID，避免重复发送

**工作流程**：
```
系统定时触发（每60秒/1分钟）
    ↓
加载配置文件（从多个可能位置）
    ↓
检查是否启用通知
    ↓
检查是否已登录（session_token）
    ↓
调用 API 获取未读通知
    ↓
加载已发送通知ID列表（持久化）
    ↓
过滤已发送的通知
    ↓
发送系统通知
    ↓
记录已发送的通知ID
    ↓
标记通知为已读（可选）
```

**持久化机制**：
- 已发送通知ID保存在用户配置目录的 `sent_notifications.json`
- 避免应用重启后重复发送相同通知

---

### 3. 系统通知工具

#### 3.1 notification.py（跨平台通知工具）
**文件**: `ui_client/utils/notification.py`

**职责**：
- 封装跨平台系统通知API
- 支持 macOS、Windows、Linux
- 提供统一的接口和回退方案

**平台实现**：

**macOS**:
- 优先使用 `NSUserNotificationCenter` (PyObjC)
- 回退到 `osascript` 命令

**Windows**:
- 优先使用 `winrt.windows.ui.notifications` (Windows 10+)
- 回退到 `win10toast` 或 PowerShell

**Linux**:
- 优先使用 `notify-send` 命令
- 回退到 `plyer` 或 `dbus-python`

**关键功能**：
- `send_notification()`: 发送系统通知
- `check_permission()`: 检查通知权限
- `request_permission()`: 请求通知权限
- `open_system_settings()`: 打开系统通知设置

**通知参数**：
- `title`: 通知标题
- `message`: 通知内容
- `subtitle`: 副标题（仅 macOS）
- `sound`: 是否播放声音
- `icon_path`: 图标路径
- `notification_id`: 通知ID（用于点击回调）
- `click_callback`: 点击回调函数

---

## 主窗口集成

### 启动流程

**文件**: `ui_client/windows/main_window.py`

```python
# 应用启动时（__init__ 中）
QTimer.singleShot(3000, self._start_polling_service)          # 延迟3秒启动轮询服务
QTimer.singleShot(2000, self._setup_background_notification_service)  # 延迟2秒设置后台服务
```

### 轮询服务启动

```python
def _start_polling_service(self):
    # 1. 检查登录状态
    is_logged_in = self._ensure_logged_in()
    
    # 2. 创建 API 客户端（如果已登录）
    api_client = ApiClient.from_config() if is_logged_in else None
    
    # 3. 获取轮询服务并启动
    polling_service = get_polling_service(api_client)
    polling_service.start_polling()
    
    # 4. 连接信号
    polling_service.notification_received.connect(self._on_notification_received)
    polling_service.notification_clicked.connect(self._on_notification_clicked)
    polling_service.version_update_available.connect(self._on_version_update_available)
```

### 通知处理

```python
def _on_notification_received(self, notification: dict):
    """收到通知时的处理"""
    # 通知已经在 polling_service 中通过系统通知显示了
    # 可以在这里添加额外的UI处理，比如更新通知列表、徽章等
    pass

def _on_notification_clicked(self, notification: dict):
    """通知被点击时的处理"""
    notification_id = notification.get("id")
    if notification_id:
        self.show_notification_detail(notification_id)
```

### 后台服务设置

```python
def _setup_background_notification_service(self):
    """设置后台通知服务（应用未运行时也能接收通知）"""
    # 1. 检查用户是否启用通知
    # 2. 检查服务是否已安装
    # 3. 如果未安装，尝试安装并启用
    # 4. 如果已安装但配置无效，尝试重新安装
    # 5. 如果已安装但未启用，启用它
```

---

## 数据流

### 运行时通知流程

```
[主窗口]
    ↓
启动 PollingService
    ↓
[每60秒]
    ↓
_NotificationCheckWorker (后台线程)
    ↓
检查登录状态 → 调用 API → 获取未读通知
    ↓
过滤已读/已处理通知
    ↓
发送系统通知 (notification.py)
    ↓
发出信号 (notification_received)
    ↓
[主窗口] _on_notification_received()
    ↓
[用户点击通知]
    ↓
发出信号 (notification_clicked)
    ↓
[主窗口] _on_notification_clicked()
    ↓
显示通知详情
```

### 后台服务流程

```
[系统定时器触发]
    ↓
notification_background_service.py
    ↓
加载配置 → 检查登录状态
    ↓
调用 API → 获取未读通知
    ↓
加载已发送通知ID列表
    ↓
过滤已发送的通知
    ↓
发送系统通知 (osascript / PowerShell / notify-send)
    ↓
记录已发送的通知ID
    ↓
标记为已读（可选）
```

---

## 去重机制

### 运行时去重
- **内存去重**：使用 `_checked_notification_ids` 集合
- **作用域**：应用运行期间
- **重置时机**：应用重启时清空

### 后台服务去重
- **持久化去重**：使用 `sent_notifications.json` 文件
- **作用域**：跨应用重启
- **清理机制**：可定期清理旧记录（当前未实现）

---

## 配置项

### 用户配置（config.json）

```json
{
  "notifications": true,           // 是否启用通知
  "session_token": "...",          // 登录令牌（用于API调用）
  "api_base": "http://...",        // API服务器地址
  "client_version": "1.0.0"        // 客户端版本
}
```

### 系统服务配置

**macOS LaunchAgent** (`~/Library/LaunchAgents/site.sanying.aiperf.notification.plist`):
- `StartInterval`: 60（秒）
- `RunAtLoad`: false
- `KeepAlive`: false

**Windows 任务计划程序**:
- 触发器：每1分钟
- 操作：运行 `pythonw.exe scripts/notification_background_service.py --once`

---

## 错误处理

### 运行时服务
- 所有异常都被静默捕获，不干扰主程序
- 网络错误自动重试（ApiClient 内置重试机制）
- 登录状态检查失败时直接返回，不发起请求

### 后台服务
- 异常记录到日志文件
- 配置加载失败时静默退出
- API 调用失败时静默跳过

---

## 安全考虑

1. **登录状态检查**：
   - 双重检查：`ApiClient.is_logged_in()` + API 客户端验证
   - 未登录时不发起任何请求

2. **权限管理**：
   - 用户可通过配置禁用通知
   - 系统级通知需要用户授权（macOS）

3. **数据持久化**：
   - 配置和令牌存储在用户目录
   - 已发送通知ID列表仅存储ID，不包含敏感信息

---

## 待优化项

1. **后台服务去重优化**：
   - 当前持久化列表可能无限增长
   - 建议添加定期清理机制（如保留最近30天的记录）

2. **通知点击回调**：
   - 当前仅支持应用运行时的点击回调
   - 后台服务发送的通知点击后无法直接打开应用（需要系统级支持）

3. **通知权限检查**：
   - macOS 权限检查不够准确（NSUserNotificationCenter 在 10.14+ 已废弃）
   - 建议使用 UserNotifications framework 进行更准确的权限检查

4. **统一服务管理**：
   - `NotificationService` 和 `PollingService` 功能重叠
   - 建议统一使用 `PollingService`，或明确两者的使用场景

---

## 相关文件清单

### 核心服务
- `ui_client/utils/polling_service.py` - 统一轮询服务（**主要使用**）
- `ui_client/utils/notification_service.py` - 独立通知服务（备用）
- `ui_client/utils/system_notification_service.py` - 系统服务管理器
- `ui_client/utils/notification.py` - 跨平台通知工具

### 后台服务
- `scripts/notification_background_service.py` - 后台服务脚本

### 主窗口集成
- `ui_client/windows/main_window.py` - 主窗口（启动和管理服务）

### 配置文件
- `ui_client/config.json` - 用户配置（通知开关、API地址等）
- `sent_notifications.json` - 已发送通知ID列表（后台服务使用）

---

## 使用示例

### 启动运行时通知服务

```python
from utils.polling_service import get_polling_service
from utils.api_client import ApiClient

# 创建 API 客户端
api_client = ApiClient.from_config()

# 获取轮询服务
polling_service = get_polling_service(api_client)

# 启动轮询
polling_service.start_polling()

# 连接信号
polling_service.notification_received.connect(on_notification)
polling_service.notification_clicked.connect(on_notification_clicked)
```

### 安装后台通知服务

```python
from utils.system_notification_service import SystemNotificationService

service = SystemNotificationService()

# 安装服务
success, msg = service.install()
if success:
    # 启用服务
    service.enable()
```

### 手动发送通知

```python
from utils.notification import send_notification

send_notification(
    title="测试通知",
    message="这是一条测试消息",
    subtitle="副标题",
    notification_id=123,
    click_callback=lambda: print("通知被点击")
)
```

---

## 总结

员工端系统通知服务采用**双重保障机制**，确保用户无论应用是否运行都能接收到通知：

1. **运行时**：通过 `PollingService` 每60秒轮询一次，实时显示通知
2. **未运行时**：通过系统级后台服务定期检查，发送系统通知

两个机制相互独立，互不干扰，共同保障通知的及时送达。
