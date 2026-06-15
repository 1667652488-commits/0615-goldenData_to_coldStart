# Badcase 自动诊断系统 — 完整流程设计

## 一、全局架构

```
输入: 剧本 JSON → 对话轨迹 → 清洗 → 归纳链路 → 拆检查点 → 特征提取 → 冷标注 → 规则学习 → 规则引擎 → 输出
                                                              ↑                          ↓
                                                              └───── 新轨迹反馈迭代 ───────┘
```

---

## 二、七阶段详细流程

### 阶段1：数据准备（轨迹生成）

**输入**：`badcase_scripts.json`（剧本定义）
**动作**：`auto_dialogue.py` 调用 LLM 虚拟顾客与 Agent 对话
**输出**：`trajectories.jsonl`（仅含 `conversation_id` + `history`）

**关键约束**：
- 轨迹中去除一切剧本信息（script_id、category、strategy、expected_badcase）
- 去除 LLM 虚拟顾客的评判字段（result、reason、evaluation）
- 仅保留对话文本：顾客说了什么、Agent 回复了什么

**数量目标**：50-200 条（覆盖通过/失败/部分通过混合）

**优化点**：
- 若用 `script_generator.py` 批量生成剧本，先跑 embedding 相似度去重，避免语义等价变体重复
- 确保冷启动数据覆盖不同业务环节（推荐、选品、确认、资金筹划、转账、购买）

---

### 阶段2：LLM 归纳业务链路

**输入**：20-30 条混合轨迹（通过 + 失败 + 部分通过）
**动作**：LLM 虚拟分析师通读全部轨迹，归纳业务链路
**输出**：结构化业务链路（步骤名 → 动作 → 用户输入 → 下一步）

**Prompt 核心**：
```
请分析以下银行客服对话轨迹，归纳 Agent 从"用户开口"到"交易结束"的完整业务链路。
要求：
1. 列出每个步骤的名称和动作
2. 标注每个步骤用户可能的输入
3. 标注步骤之间的触发条件（什么条件下进入下一步）
4. 不要遗漏任何环节（包括推荐、选品、确认、余额查询、资金筹划、转账、购买）
5. 输出结构化格式
```

**优化点：迭代化**
- 第一轮 LLM 归纳 → 人工 review 补漏（如补充"风险揭示""身份核验"等合规环节）
- 第二轮 LLM 细化 → 确认链路完整性
- 迭代直到链路覆盖全部已知业务场景

**论文支撑**：AutoSEP（迭代自监督优化描述）+ Process Mining（流程挖掘）

---

### 阶段3：LLM 归纳 badcase 类别 + 拆解检查点

**输入**：阶段2 的业务链路 + 20-30 条失败/部分通过轨迹（冷数据）
**动作**：LLM 自由归纳失败模式，再逐类别拆解可判定的检查点
**输出**：badcase 类别定义（LLM 自由归纳命名） + 每个类别下的检查点清单

**关键原则**：**不预设类别**，让 LLM 从实际 badcase 中自主发现共性。

**步骤3a：LLM 归纳类别（不给预设类别）**

Prompt：
```
给你以下 N 条失败/部分通过的对话轨迹，请分析这些轨迹中 agent 为什么做错了。

要求：
1. 找出所有失败案例中的共同模式，归纳成 3-7 个类别
2. 每个类别给一个名字（用简洁的中文命名，如"关键确认缺失""金额计算偏差"）
3. 每个类别写一段定义：什么现象属于这个类别，什么不属于
4. 给出每个类别的典型例子（从轨迹中直接引用）
5. 不要预设任何类别，完全从数据出发
6. 如果某个案例同时符合多个类别，标注出来

输出格式：
类别1：名称
定义：...
典型例子：
- 轨迹 conv-001：...
- 轨迹 conv-003：...

类别2：名称
...
```

**步骤3b：人工 review（轻量）**

- 检查 LLM 归纳的类别是否有重叠（如两个类别本质上是一件事）
- 检查是否有遗漏的类别（如合规环节 LLM 可能没发现）
- 合并相近类别，补充遗漏类别
- 最终定稿：3-7 个类别，每个类别有清晰边界

**步骤3c：LLM 拆解检查点**

类别定稿后，再逐类别拆解可判定的检查点：

Prompt：
```
基于以下类别定义，请为每个类别设计可判定的检查点：

要求：
1. 检查点必须是客观的（能从对话文本中明确判断是/否）
2. 检查点必须是二值的（0/1）
3. 检查点应覆盖该类别的主要判定依据
4. 避免主观评价（如"agent回答好不好"），只关注客观行为

输出格式：
类别：XXX
检查点：
- 检查点1：名称（如 has_confirmation）
  - 判定方式：...
  - 提取逻辑：正则/关键词/LLM辅助
- 检查点2：名称（如 amount_rounded）
  - 判定方式：...
  - 提取逻辑：...
```

**示例输出（LLM 可能归纳出的类别）**：

```json
{
  "categories": [
    {
      "id": "CAT001",
      "name": "关键确认缺失",
      "definition": "用户提供了产品和金额，但agent未询问用户是否确认购买，直接进入执行流程。核心：用户意图未被明确授权。",
      "examples": ["conv-001: 顾客说买第一个100000元，agent直接查余额"]
    },
    {
      "id": "CAT002",
      "name": "金额计算偏差",
      "definition": "转账金额与精确缺口不一致，导致到账金额不足或多余。",
      "examples": ["conv-005: 缺口200.04，转账200，差0.04"]
    },
    {
      "id": "CAT003",
      "name": "输入未校验",
      "definition": "用户输入了异常格式（负数、emoji、空值），agent未指出异常直接继续。",
      "examples": ["conv-002: 顾客输入-10000元，agent未拒绝"]
    }
  ]
}
```

**论文支撑**：Snorkel（Labeling Functions = 人为定义特征）

---

### 阶段4：特征提取

**输入**：全部轨迹 + 阶段3 的检查点清单
**动作**：按检查点定义从轨迹中提取 0/1 特征值
**输出**：特征表（每条轨迹 × 每个检查点 = 0/1 矩阵）

**三级提取策略**：

| 级别 | 方法 | 特征示例 | 确定性 | 成本 |
|------|------|---------|--------|------|
| P0 | 正则/关键词 | has_confirmation、is_negative、is_zero、金额提取 | 100% | 零 |
| P1 | 跨轮状态对比 | product_changed、amount_rounded、skipped_confirmation | 高 | 低 |
| P2 | LLM 辅助语义 | agent_婉拒、agent_误导、是否主动风险提示 | 中 | 高 |

**先跑 P0+P1，再按需引入 P2。**

**示例特征表**：

| trajectory_id | has_confirmation | amount_rounded | is_negative | gap_exact | product_mismatch |
|--------------|------------------|----------------|-------------|-----------|-----------------|
| conv-001 | 0 | 1 | 0 | 0 | 0 |
| conv-002 | 1 | 0 | 0 | 1 | 0 |
| conv-003 | 0 | 0 | 1 | 1 | 0 |

---

### 阶段5：LLM 冷标注（多信号交叉验证）

**输入**：轨迹 + 阶段2 的类别定义 + 阶段3 的检查点清单
**动作**：多个 checker prompt 版本并行标注，融合结果
**输出**：`labels_train.jsonl` + `labels_val.jsonl`

**多版本 checker 设计**：

| 版本 | 风格 | 用途 |
|------|------|------|
| 版本A：严格版 | 容易判 fail | 召回最大化（不漏 badcase） |
| 版本B：宽松版 | 容易判 pass | 精确最大化（不误杀） |
| 版本C：平衡版 | 标准 | 最终输出 |

**融合策略**：
- 三版结果一致 → 直接采用
- 两版一致、一版不同 → 采用多数意见
- 三版全不同 → 人工 review 或标记为"待复核"

**输出格式**：
```json
{
  "conversation_id": "conv-001",
  "result": "失败",
  "category": "③流程控制缺失",
  "stage": "product_select_skill",
  "confidence": 0.90,
  "reason": "agent未询问'请确认是否购买'即进入余额查询"
}
```

**论文支撑**：Brown 2020（Few-shot）+ Liu 2022（最优 in-context 示例）+ Snorkel（多弱信号融合）

---

### 阶段6：规则学习（特征选择 + 规则提取）

**输入**：特征表 + 标注结果（仅失败/部分通过样本）
**动作**：先筛选特征 → 再学习规则
**输出**：`rules.json`（规则引擎）

**步骤6a：特征筛选（互信息）**

```python
# 计算每个特征与 result 的互信息
from sklearn.feature_selection import mutual_info_classif

mi_scores = mutual_info_classif(X_features, y_result)
# 保留 MI > 0.1 的特征，删除弱预测特征
```

**步骤6b：规则学习**

| 算法 | 适用场景 | 论文 |
|------|---------|------|
| CORELS | 小样本（<1000条），追求证书级最优 | Angelino 2017 |
| BRL | 中等样本，贝叶斯框架平衡精度与简洁 | Letham 2015 |
| RIPPER | 大规模样本，快速归纳 | Cohen 1995 |

**推荐**：当前 50-200 条阶段用 CORELS 或 BRL。

**输出规则格式**：
```json
{
  "id": "R001",
  "name": "未确认直接执行购买",
  "category": "③流程控制缺失",
  "description": "用户明确提供了产品和金额，agent未询问'请确认是否购买'等确认话术，直接进入余额查询/转账/购买流程",
  "if": {
    "conditions": [
      {"feature": "has_customer_amount", "op": "==", "value": 1},
      {"feature": "has_customer_product", "op": "==", "value": 1},
      {"feature": "has_confirmation", "op": "==", "value": 0},
      {"feature": "agent_executed_purchase", "op": "==", "value": 1}
    ],
    "logic": "AND"
  },
  "then": {
    "result": "失败",
    "category": "③流程控制缺失",
    "skill": "product_select_skill",
    "confidence": 0.90
  },
  "few_shots": {
    "positive": ["顾客：买第一个，100000元。Agent：直接透传至fund_planning，不二次确认..."],
    "negative": ["顾客：买第一个，100000元。Agent：请确认是否购买？顾客：确认..."]
  },
  "evidence_chain": "因为用户给了金额(1)和产品(1)，但agent未确认(0)，且执行了购买(1)，触发规则R001"
}
```

---

### 阶段7：运行时（规则引擎 + LLM Fallback）

**输入**：新轨迹
**动作**：特征提取 → 规则匹配 → 输出结果
**输出**：结构化判定结果

**流程**：
```
新轨迹输入
  ↓
[特征提取] P0/P1/P2 提取全部特征值
  ↓
[规则引擎] 遍历 rules.json，匹配 if-else 条件
  ↓
  ├─ 单条命中 → 输出结果 + 证据链 + 置信度
  ├─ 多条命中 → 优先级排序（按严重程度：④>③>①）或取 confidence 最高
  └─ 无命中 → 触发 LLM 备用裁判
  ↓
[输出]
```

**LLM 备用裁判触发条件**：
- 规则引擎无命中
- 最高置信度 < 0.7
- 多条规则冲突且优先级无法解决

**备用裁判输出**：同阶段5格式（result + category + skill + reason）

**优化点：规则冲突处理**
- 同时命中 R001（未确认）和 R003（负金额）→ 按严重程度排序：④计算偏差 > ③流程缺失 > ①输入校验
- 输出附带"证据链"：因为 feature_X=1 且 feature_Y=0，触发规则 R001

---

## 三、新增阶段8：验证与人工审核

**规则学习完成后不能自动上线，必须经过：**

1. **验证集测试**：在 40% 验证集上跑规则引擎，计算：
   - 准确率 = (正确判定数) / 总数
   - 召回率 = (被规则捕获的真实 badcase) / 全部 badcase
   - F1 = 2 * 精确率 * 召回率 / (精确率 + 召回率)

2. **人工审核**：
   - 逐条 review 每条规则的 few-shots，看是否符合业务常识
   - 特别关注 skill 归因（product_select vs fund_planning）是否正确
   - 检查规则边界（是否过于严格/宽松）

3. **A/B 测试**：
   - 旧规则 vs 新规则并行运行 50 条轨迹
   - 准确率下降则回滚旧版本

---

## 四、数据飞轮（持续迭代）

运行时产生的新 badcase 不是只存起来，而是自动触发增量更新：

```
新 badcase 积累
  ↓
每满 50 条 → 增量特征分布更新 → 检查是否有新特征模式
  ↓
每满 200 条 → 触发阶段6重新学习 → 更新 rules.json
  ↓
新旧规则 A/B 测试 → 准确率下降则回滚
  ↓
通过测试 → 新规则上线
```

---

## 五、核心交付物清单

| 交付物 | 格式 | 阶段 | 说明 |
|-------|------|------|------|
| `trajectories.jsonl` | JSONL | 1 | 清洗后轨迹（仅 conversation_id + history） |
| `business_linkage.json` | JSON | 2 | LLM 归纳的业务链路 |
| `checkpoints.json` | JSON | 3 | 检查点清单（含提取逻辑） |
| `features.jsonl` | JSONL | 4 | 结构化特征表（轨迹 × 检查点） |
| `labels_train.jsonl` | JSONL | 5 | 训练集标注（多版本融合） |
| `labels_val.jsonl` | JSONL | 5 | 验证集标注 |
| `rules.json` | JSON | 6 | 规则引擎（含 IF-THEN + few-shots + skill 归因） |
| `rule_engine.py` | Python | 7 | 规则运行时 |
| `llm_judge.py` | Python | 7 | LLM 备用裁判 |
| `evaluate.py` | Python | 8 | 验证集评估脚本 |
| `feature_extractor.py` | Python | 4 | 特征提取器（P0/P1/P2） |
| `auto_dialogue.py` | Python | 1 | 虚拟顾客对话生成 |
| `script_generator.py` | Python | 1 | 剧本批量生成 |
| `checker.py` | Python | 5 | 多版本 LLM 标注器 |

---

## 六、论文清单

| 论文 | 作者 | 年份 | 支撑阶段 | 核心贡献 |
|------|------|------|---------|---------|
| AutoSEP | Hong et al. | 2025 | 2 | 无标注数据迭代自监督优化描述 |
| Process Mining | van der Aalst | 2011 | 2 | 从事件日志自动发现流程模型 |
| Snorkel | Ratner et al. | 2018 | 3/5 | 弱监督框架，多标注函数融合 |
| LLM Few-Shot | Brown et al. | 2020 | 5 | In-context learning 原理 |
| What makes good in-context examples | Liu et al. | 2022 | 5 | 最优 few-shot 示例选择策略 |
| BRL | Letham et al. | 2015 | 6 | 贝叶斯规则列表，平衡精度与简洁 |
| CORELS | Angelino et al. | 2017 | 6 | 证书级最优规则列表学习 |
| RIPPER | Cohen | 1995 | 6 | 经典规则归纳，快速覆盖算法 |
| Interpretable Decision Sets | Lakkaraju et al. | 2016 | 6 | 规则集合，优化描述性与预测性 |
| BERTopic | Grootendorst | 2022 | 8/飞轮 | 无监督文本主题发现（后续大规模阶段） |
| Anchors | Ribeiro et al. | 2018 | 8/飞轮 | 黑盒模型局部解释（后续阶段） |

---

## 七、当前阶段建议（50-200条）

**现在该做的**：
1. ✅ 阶段1：跑 auto_dialogue 生成 50-100 条轨迹
2. ✅ 阶段2-3：LLM 归纳链路 + 拆检查点（1-2轮迭代）
3. ✅ 阶段4：实现 P0 正则特征提取（has_confirmation、金额、关键词）
4. ✅ 阶段5：跑 checker 多版本标注，做交叉验证

**可以放 backlog 的**：
- 阶段6 规则学习（需要 100+ 条才能稳定）
- 阶段8 验证脚本（先手工 review 几条规则）
- 数据飞轮（等规则引擎上线后再建）
- BERTopic/Anchors（500+ 条后再评估）

**下一步阻塞点**：你现在有多少条轨迹了？如果够 50 条，可以直接启动阶段2-3 的 LLM 归纳。