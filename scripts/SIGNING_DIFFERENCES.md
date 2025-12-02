# 签名流程差异对比

## 问题描述
- **方式1** (`build_client.py`): 本地完整流程，一切正常
- **方式2** (`build-clients.yml` + `sign_and_notarize_from_github.py`): GitHub 打包 .app，本地签名报错

## 关键差异

### 1. 签名无扩展名 Mach-O 文件时的差异

#### build_client.py (正常)
```python
# 使用 --preserve-metadata 保留元数据
subprocess.run([
    "codesign", "--force", "--sign", codesign_identity,
    "--options", "runtime",
    "--timestamp",
    "--preserve-metadata=entitlements,requirements,flags",  # ⭐ 关键差异
    str(item)
], check=False, capture_output=True)

# 签名后立即验证
verify_result = subprocess.run(
    ["codesign", "-vvv", str(item)],
    capture_output=True,
    text=True,
    timeout=60
)
if verify_result.returncode != 0:
    # 如果验证失败，重新签名
    subprocess.run([...], check=False, capture_output=True)
```

#### sign_and_notarize_from_github.py (有问题)
```python
# 没有使用 --preserve-metadata
subprocess.run([
    "codesign", "--force", "--sign", codesign_identity,
    "--options", "runtime",
    "--timestamp",
    # ⚠️ 缺少 --preserve-metadata
    str(item)
], check=False, capture_output=True)

# ⚠️ 签名后没有立即验证
```

### 2. 签名顺序差异

#### build_client.py
1. 签名 Resources 目录
2. 签名 Frameworks 目录（.dylib、无扩展名文件、Qt 框架、.so）
3. **验证并修复关键文件签名** ⭐ (在签名主可执行文件之前)
4. 签名主可执行文件
5. 签名整个应用包
6. 签名后再次验证并修复

#### sign_and_notarize_from_github.py
1. 签名 Resources 目录
2. 签名 Frameworks 目录（.dylib、无扩展名文件、Qt 框架、.so）
3. 验证并修复关键文件签名（但位置不对）
4. 签名主可执行文件
5. 签名整个应用包
6. 签名后再次验证并修复

### 3. 主可执行文件签名失败处理

#### build_client.py
- 主可执行文件签名使用 `check=True`，失败会立即报错
- 签名前已经验证并修复了关键文件

#### sign_and_notarize_from_github.py
- 主可执行文件签名失败时会清理非二进制文件
- 但清理可能不彻底，导致签名仍然失败

## 修复建议

1. **添加 `--preserve-metadata`** 到无扩展名 Mach-O 文件的签名命令
2. **添加签名后立即验证** 步骤
3. **调整签名顺序**，确保在签名主可执行文件前验证并修复关键文件
4. **改进错误处理**，提供更详细的错误信息

