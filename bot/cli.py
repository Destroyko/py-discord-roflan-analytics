"""Command-line entry point: ``python -m bot.cli run --year 2026 --month 3``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bot.channel_tops import run_channel_tops
from bot.pipeline import ScanFailedError, run_pipeline
from bot.user_messages import run_user_messages
from bot.verify import run_verify
from bot.utils.logger import get_logger

logger = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot.cli",
        description="Collect monthly Discord reaction statistics.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Scan a month and write the leaderboard report."
    )
    run_parser.add_argument("--year", type=int, required=True, help="Year, e.g. 2026")
    run_parser.add_argument(
        "--month", type=int, required=True, help="Month number 1-12"
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted scan for this month instead of starting fresh.",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Audit SQLite data for a month (no Discord scan).",
    )
    verify_parser.add_argument("--year", type=int, required=True)
    verify_parser.add_argument("--month", type=int, required=True)
    verify_parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Show messages with links for this author (default: rank 1 only, top 5).",
    )

    messages_parser = subparsers.add_parser(
        "messages",
        help="List messages with reactions for a message author (from SQLite).",
    )
    messages_parser.add_argument("--year", type=int, required=True)
    messages_parser.add_argument("--month", type=int, required=True)
    messages_parser.add_argument(
        "--user-id",
        type=str,
        required=True,
        help="Discord user id (author of the messages).",
    )
    messages_parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional CSV export path.",
    )

    channels_parser = subparsers.add_parser(
        "channels-top",
        help="TOP-N per stats channel for a month (from SQLite).",
    )
    channels_parser.add_argument("--year", type=int, required=True)
    channels_parser.add_argument("--month", type=int, required=True)
    channels_parser.add_argument(
        "--channel-id",
        type=int,
        default=None,
        help="Only one channel from STATS_CHANNEL_IDS (default: all).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            asyncio.run(run_pipeline(args.year, args.month, resume=args.resume))
        except ScanFailedError as exc:
            logger.error("%s", exc)
            return 2
        except ValueError as exc:
            logger.error("Invalid input: %s", exc)
            return 1
        except Exception:  # noqa: BLE001 - top-level guard for the CLI
            logger.exception("Pipeline failed.")
            return 1
        return 0

    if args.command == "verify":
        try:
            asyncio.run(run_verify(args.year, args.month, user_id=args.user_id))
        except ValueError as exc:
            logger.error("Invalid input: %s", exc)
            return 1
        except Exception:  # noqa: BLE001
            logger.exception("Verify failed.")
            return 1
        return 0

    if args.command == "messages":
        try:
            asyncio.run(
                run_user_messages(
                    args.year,
                    args.month,
                    args.user_id,
                    csv_path=args.csv,
                )
            )
        except ValueError as exc:
            logger.error("Invalid input: %s", exc)
            return 1
        except Exception:  # noqa: BLE001
            logger.exception("Messages export failed.")
            return 1
        return 0

    if args.command == "channels-top":
        try:
            asyncio.run(
                run_channel_tops(
                    args.year,
                    args.month,
                    channel_id=args.channel_id,
                )
            )
        except ValueError as exc:
            logger.error("Invalid input: %s", exc)
            return 1
        except Exception:  # noqa: BLE001
            logger.exception("channels-top failed.")
            return 1
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
