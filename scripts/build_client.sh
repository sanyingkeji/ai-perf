#!/bin/bash
# 客户端打包脚本
# 支持 Windows (exe) 和 macOS (dmg)

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 带时间戳的日志输出函数
log_with_time() {
    local color="$1"
    shift
    local message="$*"
    local timestamp=$(date "+%Y-%m-%d %H:%M:%S")
    echo -e "${color}[${timestamp}] ${message}${NC}"
}

log_info() {
    log_with_time "$GREEN" "$@"
}

log_warn() {
    log_with_time "$YELLOW" "$@"
}

log_error() {
    log_with_time "$RED" "$@"
}

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 客户端类型
CLIENT_TYPE="${1:-employee}"  # employee 或 admin

if [ "$CLIENT_TYPE" != "employee" ] && [ "$CLIENT_TYPE" != "admin" ]; then
    log_error "错误: 客户端类型必须是 'employee' 或 'admin'"
    echo "用法: $0 [employee|admin] [windows|macos]"
    exit 1
fi

# 平台
PLATFORM="${2:-$(uname -s | tr '[:upper:]' '[:lower:]')}"

if [ "$PLATFORM" = "darwin" ]; then
    PLATFORM="macos"
elif [ "$PLATFORM" = "linux" ]; then
    PLATFORM="linux"
fi

# 客户端目录
if [ "$CLIENT_TYPE" = "employee" ]; then
    CLIENT_DIR="$PROJECT_ROOT/ui_client"
    APP_NAME="Ai Perf Client"
    APP_ID="site.sanying.aiperf.client"
else
    CLIENT_DIR="$PROJECT_ROOT/admin_ui_client"
    APP_NAME="Ai Perf Admin"
    APP_ID="site.sanying.aiperf.admin"
fi

cd "$CLIENT_DIR"

START_TIME=$(date +%s)
log_info "========================================"
log_info "打包 ${APP_NAME}"
log_info "平台: ${PLATFORM}"
log_info "========================================"
echo ""

# 检查 PyInstaller
if ! command -v pyinstaller &> /dev/null; then
    log_warn "安装 PyInstaller..."
    pip3 install pyinstaller
fi

# 清理之前的构建
log_warn "清理之前的构建..."
rm -rf build/ dist/ *.spec.bak

# 备份并修改 config.json（打包时使用）
CONFIG_FILE="config.json"
CONFIG_BACKUP="config.json.bak"
CONFIG_MODIFIED=false

if [ -f "$CONFIG_FILE" ]; then
    log_warn "备份并修改 config.json..."
    # 备份原始文件
    cp "$CONFIG_FILE" "$CONFIG_BACKUP"
    CONFIG_MODIFIED=true
    
    # 使用 Python 修改 JSON（更可靠）
    python3 << 'PYTHON_SCRIPT'
import json
import sys

config_file = "config.json"
try:
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 清空登录信息和隐私数据
    privacy_fields = [
        # 登录信息
        "session_token",
        "google_id_token",
        "user_id",
        "user_name",
        "user_email",
        # Jira 相关
        "jira_token",
        "jira_account_email",
        "jira_base",
    ]
    for field in privacy_fields:
        if field in config:
            config[field] = ""
    
    # 清空 update_dialog_dismissed_date
    if "update_dialog_dismissed_date" in config:
        config["update_dialog_dismissed_date"] = ""
    
    # 修改 api_base
    config["api_base"] = "https://api-perf.sanying.site"
    
    # 设置 upload_api_url
    config["upload_api_url"] = "https://file.sanying.site/api/upload"
    
    # 保存修改后的配置（确保对齐）
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False, sort_keys=True)
    
    print("✓ config.json 已修改（清空登录信息，设置 api_base 为 https://api-perf.sanying.site）")
except Exception as e:
    print(f"错误: 修改 config.json 失败: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_SCRIPT
    
    if [ $? -eq 0 ]; then
        log_info "✓ config.json 已修改"
    else
        log_error "✗ config.json 修改失败"
        exit 1
    fi
fi

if [ "$PLATFORM" = "macos" ]; then
    # macOS 打包
    log_warn "开始 macOS 打包..."
    
    # 使用 macOS spec 文件
    SPEC_FILE="build_macos.spec"
    if [ ! -f "$SPEC_FILE" ]; then
        log_error "错误: 找不到 $SPEC_FILE"
        exit 1
    fi
    
    # 打包
    log_warn "执行 PyInstaller 打包..."
    pyinstaller "$SPEC_FILE" --clean --noconfirm --log-level=ERROR > /dev/null 2>&1
    
    APP_BUNDLE="dist/${APP_NAME}.app"
    
    if [ ! -d "$APP_BUNDLE" ]; then
        log_error "错误: 应用包未生成"
        exit 1
    fi
    
    log_info "✓ 应用包生成成功: $APP_BUNDLE"
    
    # 代码签名配置（使用默认值或环境变量）
    CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Portier Hong Kong Holdings Limited (Q3JL96Q899)}"
    APPLE_ID="${APPLE_ID:-WhitentonPolino7092@outlook.com}"
    TEAM_ID="${TEAM_ID:-Q3JL96Q899}"
    NOTARY_PASSWORD="${NOTARY_PASSWORD:-qdod-qfyd-tfwc-zgjs}"
    
    # 代码签名
    if [ -n "$CODESIGN_IDENTITY" ]; then
        log_warn "代码签名（先签名所有依赖库）..."
        
        FRAMEWORKS_DIR="$APP_BUNDLE/Contents/Frameworks"
        if [ -d "$FRAMEWORKS_DIR" ]; then
            # 第一步：签名所有独立的 .dylib 文件（不包括框架内的）
            log_info "  签名独立的 .dylib 文件..."
            find "$FRAMEWORKS_DIR" -name "*.dylib" ! -path "*.framework/*" -print0 | while IFS= read -r -d '' dylib; do
                log_info "    签名: $(basename "$dylib")"
                codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$dylib" 2>/dev/null || true
            done
            
            # 签名无扩展名的 Mach-O 文件（如 QtWidgets, QtCore 等）
            log_info "  签名无扩展名的 Mach-O 文件..."
            for item in "$FRAMEWORKS_DIR"/*; do
                if [ -f "$item" ] && [ ! "${item##*.}" = "dylib" ] && [ ! "${item##*.}" = "so" ] && [[ ! "$item" =~ \.framework ]]; then
                    if file "$item" 2>/dev/null | grep -qE "(Mach-O|executable)"; then
                        log_info "    签名: $(basename "$item")"
                        codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$item" 2>/dev/null || true
                    fi
                fi
            done
            
            # 第二步：签名 Qt 框架（.framework 目录）
            QT_DIR="$FRAMEWORKS_DIR/PySide6/Qt"
            if [ -d "$QT_DIR" ]; then
                log_info "  签名 Qt 框架..."
                # 查找所有 .framework 目录
                find "$QT_DIR" -name "*.framework" -type d -print0 | while IFS= read -r -d '' framework_dir; do
                    framework_name=$(basename "$framework_dir")
                    log_info "    签名框架: $framework_name"
                    
                    # 先签名框架内的所有二进制文件
                    find "$framework_dir" -type f ! -name "*.plist" ! -name "*.qm" ! -name "*.png" ! -name "*.json" -print0 | while IFS= read -r -d '' qt_file; do
                        if file "$qt_file" 2>/dev/null | grep -qE "(Mach-O|executable)"; then
                            codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$qt_file" 2>/dev/null || true
                        fi
                    done
                    
                    # 然后签名整个框架目录
                    codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$framework_dir" 2>/dev/null || true
                done
                
                # 签名 Qt 目录中的其他二进制文件（非框架）
                log_info "  签名 Qt 其他二进制文件..."
                find "$QT_DIR" -type f ! -path "*.framework/*" ! -name "*.plist" ! -name "*.qm" ! -name "*.png" ! -name "*.json" -print0 | while IFS= read -r -d '' qt_file; do
                    if file "$qt_file" 2>/dev/null | grep -qE "(Mach-O|executable)"; then
                        log_info "    签名: $(basename "$qt_file")"
                        codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$qt_file" 2>/dev/null || true
                    fi
                done
            fi
            
            # 第三步：签名所有 .so 文件（它们依赖已签名的框架）
            log_info "  签名 .so 文件..."
            find "$FRAMEWORKS_DIR" -name "*.so" -print0 | while IFS= read -r -d '' so_file; do
                log_info "    签名: $(basename "$so_file")"
                codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$so_file" 2>/dev/null || true
            done
        fi
        
        # 最后签名整个应用包
        log_warn "签名应用包..."
        codesign --deep --force --verify --verbose --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$APP_BUNDLE"
        
        # 签名后，再次验证并修复关键文件（因为 --deep 可能会破坏签名）
        log_warn "签名后验证并修复关键文件..."
        RE_SIGN_NEEDED=false
        
        # 查找 Contents/Frameworks 下的无扩展名 Mach-O 文件
        find "$FRAMEWORKS_DIR" -maxdepth 1 -type f ! -name "*.dylib" ! -name "*.so" ! -name "*.plist" ! -name "*.qm" ! -name "*.png" ! -name "*.json" -print0 | while IFS= read -r -d '' item; do
            if file "$item" 2>/dev/null | grep -qE "(Mach-O|executable)"; then
                # 使用 -vvv 检查签名状态（这会检测到 "invalid Info.plist" 错误）
                if ! codesign -vvv "$item" 2>&1 | grep -qE "(valid on disk|satisfies)"; then
                    ERROR_MSG=$(codesign -vvv "$item" 2>&1 | grep -E "(invalid|error|fail)" | head -1)
                    log_warn "    发现签名无效: $(basename "$item")，重新签名..."
                    log_warn "      错误信息: $ERROR_MSG"
                    codesign --force --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$item" 2>/dev/null || true
                    # 再次验证
                    if codesign -vvv "$item" 2>&1 | grep -qE "(valid on disk|satisfies)"; then
                        log_info "      ✓ 重新签名成功"
                    else
                        log_warn "      ⚠ 重新签名后验证仍失败"
                    fi
                    RE_SIGN_NEEDED=true
                fi
            fi
        done
        
        # 如果修复了文件，重新签名应用包
        if [ "$RE_SIGN_NEEDED" = true ]; then
            log_warn "关键文件已修复，重新签名应用包以包含修复..."
            codesign --deep --force --verify --verbose --sign "$CODESIGN_IDENTITY" --options runtime --timestamp=none "$APP_BUNDLE"
            log_info "✓ 应用包已重新签名以包含修复"
        fi
        
        # 验证签名
        log_warn "验证签名..."
        codesign --verify --verbose --deep "$APP_BUNDLE"
        
        log_info "✓ 应用包代码签名完成"
    else
        log_warn "⚠ 跳过代码签名（设置 CODESIGN_IDENTITY 环境变量以启用）"
    fi
    
    # 创建 DMG
    log_warn "创建 DMG..."
    DMG_NAME="${APP_NAME// /_}"
    DMG_PATH="dist/${DMG_NAME}.dmg"
    
    # 创建临时目录
    TEMP_DMG_DIR="dist/dmg_temp"
    rm -rf "$TEMP_DMG_DIR"
    mkdir -p "$TEMP_DMG_DIR"
    
    # 复制应用和创建 Applications 链接
    cp -R "$APP_BUNDLE" "$TEMP_DMG_DIR/"
    ln -s /Applications "$TEMP_DMG_DIR/Applications"
    
    # 创建 DMG
    hdiutil create -volname "$APP_NAME" -srcfolder "$TEMP_DMG_DIR" -ov -format UDZO "$DMG_PATH"
    
    # 清理临时目录
    rm -rf "$TEMP_DMG_DIR"
    
    log_info "✓ DMG 创建成功: $DMG_PATH"
    
    # DMG 代码签名
    if [ -n "$CODESIGN_IDENTITY" ]; then
        log_warn "DMG 代码签名..."
        codesign --force --verify --verbose --sign "$CODESIGN_IDENTITY" --timestamp=none "$DMG_PATH"
        log_info "✓ DMG 代码签名完成"
    fi
    
    # Apple 公证（带重试机制）
    if [ -n "$APPLE_ID" ] && [ -n "$TEAM_ID" ] && [ -n "$NOTARY_PASSWORD" ]; then
        log_warn "提交 Apple 公证..."
        MAX_RETRIES=3
        RETRY_DELAY=10
        NOTARIZED=false
        
        for attempt in $(seq 1 $MAX_RETRIES); do
            log_info "  尝试 ${attempt}/${MAX_RETRIES}..."
            if xcrun notarytool submit "$DMG_PATH" \
                --apple-id "$APPLE_ID" \
                --team-id "$TEAM_ID" \
                --password "$NOTARY_PASSWORD" \
                --wait 2>&1 | tee /tmp/notary_output.log; then
                log_info "✓ Apple 公证完成"
                NOTARIZED=true
                break
            else
                NOTARY_ERROR=$(cat /tmp/notary_output.log 2>/dev/null || echo "")
                # 检查是否是网络相关错误
                if echo "$NOTARY_ERROR" | grep -qiE "(network|connection|timeout|resolve|unreachable)"; then
                    if [ $attempt -lt $MAX_RETRIES ]; then
                        log_error "✗ Apple 公证网络错误（尝试 ${attempt}/${MAX_RETRIES}）"
                        log_warn "  等待 ${RETRY_DELAY} 秒后重试..."
                        sleep $RETRY_DELAY
                        RETRY_DELAY=$((RETRY_DELAY * 2))  # 指数退避
                    else
                        log_error "✗ Apple 公证最终失败（已重试所有次数）"
                        log_warn "⚠ 继续执行，但 DMG 未通过公证"
                    fi
                else
                    log_error "✗ Apple 公证失败（尝试 ${attempt}/${MAX_RETRIES}）"
                    if [ -n "$NOTARY_ERROR" ]; then
                        echo "$NOTARY_ERROR" | head -20
                    fi
                    if [ $attempt -lt $MAX_RETRIES ]; then
                        log_warn "  等待 ${RETRY_DELAY} 秒后重试..."
                        sleep $RETRY_DELAY
                        RETRY_DELAY=$((RETRY_DELAY * 2))
                    else
                        log_error "✗ Apple 公证最终失败（已重试所有次数）"
                        log_warn "⚠ 继续执行，但 DMG 未通过公证"
                    fi
                fi
            fi
        done
        
        rm -f /tmp/notary_output.log
    else
        log_warn "⚠ 跳过 Apple 公证（需要设置 APPLE_ID, TEAM_ID, NOTARY_PASSWORD 环境变量）"
    fi
    
elif [ "$PLATFORM" = "windows" ] || [ "$PLATFORM" = "win" ]; then
    # Windows 打包
    log_warn "开始 Windows 打包..."
    
    # 使用 Windows spec 文件
    SPEC_FILE="build.spec"
    if [ ! -f "$SPEC_FILE" ]; then
        log_error "错误: 找不到 $SPEC_FILE"
        exit 1
    fi
    
    # 打包
    log_warn "执行 PyInstaller 打包..."
    pyinstaller "$SPEC_FILE" --clean --noconfirm --log-level=ERROR > /dev/null 2>&1
    
    EXE_PATH="dist/${APP_NAME}.exe"
    
    if [ ! -f "$EXE_PATH" ]; then
        log_error "错误: EXE 文件未生成"
        exit 1
    fi
    
    log_info "✓ EXE 文件生成成功: $EXE_PATH"
    
else
    log_error "错误: 不支持的平台: $PLATFORM"
    echo "支持的平台: macos, windows"
    exit 1
fi

# 恢复 config.json（如果之前修改过）
if [ "$CONFIG_MODIFIED" = true ] && [ -f "$CONFIG_BACKUP" ]; then
    log_warn "恢复 config.json..."
    mv "$CONFIG_BACKUP" "$CONFIG_FILE"
    log_info "✓ config.json 已恢复"
fi

ELAPSED_TIME=$(($(date +%s) - START_TIME))
ELAPSED_MINUTES=$(echo "scale=2; $ELAPSED_TIME / 60" | bc)
echo ""
log_info "========================================"
log_info "打包完成！"
log_info "总耗时: ${ELAPSED_TIME} 秒 (${ELAPSED_MINUTES} 分钟)"
log_info "========================================"
echo ""
echo "输出文件在: $CLIENT_DIR/dist/"

