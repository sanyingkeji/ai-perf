from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect, QApplication
from PySide6.QtCore import (
    QPropertyAnimation, QEasingCurve, Qt, QTimer
)
from PySide6.QtGui import QFont


class Toast(QLabel):
    """
    美化版轻提示（Toast）
    - 自动淡出
    - 半透明背景
    - 圆角卡片
    - 自动居中
    """

    def __init__(self, parent, message):
        super().__init__(parent)

        self.setText(message)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("Arial", 12))

        # 美化样式
        self.setStyleSheet("""
            background-color: rgba(0,0,0,180);
            color: white;
            padding: 10px 20px;
            border-radius: 8px;
        """)
        
        # 启用鼠标点击事件，添加鼠标指针样式提示可点击
        self.setCursor(Qt.PointingHandCursor)

        self.adjustSize()

        # 居中显示
        parent_w = parent.width()
        parent_h = parent.height()
        x = (parent_w - self.width()) // 2
        y = parent_h/2  # 120  # 靠上 parent_h - 120
        self.move(x, y)

        # 淡出动画
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)

        # 停留时间（正常显示时间）
        self._stay_duration = 2500  # 2.5秒
        # 淡出动画时间
        self._fade_duration = 800  # 0.8秒

        self.anim = QPropertyAnimation(self.effect, b"opacity")
        self.anim.setDuration(self._fade_duration)
        self.anim.setStartValue(1)
        self.anim.setEndValue(0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        
        # 先停留，再开始淡出动画
        QTimer.singleShot(self._stay_duration, self._start_fade_out)
        
        # 总时间后关闭
        QTimer.singleShot(self._stay_duration + self._fade_duration, self.close)
    
    def _start_fade_out(self):
        """开始淡出动画"""
        self.anim.start()

    def mousePressEvent(self, event):
        """点击时复制内容到剪贴板"""
        if event.button() == Qt.LeftButton:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.text())
            # 可选：显示一个短暂的"已复制"提示
            original_text = self.text()
            self.setText("已复制到剪贴板")
            QTimer.singleShot(500, lambda: self.setText(original_text))
        super().mousePressEvent(event)

    @classmethod
    def show_message(cls, parent, message: str):
        """
        推荐调用入口：
            Toast.show_message(self, "xxx")
        """
        toast = cls(parent, message)
        toast.show()
