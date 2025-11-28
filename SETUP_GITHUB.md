# GitHub 仓库设置指南

## 步骤 1: 初始化 Git 仓库（如果还没有）

```bash
cd /Users/liuwei/Downloads/ai-perf
git init
```

## 步骤 2: 添加远程仓库

```bash
git remote add origin git@github.com:sanyingkeji/ai-perf.git
```

## 步骤 3: 检查哪些文件会被添加

```bash
# 查看会被添加的文件
git status

# 查看会被忽略的文件
git status --ignored
```

## 步骤 4: 添加文件到暂存区

```bash
# 添加所有符合 .gitignore 规则的文件
git add .
```

## 步骤 5: 提交文件

```bash
git commit -m "Initial commit: 客户端打包相关代码和 GitHub Actions 工作流"
```

## 步骤 6: 推送到 GitHub

```bash
# 首次推送，设置上游分支
git push -u origin main

# 如果默认分支是 master，使用：
# git push -u origin master
```

## 步骤 7: 验证推送结果

1. 访问 https://github.com/sanyingkeji/ai-perf
2. 确认以下内容已上传：
   - ✅ `ui_client/` 目录
   - ✅ `admin_ui_client/` 目录
   - ✅ `scripts/build_client.py` 和 `scripts/build_client.sh`
   - ✅ `.github/workflows/build-clients.yml`
   - ✅ `requirements.txt`
   - ✅ `.gitignore`

3. 确认以下内容**未**上传：
   - ❌ `api/` 目录
   - ❌ `db.py`
   - ❌ `*.sql` 文件
   - ❌ `deploy.sh`
   - ❌ `config.json` 和 `google_client_secret.json`

## 后续操作

### 配置 GitHub Secrets（用于 macOS 代码签名和公证）

1. 进入仓库设置：https://github.com/sanyingkeji/ai-perf/settings/secrets/actions
2. 添加以下 Secrets：
   - `CODESIGN_IDENTITY`: Developer ID Application 证书名称
   - `APPLE_ID`: Apple ID 邮箱
   - `TEAM_ID`: Apple Developer Team ID
   - `NOTARY_PASSWORD`: App-Specific Password

### 测试 GitHub Actions

1. 进入 Actions 页面：https://github.com/sanyingkeji/ai-perf/actions
2. 手动触发工作流：
   - 点击 "Build Clients"
   - 点击 "Run workflow"
   - 选择客户端类型和平台
   - 点击 "Run workflow"

### 创建标签触发自动发布

```bash
# 创建标签
git tag -a v1.0.0 -m "Release version 1.0.0"

# 推送标签
git push origin v1.0.0
```

创建标签后，GitHub Actions 会自动：
1. 构建所有平台的客户端
2. 创建 GitHub Release
3. 上传所有构建产物

## 常见问题

### 如果推送失败（分支名称不匹配）

```bash
# 检查当前分支
git branch

# 如果当前分支是 master，但远程是 main
git branch -M main
git push -u origin main
```

### 如果需要更新 .gitignore

```bash
# 修改 .gitignore 后
git add .gitignore
git commit -m "Update .gitignore"
git push
```

### 如果需要强制推送（谨慎使用）

```bash
# 只有在确定要覆盖远程仓库时才使用
git push -f origin main
```

