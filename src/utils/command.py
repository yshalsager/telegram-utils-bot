from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from regex import Pattern
from telethon.events import CallbackQuery, InlineQuery, NewMessage
from telethon.tl.custom import Message


@dataclass
class Command:
    handler: Callable[[NewMessage.Event | CallbackQuery.Event], Coroutine[Any, Any, None]]
    description: str
    pattern: Pattern
    condition: Callable[[NewMessage.Event, Message | None], bool] = lambda _, __: True
    name: str | None = None
    is_applicable_for_reply: bool = False

    def __repr__(self) -> str:
        return (
            f"Command(name={self.name or self.handler.__name__}, description='{self.description}')"
        )


@dataclass
class InlineCommand:
    pattern: Pattern
    handler: Callable[[InlineQuery.Event], Coroutine[Any, Any, None]] | None = None
    name: str | None = None

    def __repr__(self) -> str:
        return (
            f'InlineCommand(name={self.name or self.handler.__name__ if self.handler else "None"})'
        )
