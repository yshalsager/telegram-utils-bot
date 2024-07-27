from collections import defaultdict
from enum import Enum, auto
from typing import Any

from telethon.events import CallbackQuery


class ReplyState(Enum):
    WAITING = auto()
    PROCESSING = auto()


reply_states: defaultdict[int, dict[str, Any]] = defaultdict(
    lambda: {'state': ReplyState.WAITING, 'media_message_id': None, 'reply_message_id': None}
)


async def handle_callback_query_for_reply_state(
    event: CallbackQuery.Event, reply_text: str
) -> None:
    await event.answer()
    bot_reply = await event.reply(reply_text, reply_to=event.message_id)
    reply_states[event.sender_id]['state'] = ReplyState.WAITING
    reply_states[event.sender_id]['reply_message_id'] = bot_reply.id
    reply_states[event.sender_id]['media_message_id'] = (await event.get_message()).reply_to_msg_id


class MergeState(Enum):
    IDLE = auto()
    COLLECTING = auto()
    MERGING = auto()


StateT = defaultdict[int, dict[str, Any]]
