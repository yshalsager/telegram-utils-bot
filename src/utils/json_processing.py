from collections.abc import Callable
from datetime import timedelta
from typing import Any

import orjson
from humanize import naturalsize, precisedelta

json_options = (
    orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_NAIVE_UTC | orjson.OPT_OMIT_MICROSECONDS
)
processors: dict[str, Callable[[Any], Any]] = {
    'size': naturalsize,
    'duration': lambda x: precisedelta(timedelta(seconds=float(x))),
}


def process_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: processors.get(k, process_dict)(v)
            for k, v in obj.items()
            if not isinstance(v, bytes)
        }
    if isinstance(obj, list):
        return [process_dict(item) for item in obj if not isinstance(item, bytes)]
    if isinstance(obj, bytes):
        return '<bytes>'
    return obj
