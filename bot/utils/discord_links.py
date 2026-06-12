"""Discord jump URL helpers."""


def message_jump_url(guild_id: int | str, channel_id: int | str, message_id: int | str) -> str:
    """Return a client link that opens the message in Discord."""
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
