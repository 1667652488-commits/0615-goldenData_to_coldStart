#!/usr/bin/env python3
"""
Badcase 冷分析全流水线 —— 串联 Phase 2~6，一键输出可部署规则

流水线：
  Phase2: 轨迹 → 归纳正确链路 + 缺失检查点
  Phase3: 缺失检查点 → 归纳类别 + binary_checkpoints
  Phase4: 类别定义 + 轨迹 → LLM 特征提取 (0/1/NA 矩阵)
  Phase5: 特征矩阵 + 类别定义 + Skills → 规则挖掘 + Skill 归因
  Phase6: 规则 → 自然语言化 + 排序筛选

输入：
  --trajectories  轨迹文件 (.jsonl/.json) 或目录
  --skills        Skill markdown 文件或目录
  --output        最终规则输出 (默认 rules.json)

输出：
  rules.json                        ← Phase5 最终规则
  rules_natural_language.txt         ← Phase6 自然语言规则
  rules_ranked.json                  ← Phase6 排序后规则
  intermediate/
    phase2output.json               ← Phase2 中间结果
    phase3output.json               ← Phase3 中间结果
    phase4output.json               ← Phase4 特征矩阵
    feature_report.json             ← Phase4 特征报告
    llm_judge_logs.jsonl            ← Phase4 LLM 判定日志
    llm_judge_cache.json            ← Phase4 缓存 (中断可复用)
    phase5_rule_cache/              ← Phase5 缓存

用法：
  # 全量运行
  python run_pipeline.py --trajectories input_trace/0611v1/chosen/ --skills skills/

  # 指定输出路径
  python run_pipeline.py --trajectories input_trace/0611v1/chosen/ --skills skills/ --output my_rules.json

  # 从某个阶段开始 (跳过已完成的阶段)
  python run_pipeline.py --trajectories input_trace/0611v1/chosen/ --skills skills/ --start-from 3

  # 调整 batch-size
  python run_pipeline.py --trajectories input_trace/0611v1/chosen/ --skills skills/ --batch-size 5
"""

import json
import re
import subprocess
import sys
import argparse
from pathlib import Path


def adapt_phase2_to_phase3(phase2_path: str, output_path: str):
    """
    Phase2→Phase3 适配：将 Phase2 的 missing_checkpoints_list
    统一转换为 Phase3 期望的字典列表格式。

    Phase2 输出可能有两种格式（由 LLM 输出不稳定导致）：
      1. 字符串列表: "强制检查点: 描述内容；受影响轨迹: id1(脚本), id2(脚本)"
      2. 字典列表（字段名不匹配）:
         {"id": "CHECKPOINT_001", "description": "...", "affected_trajectories": [...]}

    Phase3 期望格式:
      {"checkpoint_id": "CP001", "description": "...",
       "violation_pattern": "", "severity": "", "affected_trajectories": [...]}
    """
    with open(phase2_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    phase2_output = data.get('phase2_output', data)
    raw_list = phase2_output.get('missing_checkpoints_list', [])

    adapted = []
    for i, item in enumerate(raw_list, 1):
        if isinstance(item, dict):
            # 字典格式：做字段映射 + 缺失字段补全
            adapted.append({
                'checkpoint_id': item.get('checkpoint_id') or item.get('id') or f"CP{i:03d}",
                'description': item.get('description', ''),
                'violation_pattern': item.get('violation_pattern', ''),
                'severity': item.get('severity', ''),
                'affected_trajectories': item.get('affected_trajectories', []),
            })
            continue

        if isinstance(item, str):
            # 字符串格式：解析提取
            text = item.strip()
            desc = text
            affected = []

            # 格式: "强制检查点: xxx；受影响轨迹: id1(sid), id2(sid)"
            traj_match = re.search(r'[；;，,]\s*受影响轨迹[：:]\s*(.+)', text)
            if traj_match:
                affected_str = traj_match.group(1)
                affected = re.findall(r'(auto-[a-f0-9]+)', affected_str)
                desc = text[:traj_match.start()].strip()

            # 清理描述前缀
            desc = re.sub(r'^强制检查点[：:]\s*', '', desc).strip()

            adapted.append({
                'checkpoint_id': f'CP{i:03d}',
                'description': desc,
                'violation_pattern': '',
                'severity': '',
                'affected_trajectories': affected,
            })
            continue

        # 其他类型跳过

    # 回写 phase2_output，保持其余字段不变
    phase2_output['missing_checkpoints_list'] = adapted

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    str_count = sum(1 for item in raw_list if isinstance(item, str))
    dict_count = sum(1 for item in raw_list if isinstance(item, dict))
    print(f"  适配: {len(raw_list)} 个检查点 → {len(adapted)} 个结构化 (字典格式 {dict_count}, 字符串格式 {str_count})")


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


def main():
    parser = argparse.ArgumentParser(
        description="Badcase 冷分析全流水线 (Phase 2~5)"
    )
    # 必需参数
    parser.add_argument(
        "--trajectories", type=str, required=True,
        help="轨迹文件 (.jsonl/.json) 或目录"
    )
    parser.add_argument(
        "--skills", type=str, required=True,
        help="Skill markdown 文件或目录"
    )
    # 输出
    parser.add_argument(
        "--output", type=str, default="rules.json",
        help="最终规则输出文件 (默认 rules.json)"
    )
    parser.add_argument(
        "--intermediate-dir", type=str, default="intermediate",
        help="中间结果目录 (默认 intermediate)"
    )
    # 控制
    parser.add_argument(
        "--start-from", type=int, default=2, choices=[2, 3, 4, 5, 6],
        help="从哪个阶段开始 (默认 2，即全量运行)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10,
        help="Phase2/3/4 每批处理数 (默认 10)"
    )
    parser.add_argument(
        "--no-batch", action="store_true",
        help="Phase4 禁用批量判定模式"
    )
    parser.add_argument(
        "--max-turns", type=int, default=10,
        help="Phase4 每条轨迹提取的最大轮数 (默认 10)"
    )
    # Phase3 类别数量控制
    parser.add_argument(
        "--min-categories", type=int, default=0,
        help="Phase3 最少归纳类别数 (0=不限制)"
    )
    parser.add_argument(
        "--max-categories", type=int, default=0,
        help="Phase3 最多归纳类别数 (0=不限制)"
    )
    # Phase5 规则数量控制
    parser.add_argument(
        "--max-rules", type=int, default=0,
        help="Phase5 最大规则数上限 (0=不限制)"
    )
    # Phase6 筛选参数
    parser.add_argument(
        "--top-k-rules", type=int, default=None,
        help="Phase6 保留前 K 条规则 (默认 None=全部保留)"
    )
    parser.add_argument(
        "--min-score", type=float, default=0.0,
        help="Phase6 最低综合得分线 (默认 0.0)"
    )
    args = parser.parse_args()

    # 准备路径
    inter_dir = Path(args.intermediate_dir)
    inter_dir.mkdir(parents=True, exist_ok=True)

    p2_output = str(inter_dir / "phase2output.json")
    p3_output = str(inter_dir / "phase3output.json")
    p4_output = str(inter_dir / "phase4output.json")
    p4_report = str(inter_dir / "feature_report.json")
    p4_log = str(inter_dir / "llm_judge_logs.jsonl")
    p4_cache = str(inter_dir / "llm_judge_cache.json")
    p5_cache_dir = str(inter_dir / "phase5_rule_cache")

    # ═══════════════ Phase 2 ═══════════════
    if args.start_from <= 2:
        cmd = [
            "phase2_induct_linkage.py",
            "--input", args.trajectories,
            "--output", p2_output,
            "--intermediate-dir", str(inter_dir / "phase2_intermediate"),
            "--batch-size", str(args.batch_size),
        ]
        if not run_phase(cmd, "Phase 2: 归纳正确链路 + 缺失检查点"):
            sys.exit(1)

    # Phase2→Phase3 适配：将字符串列表转换为字典列表
    print(f"\n{'='*70}")
    print(f"  适配 Phase2 → Phase3 输出格式")
    print(f"{'='*70}")
    adapt_phase2_to_phase3(p2_output, p2_output)

    # ═══════════════ Phase 3 ═══════════════
    if args.start_from <= 3:
        cmd = [
            "phase3_induct_categories.py",
            "--phase2-result", p2_output,
            "--trajectories", args.trajectories,
            "--output", p3_output,
            "--batch-size", str(args.batch_size),
        ]
        if args.min_categories > 0:
            cmd.extend(["--min-categories", str(args.min_categories)])
        if args.max_categories > 0:
            cmd.extend(["--max-categories", str(args.max_categories)])
        if not run_phase(cmd, "Phase 3: 归纳 badcase 类别 + 检查点"):
            sys.exit(1)
    else:
        print(f"\n跳过 Phase 3 (从 Phase {args.start_from} 开始)")
        if not Path(p3_output).exists():
            print(f"✗ 找不到 Phase 3 输出: {p3_output}")
            sys.exit(1)

    # ═══════════════ Phase 4 ═══════════════
    if args.start_from <= 4:
        cmd = [
            "phase4_feature_extractor.py",
            "--trajectories", args.trajectories,
            "--categories", p3_output,
            "--output", p4_output,
            "--report", p4_report,
            "--log", p4_log,
            "--cache", p4_cache,
            "--batch-size", str(min(args.batch_size, 5)),
            "--max-turns", str(args.max_turns),
        ]
        if args.no_batch:
            cmd.append("--no-batch")
        if not run_phase(cmd, "Phase 4: LLM 特征提取 (0/1/NA 矩阵)"):
            sys.exit(1)
    else:
        print(f"\n跳过 Phase 4 (从 Phase {args.start_from} 开始)")
        if not Path(p4_output).exists():
            print(f"✗ 找不到 Phase 4 输出: {p4_output}")
            sys.exit(1)

    # ═══════════════ Phase 5 ═══════════════
    if args.start_from <= 5:
        cmd = [
            "phase5_rule_mining.py",
            "--features", p4_output,
            "--categories", p3_output,
            "--trajectories", args.trajectories,
            "--skills", args.skills,
            "--output", args.output,
            "--cache-dir", p5_cache_dir,
        ]
        if args.max_rules > 0:
            cmd.extend(["--max-rules", str(args.max_rules)])
        if not run_phase(cmd, "Phase 5: 规则挖掘 + Skill 归因"):
            sys.exit(1)

    # ═══════════════ Phase 6 ═══════════════
    p6_output_txt = str(Path(args.output).stem) + "_natural_language.txt"
    p6_output_json = str(Path(args.output).stem) + "_ranked.json"

    if args.start_from <= 6:
        cmd = [
            "phase6_rule_transform.py",
            "--rules", args.output,
            "--categories", p3_output,
            "--trajectories", args.trajectories,
            "--skills", args.skills,
            "--output", p6_output_txt,
            "--output-json", p6_output_json,
        ]
        if args.top_k_rules is not None:
            cmd.extend(["--top-k-rules", str(args.top_k_rules)])
        if args.min_score > 0:
            cmd.extend(["--min-score", str(args.min_score)])
        if not run_phase(cmd, "Phase 6: 规则语言化 + 排序筛选"):
            sys.exit(1)
    else:
        print(f"\n跳过 Phase 6 (从 Phase {args.start_from} 开始)")

    # ═══════════════ 完成 ═══════════════
    print(f"\n{'='*70}")
    print(f"  ✓ 全流水线完成！")
    print(f"{'='*70}")
    print(f"\n  最终规则: {args.output}")
    print(f"  自然语言规则: {p6_output_txt}")
    print(f"  排序后规则: {p6_output_json}")
    print(f"  中间结果: {inter_dir}/")
    print(f"    ├── phase2output.json      (链路 + 缺失检查点)")
    print(f"    ├── phase3output.json      (类别 + 检查点定义)")
    print(f"    ├── phase4output.json      (特征矩阵)")
    print(f"    ├── feature_report.json    (特征统计报告)")
    print(f"    ├── llm_judge_logs.jsonl   (Phase4 审计日志)")
    print(f"    └── phase5_rule_cache/     (Phase5 缓存)")


if __name__ == "__main__":
    main()
