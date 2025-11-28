# GitHub Actions 调试指南

本文档介绍如何调试 GitHub Actions 构建失败的问题。

## 方法 1：使用管理端查看日志（推荐）

### 步骤：
1. 打开管理端 → **日常运维** → **打包** 标签页
2. 点击 **"Check Actions"** 按钮
3. 在 "All Workflows" 对话框中，找到失败的工作流运行
4. 点击 **"查看日志"** 按钮
5. 查看详细的构建日志

### 优势：
- 无需离开管理端
- 自动获取最新日志
- 支持搜索和复制

---

## 方法 2：在 GitHub 网页上查看

### 步骤：
1. 访问 GitHub 仓库：`https://github.com/sanyingkeji/ai-perf`
2. 点击 **"Actions"** 标签页
3. 选择失败的工作流运行
4. 点击具体的 Job（如 "Build macOS (Apple Silicon)"）
5. 展开失败的步骤，查看详细日志

### 优势：
- 官方界面，功能完整
- 支持下载日志文件
- 可以重新运行失败的 Job

---

## 方法 3：本地调试（使用 act 工具）

### 安装 act

**macOS:**
```bash
brew install act
```

**Linux:**
```bash
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
```

**Windows:**
```bash
choco install act-cli
# 或
scoop install act
```

### 运行工作流

```bash
# 列出所有工作流
act -l

# 运行特定工作流（需要 Docker）
act push

# 运行特定 Job
act -j build-macos-arm64

# 使用本地环境变量
act push --secret-file .secrets
```

### 注意事项：
- 需要安装 Docker
- 某些 macOS 特定的步骤可能无法在本地运行
- 适合调试工作流逻辑，不适合调试平台特定问题

---

## 方法 4：添加调试输出

### 在工作流中添加调试步骤

在 `.github/workflows/build-clients.yml` 中添加：

```yaml
- name: Debug information
  if: failure()  # 只在失败时运行
  run: |
    echo "=== 环境信息 ==="
    echo "Python 版本: $(python3 --version)"
    echo "工作目录: $(pwd)"
    echo "文件列表:"
    ls -la
    echo "=== 已安装的包 ==="
    pip3 list
    echo "=== PyInstaller 信息 ==="
    python3 -m PyInstaller --version || echo "PyInstaller 未安装"
```

### 在 Python 脚本中添加调试输出

在 `scripts/build_client.py` 中，我们已经添加了详细的错误输出。如果还需要更多信息，可以：

1. 设置环境变量 `PYINSTALLER_LOG_LEVEL=DEBUG`
2. 在关键步骤添加 `log_info()` 输出

---

## 方法 5：使用 GitHub Actions 的 SSH 调试功能

### 启用 SSH 调试

1. 在 GitHub 仓库设置中启用 "Debug logging"
2. 在 Actions 运行页面，点击 "..." → "Enable debug logging"
3. 重新运行工作流
4. 在运行页面会显示 SSH 连接信息
5. 使用 SSH 连接到运行器进行实时调试

### 限制：
- 需要 GitHub 企业版或某些计划
- 调试会话有时间限制

---

## 常见问题排查

### 1. PyInstaller 执行失败

**症状：**
```
subprocess.CalledProcessError: Command '['python3', '-m', 'PyInstaller', ...]' returned non-zero exit status 1.
```

**排查步骤：**
1. 查看 PyInstaller 的详细错误输出（现在已修复，会显示完整错误）
2. 检查依赖是否正确安装：`pip3 list | grep pyinstaller`
3. 检查 spec 文件是否正确：`cat ui_client/build_macos.spec`
4. 检查 Python 版本：`python3 --version`（需要 3.10+）

### 2. 依赖安装失败

**症状：**
```
ERROR: Could not find a version that satisfies the requirement ...
```

**排查步骤：**
1. 检查 `requirements.txt` 中的版本要求
2. 检查 Python 版本是否兼容
3. 检查网络连接（GitHub Actions 通常没问题）

### 3. 证书导入失败（macOS）

**症状：**
```
security: SecKeychainItemImport: User interaction is not allowed.
```

**排查步骤：**
1. 检查 p12 证书文件是否正确 Base64 编码
2. 检查密码是否正确
3. 检查证书是否过期

### 4. 构建产物未生成

**症状：**
```
错误: 应用包未生成
```

**排查步骤：**
1. 检查 `dist/` 目录内容：`ls -la dist/`
2. 检查 PyInstaller 是否成功执行（查看日志）
3. 检查 spec 文件中的输出路径配置

---

## 调试技巧

### 1. 使用 `set -x` 显示命令执行过程

在 shell 脚本中添加：
```bash
set -x  # 显示每个命令及其参数
# ... 你的命令 ...
set +x  # 关闭调试模式
```

### 2. 保存中间文件用于调试

```yaml
- name: Save debug artifacts
  if: failure()
  uses: actions/upload-artifact@v4
  with:
    name: debug-files
    path: |
      build/
      dist/
      *.log
    retention-days: 7
```

### 3. 使用条件执行

```yaml
- name: Debug step
  if: github.event_name == 'workflow_dispatch'  # 只在手动触发时运行
  run: |
    echo "调试信息..."
```

### 4. 分步执行

将复杂的步骤拆分成多个小步骤，便于定位问题：

```yaml
- name: Step 1
  run: echo "步骤 1"
  
- name: Step 2
  run: echo "步骤 2"
  
- name: Step 3
  run: echo "步骤 3"
```

---

## 快速调试清单

当 Actions 失败时，按以下顺序检查：

- [ ] 查看完整的错误日志（使用管理端或 GitHub 网页）
- [ ] 检查 PyInstaller 是否正确安装
- [ ] 检查 Python 版本是否正确（3.10）
- [ ] 检查依赖是否正确安装
- [ ] 检查 spec 文件是否存在且格式正确
- [ ] 检查环境变量是否正确设置
- [ ] 检查文件路径是否正确
- [ ] 检查权限问题（macOS 证书、文件权限等）

---

## 获取帮助

如果以上方法都无法解决问题：

1. 复制完整的错误日志
2. 记录工作流运行的 URL
3. 检查是否有相关的 GitHub Issues
4. 在仓库中创建 Issue，附上错误日志和复现步骤

