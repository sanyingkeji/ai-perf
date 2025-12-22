#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图标转换脚本
将 ui_client/logo.png 和 admin_ui_client/logo.png 转换为不同规格和格式的图标文件
"""

import sys
from pathlib import Path
from PIL import Image
import subprocess
import shutil

# 项目根目录
ROOT_DIR = Path(__file__).parent.parent

# 源文件（两端分别使用不同的图标）
UI_CLIENT_LOGO = ROOT_DIR / "ui_client" / "logo.png"
ADMIN_CLIENT_LOGO = ROOT_DIR / "admin_ui_client" / "logo.png"

# 目标目录
UI_CLIENT_RESOURCES = ROOT_DIR / "ui_client" / "resources"
ADMIN_CLIENT_RESOURCES = ROOT_DIR / "admin_ui_client" / "resources"

# 需要的图标尺寸
ICON_SIZES = [16, 32, 48, 64, 128, 256, 512, 1024]

# macOS .icns 需要的尺寸
ICNS_SIZES = [16, 32, 128, 256, 512, 1024]

# Windows .ico 需要的尺寸（统一使用 256x256 作为主要尺寸，确保清晰显示）
# 包含多个尺寸以便 Windows 在不同 DPI 下选择合适的尺寸
ICO_SIZES = [16, 32, 48, 64, 128, 256]


def ensure_dir(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)


def create_rounded_corner_mask(size: int, radius: int) -> Image.Image:
    """创建圆角矩形遮罩
    
    Args:
        size: 图片尺寸（正方形）
        radius: 圆角半径（像素）
    
    Returns:
        圆角矩形遮罩（RGBA 模式，透明背景）
    """
    mask = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    
    # 使用 PIL 的 ImageDraw 绘制圆角矩形
    from PIL import ImageDraw
    
    draw = ImageDraw.Draw(mask)
    
    # 绘制圆角矩形（白色，不透明）
    # 使用 rounded_rectangle（PIL 9.0+）或手动绘制
    try:
        # PIL 9.0+ 支持 rounded_rectangle
        draw.rounded_rectangle(
            [(0, 0), (size - 1, size - 1)],
            radius=radius,
            fill=(255, 255, 255, 255)
        )
    except AttributeError:
        # 旧版 PIL，手动绘制圆角矩形
        # 绘制主体矩形
        draw.rectangle(
            [(radius, 0), (size - radius - 1, size - 1)],
            fill=(255, 255, 255, 255)
        )
        draw.rectangle(
            [(0, radius), (size - 1, size - radius - 1)],
            fill=(255, 255, 255, 255)
        )
        # 绘制四个圆角
        for corner_x, corner_y in [
            (radius, radius),  # 左上
            (size - radius - 1, radius),  # 右上
            (radius, size - radius - 1),  # 左下
            (size - radius - 1, size - radius - 1)  # 右下
        ]:
            draw.ellipse(
                [(corner_x - radius, corner_y - radius),
                 (corner_x + radius, corner_y + radius)],
                fill=(255, 255, 255, 255)
            )
    
    return mask


def apply_rounded_corners(img: Image.Image, radius: int = None, corner_ratio: float = 0.12) -> Image.Image:
    """应用圆角处理到图片
    
    Args:
        img: 原始图片（RGBA 模式）
        radius: 圆角半径（像素），如果为 None，则根据图片尺寸自动计算
        corner_ratio: 圆角半径比例（相对于图片尺寸），默认 0.12 (12%)
    
    Returns:
        应用圆角后的图片
    """
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    width, height = img.size
    
    # 如果未指定半径，根据图片尺寸自动计算
    if radius is None:
        radius = max(8, int(min(width, height) * corner_ratio))
    
    # 确保半径不超过图片尺寸的一半
    radius = min(radius, min(width, height) // 2)
    
    # 创建圆角遮罩
    mask = create_rounded_corner_mask(max(width, height), radius)
    
    # 如果图片不是正方形，需要调整遮罩大小
    if width != height:
        mask = mask.resize((width, height), Image.Resampling.LANCZOS)
    
    # 应用遮罩：将遮罩的 alpha 通道应用到图片
    alpha = mask.split()[3]  # 获取遮罩的 alpha 通道
    result = img.copy()
    result.putalpha(alpha)  # 使用遮罩的 alpha 通道裁剪图片
    
    return result


def apply_macos_safe_area(img: Image.Image, safe_area_ratio: float = 0.85) -> Image.Image:
    """为 macOS 图标应用安全区域（缩小内容，留出边距）
    
    Args:
        img: 原始图片（RGBA 模式）
        safe_area_ratio: 安全区域比例，默认 0.85（即内容占 85%，边距 15%）
    
    Returns:
        应用安全区域后的图片（完全透明的背景）
    """
    # 确保是 RGBA 模式
    if img.mode == 'RGB':
        # RGB 转 RGBA，添加完全透明的 alpha 通道
        img = img.convert('RGBA')
    elif img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    width, height = img.size
    
    # 计算新的尺寸（缩小内容）
    new_width = int(width * safe_area_ratio)
    new_height = int(height * safe_area_ratio)
    
    # 计算居中位置
    offset_x = (width - new_width) // 2
    offset_y = (height - new_height) // 2
    
    # 创建新图片（完全透明的背景，RGBA 模式）
    result = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    
    # 缩小原图并居中放置
    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    # 确保 resized 是 RGBA 模式以保留透明度
    if resized.mode != 'RGBA':
        resized = resized.convert('RGBA')
    
    # 使用 alpha 通道进行粘贴，确保透明度正确
    result.paste(resized, (offset_x, offset_y), resized)
    
    # 验证结果：确保背景区域是完全透明的
    # 检查四个角的像素是否透明
    corners = [(0, 0), (width-1, 0), (0, height-1), (width-1, height-1)]
    for x, y in corners:
        pixel = result.getpixel((x, y))
        if len(pixel) == 4 and pixel[3] != 0:
            # 如果角落不透明，强制设置为透明
            result.putpixel((x, y), (0, 0, 0, 0))
    
    return result


def convert_to_png(source: Path, output_dir: Path, sizes: list):
    """转换为不同尺寸的 PNG 文件（带圆角处理）"""
    print(f"  生成 PNG 文件...")
    try:
        img = Image.open(source)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # 应用圆角处理
        img = apply_rounded_corners(img)
        
        # 生成主图标（1024x1024）
        main_icon = img.resize((1024, 1024), Image.Resampling.LANCZOS)
        # 重新应用圆角（因为 resize 可能会影响圆角）
        main_icon = apply_rounded_corners(main_icon)
        main_icon_path = output_dir / "app_icon.png"
        main_icon.save(main_icon_path, "PNG", optimize=True)
        print(f"    ✓ {main_icon_path.name} (1024x1024, 圆角处理)")
        
        # 生成其他尺寸（可选，用于打包时）
        for size in sizes:
            if size != 1024:
                resized = img.resize((size, size), Image.Resampling.LANCZOS)
                # 重新应用圆角
                resized = apply_rounded_corners(resized)
                size_path = output_dir / f"app_icon_{size}x{size}.png"
                resized.save(size_path, "PNG", optimize=True)
        
        return True
    except Exception as e:
        print(f"    ✗ PNG 转换失败: {e}")
        return False


def convert_to_ico(source: Path, output_dir: Path, sizes: list):
    """转换为 Windows .ico 文件（带圆角处理）
    
    注意：PIL 的 ICO 格式支持有限，可能无法包含所有尺寸。
    为了确保 Windows 高 DPI 支持，建议使用专业工具（如 ImageMagick 或在线转换器）
    生成包含多个尺寸的 ICO 文件。
    """
    print(f"  生成 ICO 文件...")
    try:
        img = Image.open(source)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # 先应用圆角处理
        img = apply_rounded_corners(img)
        
        # .ico 文件可以包含多个尺寸
        # 注意：PIL 的 ICO 保存可能只支持部分尺寸，我们尝试保存所有尺寸
        ico_images = []
        for size in sizes:
            resized = img.resize((size, size), Image.Resampling.LANCZOS)
            # 重新应用圆角（因为 resize 可能会影响圆角）
            resized = apply_rounded_corners(resized)
            ico_images.append(resized)
        
        ico_path = output_dir / "app_icon.ico"
        
        # 确保 256 尺寸存在（这是 Windows 清晰显示的关键尺寸）
        if 256 not in sizes:
            print(f"    ⚠ 警告: 尺寸列表中不包含 256，将自动添加以确保清晰显示")
            sizes.append(256)
            # 生成 256 尺寸的图片
            size_256_img = img.resize((256, 256), Image.Resampling.LANCZOS)
            size_256_img = apply_rounded_corners(size_256_img)
            # 找到 256 应该插入的位置（保持排序）
            sizes.sort()
            ico_images.insert(sizes.index(256), size_256_img)
        
        # 尝试保存包含所有尺寸的 ICO 文件
        # Windows 标准 ICO 格式支持：16, 32, 48, 64, 128, 256
        try:
            # 确保 256 尺寸的图片在列表中（作为主要尺寸）
            size_256_img = next((img for img, size in zip(ico_images, sizes) if size == 256), None)
            if size_256_img is None:
                # 如果找不到，生成一个
                size_256_img = img.resize((256, 256), Image.Resampling.LANCZOS)
                size_256_img = apply_rounded_corners(size_256_img)
            
            # 使用 256 尺寸作为第一个图片（主要尺寸），确保 Windows 优先使用
            # 找到 256 在 sizes 中的索引
            size_256_index = sizes.index(256)
            # 重新排列，将 256 放在第一位
            main_image = ico_images[size_256_index]
            other_images = [img for i, img in enumerate(ico_images) if i != size_256_index]
            
            main_image.save(
                ico_path,
                format='ICO',
                sizes=[(s, s) for s in sizes],
                append_images=other_images if other_images else []
            )
            print(f"    ✓ {ico_path.name} (包含尺寸: {', '.join(map(str, sorted(sizes)))}, 主要尺寸: 256x256, 圆角处理)")
            print(f"    💡 提示: 256x256 尺寸将确保 Windows 桌面图标清晰显示")
            return True
        except Exception as save_error:
            # 如果保存失败，尝试只保存 256 尺寸（确保至少有一个清晰的图标）
            print(f"    ⚠ 保存多尺寸 ICO 失败: {save_error}")
            print(f"    ⚠ 尝试保存单尺寸 ICO (256x256)...")
            # 生成 256 尺寸的图片
            size_256_img = img.resize((256, 256), Image.Resampling.LANCZOS)
            size_256_img = apply_rounded_corners(size_256_img)
            size_256_img.save(ico_path, format='ICO')
            print(f"    ✓ {ico_path.name} (单尺寸: 256x256, 圆角处理)")
            print(f"    💡 提示: 单尺寸 256x256 ICO 应该足够清晰，如需多尺寸支持请使用 ImageMagick")
            return True
    except Exception as e:
        print(f"    ✗ ICO 转换失败: {e}")
        return False


def convert_to_icns(source: Path, output_dir: Path, sizes: list):
    """转换为 macOS .icns 文件（带圆角处理和安全区域）"""
    print(f"  生成 ICNS 文件（macOS 优化：更大圆角 + 安全边距）...")
    
    # macOS 需要临时目录来构建 .icns
    temp_iconset = output_dir / "app_icon.iconset"
    
    try:
        img = Image.open(source)
        print(f"   源图片模式: {img.mode}, 尺寸: {img.size}")
        
        # 确保是 RGBA 模式（支持透明度）
        if img.mode == 'RGB':
            # RGB 转 RGBA，添加完全透明的 alpha 通道
            print(f"   将 RGB 转换为 RGBA（添加透明通道）")
            img = img.convert('RGBA')
        elif img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # macOS 特殊处理（正确的顺序）：
        # 1. 先应用圆角（在原始尺寸上应用，圆角半径约 22%）
        img = apply_rounded_corners(img, corner_ratio=0.22)
        
        # 2. 再应用安全区域（缩小内容，留出边距，让图标看起来不会太大）
        # 安全区域：内容占 80%，边距 20%
        img = apply_macos_safe_area(img, safe_area_ratio=0.85)
        
        # 验证透明度：检查背景是否透明
        sample_pixels = [
            (0, 0),  # 左上角
            (img.size[0]//2, 0),  # 上边缘
            (img.size[0]-1, img.size[1]-1)  # 右下角
        ]
        for x, y in sample_pixels:
            pixel = img.getpixel((x, y))
            if len(pixel) == 4:
                alpha = pixel[3]
                if alpha == 0:
                    print(f"   ✓ 位置 ({x}, {y}) 透明 (alpha=0)")
                else:
                    print(f"   ⚠ 位置 ({x}, {y}) 不透明 (alpha={alpha})")
        
        # 验证透明度：确保背景是透明的
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # 创建临时 iconset 目录
        if temp_iconset.exists():
            shutil.rmtree(temp_iconset)
        temp_iconset.mkdir(parents=True, exist_ok=True)
        
        # 生成不同尺寸的 PNG 文件到 iconset
        # macOS .icns 需要特定的命名规则
        icon_mappings = {
            16: ["icon_16x16.png", "icon_16x16@2x.png"],
            32: ["icon_32x32.png", "icon_32x32@2x.png"],
            128: ["icon_128x128.png", "icon_128x128@2x.png"],
            256: ["icon_256x256.png", "icon_256x256@2x.png"],
            512: ["icon_512x512.png", "icon_512x512@2x.png"],
            1024: ["icon_1024x1024.png"]
        }
        
        for size in sizes:
            # 直接 resize 已经处理好的图片（已经应用了圆角和安全区域）
            resized = img.resize((size, size), Image.Resampling.LANCZOS)
            # 确保 resize 后仍然是 RGBA
            if resized.mode != 'RGBA':
                resized = resized.convert('RGBA')
            
            if size in icon_mappings:
                for filename in icon_mappings[size]:
                    filepath = temp_iconset / filename
                    # 保存 PNG，明确指定保留透明度
                    # 使用 format='PNG' 和 compress_level=0 确保最佳透明度支持
                    resized.save(filepath, format='PNG', compress_level=0, optimize=False)
        
        # 使用 iconutil 转换为 .icns（macOS 系统工具）
        icns_path = output_dir / "app_icon.icns"
        try:
            result = subprocess.run(
                ["iconutil", "-c", "icns", str(temp_iconset), "-o", str(icns_path)],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"    ✓ {icns_path.name} (使用 iconutil)")
            return True
        except subprocess.CalledProcessError as e:
            print(f"    ⚠ iconutil 失败: {e.stderr}")
            print(f"    ⚠ 提示: 在 macOS 上需要安装 Xcode Command Line Tools")
            print(f"    ⚠ 安装命令: xcode-select --install")
            return False
        except FileNotFoundError:
            print(f"    ⚠ iconutil 未找到（可能不在 macOS 上）")
            print(f"    ⚠ 跳过 .icns 生成，仅生成 PNG 和 ICO")
            return False
        finally:
            # 清理临时目录
            if temp_iconset.exists():
                shutil.rmtree(temp_iconset)
    
    except Exception as e:
        print(f"    ✗ ICNS 转换失败: {e}")
        if temp_iconset.exists():
            shutil.rmtree(temp_iconset)
        return False


def process_client_logo(source_logo: Path, resources_dir: Path, client_name: str):
    """处理单个客户端的图标转换"""
    print(f"📦 处理{client_name}图标...")
    print(f"   源文件: {source_logo}")
    print(f"   目标目录: {resources_dir}")
    
    # 检查源文件
    if not source_logo.exists():
        print(f"   ⚠️  跳过: 源文件不存在")
        return None, None, None
    
    # 验证图片尺寸
    try:
        img = Image.open(source_logo)
        width, height = img.size
        if width != 1024 or height != 1024:
            print(f"   ⚠️  警告: 源图片尺寸为 {width}x{height}，不是 1024x1024")
            print(f"   将按当前尺寸进行转换")
    except Exception as e:
        print(f"   ❌ 错误: 无法读取图片文件: {e}")
        return None, None, None
    
    print(f"   ✓ 找到源文件: {source_logo.name} ({width}x{height})")
    
    # 确保目标目录存在
    ensure_dir(resources_dir)
    
    # 转换图标
    png_ok = convert_to_png(source_logo, resources_dir, ICON_SIZES)
    ico_ok = convert_to_ico(source_logo, resources_dir, ICO_SIZES)
    icns_ok = convert_to_icns(source_logo, resources_dir, ICNS_SIZES)
    
    return png_ok, ico_ok, icns_ok


def main():
    print("=" * 60)
    print("图标转换脚本")
    print("=" * 60)
    print()
    print("说明: 两端使用不同的图标文件")
    print(f"  - 员工端: {UI_CLIENT_LOGO}")
    print(f"  - 管理端: {ADMIN_CLIENT_LOGO}")
    print()
    
    # 检查至少有一个源文件存在
    if not UI_CLIENT_LOGO.exists() and not ADMIN_CLIENT_LOGO.exists():
        print("❌ 错误: 找不到任何源文件")
        print(f"   请将 logo.png 放在以下位置之一:")
        print(f"   - {UI_CLIENT_LOGO}")
        print(f"   - {ADMIN_CLIENT_LOGO}")
        sys.exit(1)
    
    results = {}
    
    # 处理员工端
    if UI_CLIENT_LOGO.exists():
        print()
        png_ok, ico_ok, icns_ok = process_client_logo(
            UI_CLIENT_LOGO, 
            UI_CLIENT_RESOURCES, 
            "员工端 (ui_client)"
        )
        results['ui_client'] = {
            'png': png_ok,
            'ico': ico_ok,
            'icns': icns_ok,
            'resources': UI_CLIENT_RESOURCES
        }
    else:
        print()
        print(f"⚠️  跳过员工端: 源文件不存在 ({UI_CLIENT_LOGO})")
        results['ui_client'] = None
    
    # 处理管理端
    if ADMIN_CLIENT_LOGO.exists():
        print()
        png_ok, ico_ok, icns_ok = process_client_logo(
            ADMIN_CLIENT_LOGO, 
            ADMIN_CLIENT_RESOURCES, 
            "管理端 (admin_ui_client)"
        )
        results['admin_ui_client'] = {
            'png': png_ok,
            'ico': ico_ok,
            'icns': icns_ok,
            'resources': ADMIN_CLIENT_RESOURCES
        }
    else:
        print()
        print(f"⚠️  跳过管理端: 源文件不存在 ({ADMIN_CLIENT_LOGO})")
        results['admin_ui_client'] = None
    
    print()
    print("=" * 60)
    print("转换完成！")
    print("=" * 60)
    print()
    
    # 总结
    print("生成的文件:")
    
    if results['ui_client']:
        print(f"  {results['ui_client']['resources']}/")
        if results['ui_client']['png']:
            print(f"    ✓ app_icon.png")
        if results['ui_client']['ico']:
            print(f"    ✓ app_icon.ico")
        if results['ui_client']['icns']:
            print(f"    ✓ app_icon.icns")
        else:
            print(f"    ⚠ app_icon.icns (未生成，需要 macOS 环境)")
    else:
        print(f"  {UI_CLIENT_RESOURCES}/ (未处理)")
    
    if results['admin_ui_client']:
        print(f"  {results['admin_ui_client']['resources']}/")
        if results['admin_ui_client']['png']:
            print(f"    ✓ app_icon.png")
        if results['admin_ui_client']['ico']:
            print(f"    ✓ app_icon.ico")
        if results['admin_ui_client']['icns']:
            print(f"    ✓ app_icon.icns")
        else:
            print(f"    ⚠ app_icon.icns (未生成，需要 macOS 环境)")
    else:
        print(f"  {ADMIN_CLIENT_RESOURCES}/ (未处理)")
    
    print()
    print("💡 提示:")
    print("  - PNG 文件已生成，可在所有平台使用")
    print("  - ICO 文件已生成，Windows 平台会优先使用")
    print("  - ICNS 文件需要在 macOS 上生成，其他平台会使用 PNG")
    print("  - 运行客户端测试图标是否正常显示")


if __name__ == "__main__":
    main()

