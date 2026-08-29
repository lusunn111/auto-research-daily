from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from auto_research_daily.analysis import load_prompt
from auto_research_daily.config import load_config
from auto_research_daily.pipeline import RunOptions, run_daily


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-research-daily",
        description="面向个人研究画像的分层论文发现、解读与发布流水线",
    )
    parser.add_argument("--config", type=Path, default=Path("config/research.yaml"))
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="校验配置和提示词")
    daily = subparsers.add_parser("daily", help="运行一次每日科研发现流水线")
    daily.add_argument("--dry-run", action="store_true", help="运行但不写入报告和数据")
    daily.add_argument("--no-llm", action="store_true", help="仅用于离线测试的启发式分析")
    daily.add_argument("--offline-fixture", type=Path, help="使用固定 JSON 数据，不访问网络")
    daily.add_argument("--lookback-days", type=int)
    daily.add_argument("--max-papers", type=int)
    daily.add_argument("--deep-limit", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    config_path = args.config.resolve()
    config = load_config(config_path)
    project_root = config_path.parent.parent
    load_prompt(project_root, config.analysis.prompt_version)
    if args.command == "validate":
        print(f"配置有效：{config_path}")
        return 0

    if args.lookback_days is not None and args.lookback_days < 1:
        parser.error("--lookback-days 必须大于 0")
    if args.max_papers is not None and args.max_papers < 1:
        parser.error("--max-papers 必须大于 0")
    if args.deep_limit is not None and args.deep_limit < 0:
        parser.error("--deep-limit 不能小于 0")
    report = run_daily(
        config,
        RunOptions(
            project_root=project_root,
            dry_run=args.dry_run,
            no_llm=args.no_llm,
            offline_fixture=args.offline_fixture.resolve() if args.offline_fixture else None,
            lookback_days=args.lookback_days,
            max_papers=args.max_papers,
            deep_limit=args.deep_limit,
        ),
    )
    print(
        json.dumps(
            {
                "generated_at": report.generated_at.isoformat(),
                "dry_run": report.dry_run,
                "stats": report.stats.model_dump(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
