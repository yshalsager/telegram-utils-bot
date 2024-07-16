import logging

from search_engine_parser.core.base import SearchResult
from search_engine_parser.core.engines.duckduckgo import Search as DuckDuckGoSearch
from telethon import events

from src.modules.base import InlineModule, ModuleBase


class WebSearch(ModuleBase, InlineModule):
    def __init__(self) -> None:
        self.ddg_search = DuckDuckGoSearch()

    @property
    def name(self) -> str:
        return 'Web Search'

    @property
    def description(self) -> str:
        return 'Search the web using DuckDuckGo'

    def commands(self) -> ModuleBase.CommandsT:
        return {}

    async def handle_inline_query(self, event: events.InlineQuery.Event) -> None:
        query = event.text[4:].strip()
        if not query:
            return

        try:
            results: SearchResult = await self.ddg_search.async_search(query, 1)
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

    def is_applicable(self, event: events.InlineQuery.Event) -> bool:
        return isinstance(event, events.InlineQuery.Event) and event.text.startswith('ddg ')
