from collections.abc import Callable
from os import getenv
from pathlib import Path
from typing import Any, TypeVar

from plate import Plate

F = TypeVar('F', bound=Callable[..., Any])

locales_dir = Path(__file__).parent.parent / 'i18n/locales'
# languages = json.loads((locales_dir.parent / 'languages.json').read_text())
plate: Plate = Plate(root=str(locales_dir), fallback='en_US')


def get_full_language_code(language_code: str) -> Any:
    for lang in plate.locales:
        if lang.startswith(f'{language_code}_'):
            return lang
    return 'en_US'


def get_translator(language_code: str) -> Any:
    return plate.get_translator(get_full_language_code(language_code))


t = get_translator(getenv('BOT_LANGUAGE', 'en_US'))

#
# def localize(function: F) -> F:
#     @wraps(function)
#     def wrapper(event: NewMessage.Event | CallbackQuery.Event, *args: tuple, **kwargs: dict) -> F:
#         return cast(
#             F, function(event, get_translator(getenv('BOT_LANGUAGE', 'en_US')), *args, **kwargs)
#         )
#
#     return cast(F, wrapper)
