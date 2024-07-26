from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any, ClassVar

import regex as re
from telethon import TelegramClient
from telethon.events import InlineQuery, NewMessage
from telethon.tl.custom import Message

from src.utils.command import Command, InlineCommand
from src.utils.patterns import HTTP_URL_PATTERN
from src.utils.telegram import get_reply_message


def matches_command(
    event: NewMessage.Event, reply_message: Message | None, command: Command
) -> bool:
    if not command.condition(event, reply_message):
        return False

    text = event.message.raw_text
    has_file = bool(event.message.file)
    is_url_message = bool(re.search(HTTP_URL_PATTERN, text))

    if text and not has_file and not is_url_message:
        return bool(command.pattern.match(text))
    return bool(is_url_message or has_file)


class ModuleBase(ABC):
    IS_MODULE = True
    CommandHandlerT = Callable[[NewMessage.Event], Coroutine[Any, Any, None]]
    CommandsT = dict[str, Command]

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def commands(self) -> CommandsT:
        pass

    async def is_applicable(self, event: NewMessage.Event) -> bool:
        reply_message = (
            await get_reply_message(event, previous=True) if event.message.is_reply else None
        )
        return any(
            matches_command(event, reply_message, command) for command in self.commands.values()
        )

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        """
        Override this method to register custom handlers from the module using `bot.add_event_handler`
        """
        return

    async def handle(self, event: NewMessage.Event, command: str | None = None) -> bool:
        assert command is not None
        if '|' in command:
            command, _ = command.split('|', 1)
        cmd = self.commands.get(command)
        if cmd and callable(cmd.handler):
            await cmd.handler(event)
        return True


class InlineModuleBase(ModuleBase):
    commands: ClassVar[ModuleBase.CommandsT] = {}
    InlineCommandsT = dict[str, InlineCommand]

    @property
    @abstractmethod
    def inline_commands(self) -> InlineCommandsT:
        pass

    async def is_applicable(self, event: InlineQuery.Event) -> bool:
        return any(command.pattern.match(event.text) for command in self.inline_commands.values())

    async def handle(self, event: InlineQuery.Event, _: str | None = None) -> bool:
        for command in self.inline_commands.values():
            if command.pattern.match(event.text) and callable(command.handler):
                await command.handler(event)
                return True
        return False
