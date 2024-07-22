import logging
from typing import ClassVar

import regex as re
from search_engine_parser.core.base import SearchResult
from search_engine_parser.core.engines.duckduckgo import Search as DuckDuckGoSearch
from telethon import events

from src.modules.base import InlineModuleBase
from src.utils.command import InlineCommand

ddg_search = DuckDuckGoSearch()


async def handle_duckduckgo_search(event: events.InlineQuery.Event) -> None:
    query = event.text[4:].strip()
    if not query:
        return

    try:
        results: SearchResult = await ddg_search.async_search(query, 1)
    except Exception as e:  # noqa: BLE001
        logging.error(f'Error in DuckDuckGo search: {e}')
        return

    inline_results = []
    for result in results:
        title = result.get('titles', 'No title')
        link = result.get('links', '')
        if not link:
            continue
        if not link.startswith('http'):
            link = f'https://{link}'
        description = result.get('descriptions', 'No description')

        inline_results.append(
            event.builder.article(
                title=title,
                description=description,
                url=link,
                text=f'<b>{title}</b>\n\n{description}\n<a href="{link}">Link</a>',
            )
        )

    await event.answer(inline_results)


class WebSearch(InlineModuleBase):
    name = 'Web Search'
    description = 'Search the web using search engines'
    inline_commands: ClassVar[InlineModuleBase.InlineCommandsT] = {
        'ddg': InlineCommand(
            pattern=re.compile(r'^ddg\s+(.+)$'),
            handler=handle_duckduckgo_search,
            name='DuckDuckGo Search',
        )
    }
