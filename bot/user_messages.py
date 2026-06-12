"""List messages with :EBALO: reactions for a specific message author."""



from __future__ import annotations



from pathlib import Path



from bot.config import get_settings

from bot.database.db import Database

from bot.services.user_messages_service import (

    build_user_message_rows,

    format_console_user_messages,

    save_user_messages_csv,

)

from bot.utils.dates import month_bounds_utc, to_db_timestamp, validate_period





async def run_user_messages(

    year: int,

    month: int,

    user_id: str,

    *,

    csv_path: Path | None = None,

) -> None:

    """Print all stored messages for ``user_id`` in the month, with jump links."""

    validate_period(year, month)

    settings = get_settings()

    author_id = str(user_id).strip()

    if not author_id.isdigit():

        raise ValueError("user-id must be a numeric Discord snowflake")



    after_utc, before_utc = month_bounds_utc(year, month)

    after_db = to_db_timestamp(after_utc)

    before_db = to_db_timestamp(before_utc)

    guild_id = settings.guild_id



    async with Database(settings.database_path) as db:

        await db.init_db()

        raw = await db.get_messages_by_author(

            str(guild_id), after_db, before_db, author_id

        )



    rows = build_user_message_rows(raw, guild_id=guild_id)

    print(

        format_console_user_messages(

            author_id,

            rows,

            year=year,

            month=month,

            emoji_names=settings.emoji_names,

        )

    )



    if csv_path is not None:

        save_user_messages_csv(csv_path, author_id, rows)

        print(f"\nCSV: {csv_path.resolve()}")

