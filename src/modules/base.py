from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from telethon import TelegramClient
from telethon.events import InlineQuery, NewMessage


class ModuleBase(ABC):
    IS_MODULE = True
    CommandHandlerT = Callable[[NewMessage.Event], Coroutine[Any, Any, None]]
    CommandsT = dict[str, dict[str, CommandHandlerT | str | bool]]

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @abstractmethod
    def commands(self) -> CommandsT:
        pass

    @abstractmethod
    async def is_applicable(self, event: NewMessage.Event) -> bool:
        pass

    @staticmethod
    async def is_applicable_for_reply(event: NewMessage.Event) -> bool:
        """
        Check if the module is applicable to be used in CallbackQuery Event
        :param event: CallbackQuery.Event
        :return: bool
        """
        return False

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        """
        Override this method to register custom handlers from the module using `bot.add_event_handler`
        """
        return

    async def handle(self, event: NewMessage.Event, command: str | None = None) -> bool:
        assert command is not None
        handler = self.commands().get(command, {}).get('handler')
        if callable(handler):
            await handler(event)
        return True


class InlineModule(ABC):
    @abstractmethod
    async def handle_inline_query(self, event: InlineQuery.Event) -> None:
        pass
