from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from telethon.events import NewMessage


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

    @abstractmethod
    async def handle(self, event: NewMessage.Event, command: str | None = None) -> bool:
        pass
