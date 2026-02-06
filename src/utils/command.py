from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any


@dataclass
class Command:
    handler: Callable[..., Coroutine[Any, Any, None]]
    description: str
    pattern: Any
    condition: Callable[..., bool] = lambda *_: True
    name: str | None = None
    is_applicable_for_reply: bool = False

    def __repr__(self) -> str:
        handler_name = getattr(self.handler, '__name__', self.handler.__class__.__name__)
        return f"Command(name={self.name or handler_name}, description='{self.description}')"


@dataclass
class InlineCommand:
    pattern: Any
    handler: Callable[..., Coroutine[Any, Any, None]] | None = None
    name: str | None = None

    def __repr__(self) -> str:
        handler_name = (
            getattr(self.handler, '__name__', self.handler.__class__.__name__)
            if self.handler
            else 'None'
        )
        return f'InlineCommand(name={self.name or handler_name})'
