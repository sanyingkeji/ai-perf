#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周数计算工具（管理端客户端独立版本）
从 jobs/week_calculator.py 提取，不依赖数据库

周数计算规则：
- 基准：2025年11月第一个工作日（11-03）所在周是第6周
- 周数从第6周开始（11月第一个工作日所在周），一直到现在的周数（自动算）
"""

from datetime import date, timedelta
from typing import Tuple


# 基准：2025年11月第一个工作日（11-03）所在周是第6周
# 使用简单的默认规则：周一至周五为工作日
def _is_workday(target_date: date) -> bool:
    """简单的默认规则：周一至周五为工作日"""
    weekday = target_date.weekday()  # 0=周一，6=周日
    return weekday < 5  # 周一至周五（0-4）为工作日


# 找到11月第一个工作日
NOV_FIRST_WORKDAY = None
for day in range(1, 8):  # 检查11月1-7日
    test_date = date(2025, 11, day)
    if _is_workday(test_date):
        NOV_FIRST_WORKDAY = test_date
        break

if NOV_FIRST_WORKDAY is None:
    # 如果找不到，默认使用11月3日（已知是2025年11月的第一个工作日）
    NOV_FIRST_WORKDAY = date(2025, 11, 3)

# 计算11月第一个工作日所在周的周一
nov_first_workday_weekday = NOV_FIRST_WORKDAY.weekday()  # 0=周一, 6=周日
NOV_FIRST_WORKDAY_WEEK_MONDAY = NOV_FIRST_WORKDAY - timedelta(days=nov_first_workday_weekday)

# 第6周的周一是11月第一个工作日所在周的周一
WEEK_6_MONDAY = NOV_FIRST_WORKDAY_WEEK_MONDAY
WEEK_6_NUMBER = 6

# 计算第1周的周一：往前推5周（6-1=5）
WEEK_1_MONDAY = WEEK_6_MONDAY - timedelta(days=(WEEK_6_NUMBER - 1) * 7)


def get_week_number(target_date: date) -> int:
    """
    计算指定日期是第几周（从2025年11月1日所在周开始计算为第1周）
    
    Args:
        target_date: 目标日期
        
    Returns:
        周数（从1开始）
    """
    # 计算目标日期所在周的周一
    days_since_monday = target_date.weekday()  # 0=周一, 6=周日
    week_monday = target_date - timedelta(days=days_since_monday)
    
    # 计算两个周一之间的天数差
    days_diff = (week_monday - WEEK_1_MONDAY).days
    
    # 计算周数（每7天为一周）
    week_number = (days_diff // 7) + 1
    
    return week_number


def get_week_date_range(week_number: int) -> Tuple[date, date]:
    """
    获取指定周数的日期范围（周一到周日）
    
    Args:
        week_number: 周数（从1开始）
        
    Returns:
        (week_start, week_end): 周的开始日期（周一）和结束日期（周日）
    """
    # 计算目标周的周一
    target_week_monday = WEEK_1_MONDAY + timedelta(days=(week_number - 1) * 7)
    
    # 计算目标周的周日
    target_week_sunday = target_week_monday + timedelta(days=6)
    
    return target_week_monday, target_week_sunday


def get_current_week_number() -> int:
    """
    获取当前日期是第几周
    
    Returns:
        周数
    """
    return get_week_number(date.today())

