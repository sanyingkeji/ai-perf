# 客户端打包指南

本文档说明如何将 PySide6 客户端打包成各种平台的安装包：

- **macOS**: DMG 磁盘映像和 PKG 安装包
- **Windows**: EXE 安装器（Inno Setup）和 MSI 安装包（WiX Toolset）
- **Linux**: DEB 安装包（Debian/Ubuntu）和 RPM 安装包（RedHat/CentOS/Fedora）

## 前置要求

### 通用依赖

```bash
pip3 install pyinstaller
```

### macOS 额外要求

- macOS 系统（用于打包 macOS 版本）
- Xcode Command Line Tools（已包含在系统中）
- 代码签名证书（可选，但推荐用于最新 macOS）

### Windows 额外要求

- Windows 系统（用于打包 Windows 版本）
- **Inno Setup**（用于创建 EXE 安装器）
  - 下载：https://jrsoftware.org/isinfo.php
  - 安装后将 `ISCC.exe` 添加到 PATH，或使用默认安装路径
- **WiX Toolset**（用于创建 MSI 安装包，可选）
  - 下载：https://wixtoolset.org/releases/
  - 推荐版本：WiX Toolset 6.0.2 或更高版本（也支持 3.11.x）
  - 安装后将 `candle.exe` 和 `light.exe` 添加到 PATH，或使用默认安装路径

### Linux 额外要求

- Linux 系统（用于打包 Linux 版本）
- **dpkg-deb**（用于创建 .deb 包）
  - Debian/Ubuntu: `sudo apt-get install dpkg-dev`
- **rpmbuild**（用于创建 .rpm 包）
  - RedHat/CentOS: `sudo yum install rpm-build`
  - Debian/Ubuntu: `sudo apt-get install rpm`

## 打包步骤

### 1. 准备图标

确保图标文件已生成：

```bash
# 在项目根目录运行
python3 scripts/convert_icons.py
```

### 2. 打包员工端

#### macOS

```bash
cd ui_client
python3 ../scripts/build_client.py employee macos
```

或使用 shell 脚本：

```bash
cd ui_client
./../scripts/build_client.sh employee macos
```

**输出文件：**
- `dist/Ai_Perf_Client.dmg` - DMG 磁盘映像（已签名和公证）
- `dist/Ai_Perf_Client.pkg` - PKG 安装包（已签名和公证）

#### Windows

```bash
cd ui_client
python3 ../scripts/build_client.py employee windows
```

**输出文件：**
- `dist/Ai Perf Client.exe` - 可执行文件
- `dist/Ai Perf Client_Setup.exe` - EXE 安装器（需要 Inno Setup）
- `dist/Ai Perf Client.msi` - MSI 安装包（需要 WiX Toolset）

#### Linux

```bash
cd ui_client
python3 ../scripts/build_client.py employee linux
```

**输出文件：**
- `dist/ai-perf-client_1.0.0_amd64.deb` - DEB 安装包（需要 dpkg-deb）
- `dist/ai-perf-client-1.0.0-1.x86_64.rpm` - RPM 安装包（需要 rpmbuild）

### 3. 打包管理端

#### macOS

```bash
cd admin_ui_client
python3 ../scripts/build_client.py admin macos
```

#### Windows

```bash
cd admin_ui_client
python3 ../scripts/build_client.py admin windows
```

#### Linux

```bash
cd admin_ui_client
python3 ../scripts/build_client.py admin linux
```

## macOS 代码签名和公证

### 为什么需要代码签名？

从 macOS 10.15 (Catalina) 开始，Apple 要求所有应用必须经过**代码签名**和**公证（Notarization）**，否则：

1. 用户首次打开时会看到"无法验证开发者"警告
2. 可能被 Gatekeeper 阻止运行
3. 需要用户在"系统偏好设置"中手动允许

### 代码签名步骤

#### 1. 获取开发者证书

1. 注册 [Apple Developer Program](https://developer.apple.com/programs/)（$99/年）
2. 在 [Apple Developer Portal](https://developer.apple.com/account/) 创建证书：
   - 登录 Apple Developer
   - 进入 "Certificates, Identifiers & Profiles"
   - 创建 "Developer ID Application" 证书（用于分发）
   - 下载并安装到 Keychain

#### 2. 查看证书

```bash
# 查看可用的签名证书
security find-identity -v -p codesigning
```

输出示例：
```
1) ABC1234567890ABCDEF1234567890ABCDEF1234 "Developer ID Application: Your Name (TEAM_ID)"
```

#### 3. 签名应用

```bash
# 设置证书标识（从上面的输出中复制）
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAM_ID)"

# 打包并自动签名
cd ui_client
CODESIGN_IDENTITY="$CODESIGN_IDENTITY" ../scripts/build_client.sh employee macos
```

或手动签名：

```bash
# 签名应用包
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name (TEAM_ID)" \
  "dist/Ai 绩效客户端.app"

# 验证签名
codesign --verify --verbose "dist/Ai 绩效客户端.app"
spctl --assess --verbose "dist/Ai 绩效客户端.app"
```

#### 4. 公证（Notarization）

公证是 Apple 的在线验证服务，确保应用没有恶意代码。

**前提条件：**
- 已签名应用
- 有 App-Specific Password（在 [Apple ID 账户页面](https://appleid.apple.com/) 生成）

**步骤：**

1. 创建 App-Specific Password：
   - 访问 https://appleid.apple.com/
   - 登录 → "App-Specific Passwords"
   - 生成新密码（用于 notarytool）

2. 上传并公证：

```bash
# 使用 notarytool（推荐，macOS 13+）
xcrun notarytool submit "dist/Ai Perf Admin.dmg" \
  --apple-id "WhitentonPolino7092@outlook.com" \
  --team-id "Q3JL96Q899" \
  --password "qdod-qfyd-tfwc-zgjs" \
  --wait

# 或使用 altool（macOS 12 及以下）
xcrun altool --notarize-app \
  --primary-bundle-id "site.sanying.aiperf.client" \
  --username "your-email@example.com" \
  --password "app-specific-password" \
  --file "dist/SanYing_AI_绩效客户端.dmg"
```

3. 检查公证状态：

```bash
# notarytool
xcrun notarytool history --apple-id "your-email@example.com" --team-id "YOUR_TEAM_ID"

# altool
xcrun altool --notarization-history 0 \
  --username "your-email@example.com" \
  --password "app-specific-password"
```

4. 装订票据（Staple）：

公证成功后，将票据装订到应用：

```bash
xcrun stapler staple "dist/Ai 绩效客户端.app"
xcrun stapler validate "dist/Ai 绩效客户端.app"
```

### 完整签名和公证脚本

创建 `scripts/sign_and_notarize.sh`：

```bash
#!/bin/bash
# macOS 代码签名和公证脚本

set -e

APP_NAME="$1"
CODESIGN_IDENTITY="$2"
APPLE_ID="$3"
TEAM_ID="$4"
APP_SPECIFIC_PASSWORD="$5"

if [ -z "$APP_NAME" ] || [ -z "$CODESIGN_IDENTITY" ]; then
    echo "用法: $0 <应用名称> <证书标识> <Apple ID> <Team ID> <App-Specific Password>"
    exit 1
fi

APP_BUNDLE="dist/${APP_NAME}.app"
DMG_PATH="dist/${APP_NAME// /_}.dmg"

# 1. 代码签名
echo "代码签名..."
codesign --deep --force --verify --verbose --sign "$CODESIGN_IDENTITY" "$APP_BUNDLE"

# 2. 验证签名
echo "验证签名..."
codesign --verify --verbose "$APP_BUNDLE"
spctl --assess --verbose "$APP_BUNDLE"

# 3. DMG 签名
if [ -f "$DMG_PATH" ]; then
    codesign --force --verify --verbose --sign "$CODESIGN_IDENTITY" "$DMG_PATH"
fi

# 4. 公证
if [ -n "$APPLE_ID" ] && [ -n "$TEAM_ID" ] && [ -n "$APP_SPECIFIC_PASSWORD" ]; then
    echo "上传公证..."
    xcrun notarytool submit "$DMG_PATH" \
      --apple-id "$APPLE_ID" \
      --team-id "$TEAM_ID" \
      --password "$APP_SPECIFIC_PASSWORD" \
      --wait
    
    echo "装订票据..."
    xcrun stapler staple "$APP_BUNDLE"
    xcrun stapler validate "$APP_BUNDLE"
fi

echo "完成！"
```

## 常见问题

### 1. macOS: "无法打开，因为无法验证开发者"

**原因：** 应用未签名或未公证

**解决：**
- 进行代码签名
- 进行公证
- 或让用户右键点击 → "打开"（仅首次）

### 2. macOS: "资源已损坏"

**原因：** 签名不正确或文件被修改

**解决：**
- 重新签名
- 检查证书是否有效

### 3. Windows: 杀毒软件误报

**原因：** PyInstaller 打包的文件可能被误判

**解决：**
- 使用代码签名证书签名 EXE
- 向杀毒软件厂商提交误报

### 4. 应用启动慢

**原因：** PyInstaller 单文件模式需要解压

**解决：**
- 使用目录模式（修改 spec 文件）
- 或使用 UPX 压缩（已启用）

## 安装包说明

### macOS 安装包

#### DMG 磁盘映像
- **用途**: 传统 macOS 分发方式，用户双击挂载后拖拽到 Applications 文件夹
- **特点**: 包含 Applications 快捷方式，方便安装
- **签名**: 已代码签名
- **公证**: 已通过 Apple 公证

#### PKG 安装包
- **用途**: 标准 macOS 安装包，双击运行安装向导
- **特点**: 更专业的安装体验，支持卸载
- **签名**: 已代码签名
- **公证**: 已通过 Apple 公证
- **安装位置**: `/Applications/{App Name}.app`

### Windows 安装包

#### EXE 安装器（Inno Setup）
- **用途**: 用户友好的安装向导
- **特点**: 
  - 支持中文界面
  - 可创建桌面快捷方式
  - 可创建开始菜单项
  - 支持卸载
- **安装位置**: `C:\Program Files\{App Name}\`
- **要求**: 需要安装 Inno Setup

#### MSI 安装包（WiX Toolset）
- **用途**: 企业级部署，支持组策略和批量安装
- **特点**:
  - 符合 Windows 安装标准
  - 支持静默安装：`msiexec /i {App Name}.msi /quiet`
  - 支持卸载：`msiexec /x {App Name}.msi /quiet`
- **安装位置**: `C:\Program Files\{App Name}\`
- **要求**: 需要安装 WiX Toolset

### Linux 安装包

#### DEB 安装包
- **用途**: Debian/Ubuntu 系统安装
- **安装**: `sudo dpkg -i {package}.deb`
- **卸载**: `sudo dpkg -r {package-name}`
- **依赖**: 需要 `dpkg-deb` 工具

#### RPM 安装包
- **用途**: RedHat/CentOS/Fedora 系统安装
- **安装**: `sudo rpm -i {package}.rpm` 或 `sudo yum install {package}.rpm`
- **卸载**: `sudo rpm -e {package-name}` 或 `sudo yum remove {package-name}`
- **依赖**: 需要 `rpmbuild` 工具

## 发布检查清单

### macOS

- [ ] 应用已代码签名
- [ ] DMG 已代码签名
- [ ] PKG 已代码签名
- [ ] 应用已公证
- [ ] DMG 已公证
- [ ] PKG 已公证
- [ ] 票据已装订到应用包
- [ ] 票据已装订到 PKG
- [ ] 在干净的系统上测试运行
- [ ] 检查 Gatekeeper 状态

### Windows

- [ ] EXE 已生成
- [ ] EXE 安装器已创建（如果安装了 Inno Setup）
- [ ] MSI 安装包已创建（如果安装了 WiX Toolset）
- [ ] 在干净的系统上测试安装和运行
- [ ] 测试卸载功能
- [ ] 检查杀毒软件误报

### Linux

- [ ] DEB 安装包已创建（如果安装了 dpkg-deb）
- [ ] RPM 安装包已创建（如果安装了 rpmbuild）
- [ ] 在对应的 Linux 发行版上测试安装和运行
- [ ] 测试卸载功能

## 参考资源

- [Apple Code Signing Guide](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/)
- [PyInstaller Documentation](https://pyinstaller.org/)
- [macOS Notarization](https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution)

