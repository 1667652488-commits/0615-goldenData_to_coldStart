#!/usr/bin/env python3
"""
完整流水线：黄金数据标注 → 冷数据规则提取

串联 Phase1（golden_data_generator 标注）→ 桥接（筛选 badcase）→ Phase2~6（规则挖掘）

用法：
    # 全量运行（从无标注轨迹开始）
    python run_full_pipeline.py \
        --input-trace input_trace/0611v1/chosen/ \
        --skills skills_2b_checked/ \
        --output rules.json

    # 跳过 Phase1，复用已有的 golden_output.jsonl
    python run_full_pipeline.py \
        --skip-golden \
        --golden-output golden_output.jsonl \
        --skills skills_2b_checked/ \
        --output rules.json

    # 从 cold pipeline 某阶段开始续跑
    python run_full_pipeline.py \
        --skip-golden \
        --golden-output golden_output.jsonl \
        --skills skills_2b_checked/ \
        --start-from 4 \
        --output rules.json
"""

import json
import subprocess
import sys
import argparse
from pathlib import Path
from datetime import datetime


# ==================== 桥接：筛选 badcase 轨迹 ====================

def filter_badcase_traces(golden_output_path: str, badcase_dir: str) -> dict:
    """
    从 golden_data_generator 输出的 JSONL 中筛选 badcase 轨迹，
    每条写成独立 .json 文件，供下游 cold pipeline 读取。

    Args:
        golden_output_path: golden_data_generator 的 JSONL 输出文件
        badcase_dir:        筛选后的 badcase 轨迹输出目录

    Returns:
        统计信息 dict {total, pass, fail, partial, unknown}
    """
    out_dir = Path(badcase_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {"total": 0, "pass": 0, "fail": 0, "partial": 0, "unknown": 0}

    # 先清理旧文件，避免残留
    for old_file in out_dir.glob("*.json"):
        old_file.unlink()

    with open(golden_output_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  ⚠ 第 {line_no} 行 JSON 解析失败，跳过: {e}")
                continue

            stats["total"] += 1
            result = record.get("result", "未知")

            if result == "通过":
                stats["pass"] += 1
                continue
            elif result == "失败":
                stats["fail"] += 1
            elif result == "部分通过":
                stats["partial"] += 1
            else:
                stats["unknown"] += 1
                # 未知结果也保留（保守策略，避免遗漏）
                print(f"  ⚠ 轨迹 {record.get('id', '?')} result=\"{result}\"，仍保留至 badcase")

            # 写入独立 .json 文件
            conv_id = record.get("conversation_id", record.get("id", f"trace_{line_no}"))
            # 文件名安全处理
            safe_name = conv_id.replace("/", "_").replace("\\", "_")
            out_file = out_dir / f"{safe_name}.json"
            with open(out_file, "w", encoding="utf-8") as of:
                json.dump(record, of, ensure_ascii=False, indent=2)

    return stats


# ==================== 阶段执行 ====================

def run_phase(cmd: list, phase_name: str) -> bool:
    """运行一个阶段，实时输出日志，返回是否成功"""
    print(f"\n{'='*70}")
    print(f"  {phase_name}")
    print(f"{'='*70}")
    print(f"  命令: {' '.join(cmd)}\n")

    result = subprocess.run(
        [sys.executable] + cmd,
        cwd=str(Path(__file__).parent),
    )

    if result.returncode != 0:
        print(f"\n✗ {phase_name} 失败 (exit code {result.returncode})")
        return False

    print(f"\n✓ {phase_name} 完成")
    return True


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(
        description="完整流水线：黄金数据标注 → 冷数据规则提取"
    )

    # ---- Phase1 参数 ----
    parser.add_argument(
        "--input-trace", type=str, default=None,
        help="无标注轨迹目录或文件（Phase1 输入）"
    )
    parser.add_argument(
        "--skip-golden", action="store_true",
        help="跳过 Phase1（黄金数据标注），直接用已有的 golden-output"
    )
    parser.add_argument(
        "--golden-output", type=str, default="goldendata/golden_output.jsonl",
        help="Phase1 标注结果输出文件（默认 goldendata/golden_output.jsonl）"
    )
    parser.add_argument(
        "--global-understanding", type=str, default="goldendata/global_understanding.txt",
        help="全局理解缓存文件（默认 goldendata/global_understanding.txt）"
    )
    parser.add_argument(
        "--regenerate-global", action="store_true",
        help="强制重新生成全局理解（忽略缓存）"
    )

    # ---- 桥接参数 ----
    parser.add_argument(
        "--badcase-dir", type=str, default="badcase_traces",
        help="筛选后的 badcase 轨迹目录（默认 badcase_traces/）"
    )

    # ---- Cold Pipeline 参数 ----
    parser.add_argument(
        "--skills", type=str, required=True,
        help="Skill markdown 文件或目录"
    )
    parser.add_argument(
        "--output", type=str, default="rules.json",
        help="最终规则输出文件（默认 rules.json）"
    )
    parser.add_argument(
        "--intermediate-dir", type=str, default="intermediate",
        help="中间结果目录（默认 intermediate/）"
    )
    parser.add_argument(
        "--start-from", type=int, default=2, choices=[2, 3, 4, 5, 6],
        help="cold pipeline 从哪个阶段开始（默认 2，仅当 --skip-golden 时可用）"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10,
        help="Phase2/3/4 每批处理数（默认 10）"
    )
    parser.add_argument(
        "--max-turns", type=int, default=10,
        help="Phase4 每条轨迹提取的最大轮数（默认 10）"
    )
    parser.add_argument(
        "--no-batch", action="store_true",
        help="Phase4 禁用批量判定模式"
    )
    parser.add_argument(
        "--min-categories", type=int, default=0,
        help="Phase3 最少归纳类别数（0=不限制）"
    )
    parser.add_argument(
        "--max-categories", type=int, default=0,
        help="Phase3 最多归纳类别数（0=不限制）"
    )
    parser.add_argument(
        "--max-rules", type=int, default=0,
        help="Phase5 最大规则数上限（0=不限制）"
    )
    parser.add_argument(
        "--top-k-rules", type=int, default=None,
        help="Phase6 保留前 K 条规则（默认 None=全部保留）"
    )
    parser.add_argument(
        "--min-score", type=float, default=0.0,
        help="Phase6 最低综合得分线（默认 0.0）"
    )
    args = parser.parse_args()

    # ---- 参数校验 ----
    if not args.skip_golden and not args.input_trace:
        parser.error("必须指定 --input-trace 或使用 --skip-golden")

    if args.skip_golden and not Path(args.golden_output).exists():
        parser.error(f"--skip-golden 但找不到 golden-output: {args.golden_output}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    inter_dir = Path(args.intermediate_dir)
    inter_dir.mkdir(parents=True, exist_ok=True)

    # ═══════════════ Phase 1: 黄金数据标注 ═══════════════
    if not args.skip_golden:
        golden_output_path = args.golden_output if Path(args.golden_output).is_absolute() else args.golden_output
        Path(golden_output_path).parent.mkdir(parents=True, exist_ok=True)
        global_cache_path = args.global_understanding if Path(args.global_understanding).is_absolute() else args.global_understanding
        Path(global_cache_path).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "golden_data_generator.py",
            "--trace-dir", args.input_trace,
            "--output", golden_output_path,
            "--global-understanding", global_cache_path,
        ]
        if args.regenerate_global:
            cmd.append("--regenerate-global")

        if not run_phase(cmd, "Phase 1: 黄金数据标注（全局理解 → 逐条评判）"):
            sys.exit(1)
    else:
        golden_output_path = args.golden_output
        if not Path(golden_output_path).exists():
            print(f"✗ 找不到 golden-output: {args.golden_output}")
            sys.exit(1)
        print(f"\n跳过 Phase 1，复用已有标注: {golden_output_path}")

    # ═══════════════ 桥接：筛选 badcase 轨迹 ═══════════════
    badcase_dir = str(inter_dir / args.badcase_dir)

    print(f"\n{'='*70}")
    print(f"  桥接：从标注结果中筛选 badcase 轨迹")
    print(f"{'='*70}")

    stats = filter_badcase_traces(golden_output_path, badcase_dir)

    badcase_count = stats["fail"] + stats["partial"] + stats["unknown"]
    print(f"\n  标注统计:")
    print(f"    总计:   {stats['total']}")
    print(f"    通过:   {stats['pass']}")
    print(f"    失败:   {stats['fail']}")
    print(f"    部分通过: {stats['partial']}")
    print(f"    未知:   {stats['unknown']}")
    print(f"    → 筛选出 {badcase_count} 条 badcase 轨迹 → {badcase_dir}/")

    if badcase_count == 0:
        print("\n✗ 没有 badcase 轨迹，无法进行规则挖掘。流水线结束。")
        sys.exit(0)

    # ═══════════════ Phase 2~6: 冷数据规则提取 ═══════════════
    p2_output = str(inter_dir / "phase2output.json")
    p3_output = str(inter_dir / "phase3output.json")
    p4_output = str(inter_dir / "phase4output.json")
    p4_report = str(inter_dir / "feature_report.json")
    p4_log = str(inter_dir / "llm_judge_logs.jsonl")
    p4_cache = str(inter_dir / "llm_judge_cache.json")
    p5_cache_dir = str(inter_dir / "phase5_rule_cache")

    # ---- Phase 2 ----
    if args.start_from >= 2:
        # start-from=2 by default when running full pipeline
        pass

    # 调用原有 run_pipeline.py，从指定阶段开始
    pipeline_cmd = [
        "run_pipeline.py",
        "--trajectories", badcase_dir,
        "--skills", args.skills,
        "--output", args.output,
        "--intermediate-dir", str(inter_dir),
        "--start-from", str(args.start_from),
        "--batch-size", str(args.batch_size),
    ]
    if args.max_turns:
        pipeline_cmd.extend(["--max-turns", str(args.max_turns)])
    if args.no_batch:
        pipeline_cmd.append("--no-batch")
    if args.min_categories > 0:
        pipeline_cmd.extend(["--min-categories", str(args.min_categories)])
    if args.max_categories > 0:
        pipeline_cmd.extend(["--max-categories", str(args.max_categories)])
    if args.max_rules > 0:
        pipeline_cmd.extend(["--max-rules", str(args.max_rules)])
    if args.top_k_rules is not None:
        pipeline_cmd.extend(["--top-k-rules", str(args.top_k_rules)])
    if args.min_score > 0:
        pipeline_cmd.extend(["--min-score", str(args.min_score)])

    if not run_phase(pipeline_cmd, "Phase 2~6: 冷数据规则提取"):
        sys.exit(1)

    # ═══════════════ 完成 ═══════════════
    p6_output_txt = str(Path(args.output).stem) + "_natural_language.txt"
    p6_output_json = str(Path(args.output).stem) + "_ranked.json"

    print(f"\n{'='*70}")
    print(f"  ✓ 完整流水线完成！")
    print(f"{'='*70}")
    print(f"\n  Phase1 标注结果:   {golden_output_path}")
    print(f"  Badcase 轨迹:      {badcase_dir}/ ({badcase_count} 条)")
    print(f"  最终规则:          {args.output}")
    print(f"  自然语言规则:      {p6_output_txt}")
    print(f"  排序后规则:        {p6_output_json}")
    print(f"  黄金数据:          goldendata/")
    print(f"    ├── golden_output.jsonl       (Phase1 标注结果)")
    print(f"    └── global_understanding.txt  (全局理解缓存)")
    print(f"  中间结果:          {inter_dir}/")
    print(f"    ├── badcase_traces/           (筛选后的 badcase 轨迹)")
    print(f"    ├── phase2output.json         (链路 + 缺失检查点)")
    print(f"    ├── phase3output.json         (类别 + 检查点定义)")
    print(f"    ├── phase4output.json         (特征矩阵)")
    print(f"    ├── feature_report.json       (特征统计报告)")
    print(f"    ├── llm_judge_logs.jsonl      (Phase4 审计日志)")
    print(f"    └── phase5_rule_cache/        (Phase5 缓存)")


if __name__ == "__main__":
    main()
