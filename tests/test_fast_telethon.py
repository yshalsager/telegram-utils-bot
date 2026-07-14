from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import pytest
from src.utils.fast_telethon import ParallelTransferrer


class ParallelDownloadTest(IsolatedAsyncioTestCase):
    async def test_empty_download_round_fails_instead_of_spinning(self) -> None:
        transferrer = object.__new__(ParallelTransferrer)
        transferrer.senders = [SimpleNamespace(next=AsyncMock(return_value=None))]
        transferrer._init_download = AsyncMock()
        transferrer._cleanup = AsyncMock()

        with pytest.raises(EOFError):
            [
                chunk
                async for chunk in transferrer.download(
                    None, 1024, part_size_kb=1, connection_count=1
                )
            ]

        transferrer._cleanup.assert_awaited_once()
