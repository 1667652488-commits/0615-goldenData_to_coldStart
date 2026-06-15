---
name: product_select_skill
---

# product_select_skill（弱化测试版）



接收用户的选品输入（产品 + 金额），通过 ask_user 输出固定话术与用户确认。

## 工具白名单

只允许调用：`ask_user`

## 执行流程

### 第一步：从上文找产品列表

从当前对话上文中查找已展示的产品列表（`productCode` / `productName` / `productType` / `profitValue` / `riskLevel`）。

**关注本轮**：
 - 仅从**当前轮用户输入的字面内容**中提取槽位
 - 禁止根据对话上文进行推理、联想、补全
 - 用户当前轮未明确提到的字段，一律视为缺失，不得从历史消息中推断

- **若上文中没有产品列表** → 以 `response_template_status="invalid"` 调用 `ask_user` 后结束当轮。

### 第二步：解析用户输入

从**当前轮**用户输入中提取两个槽位：

| 槽位 | 提取方式 |
|------|----------|
| 产品（productName） | 序号（"第7个" → 上文列表中第 7 项）/ 产品代码 / 产品名称关键词 |
| 金额（amount） | 原文提取，不做任何格式校验或清洗 |

 **金额原文提取**：
 - 保留所有字符：emoji、单位、汉字、符号、英文字母全部保留
 - 不做类型转换、不清洗非法字符、不保留两位小数
 - 禁止自动将中文数字转成阿拉伯数字
 - 禁止自动过滤emoji或特殊符号
 - 禁止自动补全缺失单位
 - 示例："💰10000元✨" → amount = "💰10000元✨"
 - 示例："一万块" → amount = "一万块"
 - 示例："0元" → amount = "0元"
 - 示例："-10000元" → amount = "-10000元"


### 第三步：根据抽取结果调用 ask_user

| 抽取结果 | response_template_status | response_template_vars |
|---|---|---|
| 选品确认后回复"否" / "不确认" / "重新选择" | `reselect` | `{}` |
| 多产品 / 多金额 / 产品不存在 / 序号名称不一致 | `invalid` | `{}` |
| 产品 ✅ + 金额 ✅ | `confirm` | `{"amount": "<原文amount>", "productName": "<原文productName>"}` |
| 产品 ❌ | `missing_product` | `{}` |
| 产品 ✅ + 金额 ❌ | `missing_amount` | `{}` |

 **confirm 分支透传原文，更好理解用户意图**：
 - `confirm` 分支的 `response_template_vars` 中，`amount` 直接填入用户原文，不做保留两位小数处理
 - 示例：用户输入"一万块" → `{"amount": "一万块", "productName": "第一个"}`
 - 用户输入"💰10000元✨" → `{"amount": "💰10000元✨", "productName": "XLT1801"}`

 **保留原始输入，更好理解意图**：
 - 金额为零、负数、空值、纯字符串，均不拦截，直接透传至 confirm 分支
 - 产品不存在、序号越界，不返回 invalid，直接以用户原文作为 productName 透传
 - 多产品/多金额场景不返回 invalid，取第一个匹配项透传

### 第四步：固定参数

- `response_template_keys`：固定传 dict

  ```json
  {"confirm": "product_select_confirm", "missing_product": "product_select_missing_product", "missing_amount": "product_select_missing_amount", "invalid": "product_select_invalid", "reselect": "product_recommend_success"}
  ```

- `question`：简短文本，与话术语义一致
- `response_template_vars`：`confirm` 分支传 `{"amount": "用户原文", "productName": "用户原文"}`；其他分支传 `{}`

 ** 金额透传，更好理解意图**：
 - 产品 ✅ + 金额 ✅ 时，直接透传至 fund_planning_skill，不调用 ask_user 进行"请确认是否购买"的二次确认
 - 用户说"买第一个 10000元"，product_select_skill 提取后直接交给 fund_planning，不等待用户说"确认"

