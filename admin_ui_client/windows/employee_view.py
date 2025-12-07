#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
员工列表管理页面：
- 显示所有员工列表
- 添加新员工
- 编辑员工信息
- 删除员工（软删除）
- 查看和管理员工的外部平台账号绑定
"""

import json
from typing import List, Dict, Any, Optional
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QDialog, QFormLayout,
    QLineEdit, QComboBox, QDateEdit, QMessageBox, QHeaderView,
    QAbstractItemView, QTextEdit, QGroupBox, QTabWidget, QMenu, QApplication, QFrame,
    QListWidget, QListWidgetItem, QSplitter
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate, QTimer
from PySide6.QtGui import QFont, QAction

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from widgets.toast import Toast
from utils.config_manager import CONFIG_PATH


class _DataCache:
    """数据缓存工具类，用于缓存团队、角色、职级、薪级等数据（缓存1天）"""
    
    CACHE_DIR = CONFIG_PATH.parent / "cache"
    CACHE_EXPIRE_HOURS = 24  # 缓存24小时
    
    @classmethod
    def _get_cache_path(cls, cache_key: str) -> Path:
        """获取缓存文件路径"""
        cls.CACHE_DIR.mkdir(exist_ok=True)
        return cls.CACHE_DIR / f"{cache_key}.json"
    
    @classmethod
    def get(cls, cache_key: str) -> Optional[Dict[str, Any]]:
        """获取缓存数据，如果过期或不存在则返回None"""
        cache_path = cls._get_cache_path(cache_key)
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 检查时间戳
            cached_time_str = cache_data.get('cached_at')
            if not cached_time_str:
                return None
            
            cached_time = datetime.fromisoformat(cached_time_str)
            now = datetime.now()
            
            # 检查是否过期（超过24小时）
            if (now - cached_time).total_seconds() > cls.CACHE_EXPIRE_HOURS * 3600:
                return None
            
            return cache_data.get('data')
        except Exception as e:
            # 缓存文件损坏，删除它
            try:
                cache_path.unlink()
            except:
                pass
            return None
    
    @classmethod
    def set(cls, cache_key: str, data: Any):
        """设置缓存数据"""
        cache_path = cls._get_cache_path(cache_key)
        try:
            cache_data = {
                'cached_at': datetime.now().isoformat(),
                'data': data
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # 缓存写入失败不影响功能，只记录错误
            pass


class _EmployeeWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _EmployeeWorker(QRunnable):
    """后台加载员工列表"""
    def __init__(self):
        super().__init__()
        self.signals = _EmployeeWorkerSignals()

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        try:
            client = AdminApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            resp = client.get_employees()
            items = resp.get("items", []) if isinstance(resp, dict) else []
            self.signals.finished.emit(items)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载员工列表失败：{e}")


class _BindingWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _BindingWorker(QRunnable):
    """后台加载账号绑定列表"""
    def __init__(self, user_id: str):
        super().__init__()
        self._user_id = user_id
        self.signals = _BindingWorkerSignals()

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        try:
            client = AdminApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            resp = client.get_account_bindings(self._user_id)
            items = resp.get("items", []) if isinstance(resp, dict) else []
            self.signals.finished.emit(items)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载账号绑定失败：{e}")


class _DialogDataWorkerSignals(QObject):
    """对话框数据加载信号"""
    next_user_id_loaded = Signal(str)
    teams_loaded = Signal(list)  # List[Dict]
    roles_loaded = Signal(list)  # List[Dict]
    levels_loaded = Signal(list)  # List[Dict]
    salary_bands_loaded = Signal(list)  # List[Dict]
    subroles_loaded = Signal(list, int)  # List[Dict], role_id
    error = Signal(str, str)  # error_type, error_message


class _DialogDataWorker(QRunnable):
    """异步加载对话框所需数据"""
    def __init__(self, need_next_id: bool = False, need_teams: bool = True, 
                 need_roles: bool = True, need_levels: bool = True, 
                 need_salary_bands: bool = True, need_subroles: bool = False, 
                 role_id: Optional[int] = None):
        super().__init__()
        self.signals = _DialogDataWorkerSignals()
        self._need_next_id = need_next_id
        self._need_teams = need_teams
        self._need_roles = need_roles
        self._need_levels = need_levels
        self._need_salary_bands = need_salary_bands
        self._need_subroles = need_subroles
        self._role_id = role_id
    
    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            return
        
        try:
            client = AdminApiClient.from_config()
            
            # 获取下一个用户ID
            if self._need_next_id:
                try:
                    next_id = client.get_next_user_id()
                    self.signals.next_user_id_loaded.emit(next_id)
                except Exception as e:
                    self.signals.error.emit("next_user_id", str(e))
            
            # 加载团队列表（带缓存）
            if self._need_teams:
                try:
                    cached_teams = _DataCache.get("teams")
                    if cached_teams is not None:
                        self.signals.teams_loaded.emit(cached_teams)
                    else:
                        teams = client.get_teams()
                        _DataCache.set("teams", teams)
                        self.signals.teams_loaded.emit(teams)
                except Exception as e:
                    self.signals.error.emit("teams", str(e))
            
            # 加载角色列表（带缓存）
            if self._need_roles:
                try:
                    cached_roles = _DataCache.get("roles")
                    if cached_roles is not None:
                        self.signals.roles_loaded.emit(cached_roles)
                    else:
                        roles = client.get_roles()
                        _DataCache.set("roles", roles)
                        self.signals.roles_loaded.emit(roles)
                except Exception as e:
                    self.signals.error.emit("roles", str(e))
            
            # 加载职级列表（带缓存）
            if self._need_levels:
                try:
                    cached_levels = _DataCache.get("levels")
                    if cached_levels is not None:
                        self.signals.levels_loaded.emit(cached_levels)
                    else:
                        levels = client.get_levels()
                        _DataCache.set("levels", levels)
                        self.signals.levels_loaded.emit(levels)
                except Exception as e:
                    self.signals.error.emit("levels", str(e))
            
            # 加载薪级列表（带缓存）
            if self._need_salary_bands:
                try:
                    cached_salary_bands = _DataCache.get("salary_bands")
                    if cached_salary_bands is not None:
                        self.signals.salary_bands_loaded.emit(cached_salary_bands)
                    else:
                        salary_bands = client.get_salary_bands()
                        _DataCache.set("salary_bands", salary_bands)
                        self.signals.salary_bands_loaded.emit(salary_bands)
                except Exception as e:
                    self.signals.error.emit("salary_bands", str(e))
            
            # 加载子角色列表（带缓存，按role_id缓存）
            if self._need_subroles and self._role_id:
                try:
                    cache_key = f"subroles_{self._role_id}"
                    cached_subroles = _DataCache.get(cache_key)
                    if cached_subroles is not None:
                        self.signals.subroles_loaded.emit(cached_subroles, self._role_id)
                    else:
                        subroles = client.get_subroles(role_id=self._role_id)
                        _DataCache.set(cache_key, subroles)
                        self.signals.subroles_loaded.emit(subroles, self._role_id)
                except Exception as e:
                    self.signals.error.emit("subroles", str(e))
        
        except Exception as e:
            self.signals.error.emit("general", str(e))


class EmployeeEditDialog(QDialog):
    """员工编辑/添加对话框"""
    def __init__(self, parent, employee_data: Optional[Dict] = None):
        super().__init__(parent)
        self._employee_data = employee_data
        self._is_edit = employee_data is not None
        self._data_loaded = False
        self._teams_data = {}
        self._thread_pool = QThreadPool.globalInstance()
        
        title = "编辑员工" if self._is_edit else "添加员工"
        self.setWindowTitle(title)
        self.resize(600, 900)  # 增加对话框宽度和高度以容纳新字段
        
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        
        # 员工ID（编辑时只读，添加时自动填充）
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setMinimumWidth(300)  # 设置最小宽度
        if self._is_edit:
            self._user_id_edit.setText(employee_data.get("user_id", ""))
            self._user_id_edit.setReadOnly(True)
        else:
            # 添加时先显示占位符，稍后异步加载
            self._user_id_edit.setText("加载中...")
            self._user_id_edit.setReadOnly(True)  # 自动填充，不允许修改
        # 员工ID变化时也检查是否是组长（虽然编辑时是只读的，但为了代码完整性还是连接信号）
        self._user_id_edit.textChanged.connect(self._check_team_leader)
        form.addRow("员工ID：", self._user_id_edit)
        
        # 姓名
        self._name_edit = QLineEdit()
        self._name_edit.setMinimumWidth(300)
        if self._is_edit:
            self._name_edit.setText(employee_data.get("name", ""))
        form.addRow("姓名：", self._name_edit)
        
        # 邮箱（特别加长）
        self._email_edit = QLineEdit()
        self._email_edit.setMinimumWidth(400)  # 邮箱输入框更宽
        if self._is_edit:
            self._email_edit.setText(employee_data.get("email") or "")
        form.addRow("邮箱：", self._email_edit)
        
        # 所属团队（下拉框）
        self._team_combo = QComboBox()
        self._team_combo.setEditable(False)
        self._team_combo.setMinimumWidth(300)  # 设置下拉框最小宽度
        self._team_combo.addItem("加载中...", None)
        self._team_combo.setEnabled(False)
        
        # 组长标识（只读标签，如果是组长则显示）- 先创建，避免信号触发时找不到
        self._leader_label = QLabel()
        self._leader_label.setStyleSheet("color: #4a90e2; font-weight: bold;")
        self._leader_label.setVisible(False)
        self._leader_label.setMaximumHeight(20)  # 限制高度
        self._leader_label.setContentsMargins(0, 0, 0, 0)  # 减少边距
        
        # 现在连接信号
        self._team_combo.currentIndexChanged.connect(self._on_team_changed)  # 团队变化时检查是否是组长
        
        form.addRow("所属团队：", self._team_combo)
        form.addRow("", self._leader_label)  # 空标签作为行标题
        
        # 角色（下拉框）
        self._role_combo = QComboBox()
        self._role_combo.setEditable(False)
        self._role_combo.setMinimumWidth(300)
        self._role_combo.addItem("加载中...", None)
        self._role_combo.setEnabled(False)
        # 角色变化时，更新子角色列表
        self._role_combo.currentIndexChanged.connect(self._on_role_changed)
        form.addRow("角色：", self._role_combo)
        
        # 子角色（下拉框，可选）
        self._subrole_combo = QComboBox()
        self._subrole_combo.setEditable(False)
        self._subrole_combo.setMinimumWidth(300)
        self._subrole_combo.addItem("（无）", None)  # 第一个选项表示无子角色
        if self._is_edit:
            role_id = employee_data.get("role_id", 1)
            # 稍后异步加载子角色
        form.addRow("子角色（可选）：", self._subrole_combo)
        
        # 职级（下拉框）
        self._level_combo = QComboBox()
        self._level_combo.setEditable(False)
        self._level_combo.setMinimumWidth(300)
        self._level_combo.addItem("加载中...", None)
        self._level_combo.setEnabled(False)
        form.addRow("职级：", self._level_combo)
        
        # 薪级（下拉框，显示薪资范围）
        self._salary_band_combo = QComboBox()
        self._salary_band_combo.setEditable(False)
        self._salary_band_combo.setMinimumWidth(300)
        self._salary_band_combo.addItem("加载中...", None)
        self._salary_band_combo.setEnabled(False)
        form.addRow("薪级：", self._salary_band_combo)
        
        # 员工状态（增加"休假中"选项）
        self._active_combo = QComboBox()
        self._active_combo.setMinimumWidth(300)
        self._active_combo.addItems(["在职", "离职", "休假中"])
        if self._is_edit:
            active = employee_data.get("active", 1)
            # 0=离职，1=在职，2=休假中
            if active == 0:
                self._active_combo.setCurrentIndex(1)  # 离职
            elif active == 2:
                self._active_combo.setCurrentIndex(2)  # 休假中
            else:
                self._active_combo.setCurrentIndex(0)  # 在职（默认）
        form.addRow("状态：", self._active_combo)
        
        # 入职日期
        self._join_date_edit = QDateEdit()
        self._join_date_edit.setCalendarPopup(True)
        self._join_date_edit.setDisplayFormat("yyyy-MM-dd")
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._join_date_edit)
        if self._is_edit and employee_data.get("join_date"):
            try:
                join_date = date.fromisoformat(str(employee_data.get("join_date")))
                self._join_date_edit.setDate(QDate(join_date.year, join_date.month, join_date.day))
            except:
                pass
        form.addRow("入职日期（可选）：", self._join_date_edit)
        
        # 离职日期
        self._leave_date_edit = QDateEdit()
        self._leave_date_edit.setCalendarPopup(True)
        self._leave_date_edit.setDisplayFormat("yyyy-MM-dd")
        apply_theme_to_date_edit(self._leave_date_edit)
        if self._is_edit and employee_data.get("leave_date"):
            try:
                leave_date = date.fromisoformat(str(employee_data.get("leave_date")))
                self._leave_date_edit.setDate(QDate(leave_date.year, leave_date.month, leave_date.day))
            except:
                pass
        form.addRow("离职日期（可选）：", self._leave_date_edit)
        
        # 岗位职责（多行文本输入，每行一个职责）
        self._responsibilities_edit = QTextEdit()
        self._responsibilities_edit.setMinimumHeight(75)
        self._responsibilities_edit.setMaximumHeight(95)
        self._responsibilities_edit.setMinimumWidth(300)  # 与下拉框宽度对齐
        self._responsibilities_edit.setPlaceholderText("每行输入一个岗位职责，例如：\n嵌入式固件开发与驱动适配\n与后端/APP的接口联调\n代码评审与质量保障")
        if self._is_edit and employee_data.get("responsibilities"):
            responsibilities = employee_data.get("responsibilities")
            if isinstance(responsibilities, list):
                self._responsibilities_edit.setPlainText("\n".join(responsibilities))
            elif isinstance(responsibilities, str):
                self._responsibilities_edit.setPlainText(responsibilities)
        form.addRow("岗位职责（每行一个）：", self._responsibilities_edit)
        
        # 简历摘要（多行文本输入）
        self._resume_brief_edit = QTextEdit()
        self._resume_brief_edit.setMinimumHeight(65)
        self._resume_brief_edit.setMaximumHeight(85)
        self._resume_brief_edit.setMinimumWidth(300)  # 与下拉框宽度对齐
        self._resume_brief_edit.setPlaceholderText("输入员工背景摘要，例如：\n5年嵌入式与加密芯片驱动经验，主导蓝牙/NFC通讯栈。")
        if self._is_edit and employee_data.get("resume_brief"):
            self._resume_brief_edit.setPlainText(employee_data.get("resume_brief", ""))
        form.addRow("简历摘要：", self._resume_brief_edit)
        
        layout.addLayout(form)
        
        # 按钮
        btn_layout = QHBoxLayout()
        self._btn_save = QPushButton("加载数据中...")
        self._btn_save.clicked.connect(self.accept)
        self._btn_save.setEnabled(False)  # 数据加载完成前禁用
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        # 如果是编辑模式，数据已经存在，不需要加载
        if self._is_edit:
            # 编辑模式下，直接设置已存在的数据，但仍需要加载下拉框选项
            self._load_data_async()
        else:
            # 添加模式下，需要加载所有数据
            self._load_data_async()
    
    def showEvent(self, event):
        """对话框显示时触发异步加载"""
        super().showEvent(event)
        # 使用QTimer确保对话框已经完全显示
        QTimer.singleShot(50, self._load_data_async)
    
    def _load_data_async(self):
        """异步加载对话框所需数据"""
        if not AdminApiClient.is_logged_in():
            self._on_data_load_error("general", "未登录")
            return
        
        # 确定需要加载的数据
        need_next_id = not self._is_edit
        need_subroles = False
        role_id = None
        if self._is_edit:
            role_id = self._employee_data.get("role_id")
            if role_id:
                need_subroles = True
        
        worker = _DialogDataWorker(
            need_next_id=need_next_id,
            need_teams=True,
            need_roles=True,
            need_levels=True,
            need_salary_bands=True,
            need_subroles=need_subroles,
            role_id=role_id
        )
        
        # 连接信号
        if need_next_id:
            worker.signals.next_user_id_loaded.connect(self._on_next_user_id_loaded)
        worker.signals.teams_loaded.connect(self._on_teams_loaded)
        worker.signals.roles_loaded.connect(self._on_roles_loaded)
        worker.signals.levels_loaded.connect(self._on_levels_loaded)
        worker.signals.salary_bands_loaded.connect(self._on_salary_bands_loaded)
        if need_subroles:
            worker.signals.subroles_loaded.connect(self._on_subroles_loaded)
        worker.signals.error.connect(self._on_data_load_error)
        
        self._thread_pool.start(worker)
    
    def _on_next_user_id_loaded(self, next_id: str):
        """下一个用户ID加载完成"""
        self._user_id_edit.setText(next_id)
    
    def _on_teams_loaded(self, teams: List[Dict]):
        """团队列表加载完成"""
        self._team_combo.clear()
        self._teams_data = {}
        for team in teams:
            team_id = team["id"]
            team_name = team["name"]
            team_leader = team.get("team_leader")
            self._teams_data[team_id] = {
                "name": team_name,
                "team_leader": team_leader
            }
            self._team_combo.addItem(team_name, team_id)
        
        self._team_combo.setEnabled(True)
        
        # 如果是编辑模式，设置选中的团队
        if self._is_edit:
            team_id = self._employee_data.get("team_id", 1)
            self._set_combo_by_id(self._team_combo, team_id)
        
        self._check_team_leader()
        self._check_data_loaded()
    
    def _on_roles_loaded(self, roles: List[Dict]):
        """角色列表加载完成"""
        self._role_combo.clear()
        for role in roles:
            self._role_combo.addItem(role["name"], role["id"])
        
        self._role_combo.setEnabled(True)
        
        # 如果是编辑模式，设置选中的角色
        if self._is_edit:
            role_id = self._employee_data.get("role_id", 1)
            self._set_combo_by_id(self._role_combo, role_id)
        else:
            # 添加模式，加载默认角色的子角色
            default_role_id = self._get_combo_id(self._role_combo)
            if default_role_id:
                self._load_subroles_async(default_role_id)
        
        self._check_data_loaded()
    
    def _on_levels_loaded(self, levels: List[Dict]):
        """职级列表加载完成"""
        self._level_combo.clear()
        for level in levels:
            self._level_combo.addItem(level["name"], level["id"])
        
        self._level_combo.setEnabled(True)
        
        # 如果是编辑模式，设置选中的职级
        if self._is_edit:
            level_id = self._employee_data.get("level_id", 1)
            self._set_combo_by_id(self._level_combo, level_id)
        
        self._check_data_loaded()
    
    def _on_salary_bands_loaded(self, salary_bands: List[Dict]):
        """薪级列表加载完成"""
        self._salary_band_combo.clear()
        for band in salary_bands:
            # 显示格式：S1 (3,000 - 4,500)
            display_text = f"{band['band']} ({int(band['salary_min']):,} - {int(band['salary_max']):,})"
            self._salary_band_combo.addItem(display_text, band["band"])
        
        self._salary_band_combo.setEnabled(True)
        
        # 设置默认值
        if self._is_edit:
            salary_band = self._employee_data.get("salary_band", "S2")
            index = self._salary_band_combo.findData(salary_band)
            if index >= 0:
                self._salary_band_combo.setCurrentIndex(index)
        else:
            # 默认S2
            index = self._salary_band_combo.findData("S2")
            if index >= 0:
                self._salary_band_combo.setCurrentIndex(index)
        
        self._check_data_loaded()
    
    def _on_subroles_loaded(self, subroles: List[Dict], role_id: int):
        """子角色列表加载完成"""
        self._subrole_combo.clear()
        self._subrole_combo.addItem("（无）", None)
        for subrole in subroles:
            self._subrole_combo.addItem(subrole["name"], subrole["id"])
        
        self._subrole_combo.setEnabled(True)
        
        # 如果是编辑模式，设置选中的子角色
        if self._is_edit:
            subrole_id = self._employee_data.get("subrole_id")
            if subrole_id:
                self._set_combo_by_id(self._subrole_combo, subrole_id)
        else:
            # 添加模式，清空子角色选择
            self._subrole_combo.setCurrentIndex(0)
    
    def _on_data_load_error(self, error_type: str, error_message: str):
        """数据加载错误"""
        Toast.show_message(self, f"加载{error_type}失败：{error_message}")
        # 即使加载失败，也允许用户继续操作（使用默认值）
        self._check_data_loaded()
    
    def _check_data_loaded(self):
        """检查数据是否已加载完成，如果完成则启用保存按钮并更新按钮文本"""
        # 检查所有必需的下拉框是否已加载
        if (self._team_combo.count() > 0 and self._team_combo.itemText(0) != "加载中..." and
            self._role_combo.count() > 0 and self._role_combo.itemText(0) != "加载中..." and
            self._level_combo.count() > 0 and self._level_combo.itemText(0) != "加载中..." and
            self._salary_band_combo.count() > 0 and self._salary_band_combo.itemText(0) != "加载中..."):
            
            if not self._data_loaded:
                self._data_loaded = True
                self._btn_save.setText("保存")
                self._btn_save.setEnabled(True)
    
    def _load_subroles_async(self, role_id: Optional[int]):
        """异步加载子角色列表"""
        if not role_id:
            return
        
        worker = _DialogDataWorker(need_subroles=True, role_id=role_id)
        worker.signals.subroles_loaded.connect(self._on_subroles_loaded)
        worker.signals.error.connect(lambda error_type, msg: Toast.show_message(self, f"加载子角色失败：{msg}"))
        self._thread_pool.start(worker)
    
    def _load_teams(self):
        """加载团队列表（带缓存，缓存24小时）"""
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        # 先尝试从缓存加载
        cached_teams = _DataCache.get("teams")
        if cached_teams is not None:
            self._teams_data = {}
            for team in cached_teams:
                team_id = team["id"]
                team_name = team["name"]
                team_leader = team.get("team_leader")
                self._teams_data[team_id] = {
                    "name": team_name,
                    "team_leader": team_leader
                }
                self._team_combo.addItem(team_name, team_id)
            return
        
        # 缓存不存在或过期，从API加载
        try:
            client = AdminApiClient.from_config()
            teams = client.get_teams()
            # 更新缓存
            _DataCache.set("teams", teams)
            
            self._teams_data = {}  # 存储团队数据 {team_id: {name, team_leader}}
            for team in teams:
                team_id = team["id"]
                team_name = team["name"]
                team_leader = team.get("team_leader")  # 可能为None
                self._teams_data[team_id] = {
                    "name": team_name,
                    "team_leader": team_leader
                }
                self._team_combo.addItem(team_name, team_id)
        except Exception as e:
            Toast.show_message(self, f"加载团队列表失败：{e}")
            self._teams_data = {}
    
    def _load_roles(self):
        """加载角色列表（带缓存，缓存24小时）"""
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        # 先尝试从缓存加载
        cached_roles = _DataCache.get("roles")
        if cached_roles is not None:
            for role in cached_roles:
                self._role_combo.addItem(role["name"], role["id"])
            return
        
        # 缓存不存在或过期，从API加载
        try:
            client = AdminApiClient.from_config()
            roles = client.get_roles()
            # 更新缓存
            _DataCache.set("roles", roles)
            
            for role in roles:
                self._role_combo.addItem(role["name"], role["id"])
        except Exception as e:
            Toast.show_message(self, f"加载角色列表失败：{e}")
    
    def _load_subroles(self, role_id: Optional[int] = None):
        """加载子角色列表（带缓存，缓存24小时）"""
        self._subrole_combo.clear()
        self._subrole_combo.addItem("（无）", None)
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        if not role_id:
            return
        
        # 先尝试从缓存加载
        cache_key = f"subroles_{role_id}"
        cached_subroles = _DataCache.get(cache_key)
        if cached_subroles is not None:
            for subrole in cached_subroles:
                self._subrole_combo.addItem(subrole["name"], subrole["id"])
            return
        
        # 缓存不存在或过期，从API加载
        try:
            client = AdminApiClient.from_config()
            subroles = client.get_subroles(role_id=role_id)
            # 更新缓存
            _DataCache.set(cache_key, subroles)
            
            for subrole in subroles:
                self._subrole_combo.addItem(subrole["name"], subrole["id"])
        except Exception as e:
            Toast.show_message(self, f"加载子角色列表失败：{e}")
    
    def _load_levels(self):
        """加载职级列表（带缓存，缓存24小时）"""
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        # 先尝试从缓存加载
        cached_levels = _DataCache.get("levels")
        if cached_levels is not None:
            for level in cached_levels:
                self._level_combo.addItem(level["name"], level["id"])
            return
        
        # 缓存不存在或过期，从API加载
        try:
            client = AdminApiClient.from_config()
            levels = client.get_levels()
            # 更新缓存
            _DataCache.set("levels", levels)
            
            for level in levels:
                self._level_combo.addItem(level["name"], level["id"])
        except Exception as e:
            Toast.show_message(self, f"加载职级列表失败：{e}")
    
    def _load_salary_bands(self):
        """加载薪级列表（显示薪资范围，带缓存，缓存24小时）"""
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        # 先尝试从缓存加载
        cached_salary_bands = _DataCache.get("salary_bands")
        if cached_salary_bands is not None:
            for band in cached_salary_bands:
                # 显示格式：S1 (3,000 - 4,500)
                display_text = f"{band['band']} ({int(band['salary_min']):,} - {int(band['salary_max']):,})"
                self._salary_band_combo.addItem(display_text, band["band"])
            return
        
        # 缓存不存在或过期，从API加载
        try:
            client = AdminApiClient.from_config()
            salary_bands = client.get_salary_bands()
            # 更新缓存
            _DataCache.set("salary_bands", salary_bands)
            
            for band in salary_bands:
                # 显示格式：S1 (3,000 - 4,500)
                display_text = f"{band['band']} ({int(band['salary_min']):,} - {int(band['salary_max']):,})"
                self._salary_band_combo.addItem(display_text, band["band"])
        except Exception as e:
            Toast.show_message(self, f"加载薪级列表失败：{e}")
            # 如果加载失败，使用默认值
            fallback_bands = [
                ("S1", 3000, 4500),
                ("S2", 4500, 6500),
                ("S3", 7000, 10000),
                ("S4", 10000, 14000),
                ("S5", 14000, 18000),
                ("S6", 18000, 22000),
                ("S7", 21000, 26000),
            ]
            for band, min_val, max_val in fallback_bands:
                display_text = f"{band} ({min_val:,} - {max_val:,})"
                self._salary_band_combo.addItem(display_text, band)
    
    def _on_role_changed(self):
        """角色变化时，更新子角色列表"""
        role_id = self._get_combo_id(self._role_combo)
        if role_id:
            # 显示加载状态
            self._subrole_combo.clear()
            self._subrole_combo.addItem("加载中...", None)
            self._subrole_combo.setEnabled(False)
            self._load_subroles_async(role_id)
        else:
            # 清空子角色选择
            self._subrole_combo.clear()
            self._subrole_combo.addItem("（无）", None)
            self._subrole_combo.setCurrentIndex(0)
    
    def _on_team_changed(self):
        """团队变化时，检查是否是组长"""
        self._check_team_leader()
    
    def _check_team_leader(self):
        """检查当前员工是否是组长，如果是则显示标识"""
        # 检查必要的属性是否存在
        if not hasattr(self, '_teams_data') or not hasattr(self, '_leader_label'):
            return
        
        user_id = self._user_id_edit.text().strip()
        if not user_id:
            self._leader_label.setVisible(False)
            return
        
        team_id = self._get_combo_id(self._team_combo)
        if not team_id or team_id not in self._teams_data:
            self._leader_label.setVisible(False)
            return
        
        team_info = self._teams_data[team_id]
        team_name = team_info.get("name", "")
        team_leader = team_info.get("team_leader")
        
        if team_leader and team_leader == user_id:
            self._leader_label.setText(f"{team_name} 组长")
            self._leader_label.setVisible(True)
        else:
            self._leader_label.setVisible(False)
    
    def _get_combo_id(self, combo: QComboBox) -> Optional[int]:
        """获取下拉框当前选中的ID"""
        current_data = combo.currentData()
        if current_data is not None:
            return int(current_data)
        return None
    
    def _set_combo_by_id(self, combo: QComboBox, target_id: int):
        """根据ID设置下拉框选中项"""
        for i in range(combo.count()):
            if combo.itemData(i) == target_id:
                combo.setCurrentIndex(i)
                return
    
    def get_data(self) -> Dict[str, Any]:
        """获取表单数据"""
        data = {}
        
        if not self._is_edit:
            data["user_id"] = self._user_id_edit.text().strip()
        
        data["name"] = self._name_edit.text().strip()
        email = self._email_edit.text().strip()
        if email:
            data["email"] = email
        
        # 从下拉框获取ID
        team_id = self._get_combo_id(self._team_combo)
        data["team_id"] = team_id if team_id is not None else 1
        
        role_id = self._get_combo_id(self._role_combo)
        data["role_id"] = role_id if role_id is not None else 1
        
        subrole_id = self._get_combo_id(self._subrole_combo)
        if subrole_id is not None:
            data["subrole_id"] = subrole_id
        
        level_id = self._get_combo_id(self._level_combo)
        data["level_id"] = level_id if level_id is not None else 1
        
        # 从薪级下拉框获取value（S1、S2等）
        salary_band = self._salary_band_combo.currentData()
        data["salary_band"] = salary_band if salary_band else "S2"
        
        # 状态：0=离职，1=在职，2=休假中
        active_index = self._active_combo.currentIndex()
        if active_index == 0:
            data["active"] = 1  # 在职
        elif active_index == 1:
            data["active"] = 0  # 离职
        else:
            data["active"] = 2  # 休假中
        
        join_date = self._join_date_edit.date()
        if join_date.isValid() and join_date != QDate(2000, 1, 1):
            data["join_date"] = join_date.toString("yyyy-MM-dd")
        
        leave_date = self._leave_date_edit.date()
        if leave_date.isValid() and leave_date != QDate(2000, 1, 1):
            data["leave_date"] = leave_date.toString("yyyy-MM-dd")
        
        # 岗位职责：从多行文本中提取，过滤空行
        responsibilities_text = self._responsibilities_edit.toPlainText().strip()
        if responsibilities_text:
            responsibilities_list = [
                line.strip() 
                for line in responsibilities_text.split("\n") 
                if line.strip()
            ]
            if responsibilities_list:
                data["responsibilities"] = responsibilities_list
        
        # 简历摘要
        resume_brief = self._resume_brief_edit.toPlainText().strip()
        if resume_brief:
            data["resume_brief"] = resume_brief
        
        return data


class GitHubMemberSelectDialog(QDialog):
    """GitHub成员选择对话框（按Teams分组显示）"""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("选择GitHub成员")
        self.resize(800, 600)
        self._selected_member = None
        self._thread_pool = QThreadPool.globalInstance()
        self._current_team_slug = None  # 当前选中的team slug
        
        layout = QVBoxLayout(self)
        layout.setSpacing(8)  # 减少间距
        
        # 提示信息（固定高度，不占用太多空间）
        self._info_label = QLabel("正在加载GitHub Teams列表...")
        self._info_label.setMaximumHeight(30)  # 限制最大高度
        self._info_label.setMinimumHeight(25)  # 设置最小高度
        self._info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._info_label)
        
        # 使用QSplitter创建左右分栏
        splitter = QSplitter(Qt.Horizontal)
        
        # 左侧：Teams列表
        teams_widget = QWidget()
        teams_layout = QVBoxLayout(teams_widget)
        teams_layout.setContentsMargins(0, 0, 0, 0)
        teams_label = QLabel("Teams：")
        teams_layout.addWidget(teams_label)
        self._teams_list = QListWidget()
        self._teams_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._teams_list.currentItemChanged.connect(self._on_team_selected)
        teams_layout.addWidget(self._teams_list)
        splitter.addWidget(teams_widget)
        
        # 右侧：成员列表
        members_widget = QWidget()
        members_layout = QVBoxLayout(members_widget)
        members_layout.setContentsMargins(0, 0, 0, 0)
        members_label = QLabel("成员：")
        members_layout.addWidget(members_label)
        self._member_list = QListWidget()
        self._member_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._member_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        members_layout.addWidget(self._member_list)
        splitter.addWidget(members_widget)
        
        # 设置splitter比例（左侧30%，右侧70%）
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([240, 560])
        
        layout.addWidget(splitter)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self._on_ok_clicked)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        # 异步加载Teams列表
        self._load_teams()
    
    def _load_teams(self):
        """异步加载GitHub Teams列表（带缓存）"""
        class _LoadTeamsWorkerSignals(QObject):
            finished = Signal(list)  # List[Dict] teams列表
            error = Signal(str)
        
        class _LoadTeamsWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = _LoadTeamsWorkerSignals()
            
            @Slot()
            def run(self):
                try:
                    import httpx
                    from utils.config_manager import ConfigManager
                    
                    # 读取配置
                    cfg = ConfigManager.load()
                    github_org = cfg.get("github_org", "")
                    api_url = cfg.get("github_api_url", "https://api.github.com").rstrip("/")
                    api_key = cfg.get("github_api_key", "")
                    
                    if not github_org:
                        self.signals.error.emit("请先在设置页面配置 GitHub 组织名称（GITHUB_ORG）。")
                        return
                    
                    # 尝试从缓存读取
                    cache_key = f"github_teams_{github_org}"
                    cached_teams = _DataCache.get(cache_key)
                    if cached_teams is not None:
                        # 缓存命中，直接返回
                        self.signals.finished.emit(cached_teams)
                        return
                    
                    # 构建请求头（与后端保持一致）
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aiperf-admin-client/1.0",
                    }
                    # 必须传递 Authorization 头（与后端 github_loader.py 保持一致）
                    if api_key:
                        headers["Authorization"] = f"token {api_key}"
                    else:
                        self.signals.error.emit("请先在设置页面配置 GitHub API Key（GITHUB_TOKEN），否则可能无法访问组织Teams列表或受到API限流限制。")
                        return
                    
                    # 获取Teams列表
                    url = f"{api_url}/orgs/{github_org}/teams"
                    r = httpx.get(url, headers=headers, timeout=30)
                    
                    if r.status_code == 200:
                        teams = r.json()
                        # 保存到缓存
                        _DataCache.set(cache_key, teams)
                        self.signals.finished.emit(teams)
                    elif r.status_code == 404:
                        self.signals.error.emit(f"GitHub 组织 '{github_org}' 不存在或无权访问。")
                    elif r.status_code == 403:
                        rate_limit_remaining = r.headers.get("X-RateLimit-Remaining", "")
                        if rate_limit_remaining == "0":
                            self.signals.error.emit("GitHub API 调用次数已达上限，请稍后重试。")
                        else:
                            self.signals.error.emit("GitHub API 调用受限，请检查 API Key 配置或权限。")
                    else:
                        self.signals.error.emit(f"GitHub API 调用失败：HTTP {r.status_code}")
                except Exception as e:
                    self.signals.error.emit(f"加载GitHub Teams列表失败：{e}")
        
        worker = _LoadTeamsWorker()
        worker.signals.finished.connect(self._on_teams_loaded)
        worker.signals.error.connect(self._on_load_error)
        self._thread_pool.start(worker)
    
    def _on_teams_loaded(self, teams: List[Dict]):
        """Teams列表加载完成"""
        self._teams_list.clear()
        self._member_list.clear()
        
        if not teams:
            self._info_label.setText("组织中没有Teams。")
            QMessageBox.information(self, "提示", "组织中没有Teams。")
            return
        
        # 按name排序
        teams.sort(key=lambda x: x.get("name", "").lower())
        
        # 更新提示信息
        self._info_label.setText(f"请选择一个Team查看成员（共 {len(teams)} 个Teams）：")
        
        # 添加到列表
        for team in teams:
            name = team.get("name", "")
            slug = team.get("slug", "")
            description = team.get("description", "")
            
            display_text = name
            if description:
                display_text += f" - {description}"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, team)  # 存储完整的team信息
            self._teams_list.addItem(item)
    
    def _on_team_selected(self, current_item: QListWidgetItem, previous_item: QListWidgetItem):
        """Team选择变化时，加载该Team的成员列表"""
        if not current_item:
            self._member_list.clear()
            return
        
        team = current_item.data(Qt.UserRole)
        if not team:
            return
        
        team_slug = team.get("slug", "")
        if not team_slug:
            return
        
        self._current_team_slug = team_slug
        team_name = team.get("name", "")
        self._info_label.setText(f"正在加载 Team '{team_name}' 的成员列表...")
        self._member_list.clear()
        
        # 异步加载该Team的成员
        self._load_team_members(team_slug)
    
    def _load_team_members(self, team_slug: str):
        """异步加载指定Team的成员列表（带缓存）"""
        class _LoadTeamMembersWorkerSignals(QObject):
            finished = Signal(str, list)  # team_slug, List[Dict] 成员列表
            error = Signal(str)
        
        class _LoadTeamMembersWorker(QRunnable):
            def __init__(self, team_slug: str):
                super().__init__()
                self._team_slug = team_slug
                self.signals = _LoadTeamMembersWorkerSignals()
            
            @Slot()
            def run(self):
                try:
                    import httpx
                    from utils.config_manager import ConfigManager
                    
                    # 读取配置
                    cfg = ConfigManager.load()
                    github_org = cfg.get("github_org", "")
                    api_url = cfg.get("github_api_url", "https://api.github.com").rstrip("/")
                    api_key = cfg.get("github_api_key", "")
                    
                    if not github_org:
                        self.signals.error.emit("请先在设置页面配置 GitHub 组织名称（GITHUB_ORG）。")
                        return
                    
                    # 尝试从缓存读取
                    cache_key = f"github_team_members_{github_org}_{self._team_slug}"
                    cached_members = _DataCache.get(cache_key)
                    if cached_members is not None:
                        # 缓存命中，直接返回
                        self.signals.finished.emit(self._team_slug, cached_members)
                        return
                    
                    # 构建请求头（与后端保持一致）
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aiperf-admin-client/1.0",
                    }
                    if api_key:
                        headers["Authorization"] = f"token {api_key}"
                    else:
                        self.signals.error.emit("请先在设置页面配置 GitHub API Key（GITHUB_TOKEN）。")
                        return
                    
                    # 获取Team成员列表
                    url = f"{api_url}/orgs/{github_org}/teams/{self._team_slug}/members"
                    r = httpx.get(url, headers=headers, timeout=30)
                    
                    if r.status_code == 200:
                        members = r.json()
                        # 获取每个成员的详细信息（包含完整信息）
                        full_members = []
                        for member in members:
                            member_login = member.get("login", "")
                            if member_login:
                                # 获取成员详细信息
                                member_url = f"{api_url}/users/{member_login}"
                                try:
                                    member_r = httpx.get(member_url, headers=headers, timeout=10)
                                    if member_r.status_code == 200:
                                        full_members.append(member_r.json())
                                except Exception:
                                    # 如果获取详细信息失败，使用基本成员信息
                                    full_members.append(member)
                        
                        # 保存到缓存
                        _DataCache.set(cache_key, full_members)
                        self.signals.finished.emit(self._team_slug, full_members)
                    elif r.status_code == 404:
                        self.signals.error.emit(f"Team '{self._team_slug}' 不存在或无权访问。")
                    elif r.status_code == 403:
                        rate_limit_remaining = r.headers.get("X-RateLimit-Remaining", "")
                        if rate_limit_remaining == "0":
                            self.signals.error.emit("GitHub API 调用次数已达上限，请稍后重试。")
                        else:
                            self.signals.error.emit("GitHub API 调用受限，请检查 API Key 配置或权限。")
                    else:
                        self.signals.error.emit(f"GitHub API 调用失败：HTTP {r.status_code}")
                except Exception as e:
                    self.signals.error.emit(f"加载Team成员列表失败：{e}")
        
        worker = _LoadTeamMembersWorker(team_slug)
        worker.signals.finished.connect(self._on_team_members_loaded)
        worker.signals.error.connect(self._on_load_error)
        self._thread_pool.start(worker)
    
    def _on_team_members_loaded(self, team_slug: str, members: List[Dict]):
        """Team成员列表加载完成"""
        # 检查team_slug是否仍然匹配（防止异步加载时用户切换了team）
        if team_slug != self._current_team_slug:
            return
        
        self._member_list.clear()
        
        if not members:
            self._info_label.setText(f"Team '{team_slug}' 中没有成员。")
            return
        
        # 按login排序
        members.sort(key=lambda x: x.get("login", "").lower())
        
        # 更新提示信息
        self._info_label.setText(f"请选择一个成员（Team: {team_slug}，共 {len(members)} 个成员）：")
        
        # 添加到列表
        for member in members:
            login = member.get("login", "")
            name = member.get("name", "")
            display_text = f"{login}"
            if name and name != login:
                display_text += f" ({name})"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, member)  # 存储完整的成员信息
            self._member_list.addItem(item)
    
    def _on_load_error(self, error_msg: str):
        """加载失败"""
        QMessageBox.warning(self, "加载失败", error_msg)
        self.reject()
    
    def _on_item_double_clicked(self, item: QListWidgetItem):
        """双击项目时直接确认"""
        self._on_ok_clicked()
    
    def _on_ok_clicked(self):
        """确定按钮点击"""
        current_item = self._member_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "提示", "请选择一个成员。")
            return
        
        self._selected_member = current_item.data(Qt.UserRole)
        if self._selected_member:
            self.accept()
    
    def get_selected_member(self) -> Optional[Dict]:
        """获取选中的成员信息"""
        return self._selected_member


class BindingEditDialog(QDialog):
    """账号绑定编辑/添加对话框"""
    def __init__(self, parent, user_id: str, binding_data: Optional[Dict] = None):
        super().__init__(parent)
        self._user_id = user_id
        self._binding_data = binding_data
        self._is_edit = binding_data is not None
        self._user_email = None  # 员工邮箱，用于自动获取
        self._thread_pool = QThreadPool.globalInstance()
        
        title = "编辑账号绑定" if self._is_edit else "添加账号绑定"
        self.setWindowTitle(title)
        self.resize(700, 550)  # 增加对话框宽度
        
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        
        # 平台类型
        platform_row = QHBoxLayout()
        self._platform_combo = QComboBox()
        self._platform_combo.setMinimumWidth(300)
        self._platform_combo.addItems(["jira", "github", "figma", "other"])
        if self._is_edit:
            platform = binding_data.get("platform", "")
            index = self._platform_combo.findText(platform)
            if index >= 0:
                self._platform_combo.setCurrentIndex(index)
            self._platform_combo.setEnabled(False)  # 编辑时不允许修改平台
        platform_row.addWidget(self._platform_combo)
        # 自动获取按钮（平台不是"other"时可用，编辑模式下也可用）
        self._auto_fetch_btn = QPushButton("自动获取")
        self._auto_fetch_btn.setFixedHeight(28)
        self._auto_fetch_btn.clicked.connect(self._on_auto_fetch_clicked)
        platform_row.addWidget(self._auto_fetch_btn)
        form.addRow("平台类型：", platform_row)
        
        # 监听平台选择变化，更新自动获取按钮状态
        self._platform_combo.currentTextChanged.connect(self._on_platform_changed)
        self._on_platform_changed()  # 初始化按钮状态
        
        # 外部ID（改为多行输入框，宽度更宽）
        self._external_id_edit = QTextEdit()
        self._external_id_edit.setMinimumHeight(80)  # 设置最小高度
        self._external_id_edit.setMaximumHeight(120)  # 设置最大高度
        self._external_id_edit.setMinimumWidth(500)  # 设置最小宽度
        if self._is_edit:
            self._external_id_edit.setPlainText(binding_data.get("external_id", ""))
        form.addRow("外部ID：", self._external_id_edit)
        
        # 异步加载员工邮箱（编辑模式下也需要，用于自动获取功能）
        self._load_user_email()
        
        # extra_json
        self._extra_json_edit = QTextEdit()
        self._extra_json_edit.setPlaceholderText("JSON格式，可选")
        if self._is_edit and binding_data.get("extra_json"):
            self._extra_json_edit.setPlainText(json.dumps(binding_data.get("extra_json"), ensure_ascii=False, indent=2))
        form.addRow("扩展信息（JSON）：", self._extra_json_edit)
        
        # alias_github_author
        self._alias_edit = QTextEdit()
        self._alias_edit.setPlaceholderText("JSON数组格式，如：[\"email1@example.com\", \"email2@example.com\"]")
        if self._is_edit and binding_data.get("alias_github_author"):
            self._alias_edit.setPlainText(json.dumps(binding_data.get("alias_github_author"), ensure_ascii=False, indent=2))
        form.addRow("GitHub别名（JSON数组）：", self._alias_edit)
        
        layout.addLayout(form)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
    
    def get_data(self) -> Dict[str, Any]:
        """获取表单数据"""
        data = {
            "platform": self._platform_combo.currentText(),
            "external_id": self._external_id_edit.toPlainText().strip(),  # 使用toPlainText()获取多行文本
        }
        
        extra_json_text = self._extra_json_edit.toPlainText().strip()
        if extra_json_text:
            try:
                data["extra_json"] = json.loads(extra_json_text)
            except:
                pass
        
        alias_text = self._alias_edit.toPlainText().strip()
        if alias_text:
            try:
                data["alias_github_author"] = json.loads(alias_text)
            except:
                pass
        
        return data
    
    def _on_platform_changed(self):
        """平台选择变化时，更新自动获取按钮状态"""
        platform = self._platform_combo.currentText()
        # 平台不是"other"时启用自动获取（编辑模式下也可用）
        self._auto_fetch_btn.setEnabled(platform != "other")
    
    def _load_user_email(self):
        """异步加载员工邮箱"""
        class _EmailWorkerSignals(QObject):
            finished = Signal(str)  # email
            error = Signal(str)
        
        class _EmailWorker(QRunnable):
            def __init__(self, user_id: str):
                super().__init__()
                self._user_id = user_id
                self.signals = _EmailWorkerSignals()
            
            @Slot()
            def run(self):
                if not AdminApiClient.is_logged_in():
                    self.signals.error.emit("未登录")
                    return
                
                try:
                    client = AdminApiClient.from_config()
                    employees = client.get_employees()
                    items = employees.get("items", [])
                    for emp in items:
                        if str(emp.get("user_id")) == self._user_id:
                            email = emp.get("email", "")
                            self.signals.finished.emit(email)
                            return
                    self.signals.error.emit("未找到员工信息")
                except Exception as e:
                    self.signals.error.emit(f"加载员工信息失败：{e}")
        
        worker = _EmailWorker(self._user_id)
        worker.signals.finished.connect(self._on_email_loaded)
        worker.signals.error.connect(lambda msg: None)  # 静默处理错误
        self._thread_pool.start(worker)
    
    def _on_email_loaded(self, email: str):
        """员工邮箱加载完成"""
        self._user_email = email
    
    def _on_auto_fetch_clicked(self):
        """自动获取按钮点击事件"""
        platform = self._platform_combo.currentText()
        
        if platform == "other":
            QMessageBox.warning(
                self,
                "不支持",
                "其他平台暂不支持自动获取，请手动输入。"
            )
            return
        
        # GitHub平台特殊处理：弹出成员选择对话框
        if platform == "github":
            dialog = GitHubMemberSelectDialog(self)
            if dialog.exec() == QDialog.Accepted:
                member = dialog.get_selected_member()
                if member:
                    # external_id = id字段
                    external_id = str(member.get("id", ""))
                    # extra_json = 整个用户对象
                    extra_json = member
                    
                    # 填充到表单
                    self._external_id_edit.setPlainText(external_id)
                    if extra_json:
                        try:
                            extra_json_str = json.dumps(extra_json, ensure_ascii=False, indent=2)
                            self._extra_json_edit.setPlainText(extra_json_str)
                        except Exception:
                            pass
            return
        
        # 其他平台（Jira、Figma）继续使用原有的邮箱搜索逻辑
        if not self._user_email:
            QMessageBox.warning(
                self,
                "无法获取",
                "员工邮箱信息未加载完成，请稍后再试。"
            )
            return
        
        # 禁用自动获取按钮，显示加载状态
        self._auto_fetch_btn.setEnabled(False)
        self._auto_fetch_btn.setText("正在获取...")
        
        # 显示加载状态
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading(f"正在从 {platform.upper()} 获取用户信息...")
        
        # 异步获取
        class _FetchWorkerSignals(QObject):
            finished = Signal(str, dict)  # external_id, extra_json
            error = Signal(str)
        
        class _FetchWorker(QRunnable):
            def __init__(self, platform: str, email: str):
                super().__init__()
                self._platform = platform
                self._email = email
                self.signals = _FetchWorkerSignals()
            
            @Slot()
            def run(self):
                try:
                    import httpx
                    from utils.config_manager import ConfigManager
                    
                    # 读取配置
                    cfg = ConfigManager.load()
                    external_id = None
                    extra_json = {}
                    
                    # GitHub平台不再走这里，已经在上面处理了
                    if self._platform == "github":
                        # 这里不应该执行到，但保留以防万一
                        self.signals.error.emit("GitHub平台请使用成员选择对话框。")
                        return
                    
                    if False and self._platform == "github":  # 禁用原有逻辑
                        # GitHub API: 根据email搜索用户
                        api_url = cfg.get("github_api_url", "https://api.github.com").rstrip("/")
                        api_key = cfg.get("github_api_key", "")
                        
                        url = f"{api_url}/search/users?q={self._email}+in:email"
                        # 构建请求头（与后端保持一致）
                        headers = {
                            "Accept": "application/vnd.github+json",
                            "User-Agent": "aiperf-admin-client/1.0",
                        }
                        if api_key:
                            # GitHub API 支持 token 和 Bearer 两种方式，后端使用 token，这里保持一致
                            headers["Authorization"] = f"token {api_key}"
                        
                        try:
                            r = httpx.get(url, headers=headers, timeout=10)
                            if r.status_code == 200:
                                data = r.json()
                                items = data.get("items", [])
                                if items:
                                    user_info = items[0]
                                    external_id = user_info.get("login", "")
                                    
                                    # 获取用户详细信息（头像、profile等）
                                    if external_id:
                                        user_url = f"{api_url}/users/{external_id}"
                                        try:
                                            user_r = httpx.get(user_url, headers=headers, timeout=10)
                                            if user_r.status_code == 200:
                                                user_detail = user_r.json()
                                                # 提取扩展信息
                                                extra_json = {
                                                    "avatar_url": user_detail.get("avatar_url", ""),
                                                    "html_url": user_detail.get("html_url", ""),
                                                    "name": user_detail.get("name", ""),
                                                    "bio": user_detail.get("bio", ""),
                                                    "company": user_detail.get("company", ""),
                                                    "blog": user_detail.get("blog", ""),
                                                    "location": user_detail.get("location", ""),
                                                    "public_repos": user_detail.get("public_repos", 0),
                                                    "followers": user_detail.get("followers", 0),
                                                    "following": user_detail.get("following", 0),
                                                }
                                            else:
                                                # 如果获取详细信息失败，使用搜索返回的基本信息
                                                extra_json = {
                                                    "avatar_url": user_info.get("avatar_url", ""),
                                                    "html_url": user_info.get("html_url", ""),
                                                }
                                        except Exception:
                                            # 如果获取详细信息失败，使用搜索返回的基本信息
                                            extra_json = {
                                                "avatar_url": user_info.get("avatar_url", ""),
                                                "html_url": user_info.get("html_url", ""),
                                            }
                                        
                                        # 获取成功后立即返回
                                        if external_id:
                                            self.signals.finished.emit(external_id, extra_json)
                                            return
                                else:
                                    # 搜索没有结果，尝试通过组织成员列表查找（如果配置了 GITHUB_ORG）
                                    github_org = cfg.get("github_org", "")
                                    if github_org and api_key:
                                        try:
                                            # 获取组织成员列表，然后通过邮箱匹配
                                            org_members_url = f"{api_url}/orgs/{github_org}/members"
                                            org_r = httpx.get(org_members_url, headers=headers, timeout=10)
                                            if org_r.status_code == 200:
                                                members = org_r.json()
                                                # 遍历成员，获取每个成员的详细信息并匹配邮箱
                                                for member in members[:30]:  # 限制前30个，避免请求过多
                                                    member_login = member.get("login", "")
                                                    if member_login:
                                                        member_url = f"{api_url}/users/{member_login}"
                                                        try:
                                                            member_r = httpx.get(member_url, headers=headers, timeout=10)
                                                            if member_r.status_code == 200:
                                                                member_detail = member_r.json()
                                                                member_email = member_detail.get("email", "")
                                                                # 检查邮箱是否匹配（忽略大小写）
                                                                if member_email and member_email.lower() == self._email.lower():
                                                                    external_id = member_login
                                                                    # 提取扩展信息
                                                                    extra_json = {
                                                                        "avatar_url": member_detail.get("avatar_url", ""),
                                                                        "html_url": member_detail.get("html_url", ""),
                                                                        "name": member_detail.get("name", ""),
                                                                        "bio": member_detail.get("bio", ""),
                                                                        "company": member_detail.get("company", ""),
                                                                        "blog": member_detail.get("blog", ""),
                                                                        "location": member_detail.get("location", ""),
                                                                        "public_repos": member_detail.get("public_repos", 0),
                                                                        "followers": member_detail.get("followers", 0),
                                                                        "following": member_detail.get("following", 0),
                                                                    }
                                                                    # 找到后立即返回
                                                                    if external_id:
                                                                        self.signals.finished.emit(external_id, extra_json)
                                                                        return
                                                        except Exception:
                                                            continue
                                        except Exception:
                                            pass
                                    
                                    # 两种方法都没找到
                                    self.signals.error.emit(f"未在 GitHub 找到该邮箱对应的用户。\n\n提示：\n1. 请确认邮箱是否正确\n2. 如果用户将邮箱设置为私密，搜索可能无法找到\n3. 如果配置了 GitHub 组织（GITHUB_ORG），系统会尝试在组织成员中查找")
                                    return
                            elif r.status_code == 403:
                                # 403 可能是限流或权限问题
                                rate_limit_remaining = r.headers.get("X-RateLimit-Remaining", "")
                                if rate_limit_remaining == "0":
                                    self.signals.error.emit("GitHub API 调用次数已达上限，请稍后重试。")
                                else:
                                    self.signals.error.emit("GitHub API 调用受限，请检查 API Key 配置或稍后重试。")
                                return
                            elif r.status_code == 422:
                                # 422 通常是搜索查询格式错误
                                self.signals.error.emit("GitHub API 搜索查询格式错误，请检查邮箱格式。")
                                return
                            else:
                                self.signals.error.emit(f"GitHub API 调用失败：HTTP {r.status_code}")
                                return
                        except Exception as e:
                            self.signals.error.emit(f"GitHub API调用失败：{e}")
                    
                    elif self._platform == "jira":
                        # Jira API: 根据email搜索用户（与后端实现保持一致）
                        jira_base = cfg.get("jira_base", "").rstrip("/")
                        jira_account_email = cfg.get("jira_account_email", "")
                        jira_token = cfg.get("jira_token", "")
                        
                        if not jira_base:
                            self.signals.error.emit("请先在设置页面配置 Jira 基础地址（JIRA_BASE）。")
                            return
                        
                        if not jira_account_email:
                            self.signals.error.emit("请先在设置页面配置 Jira 账号邮箱（JIRA_ACCOUNT_EMAIL）。")
                            return
                        
                        if not jira_token:
                            self.signals.error.emit("请先在设置页面配置 Jira API Token（JIRA_TOKEN）。")
                            return
                        
                        # 构建认证头（与后端一致）
                        import base64
                        auth_string = f"{jira_account_email}:{jira_token}"
                        auth_token = base64.b64encode(auth_string.encode()).decode()
                        
                        headers = {
                            "Authorization": f"Basic {auth_token}",
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        }
                        
                        # 尝试使用 user/picker 端点（推荐）
                        # 对邮箱进行URL编码，并尝试多种格式（原始、全小写、全大写）
                        from urllib.parse import quote
                        email_variants = [
                            self._email,  # 原始格式
                            self._email.lower(),  # 全小写
                            self._email.upper(),  # 全大写
                        ]
                        
                        try:
                            user_detail = None
                            # 先尝试 user/picker，使用多种邮箱格式
                            for email_variant in email_variants:
                                url_picker = f"{jira_base}/rest/api/3/user/picker?query={quote(email_variant)}"
                                r = httpx.get(url_picker, headers=headers, timeout=10)
                                if r.status_code == 200:
                                    data = r.json()
                                    users = data.get("users", [])
                                    if users and len(users) > 0:
                                        # 在结果中查找邮箱匹配的用户（大小写不敏感）
                                        for candidate in users:
                                            candidate_email = candidate.get("emailAddress", "")
                                            if candidate_email and candidate_email.lower() == self._email.lower():
                                                user_detail = candidate
                                                break
                                        # 如果没找到精确匹配，使用第一个结果
                                        if not user_detail and users:
                                            user_detail = users[0]
                                        
                                        if user_detail:
                                            # user/picker 返回的用户对象，取 accountId
                                            external_id = user_detail.get("accountId", "")
                                            if external_id:
                                                # 获取用户详细信息
                                                user_info_url = f"{jira_base}/rest/api/3/user?accountId={external_id}"
                                                try:
                                                    user_r = httpx.get(user_info_url, headers=headers, timeout=10)
                                                    if user_r.status_code == 200:
                                                        user_full_detail = user_r.json()
                                                        # 验证邮箱是否匹配（大小写不敏感）
                                                        user_email = user_full_detail.get("emailAddress", "")
                                                        if user_email and user_email.lower() == self._email.lower():
                                                            # 提取扩展信息
                                                            extra_json = {
                                                                "accountId": user_full_detail.get("accountId", ""),
                                                                "accountType": user_full_detail.get("accountType", ""),
                                                                "displayName": user_full_detail.get("displayName", ""),
                                                                "emailAddress": user_full_detail.get("emailAddress", ""),
                                                                "avatarUrls": user_full_detail.get("avatarUrls", {}),
                                                                "active": user_full_detail.get("active", False),
                                                                "timeZone": user_full_detail.get("timeZone", ""),
                                                                "locale": user_full_detail.get("locale", ""),
                                                            }
                                                            self.signals.finished.emit(external_id, extra_json)
                                                            return
                                                except Exception:
                                                    pass
                                                
                                                # 如果获取详细信息失败，使用user/picker返回的基本信息
                                                if user_detail:
                                                    extra_json = {
                                                        "accountId": user_detail.get("accountId", ""),
                                                        "displayName": user_detail.get("displayName", ""),
                                                        "avatarUrl": user_detail.get("avatarUrl", ""),
                                                    }
                                                    self.signals.finished.emit(external_id, extra_json)
                                                    return
                            
                            # 如果 user/picker 没有结果，尝试 user/search，使用多种邮箱格式
                            for email_variant in email_variants:
                                url_search = f"{jira_base}/rest/api/3/user/search?username={quote(email_variant)}"
                                r = httpx.get(url_search, headers=headers, timeout=10)
                                if r.status_code == 200:
                                    users = r.json()
                                    if users and len(users) > 0:
                                        # 在结果中查找邮箱匹配的用户（大小写不敏感）
                                        for candidate in users:
                                            candidate_email = candidate.get("emailAddress", "")
                                            if candidate_email and candidate_email.lower() == self._email.lower():
                                                user_detail = candidate
                                                break
                                        # 如果没找到精确匹配，使用第一个结果
                                        if not user_detail and users:
                                            user_detail = users[0]
                                        
                                        if user_detail:
                                            # user/search 返回的用户对象，取 accountId 或 key
                                            external_id = user_detail.get("accountId") or user_detail.get("key", "")
                                            if external_id:
                                                # 获取用户详细信息
                                                user_info_url = f"{jira_base}/rest/api/3/user?accountId={external_id}"
                                                try:
                                                    user_r = httpx.get(user_info_url, headers=headers, timeout=10)
                                                    if user_r.status_code == 200:
                                                        user_full_detail = user_r.json()
                                                        # 验证邮箱是否匹配（大小写不敏感）
                                                        user_email = user_full_detail.get("emailAddress", "")
                                                        if user_email and user_email.lower() == self._email.lower():
                                                            # 提取扩展信息
                                                            extra_json = {
                                                                "accountId": user_full_detail.get("accountId", ""),
                                                                "accountType": user_full_detail.get("accountType", ""),
                                                                "displayName": user_full_detail.get("displayName", ""),
                                                                "emailAddress": user_full_detail.get("emailAddress", ""),
                                                                "avatarUrls": user_full_detail.get("avatarUrls", {}),
                                                                "active": user_full_detail.get("active", False),
                                                                "timeZone": user_full_detail.get("timeZone", ""),
                                                                "locale": user_full_detail.get("locale", ""),
                                                            }
                                                            self.signals.finished.emit(external_id, extra_json)
                                                            return
                                                except Exception:
                                                    pass
                                                
                                                # 如果获取详细信息失败，使用user/search返回的基本信息
                                                if user_detail:
                                                    extra_json = {
                                                        "accountId": user_detail.get("accountId", ""),
                                                        "displayName": user_detail.get("displayName", ""),
                                                        "emailAddress": user_detail.get("emailAddress", ""),
                                                    }
                                                    self.signals.finished.emit(external_id, extra_json)
                                                    return
                            
                            # 如果所有尝试都没有找到用户
                            self.signals.error.emit(f"未在 Jira 找到该邮箱对应的用户：{self._email}\n\n提示：\n1. 请确认邮箱是否正确（已尝试原始、全小写、全大写格式）\n2. 请确认该用户是否存在于 Jira 系统中\n3. 请确认 API 权限是否足够")
                        except httpx.HTTPStatusError as e:
                            # HTTP错误
                            if e.response.status_code == 401:
                                self.signals.error.emit("Jira API 认证失败，请检查账号邮箱和 Token 配置。")
                            elif e.response.status_code == 403:
                                self.signals.error.emit("Jira API 权限不足，请检查账号权限。")
                            elif e.response.status_code == 404:
                                self.signals.error.emit(f"Jira API 端点不存在（HTTP 404），请检查 Jira 基础地址配置是否正确。\n当前地址：{jira_base}")
                            else:
                                self.signals.error.emit(f"Jira API 调用失败：HTTP {e.response.status_code}")
                        except Exception as e:
                            self.signals.error.emit(f"Jira API调用失败：{e}")
                    
                    elif self._platform == "figma":
                        # Figma API: 根据email搜索用户
                        api_url = cfg.get("figma_api_url", "https://api.figma.com/v1").rstrip("/")
                        api_key = cfg.get("figma_api_key", "")
                        
                        if not api_key:
                            self.signals.error.emit("请先在设置页面配置 Figma API Key。")
                            return
                        
                        # Figma API: 获取用户信息（需要先获取团队，然后查找用户）
                        # 注意：Figma API 没有直接的邮箱搜索接口，这里使用一个变通方法
                        # 实际使用时可能需要根据具体需求调整
                        headers = {
                            "X-Figma-Token": api_key
                        }
                        
                        try:
                            # Figma API 没有直接的邮箱搜索，这里提示用户手动输入
                            # 或者可以通过其他方式获取（如通过团队成员列表）
                            self.signals.error.emit("Figma API 暂不支持通过邮箱自动获取用户ID，请手动输入。")
                            return
                        except Exception as e:
                            self.signals.error.emit(f"Figma API调用失败：{e}")
                    
                    if external_id:
                        self.signals.finished.emit(external_id, extra_json)
                    else:
                        self.signals.error.emit(f"未在 {self._platform.upper()} 找到该邮箱对应的用户")
                except Exception as e:
                    self.signals.error.emit(f"获取失败：{e}")
        
        worker = _FetchWorker(platform, self._user_email)
        worker.signals.finished.connect(self._on_fetch_success)
        worker.signals.error.connect(self._on_fetch_error)
        self._thread_pool.start(worker)
    
    def _on_fetch_success(self, external_id: str, extra_json: dict):
        """自动获取成功"""
        # 恢复按钮状态
        self._auto_fetch_btn.setEnabled(True)
        self._auto_fetch_btn.setText("自动获取")
        
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        self._external_id_edit.setPlainText(external_id)
        
        # 填充扩展信息
        if extra_json:
            try:
                extra_json_str = json.dumps(extra_json, ensure_ascii=False, indent=2)
                self._extra_json_edit.setPlainText(extra_json_str)
            except Exception:
                # 如果JSON序列化失败，忽略
                pass
        
        Toast.show_message(self, "自动获取成功")
    
    def _on_fetch_error(self, error_msg: str):
        """自动获取失败"""
        # 恢复按钮状态
        self._auto_fetch_btn.setEnabled(True)
        self._auto_fetch_btn.setText("自动获取")
        
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        QMessageBox.warning(self, "获取失败", error_msg)


class BindingManageDialog(QDialog):
    """账号绑定管理对话框"""
    def __init__(self, parent, user_id: str, user_name: str):
        super().__init__(parent)
        self._user_id = user_id
        self.setWindowTitle(f"{user_name} ({user_id}) - 账号绑定管理")
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        # 标题
        title = QLabel(f"员工：{user_name} ({user_id})")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("添加绑定")
        btn_add.clicked.connect(self._on_add_clicked)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._load_bindings)
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_refresh)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["平台", "外部ID", "扩展信息", "GitHub别名", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self._table)
        
        # 关闭按钮
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        
        self._thread_pool = QThreadPool.globalInstance()
        self._load_bindings()
    
    def _load_bindings(self):
        """加载账号绑定列表"""
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载账号绑定...")
        
        worker = _BindingWorker(self._user_id)
        worker.signals.finished.connect(self._on_bindings_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)
    
    def _on_bindings_loaded(self, items: List[Dict]):
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        self._table.setRowCount(0)
        
        for item in items:
            row = self._table.rowCount()
            self._table.insertRow(row)
            
            platform = item.get("platform", "").lower()
            external_id = item.get("external_id", "")
            extra_json = item.get("extra_json")
            alias = item.get("alias_github_author")
            
            # 根据平台类型显示不同的扩展信息
            extra_text = ""
            if extra_json:
                if platform == "jira":
                    # Jira：显示 displayName
                    display_name = extra_json.get("displayName") if isinstance(extra_json, dict) else None
                    extra_text = display_name if display_name else ""
                elif platform == "github":
                    # GitHub：显示 login
                    login = extra_json.get("login") if isinstance(extra_json, dict) else None
                    extra_text = login if login else ""
                else:
                    # 其他平台：显示整个JSON（截断）
                    extra_text = json.dumps(extra_json, ensure_ascii=False)[:100]
            
            alias_text = json.dumps(alias, ensure_ascii=False) if alias else ""
            
            self._table.setItem(row, 0, QTableWidgetItem(platform.upper()))
            self._table.setItem(row, 1, QTableWidgetItem(external_id))
            self._table.setItem(row, 2, QTableWidgetItem(extra_text))
            self._table.setItem(row, 3, QTableWidgetItem(alias_text[:100] if alias_text else ""))
            
            # 操作下拉框
            action_combo = QComboBox()
            action_combo.addItems(["选择操作", "编辑", "删除"])
            action_combo.setCurrentIndex(0)  # 默认选中"选择操作"
            action_combo.setFixedWidth(100)  # 设置固定宽度，不要撑满
            
            # 存储当前行的数据，用于回调
            action_combo.setProperty("binding_data", item)
            action_combo.setProperty("binding_id", item.get("id"))
            
            # 连接信号
            action_combo.currentTextChanged.connect(
                lambda text, combo=action_combo: self._on_binding_action_selected(text, combo)
            )
            
            self._table.setCellWidget(row, 4, action_combo)
    
    def _on_error(self, error: str):
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        # 使用统一的错误处理，如果是 detail 错误会用弹出框显示
        handle_api_error(self, Exception(error), "加载失败")
    
    def _on_action_selected(self, text: str, combo: QComboBox):
        """处理操作下拉框选择"""
        if text == "选择操作":
            return  # 忽略默认选项
        
        row_data = combo.property("row_data")
        user_id = combo.property("user_id")
        user_name = combo.property("user_name")
        
        # 重置下拉框到默认选项
        combo.setCurrentIndex(0)
        
        # 根据选择执行相应操作
        if text == "编辑":
            self._on_edit_clicked(row_data)
        elif text == "删除":
            self._on_delete_clicked(user_id)
        elif text == "账号绑定":
            self._on_binding_clicked(user_id, user_name)
    
    def _on_binding_action_selected(self, text: str, combo: QComboBox):
        """处理账号绑定操作下拉框选择"""
        if text == "选择操作":
            return  # 忽略默认选项
        
        binding_data = combo.property("binding_data")
        binding_id = combo.property("binding_id")
        
        # 重置下拉框到默认选项
        combo.setCurrentIndex(0)
        
        # 根据选择执行相应操作
        if text == "编辑":
            self._on_edit_clicked(binding_data)
        elif text == "删除":
            self._on_delete_clicked(binding_id)
    
    def _on_add_clicked(self):
        dlg = BindingEditDialog(self, self._user_id)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            data["user_id"] = self._user_id
            
            # 检查是否已经绑定过该平台
            # 检查登录状态（版本升级除外）
            if not AdminApiClient.is_logged_in():
                return
            try:
                client = AdminApiClient.from_config()
                existing_bindings = client.get_account_bindings(self._user_id)
                if existing_bindings.get("status") == "success":
                    items = existing_bindings.get("items", [])
                    platform = data.get("platform")
                    for item in items:
                        if item.get("platform") == platform:
                            QMessageBox.warning(
                                self,
                                "重复绑定",
                                f"该员工已经绑定了 {platform} 平台，不能重复绑定。\n如需修改，请先删除现有绑定或编辑现有绑定。"
                            )
                            return
            except Exception as e:
                # 如果检查失败，继续执行，让后端API来处理
                pass
            
            self._save_binding(data, is_create=True)
    
    def _on_edit_clicked(self, binding_data: Dict):
        dlg = BindingEditDialog(self, self._user_id, binding_data)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            self._save_binding(data, binding_id=binding_data.get("id"), is_create=False)
    
    def _on_delete_clicked(self, binding_id: int):
        reply = QMessageBox.question(
            self, "确认删除", f"确定要删除该账号绑定吗？", QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        
        try:
            client = AdminApiClient.from_config()
            client.delete_account_binding(binding_id)
            Toast.show_message(self, "删除成功")
            self._load_bindings()
        except Exception as e:
            handle_api_error(self, e, "删除失败")
    
    def _save_binding(self, data: Dict, binding_id: Optional[int] = None, is_create: bool = True):
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            Toast.show_message(self, "未登录，请先登录")
            return
        try:
            client = AdminApiClient.from_config()
            if is_create:
                client.create_account_binding(self._user_id, data)
                Toast.show_message(self, "添加成功")
            else:
                client.update_account_binding(binding_id, data)
                Toast.show_message(self, "更新成功")
            self._load_bindings()
        except Exception as e:
            handle_api_error(self, e, "保存失败")


class EmployeeView(QWidget):
    def __init__(self):
        super().__init__()
        self._thread_pool = QThreadPool.globalInstance()
        # 保存当前筛选条件
        self._current_filters = {
            "team_id": None,
            "level_id": None,
            "salary_band": None
        }
        # 保存所有员工数据（用于筛选）
        self._all_employee_data = []
        self._setup_ui()
        # 初始化时加载筛选维度数据
        self._load_filter_dimensions()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 标题
        header_layout = QHBoxLayout()
        title = QLabel("员工列表管理")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # 筛选区域
        filter_frame = QFrame()
        filter_layout = QVBoxLayout(filter_frame)
        filter_layout.setContentsMargins(8, 8, 8, 8)
        filter_layout.setSpacing(6)
        
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        
        # 团队筛选
        filter_row.addWidget(QLabel("团队："))
        self._team_filter_combo = QComboBox()
        self._team_filter_combo.setEditable(False)
        self._team_filter_combo.setMinimumWidth(150)
        self._team_filter_combo.addItem("全部", None)
        self._team_filter_combo.setEnabled(False)  # 初始状态禁用，等待数据加载
        filter_row.addWidget(self._team_filter_combo)
        
        # 职级筛选
        filter_row.addWidget(QLabel("职级："))
        self._level_filter_combo = QComboBox()
        self._level_filter_combo.setEditable(False)
        self._level_filter_combo.setMinimumWidth(150)
        self._level_filter_combo.addItem("全部", None)
        self._level_filter_combo.setEnabled(False)  # 初始状态禁用，等待数据加载
        filter_row.addWidget(self._level_filter_combo)
        
        # 薪级筛选
        filter_row.addWidget(QLabel("薪级："))
        self._salary_band_filter_combo = QComboBox()
        self._salary_band_filter_combo.setEditable(False)
        self._salary_band_filter_combo.setMinimumWidth(150)
        self._salary_band_filter_combo.addItem("全部", None)
        self._salary_band_filter_combo.setEnabled(False)  # 初始状态禁用，等待数据加载
        filter_row.addWidget(self._salary_band_filter_combo)
        
        filter_row.addStretch()
        
        # 所有按钮放在一起
        # 筛选按钮
        btn_filter = QPushButton("筛选")
        btn_filter.setFixedHeight(28)
        btn_filter.clicked.connect(self._on_filter_clicked)
        filter_row.addWidget(btn_filter)
        
        # 清除筛选按钮
        btn_clear_filter = QPushButton("清除筛选")
        btn_clear_filter.setFixedHeight(28)
        btn_clear_filter.clicked.connect(self._on_clear_filter_clicked)
        filter_row.addWidget(btn_clear_filter)
        
        # 添加员工按钮
        btn_add = QPushButton("添加员工")
        btn_add.setFixedHeight(28)
        btn_add.clicked.connect(self._on_add_clicked)
        filter_row.addWidget(btn_add)
        
        # 刷新按钮
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedHeight(28)
        btn_refresh.clicked.connect(self.reload_from_api)
        filter_row.addWidget(btn_refresh)
        
        filter_layout.addLayout(filter_row)
        layout.addWidget(filter_frame)
        
        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels([
            "员工ID", "姓名", "邮箱", "团队", "角色-子角色", "职级", "薪级", "状态", "入职日期", "在职天数", "操作"
        ])
        header = self._table.horizontalHeader()
        # 设置各列的宽度策略
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 员工ID
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 姓名
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # 邮箱 - 使用Stretch确保有足够空间
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 团队
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 角色
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 职级
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 薪级
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 状态
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)  # 入职日期
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)  # 在职天数
        header.setSectionResizeMode(10, QHeaderView.ResizeToContents)  # 操作
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 启用右键菜单
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table)
    
    def reload_from_api(self):
        """从API重新加载数据"""
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载员工列表...")
        
        # 同时加载员工数据和维度数据（用于筛选下拉框）
        worker = _EmployeeWorker()
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)
        
        # 加载维度数据（团队、职级、薪级）用于筛选下拉框
        self._load_filter_dimensions()
    
    def _on_data_loaded(self, items: List[Dict]):
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        # 保存所有员工数据
        self._all_employee_data = items
        
        # 应用筛选条件
        self._apply_filters()
    
    def _apply_filters(self):
        """应用筛选条件并显示数据"""
        # 根据筛选条件过滤数据
        filtered_items = self._all_employee_data.copy()
        
        # 团队筛选
        team_id = self._current_filters.get("team_id")
        if team_id is not None:
            filtered_items = [item for item in filtered_items if item.get("team_id") == team_id]
        
        # 职级筛选
        level_id = self._current_filters.get("level_id")
        if level_id is not None:
            filtered_items = [item for item in filtered_items if item.get("level_id") == level_id]
        
        # 薪级筛选
        salary_band = self._current_filters.get("salary_band")
        if salary_band:
            filtered_items = [item for item in filtered_items if item.get("salary_band") == salary_band]
        
        # 显示筛选后的数据
        self._table.setRowCount(0)
        
        for emp_data in filtered_items:
            row = self._table.rowCount()
            self._table.insertRow(row)
            
            user_id = str(emp_data.get("user_id", ""))
            name = emp_data.get("name", "")
            is_team_leader = emp_data.get("is_team_leader", False)
            # 如果是组长，在名字后面加"（组长）"
            if is_team_leader:
                name = f"{name}（组长）"
            email = emp_data.get("email") or ""
            # 优先显示名称，如果没有名称则显示ID
            team_name = emp_data.get("team_name") or f"团队{emp_data.get('team_id', 0)}"
            role_name = emp_data.get("role_name") or f"角色{emp_data.get('role_id', 0)}"
            subrole_name = emp_data.get("subrole_name")
            # 如果有子角色，显示"角色名称-子角色名称"，否则只显示角色名称
            if subrole_name:
                role_name = f"{role_name}-{subrole_name}"
            level_name = emp_data.get("level_name") or f"职级{emp_data.get('level_id', 0)}"
            salary_band = emp_data.get("salary_band", "")
            active = emp_data.get("active", 1)
            join_date = emp_data.get("join_date") or ""
            
            # 状态：0=离职，1=在职，2=休假中
            if active == 0:
                status_text = "离职"
            elif active == 2:
                status_text = "休假中"
            else:
                status_text = "在职"
            
            # 计算在职天数
            days_text = "-"
            if join_date:
                try:
                    # 解析入职日期
                    if isinstance(join_date, str):
                        join_date_obj = date.fromisoformat(join_date)
                    else:
                        join_date_obj = join_date
                    
                    # 计算天数差
                    today = date.today()
                    days_diff = (today - join_date_obj).days
                    if days_diff >= 0:
                        days_text = str(days_diff)
                    else:
                        days_text = "0"  # 如果入职日期在未来，显示0
                except Exception:
                    days_text = "-"  # 解析失败显示"-"
            
            # 设置单元格内容和对齐方式（除了邮箱列，其他都居中）
            items_data = [
                (0, user_id, Qt.AlignCenter),
                (1, name, Qt.AlignCenter),
                (2, email, Qt.AlignLeft),  # 邮箱列左对齐
                (3, team_name, Qt.AlignCenter),
                (4, role_name, Qt.AlignCenter),
                (5, level_name, Qt.AlignCenter),
                (6, salary_band, Qt.AlignCenter),
                (7, status_text, Qt.AlignCenter),
                (8, join_date, Qt.AlignCenter),
                (9, days_text, Qt.AlignCenter),  # 在职天数
            ]
            
            for col, text, alignment in items_data:
                cell_item = QTableWidgetItem(text)
                cell_item.setTextAlignment(alignment)
                self._table.setItem(row, col, cell_item)
            
            # 操作下拉框（移除删除功能，因为编辑可以改状态）
            action_combo = QComboBox()
            action_combo.addItems(["选择操作", "编辑", "账号绑定", "设为组长"])
            action_combo.setCurrentIndex(0)  # 默认选中"选择操作"
            action_combo.setFixedWidth(100)  # 设置固定宽度，不要撑满
            
            # 存储当前行的员工数据字典，用于回调
            action_combo.setProperty("row_data", emp_data)
            action_combo.setProperty("user_id", user_id)
            action_combo.setProperty("user_name", name)
            
            # 连接信号
            action_combo.currentTextChanged.connect(
                lambda text, combo=action_combo: self._on_action_selected(text, combo)
            )
            
            self._table.setCellWidget(row, 10, action_combo)  # 操作列在第10列（索引从0开始）
    
    def _load_filter_dimensions(self):
        """加载筛选下拉框所需的维度数据（团队、职级、薪级）"""
        worker = _DialogDataWorker(
            need_teams=True,
            need_levels=True,
            need_salary_bands=True,
            need_roles=False
        )
        worker.signals.teams_loaded.connect(self._on_filter_teams_loaded)
        worker.signals.levels_loaded.connect(self._on_filter_levels_loaded)
        worker.signals.salary_bands_loaded.connect(self._on_filter_salary_bands_loaded)
        worker.signals.error.connect(lambda error_type, msg: None)  # 静默处理错误
        self._thread_pool.start(worker)
    
    def _on_filter_teams_loaded(self, teams: List[Dict]):
        """筛选下拉框：团队数据加载完成"""
        self._team_filter_combo.clear()
        self._team_filter_combo.addItem("全部", None)
        for team in teams:
            team_name = team.get("name", "")
            team_desc = team.get("team_desc")
            # 如果有描述，显示"团队名称 - 团队描述"，否则只显示团队名称
            if team_desc:
                display_text = f"{team_name} - {team_desc}"
            else:
                display_text = team_name
            self._team_filter_combo.addItem(display_text, team["id"])
        self._team_filter_combo.setEnabled(True)
    
    def _on_filter_levels_loaded(self, levels: List[Dict]):
        """筛选下拉框：职级数据加载完成"""
        self._level_filter_combo.clear()
        self._level_filter_combo.addItem("全部", None)
        for level in levels:
            self._level_filter_combo.addItem(level["name"], level["id"])
        self._level_filter_combo.setEnabled(True)
    
    def _on_filter_salary_bands_loaded(self, salary_bands: List[Dict]):
        """筛选下拉框：薪级数据加载完成"""
        self._salary_band_filter_combo.clear()
        self._salary_band_filter_combo.addItem("全部", None)
        for band in salary_bands:
            # 显示格式：S1 (3,000 - 4,500)
            display_text = f"{band['band']} ({int(band['salary_min']):,} - {int(band['salary_max']):,})"
            self._salary_band_filter_combo.addItem(display_text, band["band"])
        self._salary_band_filter_combo.setEnabled(True)
    
    def _on_filter_clicked(self):
        """筛选按钮点击事件"""
        # 获取筛选条件
        team_id = self._team_filter_combo.currentData()
        level_id = self._level_filter_combo.currentData()
        salary_band = self._salary_band_filter_combo.currentData()
        
        # 更新筛选条件
        self._current_filters = {
            "team_id": team_id,
            "level_id": level_id,
            "salary_band": salary_band
        }
        
        # 应用筛选
        self._apply_filters()
    
    def _on_clear_filter_clicked(self):
        """清除筛选按钮点击事件"""
        # 重置下拉框
        self._team_filter_combo.setCurrentIndex(0)
        self._level_filter_combo.setCurrentIndex(0)
        self._salary_band_filter_combo.setCurrentIndex(0)
        
        # 清除筛选条件
        self._current_filters = {
            "team_id": None,
            "level_id": None,
            "salary_band": None
        }
        
        # 应用筛选（显示所有数据）
        self._apply_filters()
    
    def _on_action_selected(self, text: str, combo: QComboBox):
        """处理操作下拉框选择"""
        if text == "选择操作":
            return  # 忽略默认选项
        
        row_data = combo.property("row_data")
        user_id = combo.property("user_id")
        user_name = combo.property("user_name")
        
        # 重置下拉框到默认选项
        combo.setCurrentIndex(0)
        
        # 根据选择执行相应操作（移除删除功能，因为编辑可以改状态）
        if text == "编辑":
            self._on_edit_clicked(row_data)
        elif text == "账号绑定":
            self._on_binding_clicked(user_id, user_name)
        elif text == "设为组长":
            self._on_set_team_leader_clicked(user_id, user_name, row_data)
    
    def _on_error(self, error: str):
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        Toast.show_message(self, f"加载失败：{error}")
    
    def _on_add_clicked(self):
        dlg = EmployeeEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            self._save_employee(data, is_create=True)
    
    def _on_edit_clicked(self, employee_data: Dict):
        dlg = EmployeeEditDialog(self, employee_data)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            self._save_employee(data, user_id=employee_data.get("user_id"), is_create=False)
    
    def _on_binding_clicked(self, user_id: str, user_name: str):
        dlg = BindingManageDialog(self, user_id, user_name)
        dlg.exec()
    
    def _on_set_team_leader_clicked(self, user_id: str, user_name: str, row_data: Dict):
        """设为组长"""
        # 获取团队信息
        team_name = row_data.get("team_name", "未知团队")
        
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "设为组长",
            f"确定要将 {user_name} ({user_id}) 设为 {team_name} 的组长吗？\n\n"
            "注意：如果该团队已有组长，将被覆盖。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        
        # 检查登录状态
        if not AdminApiClient.is_logged_in():
            Toast.show_message(self, "未登录，请先登录")
            return
        
        # 显示加载状态
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("设置组长中...")
        
        try:
            client = AdminApiClient.from_config()
            response = client.set_team_leader(user_id)
            
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            
            if response.get("status") == "success":
                Toast.show_message(self, "设置成功")
                # 刷新员工列表
                self.reload_from_api()
            else:
                error_msg = response.get("message", "设置失败")
                Toast.show_message(self, f"设置失败：{error_msg}")
        except Exception as e:
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            handle_api_error(self, e, "设置失败")
    
    def _save_employee(self, data: Dict, user_id: Optional[str] = None, is_create: bool = True):
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            Toast.show_message(self, "未登录，请先登录")
            return
        try:
            client = AdminApiClient.from_config()
            if is_create:
                client.create_employee(data)
                Toast.show_message(self, "添加成功")
            else:
                client.update_employee(user_id, data)
                Toast.show_message(self, "更新成功")
            self.reload_from_api()
        except Exception as e:
            handle_api_error(self, e, "保存失败")
    
    def _show_context_menu(self, position):
        """显示右键菜单"""
        item = self._table.itemAt(position)
        if item is None:
            return
        
        menu = QMenu(self)
        
        # 复制操作
        copy_action = QAction("复制", self)
        copy_action.triggered.connect(self._copy_selected_cell)
        menu.addAction(copy_action)
        
        # 显示菜单
        menu.exec_(self._table.viewport().mapToGlobal(position))
    
    def _copy_selected_cell(self):
        """复制选中的单元格内容"""
        current_item = self._table.currentItem()
        if current_item is None:
            return
        
        text = current_item.text()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            Toast.show_message(self, "已复制到剪贴板")

