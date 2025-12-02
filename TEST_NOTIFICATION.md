# 开发模式下测试通知功能

## 快速开始

### 1. 测试基本系统通知

最简单的测试方法，直接发送一条系统通知：

```bash
# 在 ui_client 目录下运行
python3 test_notification.py --basic
```

或者直接运行通知模块：

```bash
python3 -m utils.notification
```

### 2. 测试后台服务管理

测试服务的安装、启用、禁用功能：

```bash
python3 test_notification.py --service
```

这会：
- 检查服务状态
- 如果未安装，自动安装服务
- 如果未启用，自动启用服务
- 显示服务状态信息

### 3. 测试后台服务脚本

测试后台服务脚本（模拟应用未运行时的场景）：

```bash
python3 test_notification.py --background
```

或者直接运行后台服务脚本：

```bash
# 单次执行模式（检查一次通知）
python3 ../scripts/notification_background_service.py --once

# 持续运行模式（每60秒检查一次）
python3 ../scripts/notification_background_service.py
```

### 4. 测试从 API 获取通知

需要先登录应用，然后测试从 API 获取通知：

```bash
python3 test_notification.py --api
```

### 5. 运行所有测试

```bash
python3 test_notification.py --all
```

## 详细测试步骤

### 场景 1: 测试系统通知（应用运行时）

1. **确保通知已启用**
   - 检查 `config.json` 中的 `notifications` 字段为 `true`

2. **运行测试**
   ```bash
   python3 test_notification.py --basic
   ```

3. **预期结果**
   - 系统通知栏应该显示一条测试通知
   - macOS: 右上角通知中心
   - Windows: 右下角 Toast 通知

### 场景 2: 测试后台服务（应用未运行时）

1. **安装后台服务**
   ```bash
   python3 test_notification.py --service
   ```

2. **关闭应用**（确保应用未运行）

3. **手动运行后台服务脚本**
   ```bash
   python3 ../scripts/notification_background_service.py --once
   ```

4. **预期结果**
   - 如果 API 有未读通知，应该发送系统通知
   - 即使应用未运行也能收到通知

### 场景 3: 测试服务自动运行

1. **安装并启用服务**
   ```bash
   python3 test_notification.py --service
   ```

2. **验证服务状态**
   - macOS: 运行 `launchctl list | grep site.sanying.aiperf.notification`
   - Windows: 运行 `schtasks /query /tn AiPerfNotificationService`

3. **等待 60 秒**，服务应该自动运行一次

4. **查看日志**
   - macOS: `~/.ai_perf_client/notification_service.log`
   - Windows: 任务计划程序日志

### 场景 4: 测试从 API 获取通知

1. **确保已登录**
   - 运行应用并登录
   - 检查 `config.json` 中有 `session_token`

2. **在管理端创建测试通知**
   - 打开管理端应用
   - 进入"通知管理"页面
   - 创建一条测试通知

3. **运行测试**
   ```bash
   python3 test_notification.py --api
   ```

4. **预期结果**
   - 应该能获取到未读通知
   - 第一条通知会被发送为系统通知

## 手动测试命令

### 测试系统通知

```bash
# 方法 1: 使用测试脚本
python3 test_notification.py --basic

# 方法 2: 直接运行通知模块
python3 -m utils.notification

# 方法 3: 使用示例代码
python3 -m utils.notification_example
```

### 测试服务管理

```bash
# 安装服务
python3 -m utils.system_notification_service install

# 启用服务
python3 -m utils.system_notification_service enable

# 禁用服务
python3 -m utils.system_notification_service disable

# 卸载服务
python3 -m utils.system_notification_service uninstall

# 查看状态
python3 -m utils.system_notification_service
```

### 测试后台服务脚本

```bash
# 单次执行（检查一次）
python3 ../scripts/notification_background_service.py --once

# 持续运行（每60秒检查一次，按 Ctrl+C 停止）
python3 ../scripts/notification_background_service.py
```

## 常见问题

### Q: macOS 通知权限问题

**问题**: macOS 上通知不显示

**解决**:
1. 首次运行时会自动弹出权限请求，点击"允许"
2. 如果已拒绝，需要手动授权：
   - 打开"系统偏好设置" > "通知"
   - 找到 "Python" 或应用名称
   - 启用通知权限

### Q: 后台服务脚本找不到

**问题**: `找不到后台服务脚本文件`

**解决**:
1. 确保 `scripts/notification_background_service.py` 存在
2. 检查路径是否正确
3. 在开发模式下，脚本应该在项目根目录的 `scripts` 文件夹

### Q: API 测试失败

**问题**: `未登录，无法测试 API 通知`

**解决**:
1. 先运行应用并登录
2. 确保 `config.json` 中有 `session_token`
3. 确保 `api_base` 配置正确

### Q: 服务安装失败

**问题**: macOS 上 `launchctl load` 失败

**解决**:
1. 检查是否有权限（可能需要管理员权限）
2. 如果服务已存在，先卸载再安装：
   ```bash
   launchctl unload ~/Library/LaunchAgents/site.sanying.aiperf.notification.plist
   ```

### Q: Windows 任务计划程序创建失败

**问题**: `创建任务失败`

**解决**:
1. 确保以管理员权限运行
2. 检查任务名称是否已存在：
   ```cmd
   schtasks /query /tn AiPerfNotificationService
   ```
3. 如果存在，先删除：
   ```cmd
   schtasks /delete /tn AiPerfNotificationService /f
   ```

## 调试技巧

### 查看日志

**macOS 后台服务日志**:
```bash
tail -f ~/.ai_perf_client/notification_service.log
tail -f ~/.ai_perf_client/notification_service_error.log
```

**Windows 任务计划程序日志**:
- 打开"任务计划程序"
- 找到 "AiPerfNotificationService" 任务
- 查看"历史记录"标签页

### 手动验证服务

**macOS**:
```bash
# 查看服务状态
launchctl list | grep site.sanying.aiperf.notification

# 手动加载服务
launchctl load ~/Library/LaunchAgents/site.sanying.aiperf.notification.plist

# 手动卸载服务
launchctl unload ~/Library/LaunchAgents/site.sanying.aiperf.notification.plist
```

**Windows**:
```cmd
# 查看任务状态
schtasks /query /tn AiPerfNotificationService /fo list

# 手动运行任务
schtasks /run /tn AiPerfNotificationService

# 启用任务
schtasks /change /tn AiPerfNotificationService /enable

# 禁用任务
schtasks /change /tn AiPerfNotificationService /disable
```

## 测试检查清单

- [ ] 基本系统通知可以正常显示
- [ ] macOS 通知权限已授权
- [ ] 后台服务可以正常安装
- [ ] 后台服务可以正常启用
- [ ] 后台服务脚本可以正常运行
- [ ] 应用未运行时，后台服务可以发送通知
- [ ] 从 API 可以正常获取通知
- [ ] 通知点击后可以打开应用（如果实现了）

