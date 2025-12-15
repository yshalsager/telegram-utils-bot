from asyncio import Task, create_task, sleep
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import regex as re
from regex import Pattern
from telethon import Button
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message


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


@dataclass
class FileCollector:
    sender_id: int
    chat_id: int
    prompt_message_id: int
    file_message_ids: list[int]
    accept: Callable[[NewMessage.Event], bool]
    on_finish: Callable[[CallbackQuery.Event, list[int]], Awaitable[None]] | None
    on_complete: Callable[[NewMessage.Event, list[int]], Awaitable[None]] | None
    min_files: int
    max_files: int | None
    not_enough_files_text: str | None
    added_reply_text: str | None
    allow_non_reply: bool
    timeout_seconds: int | None


class FileCollectorManager:
    def __init__(self) -> None:
        self.collectors: dict[tuple[int, int], FileCollector] = {}
        self.timeout_tasks: dict[tuple[int, int], Task[None]] = {}

    async def start(
        self,
        event: NewMessage.Event | CallbackQuery.Event,
        prompt_text: str,
        *,
        first_message_id: int,
        accept: Callable[[NewMessage.Event], bool],
        on_finish: Callable[[CallbackQuery.Event, list[int]], Awaitable[None]] | None = None,
        on_complete: Callable[[NewMessage.Event, list[int]], Awaitable[None]] | None = None,
        min_files: int = 1,
        max_files: int | None = None,
        not_enough_files_text: str | None = None,
        added_reply_text: str | None = None,
        finish_button_text: str | None = None,
        allow_non_reply: bool = True,
        reply_to: int | None = None,
        timeout_seconds: int | None = 60 * 15,
    ) -> Message:
        if isinstance(event, CallbackQuery.Event):
            await event.answer()
            message = await event.get_message()
            chat_id = event.chat_id or message.chat_id
            default_reply_to = event.message_id
        else:
            chat_id = event.chat_id
            default_reply_to = event.message.id

        buttons = [Button.inline(finish_button_text, b'c|finish')] if finish_button_text else None

        bot_reply = await event.reply(
            prompt_text,
            reply_to=reply_to or default_reply_to,
            buttons=buttons,
        )
        key = (chat_id, bot_reply.id)
        self.collectors[key] = FileCollector(
            sender_id=event.sender_id,
            chat_id=chat_id,
            prompt_message_id=bot_reply.id,
            file_message_ids=[first_message_id],
            accept=accept,
            on_finish=on_finish,
            on_complete=on_complete,
            min_files=min_files,
            max_files=max_files,
            not_enough_files_text=not_enough_files_text,
            added_reply_text=added_reply_text,
            allow_non_reply=allow_non_reply,
            timeout_seconds=timeout_seconds,
        )

        if timeout_seconds:
            self.timeout_tasks[key] = create_task(self._expire(key, timeout_seconds))
        return bot_reply

    async def _expire(self, key: tuple[int, int], seconds: int) -> None:
        await sleep(seconds)
        self._pop(key)

    def _pop(self, key: tuple[int, int]) -> None:
        self.collectors.pop(key, None)
        if task := self.timeout_tasks.pop(key, None):
            task.cancel()

    def _get_candidate(self, event: NewMessage.Event) -> tuple[int, int] | None:
        if event.is_reply and (reply_to := event.message.reply_to_msg_id):
            if (event.chat_id, reply_to) in self.collectors:
                return (event.chat_id, reply_to)
            return None

        candidates = [
            key
            for key, collector in self.collectors.items()
            if collector.chat_id == event.chat_id
            and collector.sender_id == event.sender_id
            and collector.allow_non_reply
        ]
        candidates.sort(key=lambda k: k[1], reverse=True)
        return candidates[0] if len(candidates) == 1 else None

    async def handle_new_message(self, event: NewMessage.Event) -> bool:
        if event.message.raw_text and event.message.raw_text.startswith('/'):
            return False

        if not (key := self._get_candidate(event)):
            return False

        collector = self.collectors.get(key)
        if not collector or collector.sender_id != event.sender_id:
            return False
        if not collector.accept(event):
            return False

        collector.file_message_ids.append(event.id)
        if collector.added_reply_text:
            await event.reply(collector.added_reply_text)

        if (
            collector.on_complete
            and collector.max_files is not None
            and len(collector.file_message_ids) >= collector.max_files
        ):
            try:
                await collector.on_complete(event, collector.file_message_ids)
            finally:
                self._pop(key)
            return True

        return True

    async def handle_callback(self, event: CallbackQuery.Event) -> bool:
        data = event.data.decode('utf-8', errors='ignore')
        if not data.startswith('c|'):
            return False

        action = data.split('|', 2)[1] if '|' in data else ''
        message = await event.get_message()
        chat_id = event.chat_id or message.chat_id
        key = (chat_id, event.message_id)
        collector = self.collectors.get(key)
        if not collector or collector.sender_id != event.sender_id:
            return False

        if action == 'finish' and collector.on_finish:
            if len(collector.file_message_ids) < collector.min_files:
                await event.answer(
                    collector.not_enough_files_text or 'Not enough files', alert=True
                )
                return True
            try:
                await collector.on_finish(event, collector.file_message_ids)
            finally:
                self._pop(key)
            return True

        if action == 'cancel':
            self._pop(key)
            await event.answer()
            return True

        return False
