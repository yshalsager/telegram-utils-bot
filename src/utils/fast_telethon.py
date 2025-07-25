# copied from https://github.com/tulir/mautrix-telegram/blob/master/mautrix_telegram/util/parallel_file_transfer.py
# Copyright (C) 2021-2023 Tulir Asokan

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from hashlib import md5
from io import BufferedWriter
from logging import Logger, getLogger
from math import ceil
from pathlib import Path
from tempfile import _TemporaryFileWrapper
from typing import Any, BinaryIO, Union, cast

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.helpers import _maybe_await, generate_random_long
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import (
    ExportAuthorizationRequest,
    ImportAuthorizationRequest,
)
from telethon.tl.functions.upload import (
    GetFileRequest,
    SaveBigFilePartRequest,
    SaveFilePartRequest,
)
from telethon.tl.types import (
    Document,
    InputDocumentFileLocation,
    InputFile,
    InputFileBig,
    InputFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    TypeInputFile,
)
from telethon.utils import get_appropriated_part_size, get_input_location

log: Logger = getLogger('_FastTelethon')

TypeLocation = Union[  # noqa: UP007
    Document,
    InputDocumentFileLocation,
    InputPeerPhotoFileLocation,
    InputFileLocation,
    InputPhotoFileLocation,
]


class DownloadSender:
    client: TelegramClient
    sender: MTProtoSender
    request: GetFileRequest
    remaining: int
    stride: int

    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file: TypeLocation,
        offset: int,
        limit: int,
        stride: int,
        count: int,
    ) -> None:
        self.sender = sender
        self.client = client
        self.request = GetFileRequest(file, offset=offset, limit=limit)
        self.stride = stride
        self.remaining = count

    async def next(self) -> bytes | None:
        if not self.remaining:
            return None
        result = await self.client._call(self.sender, self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return cast(bytes, result.bytes)

    def disconnect(self) -> Any:
        return self.sender.disconnect()


class UploadSender:
    client: TelegramClient
    sender: MTProtoSender
    request: SaveFilePartRequest | SaveBigFilePartRequest
    part_count: int
    stride: int
    previous: asyncio.Task | None
    loop: asyncio.AbstractEventLoop

    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file_id: int,
        part_count: int,
        big: bool,
        index: int,
        stride: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.client = client
        self.sender = sender
        self.part_count = part_count
        if big:
            self.request = SaveBigFilePartRequest(file_id, index, part_count, b'')
        else:
            self.request = SaveFilePartRequest(file_id, index, b'')
        self.stride = stride
        self.previous = None
        self.loop = loop

    async def next(self, data: bytes) -> None:
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data))

    async def _next(self, data: bytes) -> None:
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride

    async def disconnect(self) -> Any:
        if self.previous:
            await self.previous
        return await self.sender.disconnect()


class ParallelTransferrer:
    client: TelegramClient
    loop: asyncio.AbstractEventLoop
    dc_id: int
    senders: list[DownloadSender | UploadSender] | None
    auth_key: AuthKey
    upload_ticker: int

    def __init__(self, client: TelegramClient, dc_id: int | None = None) -> None:
        self.client = client
        with suppress(AttributeError):
            self.client.refresh_auth(client)
        self.loop = self.client.loop
        self.dc_id = dc_id or self.client.session.dc_id
        self.auth_key = (
            None if dc_id and self.client.session.dc_id != dc_id else self.client.session.auth_key
        )
        self.senders = None
        self.upload_ticker = 0
        with suppress(AttributeError):
            self.client.clear_auth(self.client)

    async def _cleanup(self) -> None:
        await asyncio.gather(*[sender.disconnect() for sender in self.senders])  # type: ignore[union-attr]
        self.senders = None

    @staticmethod
    def _get_connection_count(
        file_size: int,
    ) -> int:
        full_size = 100 * (1024**2)
        if file_size > full_size:
            return 20
        return ceil((file_size / full_size) * 20)

    async def _init_download(
        self, connections: int, file: TypeLocation, part_count: int, part_size: int
    ) -> None:
        minimum, remainder = divmod(part_count, connections)

        def get_part_count() -> int:
            nonlocal remainder
            if remainder > 0:
                remainder -= 1
                return minimum + 1
            return minimum

        # The first cross-DC sender will export+import the authorization, so we always create it
        # before creating any other senders.
        self.senders = [
            await self._create_download_sender(
                file, 0, part_size, connections * part_size, get_part_count()
            ),
            *await asyncio.gather(
                *[
                    self._create_download_sender(
                        file, i, part_size, connections * part_size, get_part_count()
                    )
                    for i in range(1, connections)
                ]
            ),
        ]

    async def _create_download_sender(
        self,
        file: TypeLocation,
        index: int,
        part_size: int,
        stride: int,
        part_count: int,
    ) -> DownloadSender:
        return DownloadSender(
            self.client,
            await self._create_sender(),
            file,
            index * part_size,
            part_size,
            stride,
            part_count,
        )

    async def _init_upload(
        self, connections: int, file_id: int, part_count: int, big: bool
    ) -> None:
        self.senders = [
            await self._create_upload_sender(file_id, part_count, big, 0, connections),
            *await asyncio.gather(
                *[
                    self._create_upload_sender(file_id, part_count, big, i, connections)
                    for i in range(1, connections)
                ]
            ),
        ]

    async def _create_upload_sender(
        self, file_id: int, part_count: int, big: bool, index: int, stride: int
    ) -> UploadSender:
        return UploadSender(
            self.client,
            await self._create_sender(),
            file_id,
            part_count,
            big,
            index,
            stride,
            loop=self.loop,
        )

    async def _create_sender(self) -> MTProtoSender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(
            self.client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=self.client._log,
                proxy=self.client._proxy,
            )
        )
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            req = InvokeWithLayerRequest(LAYER, self.client._init_request)
            await sender.send(req)
            self.auth_key = sender.auth_key
        return sender

    async def init_upload(
        self,
        file_id: int,
        file_size: int,
        part_size_kb: float | None = None,
        connection_count: int | None = None,
    ) -> tuple[int, int, bool]:
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or get_appropriated_part_size(file_size)) * 1024
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * (1024**2)
        await self._init_upload(connection_count, file_id, part_count, is_large)  # type: ignore[arg-type]
        return part_size, part_count, is_large  # type: ignore[return-value]

    async def upload(self, part: bytes) -> None:
        await self.senders[self.upload_ticker].next(part)  # type: ignore[index, call-arg]
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)  # type: ignore[arg-type]

    async def finish_upload(self) -> None:
        await self._cleanup()

    async def download(
        self,
        file: TypeLocation,
        file_size: int,
        part_size_kb: float | None = None,
        connection_count: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or get_appropriated_part_size(file_size)) * 1024
        part_count = ceil(file_size / part_size)
        await self._init_download(connection_count, file, part_count, part_size)  # type: ignore[arg-type]

        part = 0
        while part < part_count:
            tasks = [self.loop.create_task(sender.next()) for sender in self.senders]  # type: ignore[call-arg, union-attr]
            for task in tasks:
                data = await task
                if not data:
                    break
                yield data
                part += 1
        await self._cleanup()


parallel_transfer_locks: defaultdict[int, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())


def stream_file(file_to_stream: BinaryIO, chunk_size: int = 1024) -> AsyncGenerator[bytes, None]:  # type: ignore[misc]
    while True:
        data_read = file_to_stream.read(chunk_size)
        if not data_read:
            break
        yield data_read


async def _internal_transfer_to_telegram(
    client: TelegramClient,
    response: BinaryIO,
    filename: str,
    progress_callback: Callable,
) -> tuple[TypeInputFile, int]:
    file_id = generate_random_long()
    file_size = Path(response.name).stat().st_size
    hash_md5 = md5(usedforsecurity=False)
    uploader = ParallelTransferrer(client)
    part_size, part_count, is_large = await uploader.init_upload(file_id, file_size)
    buffer = bytearray()
    for data in stream_file(response):  # type: ignore[attr-defined]
        if progress_callback:  # type: ignore[truthy-function]
            with suppress(BaseException):
                await _maybe_await(progress_callback(response.tell(), file_size))
        if not is_large:
            hash_md5.update(data)
        if len(buffer) == 0 and len(data) == part_size:
            await uploader.upload(data)
            continue
        new_len = len(buffer) + len(data)
        if new_len >= part_size:
            cutoff = part_size - len(buffer)
            buffer.extend(data[:cutoff])
            await uploader.upload(bytes(buffer))
            buffer.clear()
            buffer.extend(data[cutoff:])
        else:
            buffer.extend(data)
    if len(buffer) > 0:
        await uploader.upload(bytes(buffer))
    await uploader.finish_upload()
    if is_large:
        return InputFileBig(file_id, part_count, filename), file_size
    return InputFile(file_id, part_count, filename, hash_md5.hexdigest()), file_size


async def download_file(
    client: TelegramClient,
    location: TypeLocation,
    out: _TemporaryFileWrapper | BinaryIO | BufferedWriter,
    progress_callback: Callable | None = None,
) -> BinaryIO:
    size = location.size
    dc_id, location = get_input_location(location)
    # We lock the transfers because telegram has connection count limits
    downloader = ParallelTransferrer(client, dc_id)
    downloaded = downloader.download(location, size)
    async for x in downloaded:
        out.write(x)
        if progress_callback:
            with suppress(BaseException):
                await _maybe_await(progress_callback(out.tell(), size))
    return out  # type: ignore[return-value]


async def upload_file(
    client: TelegramClient,
    file: _TemporaryFileWrapper | BinaryIO,
    filename: str,
    progress_callback: Callable | None = None,
) -> TypeInputFile:
    return (
        await _internal_transfer_to_telegram(client, file, filename, progress_callback)  # type: ignore[arg-type]
    )[0]
