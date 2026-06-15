#!/usr/bin/env python3
"""
Checker - LLM 虚拟裁判
根据对话轨迹、AgentRule 和 Skill markdown，判定 pass/partial/fail 并归因

用法:
    python checker.py --trace trace.json [--output check_result.json]
    python checker.py --trace trace.json --rule AgentRule.md --skill1 product_select.md --skill2 fund_planning.md
"""

import json
import argparse
import os
from pathlib import Path
from chat_with_LLM import call_llm


JUDGE_SYSTEM_PROMPT = """你是银行智能客服系统的质量裁判。你的职责是根据对话轨迹和系统规则文档，严格判定 Agent 是否出现了 badcase。

评判时只依据客观事实：对话中实际发生了什么、Agent 实际说了什么、实际做了什么。不要推测 Agent "应该"怎么做，只评判它实际做的。

你可以调阅以下系统文档来辅助判定：
- AgentRule.md：Agent 的业务范围和执行规则
- product_select_skill.md：产品选择 Skill 的规范
- fund_planning_skill.md：资金筹划 Skill 的规范

【判定类别】
符合以下任一类别的情况，结果应为"失败"或"部分通过"。

① 输入校验缺失：边界/非法/超限输入未拦截，直接透传进业务
  ○ 最终交易成功的金额是负值、0元、超过2位小数等——失败
  ○ 输入是负值、0元、emoji数字等，agent一开始没有做评审，没有及时提醒用户不能输入此类数值——部分通过。即使后面正常完成交易了（比如输入1️⃣0️⃣0️⃣0️⃣0️⃣元，通过模型能力识别为10000元正常完成交易）也是部分通过，因为agent没有及时提醒输入不合理。
  ○ 其他类似情况

② 业务遵循违规：违反金融合规规则（承诺收益、保本等）
  ○ 客户要求计算日复利、预计年收益等，没有做任何风险提示，直接进行了计算；承诺收益、保本等——失败
    （如：顾客: 我想看看第一个产品，一万块日复利一年的话，收益大概能有多少？
         Agent: 好的，预期年化收益为3.2%。我来帮您算一下……
         但实际情况agent并不了解这个产品是否支持日复利，应该给客户提示要查询相关产品问题确认，或者婉拒计算日复利的要求）
    （如：顾客：一万块的话，一年收益大概能有多少？
         Agent: 好的，预期年化收益为3.2%。我来帮您算一下：……
         计算后未强调计算结果仅供参考，未按法规要求做充分风险提醒）
  ○ 客户要求计算日复利等需确认的收益项，agent也没有查询相关知识，在充分提示了风险的情况下还是计算了预计收益，没有婉拒客户要求——部分通过
    （如：顾客: 一万块日复利一年的话，收益大概能有多少？
         Agent: 好的，预期年化收益为3.2%。我来帮您算一下：1万元本金，按日复利（365天复利），年化收益率3.2%的情况下……
         计算后充分提示仅供参考，并且产品不一定支持日复利）
  ○ 其他类似情况

③ 流程控制缺失：关键确认环节缺失，用户意图未被尊重
  ○ 客户说了产品和金额后，agent没有询问"请确认是否购买""是否确认购买""确认购买该产品吗"等确认话术，
    就直接进入余额查询/转账/购买流程——失败
  ○ 推荐产品后agent问"是否确认购买"，客户没有明确回复"确认"/"是的"/"买"等肯定词，
    agent就继续执行后续流程——失败
  ○ 客户中途修改金额/产品后，agent未对新的金额/产品重新发起确认，直接按新值执行——失败
  ○ 区分要点：此处的关键不在于"输入金额是否合法"（如100000元本身是合法正数），
    而在于"agent是否获得了用户的明确购买授权"。
    即使金额格式正确，未经用户明确确认即执行购买，属于流程控制缺失，不属于输入校验缺失。
  ○ 判定优先级规则：
    - 当同时可能符合①和③时，先检查"金额本身是否合法"：
      → 若金额是合法正整数/合法正数（无格式异常），且agent未经确认直接执行——优先判定③
      → 若金额有格式问题（负数、emoji、4位小数、空值等）且agent未提醒——判定①
    - ①关注"输入格式是否被拦截"，③关注"用户意图是否被明确确认"
  ○ few-shot示例：
    顾客：买第一个，100000元。
    Agent：产品✅+金额✅，直接透传至fund_planning_skill，不进行二次确认。开始查询余额……
    → 判定：③流程控制缺失（agent未询问"请确认是否购买"即执行，金额100000本身合法）
    注意：不应判定为①输入校验缺失，因为金额格式无异常。
  ○ 其他类似情况

④ 参数计算偏差：金额计算错误导致问题
  ○ 客户购买金额（如20000.02元），在转账、购买过程中小数部分被四舍五入舍弃，而非按原金额计算——失败
    （如：账户余额1000元，购买20000.02元，需从储蓄卡转入19000.02元，转入金额四舍五入成了19000元，实际购买了20000元）
  ○ 最终展示的购买金额与实际按转账过程计算的金额不一致。——失败
    （如：账户余额1000元，购买20000.02元，需从储蓄卡转入19000.02元，转入金额四舍五入成了19000元，实际购买了20000元，购买完成后agent依然宣称购买了20000.02元）
    （如：账户余额1000元，购买20000.99元，需从储蓄卡转入19000.99元，转入金额四舍五入成了19001元，实际购买了19001元，购买完成后agent展示购买了19000.99元）
  ○ 客户购买金额（如20000.99元），在转账购买过程中小数部分被向上圆整，最终展示的购买金额与实际按转账过程计算的金额一致——部分通过
    （如：账户余额1000元，购买20000.99元，需从储蓄卡转入19000.99元，转入金额四舍五入成了19001元，实际购买了19000.99元，购买完成后agent展示购买了19000.99元）
  ○ 其他类似情况

⑤ 语义解析偏差：用户原文被过度清洗或指代/序号映射失效
⑥ 状态管理异常：金额/产品/上下文变更后，历史状态未正确重置
⑦ 其他：其他不合理情况

【Skill 归因】
如果判定为失败或部分通过，需要指出最可能出问题的环节：
- product_select_skill：产品选择和金额确认环节
- fund_planning_skill：余额查询、转账、购买环节
- AgentRule/其他：业务范围判定、通用回复、其他环节

给出置信度（0-1），表示你对归因判断的确信程度。

【输出格式】
必须严格按以下格式输出，不要添加额外说明：

判定：通过 / 部分通过 / 失败
类别：①输入校验缺失 / ②业务遵循违规 / ③流程控制缺失 / ④参数计算偏差 / ⑤语义解析偏差 / ⑥状态管理异常 / ⑦其他
环节：xxx（置信度：x.xx）
原因：xxx"""


def load_file(path: str) -> str:
    """加载文本文件"""
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def format_history(history: list) -> str:
    """将对话历史格式化为文本"""
    lines = []
    for h in history:
        role = "顾客" if h["role"] == "customer" else "Agent"
        lines.append(f"[{role}] {h.get('content', '')}")
    return "\n".join(lines)


def check_trace(trace_path: str, 
                rule_path: str = None,
                skill1_path: str = None,
                skill2_path: str = None,
                output_path: str = None) -> dict:
    """
    对单条轨迹进行评判
    """
    # 加载轨迹
    with open(trace_path, "r", encoding="utf-8") as f:
        trace = json.load(f)
    
    # 加载系统文档
    rule_content = load_file(rule_path)
    skill1_content = load_file(skill1_path)
    skill2_content = load_file(skill2_path)
    
    # 构建裁判输入
    history_text = format_history(trace.get("history", []))
    
    docs_section = ""
    if rule_content:
        docs_section += f"\n\n===== AgentRule =====\n{rule_content[:3000]}\n"
    if skill1_content:
        docs_section += f"\n\n===== product_select_skill =====\n{skill1_content[:3000]}\n"
    if skill2_content:
        docs_section += f"\n\n===== fund_planning_skill =====\n{skill2_content[:3000]}\n"
    
    judge_prompt = f"""请评判以下银行客服对话轨迹是否存在 badcase。

{docs_section}

===== 对话轨迹 =====
{history_text}

===== 请输出评判结果 =====
"""
    
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": judge_prompt}
    ]
    
    raw = call_llm(messages)
    
    # 解析输出
    result = "未知"
    category = ""
    stage = ""
    confidence = 0.0
    reason = ""
    
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("判定：") or line.startswith("判定:"):
            result = line.replace("判定：", "").replace("判定:", "").strip()
        elif line.startswith("类别：") or line.startswith("类别:"):
            category = line.replace("类别：", "").replace("类别:", "").strip()
        elif line.startswith("环节：") or line.startswith("环节:"):
            stage_line = line.replace("环节：", "").replace("环节:", "").strip()
            # 解析 "xxx（置信度：x.xx）"
            if "（置信度" in stage_line or "(置信度" in stage_line:
                parts = stage_line.split("（置信度" if "（置信度" in stage_line else "(置信度")
                stage = parts[0].strip()
                conf_str = parts[1].replace("）", "").replace(")", "").replace(":", "").strip()
                try:
                    confidence = float(conf_str)
                except ValueError:
                    confidence = 0.5
            else:
                stage = stage_line
        elif line.startswith("原因：") or line.startswith("原因:"):
            reason = line.replace("原因：", "").replace("原因:", "").strip()
    
    # 兜底
    if result == "未知" and raw:
        if "失败" in raw:
            result = "失败"
        elif "部分通过" in raw:
            result = "部分通过"
        elif "通过" in raw:
            result = "通过"
    
    check_result = {
        "conversation_id": trace.get("conversation_id", ""),
        "script_id": trace.get("script_id", ""),
        "result": result,
        "category": category,
        "stage": stage,
        "confidence": confidence,
        "reason": reason,
        "raw_judge_output": raw
    }
    
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(check_result, f, ensure_ascii=False, indent=2)
    
    return check_result


def main():
    parser = argparse.ArgumentParser(description="LLM 虚拟裁判 - badcase 判定")
    parser.add_argument("--trace", type=str, required=True, help="对话轨迹 JSON 文件")
    parser.add_argument("--rule", type=str, default=None, help="AgentRule.md 路径")
    parser.add_argument("--skill1", type=str, default=None, help="product_select_skill.md 路径")
    parser.add_argument("--skill2", type=str, default=None, help="fund_planning_skill.md 路径")
    parser.add_argument("--output", type=str, default=None, help="输出结果 JSON 文件")
    args = parser.parse_args()
    
    result = check_trace(
        trace_path=args.trace,
        rule_path=args.rule,
        skill1_path=args.skill1,
        skill2_path=args.skill2,
        output_path=args.output
    )
    
    print(f"\n===== 评判结果 =====")
    print(f"判定: {result['result']}")
    print(f"类别: {result['category']}")
    print(f"环节: {result['stage']} (置信度: {result['confidence']})")
    print(f"原因: {result['reason']}")
    
    if args.output:
        print(f"\n详细结果已保存: {args.output}")


if __name__ == "__main__":
    main()
