# 系统通知流程文档

## 快速概览

```
┌─────────────────────────────────────────────────────────────┐
│                   系统通知服务架构                            │
└─────────────────────────────────────────────────────────────┘

应用运行时                         应用未运行时
    │                                    │
    ├─ PollingService                    ├─ SystemNotificationService
    │  (每60秒轮询)                      │  (系统定时器触发)
    │                                    │
    └─ 实时显示通知                      └─ 后台发送通知
```

---

## 详细流程

### 1. 应用启动流程

```
应用启动 (main_window.py)
    │
    ├─ 延迟2秒 → _setup_background_notification_service()
    │   │
    │   ├─ 检查通知开关
    │   ├─ 检查服务是否已安装
    │   ├─ 未安装 → 安装并启用
    │   └─ 已安装但未启用 → 启用
    │
    └─ 延迟3秒 → _start_polling_service()
        │
        ├─ 检查登录状态
        ├─ 创建 API 客户端
        ├─ 获取 PollingService 实例
        ├─ 启动轮询 (start_polling)
        └─ 连接信号
            ├─ notification_received → _on_notification_received
            ├─ notification_clicked → _on_notification_clicked
            └─ version_update_available → _on_version_update_available
```

### 2. 运行时通知检查流程

```
[定时器触发 - 每60秒]
    │
    └─ PollingService._check_notifications()
        │
        ├─ 检查登录状态 (ApiClient.is_logged_in())
        │   └─ 未登录 → 直接返回
        │
        ├─ 检查通知开关 (config.get("notifications"))
        │   └─ 已禁用 → 直接返回
        │
        └─ 启动后台线程 (_NotificationCheckWorker)
            │
            ├─ 再次检查登录状态 (双重检查)
            ├─ 调用 API: GET /api/notifications?unread_only=true&limit=10
            │
            ├─ 解析响应
            │   └─ 提取 items 数组
            │
            └─ 遍历通知项
                │
                ├─ 检查 is_read → 已读则跳过
                ├─ 检查是否已处理 (_checked_notification_ids)
                │   └─ 已处理则跳过
                │
                └─ 发出信号 (notification_found)
                    │
                    └─ PollingService._on_notification_received()
                        │
                        ├─ 再次检查 is_read (双重保险)
                        ├─ 再次检查是否已处理 (去重)
                        ├─ 添加到已处理集合
                        │
                        └─ 发送系统通知
                            │
                            ├─ notification.py.send_notification()
                            │   ├─ macOS: NSUserNotificationCenter / osascript
                            │   ├─ Windows: winrt / win10toast / PowerShell
                            │   └─ Linux: notify-send / plyer / dbus
                            │
                            └─ 发出信号 (notification_received)
                                │
                                └─ 主窗口._on_notification_received()
                                    └─ (可在此更新UI，如通知列表、徽章等)
```

### 3. 通知点击流程

```
用户点击系统通知
    │
    └─ 系统调用点击回调 (click_callback)
        │
        └─ PollingService.notification_clicked.emit(notification)
            │
            └─ 主窗口._on_notification_clicked(notification)
                │
                └─ show_notification_detail(notification_id)
                    └─ 显示通知详情窗口
```

### 4. 后台服务流程（应用未运行时）

```
[系统定时器触发 - 每60秒/1分钟]
    │
    └─ notification_background_service.py
        │
        ├─ 加载配置文件
        │   └─ 从多个可能位置查找 config.json
        │
        ├─ 检查通知开关
        │   └─ 已禁用 → 退出
        │
        ├─ 检查登录状态 (session_token)
        │   └─ 未登录 → 退出
        │
        ├─ 调用 API: GET /api/notifications?unread_only=true&limit=10
        │
        ├─ 加载已发送通知ID列表
        │   └─ 从 sent_notifications.json 读取
        │
        └─ 遍历通知项
            │
            ├─ 检查是否已发送 (sent_notification_ids)
            │   └─ 已发送 → 跳过
            │
            ├─ 检查 is_read
            │   └─ 已读 → 添加到已发送列表，跳过
            │
            └─ 发送系统通知
                │
                ├─ macOS: osascript
                ├─ Windows: PowerShell / win10toast
                └─ Linux: notify-send
                │
                ├─ 记录到已发送列表
                └─ 保存到 sent_notifications.json
                    │
                    └─ (可选) 标记通知为已读
```

---

## 关键组件交互图

```
┌─────────────────┐
│   MainWindow    │
└────────┬────────┘
         │
         ├─ 启动
         │   │
         │   ├─→ SystemNotificationService
         │   │   └─ 安装/启用后台服务
         │   │
         │   └─→ PollingService
         │       └─ 启动轮询
         │
         ├─ 信号连接
         │   │
         │   ├─ notification_received
         │   ├─ notification_clicked
         │   └─ version_update_available
         │
         └─ 处理通知
             │
             ├─ _on_notification_received()
             └─ _on_notification_clicked()
                 └─ show_notification_detail()

┌─────────────────┐
│ PollingService  │
└────────┬────────┘
         │
         ├─ 定时器 (60秒)
         │   └─ _check_notifications()
         │
         ├─ 后台线程
         │   └─ _NotificationCheckWorker
         │       └─ API 调用
         │
         ├─ 发送通知
         │   └─ notification.send_notification()
         │
         └─ 发出信号
             ├─ notification_received
             └─ notification_clicked

┌──────────────────────────┐
│ SystemNotificationService│
└────────┬─────────────────┘
         │
         ├─ 安装服务
         │   ├─ macOS: LaunchAgent
         │   └─ Windows: 任务计划程序
         │
         ├─ 启用/禁用服务
         └─ 检查服务状态

┌─────────────────────────────┐
│notification_background_     │
│service.py (后台脚本)        │
└────────┬────────────────────┘
         │
         ├─ 系统定时触发
         ├─ 加载配置
         ├─ API 调用
         ├─ 发送系统通知
         └─ 持久化已发送ID

┌─────────────────┐
│  notification.py │
└────────┬────────┘
         │
         ├─ send_notification()
         │   ├─ macOS: NSUserNotificationCenter / osascript
         │   ├─ Windows: winrt / win10toast / PowerShell
         │   └─ Linux: notify-send / plyer / dbus
         │
         ├─ check_permission()
         └─ request_permission()
```

---

## 数据流

### API 请求

```
客户端
    │
    └─ GET /api/notifications?unread_only=true&limit=10
        │
        └─ Headers: Authorization: Bearer {session_token}
            │
            └─ 服务器返回
                │
                └─ {
                      "status": "success",
                      "items": [
                        {
                          "id": 123,
                          "title": "通知标题",
                          "message": "通知内容",
                          "subtitle": "副标题",
                          "is_read": false,
                          "created_at": "2025-01-01T00:00:00"
                        }
                      ]
                    }
```

### 通知去重

```
运行时去重 (内存)
    │
    └─ _checked_notification_ids: set
        └─ 应用运行期间有效
            └─ 应用重启后清空

后台服务去重 (持久化)
    │
    └─ sent_notifications.json
        └─ {
              "sent_ids": [123, 124, 125],
              "last_check": "2025-01-01T00:00:00"
            }
        └─ 跨应用重启有效
```

---

## 配置项说明

### 用户配置 (config.json)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `notifications` | bool | `true` | 是否启用通知 |
| `session_token` | string | - | 登录令牌（必需） |
| `api_base` | string | - | API服务器地址（必需） |
| `client_version` | string | `"1.0.0"` | 客户端版本 |

### 系统服务配置

**macOS LaunchAgent**:
- 文件位置: `~/Library/LaunchAgents/site.sanying.aiperf.notification.plist`
- 运行间隔: 60秒
- 日志位置: `~/.ai_perf_client/logs/`

**Windows 任务计划程序**:
- 任务名称: `AiPerfNotificationService`
- 运行间隔: 1分钟
- 可执行文件: `pythonw.exe` (无窗口)

---

## 错误处理策略

### 运行时服务
- ✅ 所有异常静默捕获，不干扰主程序
- ✅ 网络错误自动重试（ApiClient 内置3次重试）
- ✅ 登录检查失败直接返回，不发起请求
- ✅ API 调用失败静默跳过

### 后台服务
- ✅ 异常记录到日志文件
- ✅ 配置加载失败静默退出
- ✅ API 调用失败静默跳过
- ✅ 通知发送失败不影响后续检查

---

## 性能考虑

1. **轮询间隔**：
   - 通知检查：60秒（平衡及时性和服务器负载）
   - 版本检查：5分钟（版本更新频率低）

2. **后台线程**：
   - 所有网络请求在后台线程执行
   - 不阻塞主线程和UI

3. **去重机制**：
   - 内存集合：O(1) 查找
   - 持久化文件：仅在后台服务中使用

4. **API 限制**：
   - 每次最多获取10条未读通知
   - 使用 `unread_only=true` 减少数据传输

---

## 测试建议

### 运行时通知测试
1. 启动应用
2. 等待3秒后检查轮询服务是否启动
3. 在服务器端创建测试通知
4. 等待最多60秒，应收到系统通知
5. 点击通知，应打开通知详情

### 后台服务测试
1. 安装后台服务
2. 关闭应用
3. 在服务器端创建测试通知
4. 等待最多60秒，应收到系统通知（即使应用未运行）

### 去重测试
1. 创建通知A
2. 收到通知A
3. 再次创建相同通知A（相同ID）
4. 不应重复收到通知

---

## 常见问题

### Q: 为什么有两个通知服务（NotificationService 和 PollingService）？
A: `NotificationService` 是早期实现，目前主窗口使用 `PollingService`（同时检查通知和版本）。`NotificationService` 保留作为备用。

### Q: 后台服务发送的通知点击后无法打开应用？
A: 这是系统限制。后台服务发送的通知是系统级通知，点击后无法直接打开应用。建议用户手动打开应用查看详情。

### Q: 如何禁用通知？
A: 在配置文件中设置 `"notifications": false`，或通过设置界面关闭通知开关。

### Q: 通知权限在哪里设置？
A: 
- macOS: 系统偏好设置 → 通知与专注模式
- Windows: 设置 → 系统 → 通知和操作
- Linux: 取决于桌面环境（GNOME/KDE/XFCE等）

---

## 更新日志

- 2025-01-XX: 修复主窗口未连接 notification_clicked 信号的问题
- 2025-01-XX: 添加登录状态双重检查，避免未登录时发起请求
- 2025-01-XX: 统一使用 PollingService，保留 NotificationService 作为备用
