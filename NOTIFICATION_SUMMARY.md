# 系统通知服务总结

## 核心架构

**双重保障机制**：确保用户无论应用是否运行都能收到通知

```
┌─────────────────────────────────────────┐
│  运行时通知 (PollingService)              │
│  - 每60秒轮询一次                         │
│  - 实时显示系统通知                        │
│  - 支持点击回调                            │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  后台服务 (SystemNotificationService)    │
│  - 系统级定时任务                          │
│  - 应用未运行时也能发送通知                 │
│  - macOS: LaunchAgent                    │
│  - Windows: 任务计划程序                   │
└─────────────────────────────────────────┘
```

---

## 关键文件

| 文件 | 职责 |
|------|------|
| `ui_client/utils/polling_service.py` | **主要使用** - 统一轮询服务（通知+版本） |
| `ui_client/utils/notification_service.py` | 备用 - 独立通知服务 |
| `ui_client/utils/system_notification_service.py` | 系统服务管理器（安装/启用/禁用） |
| `ui_client/utils/notification.py` | 跨平台通知工具（macOS/Windows/Linux） |
| `scripts/notification_background_service.py` | 后台服务脚本（应用未运行时） |
| `ui_client/windows/main_window.py` | 主窗口集成（启动和管理服务） |

---

## 工作流程

### 应用启动时
1. 延迟2秒 → 设置后台通知服务（安装/启用）
2. 延迟3秒 → 启动轮询服务（开始检查通知）

### 运行时通知检查
```
每60秒 → 检查登录 → 调用API → 过滤去重 → 发送系统通知
```

### 后台服务检查
```
系统定时器（每60秒/1分钟）→ 加载配置 → 调用API → 发送系统通知
```

---

## 去重机制

- **运行时**：内存集合 `_checked_notification_ids`（应用运行期间）
- **后台服务**：持久化文件 `sent_notifications.json`（跨应用重启）

---

## 配置项

```json
{
  "notifications": true,        // 通知开关
  "session_token": "...",      // 登录令牌
  "api_base": "http://...",    // API地址
  "client_version": "1.0.0"    // 客户端版本
}
```

---

## API 端点

```
GET /api/notifications?unread_only=true&limit=10
Headers: Authorization: Bearer {session_token}
```

---

## 信号和回调

### PollingService 信号
- `notification_received(dict)` - 收到新通知
- `notification_clicked(dict)` - 通知被点击
- `version_update_available(dict)` - 检测到新版本

### 主窗口处理
- `_on_notification_received()` - 处理收到通知（可更新UI）
- `_on_notification_clicked()` - 处理通知点击（显示详情）

---

## 平台支持

| 平台 | 通知实现 | 后台服务 |
|------|---------|---------|
| macOS | NSUserNotificationCenter / osascript | LaunchAgent |
| Windows | winrt / win10toast / PowerShell | 任务计划程序 |
| Linux | notify-send / plyer / dbus | (待实现) |

---

## 关键特性

✅ **双重保障**：运行时 + 后台服务  
✅ **去重机制**：避免重复通知  
✅ **登录检查**：未登录时不发起请求  
✅ **后台线程**：不阻塞主线程  
✅ **错误处理**：静默失败，不干扰主程序  
✅ **跨平台**：支持 macOS/Windows/Linux  

---

## 注意事项

1. **主窗口使用 PollingService**，不是 NotificationService
2. **后台服务需要系统权限**（macOS/Windows）
3. **通知权限需要用户授权**（首次使用时）
4. **已发送通知ID持久化**，避免重复发送

---

## 相关文档

- `NOTIFICATION_SYSTEM_ARCHITECTURE.md` - 详细架构文档
- `NOTIFICATION_FLOW.md` - 流程图和交互图
- `NOTIFICATION_GUIDE.md` - 使用指南（如果存在）
