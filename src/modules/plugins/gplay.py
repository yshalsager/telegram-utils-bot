from pathlib import Path
from typing import Any, ClassVar

import regex as re
from humanize import naturalsize
from telethon import Button
from telethon.events import CallbackQuery, NewMessage

from src import BOT_ADMINS
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import upload_file_and_cleanup
from src.utils.filters import is_admin_in_private
from src.utils.gplay import (
    GPlayError,
    arch_label,
    download_gplay_variants,
    extract_gplay_package,
)
from src.utils.i18n import t
from src.utils.telegram import get_reply_message, send_progress_message

GPLAY_COMMAND_PATTERN = re.compile(r'^/gplay(?:\s+(.+))?$')
GPLAY_VARIANTS = {
    'arm64': ['arm64'],
    'armv7': ['armv7'],
    'both': ['arm64', 'armv7'],
}


def extract_gplay_command_input(text: str) -> str:
    match = GPLAY_COMMAND_PATTERN.match(text)
    return (match.group(1) if match else '') or ''


def has_gplay_input(event: NewMessage.Event, reply_message: object | None) -> bool:
    if extract_gplay_package(extract_gplay_command_input(event.message.raw_text or '')):
        return True
    if extract_gplay_package(event.message.raw_text or ''):
        return True
    return bool(
        reply_message and extract_gplay_package(getattr(reply_message, 'raw_text', '') or '')
    )


def has_gplay_permission(event: NewMessage.Event | CallbackQuery.Event) -> bool:
    user_id = event.sender_id or event.chat_id
    is_private = getattr(event, 'is_private', event.chat_id == user_id)
    return bool(
        is_private
        and user_id in BOT_ADMINS
        and event.client.permission_manager.has_permission('GPlay', user_id, event.chat_id)
    )


async def get_gplay_input_message(event: NewMessage.Event | CallbackQuery.Event) -> str:
    if isinstance(event, CallbackQuery.Event):
        reply_message = await get_reply_message(event, previous=True)
        return reply_message.raw_text if reply_message else ''

    input_text = extract_gplay_command_input(event.message.raw_text or '')
    if input_text:
        return input_text

    reply_message = (
        await get_reply_message(event, previous=True) if event.message.is_reply else None
    )
    return reply_message.raw_text if reply_message else ''


async def show_gplay_variants(
    event: NewMessage.Event | CallbackQuery.Event,
    package: str,
    *,
    reply_to_source: bool = False,
) -> None:
    buttons = [
        [
            Button.inline('ARM64', b'm|gplay|arm64'),
            Button.inline('ARMv7', b'm|gplay|armv7'),
            Button.inline('Both', b'm|gplay|both'),
        ]
    ]
    text = t('gplay_choose_variant', package=package)
    if isinstance(event, CallbackQuery.Event):
        await event.edit(text, buttons=buttons)
    elif reply_to_source:
        reply_message = await get_reply_message(event, previous=True)
        await event.client.send_message(
            event.chat_id,
            text,
            buttons=buttons,
            reply_to=reply_message.id if reply_message else event.message.id,
        )
    else:
        await event.reply(text, buttons=buttons)


async def gplay_entrypoint(event: NewMessage.Event | CallbackQuery.Event) -> None:
    command_input = (
        extract_gplay_command_input(event.message.raw_text or '')
        if isinstance(event, NewMessage.Event)
        else ''
    )
    input_text = await get_gplay_input_message(event)
    package = extract_gplay_package(input_text)
    if not package:
        if isinstance(event, CallbackQuery.Event):
            await event.answer(t('gplay_no_valid_package'), alert=True)
        else:
            await event.reply(t('gplay_no_valid_package'))
        return
    await show_gplay_variants(event, package, reply_to_source=not command_input)


def build_caption(
    download_path: Path, title: str, version: str, arch: str, files_count: int
) -> str:
    size = naturalsize(download_path.stat().st_size, binary=True)
    return (
        f'<b>{title}</b>\n'
        f'<code>{version}</code> - {arch_label(arch)} - {files_count} file(s) - {size}'
    )


async def download_gplay_selection(event: CallbackQuery.Event, variant: str) -> None:
    if not has_gplay_permission(event):
        await event.answer(t('gplay_not_allowed'), alert=True)
        return

    variants = GPLAY_VARIANTS.get(variant)
    if not variants:
        await event.answer(t('gplay_unknown_variant'), alert=True)
        return

    input_text = await get_gplay_input_message(event)
    package = extract_gplay_package(input_text)
    if not package:
        await event.answer(t('gplay_no_valid_package'), alert=True)
        return

    progress_message = await send_progress_message(
        event, t('gplay_starting_download', package=package)
    )

    async def update_status(text: str) -> None:
        await progress_message.edit(text)

    try:
        downloads = await download_gplay_variants(package, variants, status=update_status)
        for idx, download in enumerate(downloads, start=1):
            await progress_message.edit(t('uploading_item', index=idx, total=len(downloads)))
            await upload_file_and_cleanup(
                event,
                download.path,
                progress_message,
                force_document=True,
                caption=build_caption(
                    download.path,
                    download.info.title,
                    download.info.version,
                    download.info.arch,
                    download.files_count,
                ),
            )
        await progress_message.edit(t('gplay_download_complete', package=package))
    except GPlayError as e:
        await progress_message.edit(t('gplay_download_failed', error=f'{e!s}'))


class GPlay(ModuleBase):
    name = 'GPlay'
    description = t('_gplay_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'gplay': Command(
            handler=gplay_entrypoint,
            description=t('_gplay_description'),
            pattern=GPLAY_COMMAND_PATTERN,
            condition=lambda event, message: (
                is_admin_in_private(event, message) and has_gplay_input(event, message)
            ),
            is_applicable_for_reply=True,
        ),
    }

    async def handle(self, event: Any, command: str | None = None) -> bool:
        if isinstance(event, CallbackQuery.Event) and command and command.startswith('gplay|'):
            await download_gplay_selection(event, command.split('|', 1)[1])
            return True

        await gplay_entrypoint(event)
        return True
