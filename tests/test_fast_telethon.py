from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from src.utils.fast_telethon import ParallelTransferrer, upload_file


class ParallelDownloadTest(IsolatedAsyncioTestCase):
    async def test_sender_offsets_use_part_indexes(self) -> None:
        transferrer = object.__new__(ParallelTransferrer)
        transferrer._create_download_sender = AsyncMock(side_effect=[object(), object(), object()])

        await transferrer._init_download(3, None, 3, 1024)

        assert [call.args[1] for call in transferrer._create_download_sender.await_args_list] == [
            0,
            1,
            2,
        ]

    async def test_empty_download_round_fails_instead_of_spinning(self) -> None:
        transferrer = object.__new__(ParallelTransferrer)
        transferrer.senders = [SimpleNamespace(next=AsyncMock(return_value=None))]
        transferrer._init_download = AsyncMock()
        transferrer._cleanup = AsyncMock()

        with self.assertRaises(EOFError):  # noqa: PT027
            [
                chunk
                async for chunk in transferrer.download(
                    None, 1024, part_size_kb=1, connection_count=1
                )
            ]

        transferrer._cleanup.assert_awaited_once()

    async def test_large_upload_retries_with_fewer_connections(self) -> None:
        client = SimpleNamespace(session=SimpleNamespace(dc_id=1))
        result = object()
        with (
            NamedTemporaryFile() as file,
            patch(
                'src.utils.fast_telethon._internal_transfer_to_telegram',
                new=AsyncMock(side_effect=[OSError, OSError, (result, 0)]),
            ) as transfer,
            patch('src.utils.fast_telethon.asyncio.sleep', new=AsyncMock()),
        ):
            file.truncate(101 * 1024**2)
            assert await upload_file(client, file, 'file.bin') is result

        assert [call.args[-1] for call in transfer.await_args_list] == [20, 8, 4]
