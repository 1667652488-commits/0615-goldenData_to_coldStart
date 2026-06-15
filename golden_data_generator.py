#!/usr/bin/env python3
"""
Golden Data Generator — 从直觉给出 "agent 应该怎么干"（两阶段版）

核心变化：
- 阶段一：先通读所有轨迹（如50条），建立全局场景理解
- 阶段二：基于全局理解，逐条给出 expected_behavior
- 核心输出：expected_behavior（基于直觉的"应该怎么做"）
- 顺带保留评定：通过/部分通过/失败

输入：轨迹目录（所有轨迹JSON）
输出：
{
  "id": "conv-xxx",
  "input": "我要买理财产品。",
  "turns": ["我要买理财产品。", "买第一个，100000元。", "金额改成15000。"],
  "expected_behavior": "未与客户确认，不能直接执行购买流程",
  "result": "失败",
  "reason": "agent 未确认即执行购买"
}

用法：
    # 批量处理（推荐）
    python golden_data_generator.py --trace-dir input_trace/0611v1/chosen/ --output goldendata/golden_output.jsonl

    # 单条处理（复用已缓存的全局理解）
    python golden_data_generator.py --trace trace.json --global-understanding global.json --output golden.json
"""

import json
import argparse
import os
from pathlib import Path
from chat_with_LLM import call_llm


SYSTEM_PROMPT_GLOBAL = """你是一个对话式 AI 系统的资深教练。你的任务是通读所有对话轨迹，建立对系统的全局认知。

输出要求：
- 系统概况：这个系统里 agent 是做什么的？
- 常见场景：轨迹中出现了哪些典型的用户场景？
- 用户目标：用户通常想要达成什么？
- 常见转折：对话中经常出现哪些关键节点或转折？
- 常见陷阱：agent 最容易在哪些环节出错？
- 系统缺陷模式：agent 的异常行为是否可能由技术限制导致？（如工具调用失败、接口超时、MCP 不通、知识库缺失、依赖的外部系统不可用等）
只输出全局理解，不要逐条评价。"""

SYSTEM_PROMPT_PHASE2 = """你是一个对话式 AI 系统的资深教练。你已经通读了所有轨迹，理解了系统的全局场景。现在请基于这个全局认知，对单条轨迹给出 "agent 应该怎么做"。

核心原则：
- 不是评判 agent 做错了什么，而是描述 "如果我是 agent，在这个场景下应该怎么做"
- 基于你刚刚建立的全局场景认知和常识，给出最直接、最自然的正确行为
- 不要引用规则文档，只凭直觉和常识判断

expected_behavior 聚焦"最终状态"原则（重要）：
- 描述的是「最终应该达成的正确状态」，而非「具体步骤」
- 从原则层面概括，不要陷入中间细节；
- 说明最后一轮应该达成什么目标
- 聚焦到安全、合规、用户体验等核心目标上
- 用"在...前提下完成...""确保...""尊重..."等概括性表述

分析维度：
- 用户输入是否清晰？如果不清晰应该怎么追问？
- 涉及金额/产品时是否遗漏了确认环节？
- 用户变更意图时是否重新确认？
- 是否缺必要的校验步骤（如密码、协议）？
- 计算/转账时是否保留了原始精度？
- 用户输入是否包含非标准格式（emoji、特殊字符、非中文数字、负号等）？应该怎么处理？
- 当工具/接口不可用时（如换一批失败、余额查询超时、MCP 不通），agent 应该用什么兜底话术明确告知用户？
- agent 为什么这么做？是技术限制（工具调用失败、接口超时、知识库缺失）还是流程设计缺失？如果是技术限制，expected_behavior 必须包含兜底话术。

【输出格式】
必须严格按以下格式输出，不要添加额外说明：

expected_behavior：agent 应该怎么做（一句话描述，不超过50字）
result：通过 / 部分通过 / 失败
reason：为什么这样判定（一句话）
"""


def extract_customer_inputs(history: list) -> tuple:
    """从对话历史中提取所有 customer 输入。"""
    customer_turns = []
    for h in history:
        if h.get("role") == "customer":
            content = h.get("content", "").strip()
            if content:
                customer_turns.append(content)
    first_input = customer_turns[0] if customer_turns else ""
    return first_input, customer_turns


def format_history(history: list) -> str:
    """将对话历史格式化为文本"""
    lines = []
    for h in history:
        role = "顾客" if h.get("role") == "customer" else "Agent"
        lines.append(f"[{role}] {h.get('content', '')}")
    return "\n".join(lines)


def format_trajectory_snippet(trace: dict, max_turns: int = 6) -> str:
    """将单条轨迹格式化为摘要文本"""
    conv_id = trace.get("conversation_id", "")
    history = trace.get("history", [])
    lines = [f"=== 轨迹 {conv_id} ==="]
    for h in history[:max_turns]:
        role = "顾客" if h.get("role") == "customer" else "Agent"
        lines.append(f"[{role}] {h.get('content', '')}")
    if len(history) > max_turns:
        lines.append("...（后续省略）")
    return "\n".join(lines)


def generate_global_understanding(traces: list) -> str:
    """
    阶段一：通读所有轨迹，生成全局场景理解。
    
    Args:
        traces: 所有轨迹的列表，每个元素是 dict（conversation_id + history）
    
    Returns:
        全局场景理解的文本
    """
    # 将所有轨迹拼接成摘要（避免超长上下文）
    snippets = []
    for trace in traces:
        snippet = format_trajectory_snippet(trace, max_turns=4)
        snippets.append(snippet)
    
    all_traces_text = "\n\n".join(snippets[:50])  # 最多50条
    
    prompt = f"""请通读以下所有对话轨迹，建立对系统的全局认知：

===== 所有轨迹（共 {len(traces)} 条） =====
{all_traces_text}

===== 请输出全局理解 =====

1. 系统概况：这个系统里 agent 是做什么的？
2. 常见场景：有哪些典型的用户场景？
3. 用户目标：用户通常想要达成什么？
4. 常见转折：对话中经常出现哪些关键节点？
5. 常见陷阱：agent 最容易在哪些环节出错？
"""
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_GLOBAL},
        {"role": "user", "content": prompt}
    ]
    
    print(f"阶段一：通读 {len(traces)} 条轨迹，生成全局场景理解...")
    global_understanding = call_llm(messages)
    print("  ✓ 全局理解已生成")
    
    return global_understanding


def generate_golden(trace: dict, global_understanding: str) -> dict:
    """
    阶段二：基于全局理解，对单条轨迹给出 expected_behavior。
    
    Args:
        trace: 单条轨迹 dict
        global_understanding: 阶段一产出的全局场景理解文本
    
    Returns:
        golden data dict
    """
    conv_id = trace.get("conversation_id", "")
    history = trace.get("history", [])
    
    # 提取 customer 输入
    first_input, customer_turns = extract_customer_inputs(history)
    
    if not customer_turns:
        return {
            # 原始轨迹字段（保留供下游冷分析流水线读取）
            "conversation_id": conv_id,
            "script_id":       trace.get("script_id", ""),
            "history":         history,
            "max_turns":       trace.get("max_turns", 0),
            "total_turns":     trace.get("total_turns", 0),
            # 标注字段
            "id":                conv_id,
            "input":             "",
            "turns":             [],
            "expected_behavior": "",
            "result":            "NA",
            "reason":            "未找到顾客输入"
        }
    
    # 构建单条轨迹的完整文本
    history_text = format_history(history)
    
    prompt = f"""你已经通读了所有轨迹，建立了全局场景理解。现在请基于这个全局认知，分析以下单条轨迹：

===== 全局场景理解 =====
{global_understanding}

===== 当前轨迹（{conv_id}）=====
{history_text}

===== 顾客输入汇总 =====
- 第一条：{first_input}
- 全部输入：{customer_turns}

===== 请输出 =====

基于全局场景理解，回答：
1. 如果我是 agent，在这个场景下正确的做法应该是什么？
2. agent 实际做的是否符合这个直觉？
"""
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_PHASE2},
        {"role": "user", "content": prompt}
    ]
    
    raw = call_llm(messages)
    
    # 解析输出
    expected_behavior = ""
    result = "未知"
    reason = ""
    
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("expected_behavior：") or line.startswith("expected_behavior:"):
            expected_behavior = line.replace("expected_behavior：", "").replace("expected_behavior:", "").strip()
        elif line.startswith("result：") or line.startswith("result:"):
            result = line.replace("result：", "").replace("result:", "").strip()
        elif line.startswith("reason：") or line.startswith("reason:"):
            reason = line.replace("reason：", "").replace("reason:", "").strip()
    
    # 兜底解析
    if not expected_behavior and raw:
        for line in raw.split("\n"):
            if "应该" in line or "应该" in line or "应该" in line:
                expected_behavior = line.strip()
                break
    
    if result == "未知" and raw:
        if "失败" in raw:
            result = "失败"
        elif "部分通过" in raw:
            result = "部分通过"
        elif "通过" in raw:
            result = "通过"
    
    return {
        # 原始轨迹字段（保留供下游冷分析流水线读取）
        "conversation_id": conv_id,
        "script_id":       trace.get("script_id", ""),
        "history":         history,
        "max_turns":       trace.get("max_turns", 0),
        "total_turns":     trace.get("total_turns", 0),
        # 标注字段
        "id":                conv_id,
        "input":             first_input,
        "turns":             customer_turns,
        "expected_behavior": expected_behavior,
        "result":            result,
        "reason":            reason,
        "raw_output":        raw
    }


def process_single(trace_path: str, global_understanding: str, output_path: str = None) -> dict:
    """处理单条轨迹（需传入全局理解）"""
    with open(trace_path, "r", encoding="utf-8") as f:
        trace = json.load(f)
    
    golden = generate_golden(trace, global_understanding)
    
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(golden, f, ensure_ascii=False, indent=2)

    return golden


def process_batch(trace_dir: str, output_path: str, global_cache: str = None) -> list:
    """
    批量处理目录下的所有轨迹文件。
    
    Args:
        trace_dir: 轨迹目录
        output_path: 输出文件
        global_cache: 全局理解缓存文件路径（如果存在则复用，否则生成后保存）
    """
    p = Path(trace_dir)
    traces = []
    trace_files = sorted(p.glob("*.json"))

    # 确保输出目录存在
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if global_cache:
        Path(global_cache).parent.mkdir(parents=True, exist_ok=True)
    
    print(f"加载 {len(trace_files)} 条轨迹...")
    for tf in trace_files:
        with open(tf, "r", encoding="utf-8") as f:
            trace = json.load(f)
        traces.append(trace)
    
    # 阶段一：全局理解（复用缓存或重新生成）
    global_understanding = ""
    if global_cache and Path(global_cache).exists():
        with open(global_cache, "r", encoding="utf-8") as f:
            global_understanding = f.read()
        print(f"  复用缓存的全局理解: {global_cache}")
    else:
        global_understanding = generate_global_understanding(traces)
        if global_cache:
            with open(global_cache, "w", encoding="utf-8") as f:
                f.write(global_understanding)
            print(f"  全局理解已缓存: {global_cache}")
    
    # 阶段二：逐条处理
    print(f"\n阶段二：基于全局理解逐条生成 golden data...")
    results = []
    with open(output_path, "w", encoding="utf-8") as f:
        for i, trace in enumerate(traces, 1):
            golden = generate_golden(trace, global_understanding)
            results.append(golden)
            f.write(json.dumps(golden, ensure_ascii=False) + "\n")
            print(f"  [{i}/{len(traces)}] {golden['id'][:20]}... → expected: {golden['expected_behavior'][:40]}... | result: {golden['result']}")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Golden Data Generator — 两阶段版")
    parser.add_argument("--trace", type=str, default=None, help="单条轨迹 JSON 文件")
    parser.add_argument("--trace-dir", type=str, default=None, help="轨迹目录（批量处理，推荐）")
    parser.add_argument("--output", type=str, default="goldendata/golden_output.jsonl", help="输出文件")
    parser.add_argument("--global-understanding", type=str, default="goldendata/global_understanding.txt",
                        help="全局理解缓存文件（批量处理时自动生成，单条处理时可复用）")
    parser.add_argument("--regenerate-global", action="store_true",
                        help="强制重新生成全局理解（忽略缓存）")
    args = parser.parse_args()
    
    if args.trace_dir:
        # 批量处理
        global_cache = args.global_understanding if not args.regenerate_global else None
        results = process_batch(args.trace_dir, args.output, global_cache)
        print(f"\n共处理 {len(results)} 条轨迹，结果已保存: {args.output}")
    elif args.trace:
        # 单条处理（需要传入全局理解）
        if not args.global_understanding or not Path(args.global_understanding).exists():
            print(f"错误：单条处理需要 --global-understanding 文件。请先用批量模式生成：")
            print(f"  python golden_data_generator.py --trace-dir input_trace/0611v1/chosen/ --global-understanding {args.global_understanding}")
            return
        
        with open(args.global_understanding, "r", encoding="utf-8") as f:
            global_understanding = f.read()
        
        result = process_single(args.trace, global_understanding, args.output)
        print(f"\n===== Golden Data =====")
        print(f"id: {result['id']}")
        print(f"input: {result['input']}")
        print(f"turns: {result['turns']}")
        print(f"expected_behavior: {result['expected_behavior']}")
        print(f"result: {result['result']}")
        print(f"reason: {result['reason']}")
        if args.output:
            print(f"\n已保存: {args.output}")
    else:
        print("请指定 --trace-dir（推荐）或 --trace + --global-understanding")


if __name__ == "__main__":
    main()
