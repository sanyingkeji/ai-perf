# Ai Perf 员工端客户端

员工端客户端用于员工查看自己的绩效评分数据。



## 用户偏好设置保存位置

用户偏好设置（`config.json`）保存在用户配置目录中，而不是应用目录，这样可以避免在版本升级时被覆盖。

### 配置文件位置

根据操作系统不同，配置文件保存在以下位置：

- **Windows**: `%APPDATA%\ai-perf\config.json`
- **macOS**: `~/Library/Application Support/ai-perf/config.json`
- **Linux**: `~/.config/ai-perf/config.json`

### 自动迁移机制

如果检测到旧版本的应用目录中存在 `config.json`（`ui_client/config.json`），系统会自动将其迁移到新的用户配置目录，并保留所有用户自定义配置。

### 配置文件内容

配置文件包含以下用户偏好设置：
- API 基础地址（`api_base`）
- 登录凭证（`session_token`、`google_id_token`）
- 用户信息（`user_id`、`user_name`、`user_email`）
- 主题设置（`theme`：auto/light/dark）
- 自动刷新（`auto_refresh`）
- 通知设置（`notifications`）
- 日志保留时长（`log_retention_hours`）
- 客户端版本号（`client_version`）
- 其他应用设置

配置文件会在首次运行时自动创建，使用默认值。用户可以通过应用内的设置界面修改这些偏好。

