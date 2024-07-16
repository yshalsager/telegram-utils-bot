from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from telethon.events import InlineQuery, NewMessage


class ModuleBase(ABC):
    IS_MODULE = True
    CommandHandlerT = Callable[[NewMessage.Event], Coroutine[Any, Any, None]]
    CommandsT = dict[str, dict[str, CommandHandlerT | str]]

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
    def is_applicable(self, event: NewMessage.Event) -> bool:
        pass

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
