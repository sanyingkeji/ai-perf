# Ai Perf 管理端客户端

管理端客户端用于管理员查看和管理所有员工的绩效评分数据，以及维护员工信息和账号绑定。

## 目录结构

```
admin_ui_client/
├── main.py                    # 主程序入口
├── config.json                # 配置文件（自动生成）
├── google_client_secret.json  # Google OAuth 客户端密钥（需手动配置）
│
├── utils/                     # 工具类
│   ├── __init__.py
│   ├── api_client.py          # 管理端 API 客户端
│   ├── config_manager.py      # 配置管理
│   ├── google_login.py        # Google OAuth 登录
│   └── theme_manager.py       # 主题管理
│
├── widgets/                   # 通用组件
│   ├── __init__.py
│   ├── toast.py               # 提示消息组件
│   └── loading_overlay.py     # 加载遮罩组件
│
├── windows/                   # 页面窗口
│   ├── __init__.py
│   ├── main_window.py         # 主窗口（导航和页面管理）
│   ├── history_score_view.py  # 历史评分管理页面
│   ├── employee_view.py       # 员工列表管理页面
│   └── settings_view.py       # 设置页面
│
└── themes/                    # 主题样式
    ├── theme_light.qss         # 浅色主题
    └── theme_dark.qss         # 深色主题
```

## 功能说明

### 1. 历史评分管理
- 支持按日期和员工筛选历史评分数据
- 查看每个员工的原始输入数据（AI输入JSON）
- 查看每个员工的输出数据（包含复评数据）
- 重新拉取指定员工的原始输入数据（重新执行ETL）

### 2. 员工列表管理
- 查看所有员工列表
- 添加新员工
- 编辑员工信息
- 删除员工（软删除：设置active=0）
- 查看和管理员工的外部平台账号绑定（Jira/GitHub/Figma等）
- 对账号绑定进行增删改操作

### 3. 设置
- 查看登录状态
- Google OAuth 登录
- 主题切换（自动/浅色/深色）
- API服务器地址配置

## 技术栈

- **UI框架**: PySide6 (Qt for Python)
- **API通信**: httpx
- **认证**: Google OAuth 2.0 + JWT
- **主题**: QSS (Qt Style Sheets)

## 配置要求

### 1. 环境变量（后端）
在项目根目录的 `.env` 文件中配置：
```
GOOGLE_CLIENT_ID=你的Google OAuth Client ID
ADMIN_EMAIL=管理员邮箱（如：admin@example.com）
SESSION_SECRET=随机密钥（用于签发JWT）
SESSION_EXPIRE_DAYS=7
ADMIN_API_PORT=8880  # 管理端API端口
```

### 2. Google OAuth 客户端密钥
1. 在 Google Cloud Console 创建 OAuth 2.0 Client（应用类型：桌面应用）
2. 下载 `client_secret.json`
3. 重命名为 `google_client_secret.json`
4. 放到 `admin_ui_client/` 目录下

### 3. 配置文件
首次运行会自动创建 `config.json`，默认配置：
```json
{
  "api_base": "http://127.0.0.1:8880",
  "session_token": "",
  "user_id": "",
  "user_name": "",
  "user_email": "",
  "theme": "auto"
}
```

## 运行方式

### 启动后端API服务器
```bash
cd /path/to/ai-perf
python api/admin_api_server.py
```

### 启动管理端客户端
```bash
cd /path/to/ai-perf/admin_ui_client
python main.py
```

## 权限说明

- 只有配置在 `.env` 中的 `ADMIN_EMAIL` 邮箱才能登录管理端
- 管理端和用户端接口完全独立，使用不同的端口和路径前缀
- 管理端API路径前缀：`/admin/api/*`
- 用户端API路径前缀：`/api/*`

## 依赖安装

```bash
pip install PySide6 httpx google-auth-oauthlib google-auth PyJWT
```

## 注意事项

1. 管理端和用户端使用相同的 Google OAuth 配置，但登录接口不同
   - 管理端：`POST /admin/auth/google_login`
   - 用户端：`POST /auth/google_login`

2. 管理端API服务器默认运行在 `8880` 端口，用户端API服务器默认运行在 `8000` 端口

3. 重新拉取数据功能会重新执行ETL流程，可能需要较长时间，请耐心等待

4. 员工删除为软删除（设置 `active=0`），不会真正删除数据库记录

