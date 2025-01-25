import logging
from typing import Any

from aiohttp import ClientResponse, ClientSession


async def fetch_json(url: str, params: dict | None = None) -> Any:
    async with ClientSession() as session:
        try:
            response: ClientResponse
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                logging.error(f'HTTP error: {response.status} for URL: {url}')
                return None
        except Exception as e:  # noqa: BLE001
            logging.error(f'Error fetching data from {url}: {e}')
            return None
