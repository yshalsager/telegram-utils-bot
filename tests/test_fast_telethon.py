from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from src.utils.fast_telethon import ParallelTransferrer, upload_file


class ParallelDownloadTest(IsolatedAsyncioTestCase):
    async def test_temporary_sender_uses_telethon_reconnect(self) -> None:
        client: Any = SimpleNamespace(
            _get_dc=AsyncMock(return_value=SimpleNamespace(ip_address='127.0.0.1', port=443, id=1)),
            _log={},
            _proxy=None,
            _connection=Mock(return_value=object()),
        )
        transferrer = object.__new__(ParallelTransferrer)
        transferrer.client = client
        transferrer.dc_id = 1
        transferrer.auth_key = object()

        with patch('src.utils.fast_telethon.MTProtoSender') as sender_class:
            sender_class.return_value.connect = AsyncMock()
            await transferrer._create_sender()

        sender_class.assert_called_once_with(transferrer.auth_key, loggers=client._log)

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

    async def test_upload_does_not_retry_failed_transfer(self) -> None:
        client: Any = SimpleNamespace(session=SimpleNamespace(dc_id=1))
        with (
            NamedTemporaryFile() as file,
            patch(
                'src.utils.fast_telethon._internal_transfer_to_telegram',
                new=AsyncMock(side_effect=OSError),
            ) as transfer,
            self.assertRaises(OSError),  # noqa: PT027
        ):
            await upload_file(client, file, 'file.bin')

        transfer.assert_awaited_once()
