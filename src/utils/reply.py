from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import regex as re
from regex import Pattern
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message


class MergeState(Enum):
    IDLE = auto()
    COLLECTING = auto()
    MERGING = auto()


StateT = defaultdict[int, dict[str, Any]]


@dataclass
class ReplyPrompt:
    sender_id: int
    chat_id: int
    reply_message_id: int
    media_message_id: int | None
    pattern: Pattern
    handler: Callable[[NewMessage.Event, Message | None, re.Match], Awaitable[None]]
    invalid_reply_text: str


class ReplyPromptManager:
    def __init__(self) -> None:
        self.prompts: dict[tuple[int, int], ReplyPrompt] = {}

    async def ask(
        self,
        event: CallbackQuery.Event,
        prompt_text: str,
        *,
        pattern: Pattern,
        handler: Callable[[NewMessage.Event, Message | None, re.Match], Awaitable[None]],
        invalid_reply_text: str,
        media_message_id: int | None = None,
    ) -> None:
        await event.answer()
        bot_reply = await event.reply(prompt_text, reply_to=event.message_id)
        message = await event.get_message()
        if media_message_id is None:
            media_message_id = message.reply_to_msg_id

        chat_id = event.chat_id or message.chat_id
        self.prompts[(chat_id, bot_reply.id)] = ReplyPrompt(
            sender_id=event.sender_id,
            chat_id=chat_id,
            reply_message_id=bot_reply.id,
            media_message_id=media_message_id,
            pattern=pattern,
            handler=handler,
            invalid_reply_text=invalid_reply_text,
        )

    async def handle(self, event: NewMessage.Event) -> bool:
        if not event.is_reply:
            return False

        reply_to = event.message.reply_to_msg_id
        if not reply_to:
            return False

        prompt = self.prompts.get((event.chat_id, reply_to))
        if not prompt or prompt.sender_id != event.sender_id:
            return False

        if not (match := prompt.pattern.search(event.message.raw_text or '')):
            await event.reply(prompt.invalid_reply_text)
            return True

        original_message = (
            await event.client.get_messages(event.chat_id, ids=prompt.media_message_id)
            if prompt.media_message_id
            else None
        )
        try:
            await prompt.handler(event, original_message, match)
        finally:
            self.prompts.pop((event.chat_id, reply_to), None)
        return True
