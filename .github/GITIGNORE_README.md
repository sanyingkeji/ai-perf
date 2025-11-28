# .gitignore 配置说明

此 `.gitignore` 文件配置为**只保留打包相关的文件和目录**，排除所有后端代码、数据库、部署脚本等。

## ✅ 会被包含的文件和目录

### 客户端代码
- `ui_client/` - 员工端客户端代码（排除构建产物和敏感配置）
- `admin_ui_client/` - 管理端客户端代码（排除构建产物和敏感配置）

### 打包脚本
- `scripts/build_client.py` - Python 打包脚本
- `scripts/build_client.sh` - Bash 打包脚本
- `scripts/PACKAGING.md` - 打包文档
- `scripts/inno_setup_template.iss` - Windows 打包模板

### GitHub Actions
- `.github/workflows/` - GitHub Actions 工作流配置

### 依赖文件
- `requirements.txt` - Python 依赖（虽然包含后端依赖，但客户端也需要）

### 项目文档
- `README.md` - 项目说明（如果存在）

## ❌ 会被排除的文件和目录

### 后端服务代码
- `api/` - 后端 API 服务
- `jobs/` - 后端任务
- `scorer/` - 评分系统
- `etl/` - ETL 数据处理
- `db.py` - 数据库连接
- `util_time.py` - 工具函数

### 数据库相关
- `*.sql` - 所有 SQL 文件
- `ai_perf.sql`
- `ai_perf.online.sql`

### 部署相关
- `deploy.sh` - 部署脚本（包含敏感信息）
- `DEPLOYMENT.md`
- `DEPLOYMENT_FILES.md`
- `AI_DEPLOYMENT_ASSISTANT.md`

### 测试和数据
- `tests/` - 测试代码
- `input/` - 输入数据
- `output/` - 输出数据
- `logs/` - 日志文件
- `backups/` - 备份文件
- `uploads/` - 上传文件

### AI 相关
- `prompts/` - AI 提示词
- `AI_Input_fields.json`
- `review_AI_Input_fields.json`
- `AI_md/` - AI 文档

### 其他后端脚本
- `scripts/` 目录中除了打包相关的脚本外，其他所有脚本

### 敏感配置文件
- `**/config.json` - 客户端配置文件（包含敏感信息）
- `**/google_client_secret.json` - Google OAuth 密钥
- `.env` - 环境变量文件
- 所有证书和密钥文件

### 构建产物和缓存
- `build/` - 构建目录
- `dist/` - 分发目录
- `__pycache__/` - Python 缓存
- `**/cache/` - 缓存目录

### 项目资料
- `项目背景资料/` - 项目背景资料
- `www/` - 网站文件

## 📝 使用说明

1. **初始化 Git 仓库**（如果还没有）：
   ```bash
   git init
   ```

2. **添加文件到 Git**：
   ```bash
   git add .
   ```

3. **检查哪些文件会被添加**：
   ```bash
   git status
   ```

4. **验证特定文件是否被排除**：
   ```bash
   git check-ignore -v <文件路径>
   ```

## ⚠️ 注意事项

1. **敏感信息**：客户端的 `config.json` 和 `google_client_secret.json` 会被排除，这些文件包含敏感信息，不应该上传到 GitHub。

2. **构建产物**：客户端的 `build/` 和 `dist/` 目录会被排除，这些是构建产物，不需要版本控制。

3. **缓存文件**：所有 `__pycache__/` 和 `cache/` 目录会被排除。

4. **如果需要上传配置文件模板**：可以创建 `config.json.example` 或 `config.json.template` 文件作为模板。

