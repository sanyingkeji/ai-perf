# 员工端菜单更新机制文档

## 概述

员工端通过 `/api/health` 接口获取菜单配置（如 `data_trend`、`help_text`），并根据返回结果动态更新菜单的显示/隐藏和文案。

## 更新流程

### 1. 接口调用

**触发时机**：
- 应用启动时（延迟检查）
- 定期检查（通过 `_check_help_text()` 方法）

**接口**：`GET /api/health`

**参数**：
- `current_version`（可选）：当前客户端版本

**响应结构**：
```json
{
  "status": "success",
  "data": {
    "version_info": {...},
    "data_trend": "https://...",  // 图文趋势链接（可选）
    "data_trend_text": "图文趋势",  // 图文趋势菜单文案（可选）
    "help_text": "帮助中心"  // 帮助中心标签文案（可选）
  }
}
```

### 2. 数据处理

**方法**：`_on_health_data_received(health_data: dict)`

**处理逻辑**：
1. **优先处理 data_trend**：
   - 调用 `_update_data_trend_link()` 更新图文趋势菜单
   - 根据 `data_trend` 和 `data_trend_text` 字段控制菜单显隐和文案

2. **处理 help_text**：
   - 检查 `help_text` 是否存在且非空
   - 如果存在：更新帮助中心标签文字并显示
   - 如果不存在或为空：隐藏标签

### 3. 菜单更新

#### 3.1 图文趋势菜单更新

**方法**：`_update_data_trend_link(data_trend_value, data_trend_text)`

**逻辑**：
1. **更新菜单文案**：
   - 如果 `data_trend_text` 存在，使用该文案
   - 否则使用默认文案 `"图文趋势"`
   - 调用 `_recalculate_nav_width()` 重新计算导航宽度

2. **验证链接格式**：
   - 仅接受 `http://` 或 `https://` 开头的链接
   - 不合法或缺失的链接会被忽略

3. **控制菜单显隐**：
   - **有有效链接**：
     - 显示菜单项：`self.data_trend_item.setHidden(False)`
     - 调用 `_adjust_nav_height()` 调整导航高度
     - 如果 URL 变化，重置加载状态并加载新 URL
   - **无有效链接**：
     - 隐藏菜单项：`self.data_trend_item.setHidden(True)`
     - 如果当前在该页面，切换到首页（索引0）
     - 调用 `_adjust_nav_height()` 调整导航高度

#### 3.2 导航高度调整

**方法**：`_adjust_nav_height()`

**逻辑**：
```python
def adjust_nav_height():
    # 1. 获取所有可见的菜单项索引
    visible_indices = [
        i for i in range(self.nav.count())
        if self.nav.item(i) and not self.nav.item(i).isHidden()
    ]
    
    # 2. 如果没有可见项，设置最小高度
    if not visible_indices:
        self.nav.setFixedHeight(30)
        return
    
    # 3. 计算总高度
    first_index = visible_indices[0]
    item_height = self.nav.sizeHintForRow(first_index)  # 获取单个菜单项高度
    if item_height <= 0:
        item_height = 30  # 默认高度
    
    total_height = len(visible_indices) * item_height + 4  # 4px 边距
    self.nav.setFixedHeight(total_height)
```

**调用时机**：
- 菜单项显隐变化时
- 窗口显示时（延迟100ms执行）

### 4. 窗口状态处理

**当前实现**：
- 菜单更新时**不会**主动调用 `show()`、`hide()`、`raise_()` 或 `activateWindow()`
- 只更新菜单项的显隐状态和导航高度

**Windows 端可能的闪烁问题**：
- 在 Windows 上，调用 `setFixedHeight()` 可能会触发窗口重绘
- 如果窗口当前处于最小化或隐藏状态，可能会被意外激活
- 需要检查是否有其他代码在菜单更新时触发了窗口操作

## 代码位置

### 关键方法

| 方法 | 位置 | 说明 |
|------|------|------|
| `_check_version_on_startup()` | `main_window.py:1777` | 启动时检查版本和健康数据 |
| `_check_help_text()` | `main_window.py:1829` | 定期检查 help_text |
| `_on_health_data_received()` | `main_window.py:1873` | 处理健康检查数据 |
| `_update_data_trend_link()` | `main_window.py:1914` | 更新图文趋势菜单 |
| `_adjust_nav_height()` | `main_window.py:224` | 调整导航高度 |

### 接口调用

**启动时检查**：
```python
# main_window.py:301
QTimer.singleShot(3000, self._check_version_on_startup)
```

**定期检查**：
```python
# main_window.py:305
QTimer.singleShot(5000, self._check_help_text)
```

## Windows 端窗口闪烁问题分析

### 可能的原因

1. **`setFixedHeight()` 触发重绘**：
   - Windows 上调用 `setFixedHeight()` 可能会触发整个窗口的重绘
   - 如果窗口当前不在前台，可能会被短暂激活

2. **布局更新导致窗口状态变化**：
   - 菜单项显隐时，Qt 布局系统会重新计算
   - 可能会触发 `resizeEvent` 或其他窗口事件

3. **其他窗口操作**：
   - 检查是否有代码在菜单更新时调用了 `raise_()` 或 `activateWindow()`

### 建议的优化方案

1. **避免不必要的窗口操作**：
   - 确保菜单更新时不会调用 `raise_()` 或 `activateWindow()`
   - 只在用户主动操作时才激活窗口

2. **优化布局更新**：
   - 使用 `setUpdatesEnabled(False)` 临时禁用更新
   - 批量更新后再启用更新

3. **延迟执行**：
   - 如果窗口当前不在前台，延迟执行菜单更新
   - 或者只在窗口可见时才更新菜单

## 相关代码片段

### 菜单更新触发点

```python
# _on_health_data_received() 中
self._update_data_trend_link(
    health_data.get("data_trend"),
    health_data.get("data_trend_text"),
)

# _update_data_trend_link() 中
if url:
    self.data_trend_item.setHidden(False)
    if hasattr(self, "_adjust_nav_height"):
        self._adjust_nav_height()  # 这里可能会触发窗口重绘
else:
    self.data_trend_item.setHidden(True)
    if hasattr(self, "_adjust_nav_height"):
        self._adjust_nav_height()  # 这里可能会触发窗口重绘
```

### 导航高度调整

```python
def adjust_nav_height():
    # ... 计算逻辑 ...
    self.nav.setFixedHeight(total_height)  # Windows 上可能触发窗口重绘
```

## 待检查项

1. ✅ 菜单更新时是否调用了窗口操作（`raise_()`、`activateWindow()` 等）
2. ✅ `setFixedHeight()` 在 Windows 上的行为
3. ✅ 是否有其他代码在菜单更新时触发了窗口状态变化
4. ⚠️ 需要实际测试 Windows 端的表现，确认是否真的存在窗口关闭再打开的问题
