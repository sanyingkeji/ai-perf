## 数据合理性评估（DQ）与异常判定规则（现行）

本文档描述当前系统里**已经实现**的“原始数据合理性评估/异常判定”规则，用于防范误填/作弊/采集口径异常，并将结果**直接体现在评分置信度（confidence）**上，从而对 `total_ai` 产生扣分影响。

---

## 0. 规则落点与数据流

- **规则生成位置（程序侧）**：`jobs/build_ai_request.py` → `evaluate_reasonableness(...)`
  - 输出写入 `ai_input` 的 `data_quality`：
    - `data_quality.anomalies`: 异常列表（供 AI 解释/复核）
    - `data_quality.confidence_cap`: 建议置信度上限（0~1）
    - `data_quality.confidence_cap_reasons`: 用于解释 cap 的原因（最多 3 条）
- **规则生效位置（评分侧强制落地）**
  - `scorer/run_daily_score.py`：对模型返回的 `confidence` 做 `min(confidence, confidence_cap)` 裁剪
  - `scorer/run_review_score.py`：复评默认同样裁剪（cap 来自 `original_input.data_quality`）
    - 但复评允许在“解释充分且可核验”时，通过复评输出的 `review.dq_override.new_confidence_cap` 上调/解除 cap（用于避免规则误报导致的误判）
- **提示词约束（模型侧）**：`prompts/system_v1.0.md`
  - 明确要求模型根据 `data_quality.anomalies` 降低置信度，且在日常评分中 `confidence` 不应高于 `confidence_cap`
  - 复评模式默认同样遵守，但允许在 `review.dq_override` 中提出新的 cap（并给出理由），程序侧会按“有效 cap”执行裁剪

---

## 1. anomalies 数据结构与严重度

### 1.1 anomalies 单条结构

`data_quality.anomalies[]` 的每个元素结构：

- `code`: 异常码（稳定标识，方便统计/复核）
- `severity`: 严重度枚举：`info | warn | severe | critical`
- `message`: 人类可读说明（中文）
- `evidence`（可选）: 结构化证据（数值、比值等）

### 1.2 严重度 → 置信度上限（程序侧）

当前固定映射（取更低者生效）：

- `critical` → `confidence_cap = 0.35`
- `severe`   → `confidence_cap = 0.55`
- `warn`     → `confidence_cap = 0.75`
- `info`     → `confidence_cap = 0.90`
- 若无异常：`confidence_cap = 1.0`

> 说明：最终输出的 `confidence` 会被评分程序裁剪为 `min(model_confidence, confidence_cap)`。

---

## 2. Jira 异常规则（现行）

### 2.1 数据来源

- 指标来源：`metrics_raw.jira.*`（由 `raw_jira_daily` 读取拼装）
- worklog 统计来源：`metrics_raw.jira.raw_meta.worklog_stats`
  - 由 `etl/jira_loader.py` 在采集时计算并写入 `raw_jira_daily.raw_meta` 的 `worklog_stats` 字段。

### 2.2 规则列表

#### A) 数值合法性（明显不合理）

- **code**：`non_numeric_value`（severe）
  - **条件**：Jira 以下字段出现“非数字”：
    - `issues_completed / on_time_count / status_updates / worklog_minutes / attachments_count`
- **code**：`negative_value`（severe）
  - **条件**：上述任一字段 < 0

#### B) 指标一致性（轻量逻辑校验）

- **code**：`jira_on_time_gt_completed`（warn）
  - **条件**：`on_time_count > issues_completed`
  - **evidence**：`{"on_time_count": x, "issues_completed": y}`

#### C) 工时高值（绝对阈值）

- **code**：`jira_worklog_too_high`（critical / severe）
  - **条件**：
    - `worklog_minutes >= DQ_WORKLOG_CRITICAL_MIN` → critical（默认 1440 分钟，即 24h）
    - `worklog_minutes >= DQ_WORKLOG_SEVERE_MIN`   → severe（默认 960 分钟，即 16h）
- **code**：`jira_worklog_high`（warn）
  - **条件**：`worklog_minutes >= DQ_WORKLOG_WARN_MIN`（默认 720 分钟，即 12h）

#### D) 工时重叠/并行（强证据）

依赖 `raw_meta.worklog_stats`：

- **code**：`jira_worklog_overlap`
  - **severe 条件**：`overlap_minutes >= 120` 或 `overlap_ratio >= 0.3`
  - **warn 条件**：`overlap_minutes > 0`
  - **evidence**：`{"overlap_minutes": x, "overlap_ratio": r}`

- **code**：`jira_worklog_same_start_large`（severe）
  - **条件**：`same_started_large_groups > 0`
  - 含义：存在“同一分钟开始的多条大额工时记录”，常见于同一时间给多个 issue 记 8h 的误填/作弊模式。

#### E) 相对团队基线离群

- **code**：`jira_worklog_outlier_vs_team`（warn）
  - **条件**：
    - `team_avg.worklog_minutes > 0`
    - `worklog_minutes >= DQ_WORKLOG_WARN_MIN`
    - `worklog_minutes / team_avg.worklog_minutes >= 3.0`
  - **evidence**：`{"worklog_minutes": x, "team_avg_worklog_minutes": t, "ratio": r}`

---

## 3. GitHub 异常规则（现行）

### 3.1 数据来源

- 指标来源：`metrics_raw.github.*`（由 `raw_github_daily` 读取拼装）
  - `commits / pr_opened / pr_merged / reviews`
  - `diff_stats.added / diff_stats.deleted / diff_stats.files`

### 3.2 规则列表

#### A) 数值合法性（明显不合理）

- **code**：`non_numeric_value`（severe）
  - **条件**：上述任一字段出现“非数字”
- **code**：`negative_value`（severe）
  - **条件**：上述任一字段 < 0

#### B) 代码改动行数异常（绝对阈值）

令 `diff_sum = added + deleted`：

- **code**：`github_diff_too_high`（severe）
  - **条件**：`diff_sum >= DQ_GH_DIFF_SEVERE`（默认 100000）
- **code**：`github_diff_high`（warn）
  - **条件**：`diff_sum >= DQ_GH_DIFF_WARN`（默认 20000）

#### C) 相对团队基线离群

- **code**：`github_diff_outlier_vs_team`（warn）
  - **条件**：
    - `diff_sum >= DQ_GH_DIFF_WARN`
    - `team_avg.(diff_added+diff_deleted) > 0`
    - `diff_sum / team_avg_diff_sum >= 6.0`

---

## 4. Figma 异常规则（现行）

### 4.1 数据来源与口径说明（重要）

- 指标来源：`metrics_raw.figma.*`（由 `raw_figma_daily` 读取拼装）
  - `files_updated / nodes_changed / comments_added / comments_resolved / mentions / components_published / shared_links`
- 当前采集口径在 `etl/figma_loader.py`：
  - `nodes_changed`：并非“diff 变更节点数”，而是“用户当天更新过的文件 → 统计该文件 document 的总节点数（近似）”
  - `components_published`：并非“当天发布组件数”，而是“用户当天更新过的文件 → 统计文件内组件节点总数（近似）”
  - `shared_links`：简化为“用户当天更新过的文件数”（近似）

因此，Figma 的异常规则既可用于识别“批量操作/误采集”，也可用于暴露“采集口径导致的夸大”。

### 4.2 规则列表

#### A) 数值合法性（明显不合理）

- **code**：`non_numeric_value`（severe）
  - **条件**：任一 Figma 指标出现“非数字”
- **code**：`negative_value`（severe）
  - **条件**：任一 Figma 指标 < 0

#### B) 单指标极端值（绝对阈值）

- **files_updated**
  - `figma_files_too_high`（severe）：`files_updated >= DQ_FIGMA_FILES_SEVERE`（默认 60）
  - `figma_files_high`（warn）：`files_updated >= DQ_FIGMA_FILES_WARN`（默认 20）

- **nodes_changed**
  - `figma_nodes_too_high`（severe）：`nodes_changed >= DQ_FIGMA_NODES_SEVERE`（默认 50000）
  - `figma_nodes_high`（warn）：`nodes_changed >= DQ_FIGMA_NODES_WARN`（默认 10000）

- **comments_added + comments_resolved（合计）**
  - `figma_comments_too_high`（severe）：合计 `>= DQ_FIGMA_COMMENTS_SEVERE`（默认 150）
  - `figma_comments_high`（warn）：合计 `>= DQ_FIGMA_COMMENTS_WARN`（默认 30）

- **mentions**
  - `figma_mentions_too_high`（severe）：`mentions >= DQ_FIGMA_MENTIONS_SEVERE`（默认 100）
  - `figma_mentions_high`（warn）：`mentions >= DQ_FIGMA_MENTIONS_WARN`（默认 20）

- **components_published**
  - `figma_components_too_high`（severe）：`components_published >= DQ_FIGMA_COMPONENTS_SEVERE`（默认 200）
  - `figma_components_high`（warn）：`components_published >= DQ_FIGMA_COMPONENTS_WARN`（默认 50）

- **shared_links**
  - `figma_shared_links_too_high`（severe）：`shared_links >= DQ_FIGMA_SHARED_LINKS_SEVERE`（默认 60）
  - `figma_shared_links_high`（warn）：`shared_links >= DQ_FIGMA_SHARED_LINKS_WARN`（默认 20）

#### C) 结构一致性（明显不合理组合）

- **code**：`figma_inconsistent_metrics`（warn）
  - **条件**：`files_updated == 0` 但 `nodes_changed > 0` 或 `components_published > 0` 或 `shared_links > 0`

- **code**：`figma_shared_links_gt_files`（severe）
  - **条件**：`shared_links > files_updated`
  - 说明：按当前采集逻辑通常不应出现，出现则怀疑口径/写入异常。

#### D) 相对团队基线离群（防止基线接近 0 的误报）

规则原则：仅在“达到一定绝对值阈值”后，才与团队均值比值；且团队均值必须 > 0；比值阈值为 6.0。

- `figma_nodes_outlier_vs_team`（warn）
  - 条件：`nodes_changed >= DQ_FIGMA_NODES_WARN` 且 `nodes_changed / team_avg.nodes_changed >= 6.0`
- `figma_files_outlier_vs_team`（warn）
  - 条件：`files_updated >= DQ_FIGMA_FILES_WARN` 且 `files_updated / team_avg.files_updated >= 6.0`
- `figma_comments_added_outlier_vs_team`（warn）
  - 条件：`comments_added >= max(1, DQ_FIGMA_COMMENTS_WARN//2)` 且 `comments_added / team_avg.comments_added >= 6.0`
- `figma_comments_resolved_outlier_vs_team`（warn）
  - 同上
- `figma_mentions_outlier_vs_team`（warn）
  - 条件：`mentions >= DQ_FIGMA_MENTIONS_WARN` 且 `mentions / team_avg.mentions >= 6.0`
- `figma_components_outlier_vs_team`（warn）
  - 条件：`components_published >= DQ_FIGMA_COMPONENTS_WARN` 且 `components_published / team_avg.components_published >= 6.0`
- `figma_shared_links_outlier_vs_team`（warn）
  - 条件：`shared_links >= DQ_FIGMA_SHARED_LINKS_WARN` 且 `shared_links / team_avg.shared_links >= 6.0`

---

## 5. 跨平台一致性异常（现行）

- **code**：`high_worklog_but_no_artifacts`（warn）
  - **条件**：
    - `jira.worklog_minutes >= DQ_WORKLOG_SEVERE_MIN`（默认 >=960）
    - 且以下“可验证信号”全部为 0：
      - Jira：`issues_completed + status_updates + attachments_count`
      - GitHub：`commits + pr_opened + pr_merged + reviews`
      - Figma：`files_updated + nodes_changed + comments_added + comments_resolved`
  - **目的**：防止“工时很高但看不到任何客观痕迹”的情况给高置信度。

---

## 6. 阈值环境变量（默认值）

这些变量已追加到项目根目录 `.env` 末尾，可按团队实际情况调参：

### 6.1 Jira

- `DQ_WORKLOG_WARN_MIN=720`：>=12h → warn
- `DQ_WORKLOG_SEVERE_MIN=960`：>=16h → severe
- `DQ_WORKLOG_CRITICAL_MIN=1440`：>=24h → critical

### 6.2 GitHub

- `DQ_GH_DIFF_WARN=20000`：added+deleted >= 20000 → warn
- `DQ_GH_DIFF_SEVERE=100000`：added+deleted >= 100000 → severe

### 6.3 Figma

- `DQ_FIGMA_FILES_WARN=20`
- `DQ_FIGMA_FILES_SEVERE=60`
- `DQ_FIGMA_NODES_WARN=10000`
- `DQ_FIGMA_NODES_SEVERE=50000`
- `DQ_FIGMA_COMMENTS_WARN=30`
- `DQ_FIGMA_COMMENTS_SEVERE=150`
- `DQ_FIGMA_MENTIONS_WARN=20`
- `DQ_FIGMA_MENTIONS_SEVERE=100`
- `DQ_FIGMA_COMPONENTS_WARN=50`
- `DQ_FIGMA_COMPONENTS_SEVERE=200`
- `DQ_FIGMA_SHARED_LINKS_WARN=20`
- `DQ_FIGMA_SHARED_LINKS_SEVERE=60`

---

## 7. 输出与复核建议

- anomalies 的目标不是直接“判作弊”，而是把异常显式化，并通过 confidence 扣分，促使：
  - 员工补充说明（加班/事故/批量迁移/集中评审等）
  - 或定位采集口径问题（尤其是 Figma 指标的口径近似）
  - 或发现明显误填（如同一时间对多个 issue 记 8h）

