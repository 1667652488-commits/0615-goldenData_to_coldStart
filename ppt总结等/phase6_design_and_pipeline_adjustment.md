# Phase6 设计方案及 Pipeline 参数调整

> 日期：2026-06-12
> 状态：已确认，待实现

---

## 一、Phase6 设计：规则语言化 + 排序筛选

### 1.1 规则语言化输出

**目标**：将 `rules.json` 转化为类似 `人工归纳规则.txt` 的自然语言格式，方便作为评估器（checker）的提示词输入。

#### (a) 整体规则描述部分

参照人工归纳规则的写法：

```
是 agent 是否产生了 badcase 的评判者，根据以下类别进行评判，并界定问题出在哪里。
按几个类别评判，符合如下类别的属于"失败/部分通过"：

① [错误类别名称]：
    判定标准：[error_reason 自然语言化]
    检查条件：[IF 条件用自然语言描述]
    典型表现：[从 few_shots negative_examples 中提炼]
    ...
```

#### (b) IF-THEN 规则的自然语言转化

采用 **"当…时，必须…否则判定为[类别]"** 的三元组格式：

```
IF   当 [feature_description 用自然语言描述触发场景]
THEN agent 必须 [judgment_criteria 中的 True 条件描述]
否则 判定为 [then_category]
```

示例（R001）：

```
IF   当用户要求"换一批"后，agent 展示的产品列表中至少有一款产品与上一轮推荐列表中的某产品名称和 ID 完全相同
THEN agent 必须确保不存在任何产品在名称和 ID 上与上一轮列表相同，或明确告知用户"已过滤重复产品"
否则 判定为 "重复推荐未去重"
```

比 `CHK001_final == 0` 更适合作为 checker 提示词，同时保留原始 checkpoint ID 作为引用锚点。

#### (c) Skill 归因部分

```
责任归属：
  - 主要：[skill_name] — [problematic_rule]（置信度 X%）
  - 次要：[skill_name] — [problematic_rule]（置信度 X%）
```

### 1.2 规则排序与筛选

#### 排序维度（三维加权）

| 维度 | 数据来源 | 计算方式 |
|---|---|---|
| **合理性** | LLM 二次评估 + confidence | 对每条规则，让 LLM 从"规则逻辑是否自洽、IF 条件是否可判定、THEN 结论是否合理"角度打分 0-1 |
| **重要性** | 规则的 severity + 业务影响面 | phase3 的 severity（高/中/低）映射为权重，结合 skill_attribution 中最高置信度 |
| **出现频率** | phase4 特征矩阵 | `supporting_trajectories` 数量 / 总轨迹数 = 触发率；以及该类别下所有 checkpoint 的 fail_rate 加权 |

**综合得分公式**：

```
score = w1 * 合理性 + w2 * 重要性 + w3 * 出现频率
```

默认权重 `w1=0.4, w2=0.3, w3=0.3`，可通过命令行参数调整。

#### 筛选策略

- `--top-k-rules`：保留前 K 条规则（默认 None = 全部保留）
- `--min-score`：最低分数线（默认 0.3）
- `--drop-bottom`：去掉排名垫底的 N 条（默认 0）
- 被筛掉的规则仍保留在 `rejected_rules` 字段中，不直接删除，方便回溯

#### 通盘分析

- 加载全部轨迹 + skill MD，让 LLM 对每条规则做 **"对抗性审查"**
- 是否存在反例、是否过于宽泛/狭窄、是否存在冗余重叠
- 审查结果作为"合理性"维度的主要来源

### 1.3 Phase6 输入/输出

```
输入：
  - rules.json（Phase5 输出）
  - phase3output.json（类别定义 + severity）
  - input_trace/0611v1/chosen/（全量轨迹，用于频率统计和 LLM 审查）
  - skills_2b_checked/（Skill MD，用于 LLM 审查）

输出：
  - rules_natural_language.txt  — 自然语言规则文本（给 checker 用）
  - rules_ranked.json           — 排序后的规则（含得分、筛选结果）
```

命令行参数：

```
python phase6_rule_transform.py \
  --rules rules.json \
  --categories phase3output.json \
  --trajectories input_trace/0611v1/chosen/ \
  --skills skills_2b_checked \
  --output rules_natural_language.txt \
  --output-json rules_ranked.json \
  --top-k-rules 8 \
  --min-score 0.3 \
  --drop-bottom 0 \
  --weights 0.4,0.3,0.3
```

---

## 二、前置阶段参数调整

### 2.1 Phase2 去重逻辑

**现状**：`load_trajectories()` 函数已有 `dedup` 参数（默认 `True`），命令行已有 `--no-dedup` 参数。

**调整**：**将 `--no-dedup` 的行为改为默认**，即 `dedup` 默认值改为 `False`。

**理由**：
- 单一重复的数据代表场景的高频出现，需要重点关注
- 对于难以复现但很重要的场景，可以有意在一批轨迹数据中增加其数量以体现重要性
- 去重应该是一个显式选择而非默认行为——需要去重时使用 `--dedup` 参数

**改动范围**：
- `phase2_induct_linkage.py`：`load_trajectories()` 的 `dedup` 默认值 `True` → `False`
- `phase2_induct_linkage.py`：命令行参数从 `--no-dedup` 改为 `--dedup`
- 同时调整 prompt，在不去重时提示 LLM 重复轨迹代表场景高频出现

> **注意**：`run_pipeline.py` 中也调用了 `load_trajectories()`，需同步调整。
> `phase4_feature_extractor.py` 和 `phase5_rule_mining.py` 中也有轨迹加载逻辑，需确认是否也受影响。

### 2.2 规则数量控制

**现状问题**：

当前规则数量 = Phase3 输出的 categories 数量（Phase5 是 1 category → 1 rule）。

Phase3 的 prompt 中没有显式约束类别数量，LLM 自由归纳时倾向于合并，导致类别太少。

**调整方案**：

#### (a) Phase3 增加 `--min-categories` 和 `--max-categories` 参数

在 prompt 中加入 `请归纳出 {min}~{max} 个类别` 的约束。

轨迹数量与规则数量的参考关系：

| 轨迹数量 | 建议类别区间 | 建 min | 建max |
|---|---|---|---|
| 10~20 | 3~6 | 3 | 6 |
| 20~50 | 5~10 | 5 | 10 |
| 50~100 | 8~15 | 8 | 15 |
| 100+ | 10~20 | 10 | 20 |

核心原则：类别数 ≈ checkpoint 数 / 2~3（每类 2-3 个检查点），同时每条规则至少有 2 条支撑轨迹。

#### (b) Phase5 增加 `--max-rules` 参数

虽然 Phase5 目前是 1 category → 1 rule，但如果一个类别有多个不同的 fail 模式，可以让 LLM 在同一类别下拆分出多条规则。

#### (c) Prompt 引导增强

- **Phase2 prompt**：增加"如果不同轨迹呈现不同的错误模式，请分别列出独立的检查点，而非过度合并"
- **Phase3 prompt**：增加"请归纳出 N~M 个类别"的约束
- **Phase5 prompt**：增加"如果同一类别下存在明显不同的触发模式，可拆分为多条规则"

---

## 三、更新后的执行命令

```bash
# Phase2: 默认不去重，需要去重时加 --dedup
python phase2_induct_linkage.py --input input_trace/0611v1/chosen/ --batch-size 10 --output phase2output.json

# Phase3: 加入类别数量约束
python phase3_induct_categories.py --phase2-result phase2output.json --trajectories input_trace/0611v1/chosen/ --output phase3output.json --min-categories 5 --max-categories 10

# Phase4: 不变
python phase4_feature_extractor.py --trajectories input_trace/0611v1/chosen/ --categories phase3output.json --output phase4output.json

# Phase5: 可选 --max-rules 控制上界
python phase5_rule_mining.py --features phase4output.json --categories phase3output.json --trajectories input_trace/0611v1/chosen/ --skills skills_2b_checked --output rules.json --top-k-checkpoints 3

# Phase6: 新增
python phase6_rule_transform.py --rules rules.json --categories phase3output.json --trajectories input_trace/0611v1/chosen/ --skills skills_2b_checked --output rules_natural_language.txt --top-k-rules 8
```

---

## 四、改动清单

| 文件 | 改动内容 |
|---|---|
| `phase2_induct_linkage.py` | `dedup` 默认值 `True`→`False`；`--no-dedup`→`--dedup`；prompt 中提示重复轨迹含义 |
| `phase3_induct_categories.py` | 增加 `--min-categories`、`--max-categories` 参数及 prompt 约束；prompt 引导不过度合并 |
| `phase5_rule_mining.py` | 增加 `--max-rules` 参数；prompt 引导同类别可拆分多规则 |
| `phase6_rule_transform.py` | **新建**：规则语言化 + 排序筛选 |
| `执行命令` | 更新为新的命令格式 |
| `run_pipeline.py` | 同步调整 phase2 调用参数 |
