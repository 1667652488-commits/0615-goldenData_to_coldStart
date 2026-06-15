# Goodcase 使用思路

> 日期：2026-06-15
> 背景：当前流水线中，Phase1 标注后的"通过"轨迹（goodcase）在桥接层被直接丢弃，未被任何下游阶段消费。本文档分析 goodcase 可以在哪里发挥价值。

---

## 当前数据流与问题

```
input_trace/0611v1/chosen/ (20条轨迹)
        ↓
Phase1: golden_data_generator.py
        ↓ 标注结果
goldendata/golden_output.jsonl
  ├── 通过:     1条  ← 🚨 在 filter_badcase_traces() 中被 continue 丢弃
  ├── 失败:    11条  → badcase_traces/
  ├── 部分通过:  8条  → badcase_traces/
  └── 合计:    20条, goodcase仅1条(5%)
        ↓
Phase2~6: 仅消费 badcase_traces/ 中的19条
```

**核心问题**：`run_full_pipeline.py` 第75行 `if result == "通过": stats["pass"] += 1; continue` —— goodcase 直接跳过，不写入任何下游目录。

---

## 思路1：Phase 5 Few-Shot 正例来源（最直接、最紧迫）

### 当前痛点

Phase5 的提示词中明确写了：

> positive_examples：从提供的轨迹中找 agent 正确处理该场景的 1-2 个例子（**如果全量都是badcase则可能没有**）

`rules.json` 中的 `positive_examples` 字段当前基本为空。但这恰好是评估器最需要的东西——"什么是对的"。

### 用法

在 `filter_badcase_traces()` 中，不再丢弃通过轨迹，而是同时写入 `goldendata/goodcase_traces/`。Phase5 规则挖掘时，除了传入 badcase 轨迹，还可传入 goodcase 轨迹作为正例候选。LLM 在归纳 few_shots 时就能找到真实的正例。

### 投入产出

改动最小（桥接层+Phase5参数），收益最直接。

---

## 思路2：Phase 4 特征矩阵的"真 1"来源（统计意义最大）

### 当前痛点

Phase4 的 0/1/NA 矩阵中，因为只有 badcase 轨迹，`1`（通过）值极少——只有 NA（不相关）和 `0`（违反）。这导致：
- 规则挖掘的统计基础偏斜：只有 fail 率，缺少 pass 率
- 无法计算真正的精确率（precision）：规则说"CHK004=0 触发"，但不知道 CHK004=1 的情况下是否不触发

### 用法

将 goodcase 轨迹也送入 Phase4 判定。对 goodcase 轨迹，检查点应该判定为 `1`（通过）。如果某条 goodcase 被判定为 `0`，说明检查点定义有问题（假阳性）。这样特征矩阵变成：

```
          CHK001  CHK002  CHK003  ...
bad-9d9b    0       0       1     ← 违反
good-a1b2   1       1       1     ← 通过（新增！）
good-c3d4   1       NA      1     ← 通过（新增！）
```

### 收益

- 规则的 confidence 更可信（能区分"真违反"和"假阳性"）
- Phase6 的"合理性"评分有了真实验证数据
- 可以自动发现检查点定义的误判

### 注意

需要区分 goodcase 和 badcase 在特征矩阵中的来源，否则 Phase5 统计时不能混在一起算 fail 率。

---

## 思路3：Phase 2 链路归纳的对照组（认知提升）

### 当前痛点

Phase2 只看 badcase 归纳"缺失检查点"，缺少"正确做法"的参考。LLM 需要猜"正确链路应该是什么"，没有实际对照。

### 用法

Phase2 的首批归纳中，加入几条 goodcase 轨迹作为"正面参照"。提示词可以改为：

> 以下是N条通过的正确轨迹和M条失败的badcase轨迹，请对比归纳：正确链路是什么？badcase缺失了哪些检查点？

### 收益

- 正确链路的归纳不再凭空想象，而是有实证基础
- 检查点的描述更精准——"goodcase做了X而badcase没做"比"badcase应该做X"更客观
- 与提示词优化原则（系统理解6维）形成互补——goodcase直接提供正面证据

---

## 思路4：规则验证/假阳性检测（质量保障层）

### 当前痛点

产出的5条规则只验证了"在badcase上能触发"，没验证"在goodcase上不误触发"。Phase6 的"合理性"评分纯粹靠LLM主观判断。

### 用法

在 Phase6 或新增一个验证阶段：

1. 把 goodcase 轨迹逐条跑规则判断（IF CHK004=0 THEN 触发）
2. 如果规则对 goodcase 触发了 → 这条规则太宽泛 → 降低合理性分数或标记"需人工复核"
3. 规则的实测精确率 = goodcase 中未触发的比例

### 收益

- 自动化的假阳性检测，不依赖LLM主观判断
- 规则排序更可靠：一条在goodcase上也会触发的规则，综合分应该降级

---

## 思路5：评估器的黄金语料 / 案例元（长期闭环）

### 当前痛点

经验元体系中的"案例元"（黄金语料）缺少标准化来源。目前靠人工精选。

### 用法

Phase1 产出的 goodcase 轨迹，经过格式标准化后，自动成为案例元。具体路径：

1. goodcase + expected_behavior → 案例元（正例标准）
2. badcase + expected_behavior → 案例元（负例标准）
3. 评估器加载规则时，同时加载正/负例案例元作为 Few-Shot 参考

### 收益

- 案例元与规则元同源，一致性有保障
- 迭代时新数据自动补充案例元，不需要人工维护

---

## 优先级对比

| 思路 | 改动范围 | 投入 | 收益 | 优先级 |
|------|----------|------|------|--------|
| 1. Few-Shot正例 | 桥接层+Phase5参数+提示词 | 小 | 直接填空positive_examples | ⭐⭐⭐⭐⭐ |
| 2. 特征矩阵真1 | Phase4+Phase5统计逻辑 | 中 | 统计基础质变 | ⭐⭐⭐⭐ |
| 3. Phase2对照组 | Phase2提示词+输入 | 小 | 归纳质量提升 | ⭐⭐⭐ |
| 4. 假阳性检测 | 新增验证阶段或Phase6扩展 | 中 | 规则质量保障 | ⭐⭐⭐⭐ |
| 5. 黄金语料闭环 | 桥接层+案例元标准化 | 中 | 长期价值 | ⭐⭐⭐ |

## 推荐实施顺序

### 第一步（最小改动，最大收益）

思路1+5 合做——桥接层不再丢弃 goodcase，而是同时写出 `goldendata/goodcase_traces/`，Phase5 可以引用。goodcase 自动成为案例元正例。

### 第二步（统计质变）

思路2——Phase4 传入 goodcase，让特征矩阵包含真实 `1` 值。

### 第三步（质量保障）

思路4——在 Phase6 中增加假阳性检测，用 goodcase 验证规则不误触发。

## 当前数据限制

20条轨迹中只有1条 goodcase（5%），正例样本太少。如果将来 input_trace 中包含未被筛选为 badcase 的全量轨迹（含更多通过轨迹），上述思路的价值会显著提升。
