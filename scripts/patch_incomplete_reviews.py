#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
临时补丁脚本：补全因客户端超时未完成的复评

问题场景：
- 员工提交复评后，AI 已评分成功（生成了 output/review/{date}/{user_id}.json）
- 但客户端超时断开，导致 save_review_score_to_db 未执行
- 数据库中的 is_reviewed 仍为 0，但输出文件已存在

用法：
  # 补全指定日期的未完成复评
  python scripts/patch_incomplete_reviews.py 2025-12-18

  # 补全多个日期（用逗号分隔）
  python scripts/patch_incomplete_reviews.py 2025-12-18,2025-12-19

  # 不传参数，默认处理今天
  python scripts/patch_incomplete_reviews.py
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Tuple

# 把工程根目录加入 sys.path
ROOT_DIR = Path(os.getenv("AI_PERF_ROOT", Path(__file__).resolve().parents[1]))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import get_conn  # type: ignore

OUTPUT_REVIEW_ROOT = ROOT_DIR / "output" / "review"


def find_incomplete_reviews(target_date: date) -> List[Tuple[str, Path]]:
    """
    查找指定日期下未完成的复评（有输出文件但数据库未标记为已复评）
    
    Returns:
        List[Tuple[user_id, output_file_path]]
    """
    incomplete: List[Tuple[str, Path]] = []
    
    # 检查输出目录
    day_dir = OUTPUT_REVIEW_ROOT / target_date.isoformat()
    if not day_dir.exists():
        print(f"[patch] 输出目录不存在: {day_dir}")
        return incomplete
    
    # 查找所有输出文件
    output_files = []
    for p in sorted(day_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.startswith("summary_"):
            continue
        if p.suffix.lower() != ".json":
            continue
        user_id = p.stem  # 文件名即 user_id，如 u1001.json → u1001
        output_files.append((user_id, p))
    
    if not output_files:
        print(f"[patch] 日期 {target_date} 没有找到任何输出文件")
        return incomplete
    
    # 检查数据库中哪些未标记为已复评
    with get_conn() as conn:
        with conn.cursor() as cur:
            for user_id, output_path in output_files:
                cur.execute(
                    """
                    SELECT is_reviewed, total_ai
                    FROM ai_score_daily
                    WHERE date = %s AND user_id = %s
                    """,
                    (target_date, user_id)
                )
                row = cur.fetchone()
                
                if row:
                    is_reviewed = bool(row[0] or 0)
                    if not is_reviewed:
                        # 有输出文件但数据库未标记为已复评
                        incomplete.append((user_id, output_path))
                        print(f"[patch] 发现未完成复评: {user_id} (输出文件存在，但数据库 is_reviewed=0)")
                else:
                    # 数据库中没有记录，但输出文件存在（可能是异常情况）
                    print(f"[patch] 警告: {user_id} 有输出文件但数据库无记录，跳过")
    
    return incomplete


def patch_review(target_date: date, user_id: str, output_path: Path) -> bool:
    """
    补全单个用户的复评（将输出文件保存到数据库）
    
    Returns:
        bool: 是否成功
    """
    try:
        # 读取输出文件
        with output_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 解析数据
        scores = data.get("scores") or {}
        execution = int(scores.get("execution") or 0)
        quality = int(scores.get("quality") or 0)
        collaboration = int(scores.get("collaboration") or 0)
        reflection = int(scores.get("reflection") or 0)
        
        confidence = float(data.get("confidence") or 0.0)
        eligible = data.get("eligible", False)
        
        # 计算 total_ai
        if eligible:
            sum_scores = execution + quality + collaboration + reflection
            total_ai = round(sum_scores * confidence)
        else:
            total_ai = 0
        
        missing_dims = data.get("missing_dims")
        evidence = data.get("evidence")
        recommendations = data.get("recommendations")
        expectation_adjustments = data.get("expectation_adjustments")
        growth_rate_estimate = data.get("growth_rate_estimate")
        reason = data.get("reason")
        
        # 查询原始分数（复评前的 total_ai）
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 先查询原始分数和记录是否存在
                cur.execute(
                    "SELECT total_ai FROM ai_score_daily WHERE date = %s AND user_id = %s",
                    (target_date, user_id)
                )
                row = cur.fetchone()
                if not row:
                    print(f"[patch] ✗ 跳过: {user_id} 数据库中没有记录（异常情况）", file=sys.stderr)
                    return False
                
                original_total_ai = int(row[0]) if row[0] is not None else None
                
                # 执行更新
                sql = """
                UPDATE ai_score_daily SET
                  execution               = %s,
                  quality                 = %s,
                  collaboration           = %s,
                  reflection              = %s,
                  confidence              = %s,
                  total_ai                = %s,
                  missing_dims            = %s,
                  evidence                = %s,
                  recommendations         = %s,
                  expectation_adjustments = %s,
                  growth_rate_estimate    = %s,
                  eligible                = %s,
                  reason                  = %s,
                  is_reviewed             = 1,
                  original_total_ai       = %s
                WHERE date = %s AND user_id = %s
                """.strip()
                
                params = (
                    execution,
                    quality,
                    collaboration,
                    reflection,
                    round(confidence, 2),
                    total_ai,
                    json.dumps(missing_dims, ensure_ascii=False) if missing_dims is not None else None,
                    json.dumps(evidence, ensure_ascii=False) if evidence is not None else None,
                    json.dumps(recommendations, ensure_ascii=False) if recommendations is not None else None,
                    expectation_adjustments,
                    growth_rate_estimate,
                    int(eligible) if eligible is not None else 1,
                    reason,
                    original_total_ai,
                    target_date,
                    user_id,
                )
                
                cur.execute(sql, params)
                conn.commit()
                
                print(f"[patch] ✓ 补全成功: {user_id} (date={target_date}, total_ai={total_ai}, original={original_total_ai})")
                return True
                
    except Exception as e:
        print(f"[patch] ✗ 补全失败: {user_id} - {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    """主函数"""
    if len(sys.argv) > 1:
        date_strs = sys.argv[1].split(",")
        dates = []
        for ds in date_strs:
            ds = ds.strip()
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").date()
                dates.append(d)
            except ValueError:
                print(f"[patch] 错误: 无效的日期格式 '{ds}'，应为 YYYY-MM-DD", file=sys.stderr)
                sys.exit(1)
    else:
        dates = [date.today()]
    
    print(f"[patch] 开始处理 {len(dates)} 个日期: {[d.isoformat() for d in dates]}")
    
    total_found = 0
    total_patched = 0
    
    for target_date in dates:
        print(f"\n[patch] ===== 处理日期: {target_date} =====")
        
        # 查找未完成的复评
        incomplete = find_incomplete_reviews(target_date)
        total_found += len(incomplete)
        
        if not incomplete:
            print(f"[patch] 日期 {target_date} 没有未完成的复评")
            continue
        
        print(f"[patch] 找到 {len(incomplete)} 个未完成的复评，开始补全...")
        
        # 逐个补全
        for user_id, output_path in incomplete:
            if patch_review(target_date, user_id, output_path):
                total_patched += 1
    
    print(f"\n[patch] ===== 处理完成 =====")
    print(f"[patch] 总计: 发现 {total_found} 个未完成复评，成功补全 {total_patched} 个")
    
    if total_found > total_patched:
        print(f"[patch] 警告: {total_found - total_patched} 个复评补全失败，请检查错误信息", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
