"""Bot Admin module."""

import logging
from asyncio import sleep
from os import execl, getenv
from pathlib import Path
from shutil import copy2, copytree, rmtree, which
from sys import executable
from tempfile import TemporaryDirectory
from typing import ClassVar
from zipfile import BadZipFile, ZipFile

import aiohttp
import orjson
import regex as re
from telethon.errors import FloodWaitError
from telethon.events import NewMessage

from src import PARENT_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private, is_reply_in_private
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import edit_or_send_as_file

DEFAULT_UPDATE_REPO_URL = 'https://github.com/yshalsager/telegram-utils-bot.git'
DEFAULT_UPDATE_REPO_BRANCH = 'master'


def build_github_archive_url(repo_url: str, branch: str) -> str:
    normalized_repo_url = repo_url.strip()
    if normalized_repo_url.startswith('git@github.com:'):
        normalized_repo_url = f'https://github.com/{normalized_repo_url.split(":", 1)[1]}'
    if normalized_repo_url.endswith('.git'):
        normalized_repo_url = normalized_repo_url[:-4]
    if not normalized_repo_url.startswith('https://github.com/'):
        raise ValueError(f'Unsupported UPDATE_REPO_URL: {repo_url}')

    repo_path = normalized_repo_url.removeprefix('https://github.com/').strip('/')
    if not repo_path:
        raise ValueError(f'Unsupported UPDATE_REPO_URL: {repo_url}')
    return f'https://codeload.github.com/{repo_path}/zip/refs/heads/{branch}'


async def download_update_archive(archive_url: str, archive_path: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=300)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.get(archive_url) as response,
    ):
        if response.status != 200:
            body = (await response.text())[:1000]
            raise RuntimeError(f'HTTP {response.status}\n{body}')
        with archive_path.open('wb') as archive_file:
            async for chunk in response.content.iter_chunked(1024 * 1024):
                archive_file.write(chunk)


def get_checkout_dir_from_archive(extract_dir: Path) -> Path:
    extracted_dirs = [path for path in extract_dir.iterdir() if path.is_dir()]
    if not extracted_dirs:
        raise RuntimeError('Archive has no extracted directory')
    checkout_dir = extracted_dirs[0]
    if not (checkout_dir / 'src').exists():
        raise RuntimeError('Archive missing src directory')
    return checkout_dir


def apply_checkout_files(checkout_dir: Path) -> list[str]:
    rmtree(PARENT_DIR / 'src', ignore_errors=True)
    copytree(checkout_dir / 'src', PARENT_DIR / 'src', dirs_exist_ok=True)

    partial_copy_failures: list[str] = []
    for file_name in ('pyproject.toml', 'uv.lock'):
        source_file = checkout_dir / file_name
        target_file = PARENT_DIR / file_name
        try:
            copy2(source_file, target_file)
        except OSError as e:
            partial_copy_failures.append(file_name)
            logging.warning(f'Failed to copy {file_name} while updating: {e}')
    return partial_copy_failures


async def update_from_archive(repo_url: str, branch: str) -> list[str]:
    archive_url = build_github_archive_url(repo_url, branch)
    with TemporaryDirectory() as temp_dir:
        archive_path = Path(temp_dir) / 'source.zip'
        extract_dir = Path(temp_dir) / 'extract'
        await download_update_archive(archive_url, archive_path)
        with ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)
        checkout_dir = get_checkout_dir_from_archive(extract_dir)
        return apply_checkout_files(checkout_dir)


async def restart(event: NewMessage.Event) -> None:
    """Restart the bot."""
    restart_message = await event.reply(t('restarting_please_wait'))
    Path('restart.json').write_text(
        orjson.dumps({'chat': restart_message.chat_id, 'message': restart_message.id}).decode()
    )
    execl(executable, executable, '-m', 'src')  # noqa: S606


async def update(event: NewMessage.Event) -> None:  # noqa: PLR0911
    """Update the bot."""
    message = await event.reply(t('updating_please_wait'))
    has_git_checkout = (PARENT_DIR / '.git').exists()
    has_git_binary = which('git') is not None
    if not (has_git_checkout and has_git_binary):
        repo_url = getenv('UPDATE_REPO_URL') or DEFAULT_UPDATE_REPO_URL
        branch = getenv('UPDATE_REPO_BRANCH') or DEFAULT_UPDATE_REPO_BRANCH
        await message.edit(t('gitless_update_fetching_source', repo_url=repo_url, branch=branch))
        try:
            partial_copy_failures = await update_from_archive(repo_url, branch)
        except (ValueError, RuntimeError, BadZipFile) as e:
            await message.edit(f'{t("failed_to_fetch_update_source")}:\n<pre>{e}</pre>')
            return None
        except OSError as e:
            await message.edit(f'{t("failed_to_update")}:\n<pre>{e}</pre>')
            return None

        if partial_copy_failures:
            await message.edit(
                f'{t("update_partial_copy_warning", files=", ".join(partial_copy_failures))}\n'
                f'{t("source_update_successful_updating_requirements")}'
            )
        else:
            await message.edit(t('source_update_successful_updating_requirements'))
    else:
        output, code = await run_command('git pull --rebase', cwd=PARENT_DIR)
        if code and code != 0:
            await message.edit(f'{t("failed_to_update")}:\n<pre>{output}</pre>')
            return None
        if output.strip() == 'Already up to date.':
            await message.edit(t('already_up_to_date'))
            return None
        await message.edit(
            f'{t("git_update_successful_updating_requirements")}\n<pre>{output}</pre>'
        )

    output, code = await run_command('uv sync --frozen --no-cache', cwd=PARENT_DIR)
    if code and code != 0:
        await edit_or_send_as_file(
            event, message, f'{t("failed_to_update_requirements")}:\n<pre>{output}</pre>'
        )
        return None

    await message.edit(t('syncing_plugin_dependencies'))
    output, code = await run_command(
        f'{executable} -m src.utils.plugin_deps_sync',
        cwd=PARENT_DIR,
    )
    if code and code != 0:
        await edit_or_send_as_file(
            event, message, f'{t("failed_to_update_requirements")}:\n<pre>{output}</pre>'
        )
        return None

    await message.edit(t('updated_successfully'))
    return await restart(event)


async def broadcast(event: NewMessage.Event) -> None:
    """Broadcast a message to all bot users."""
    permission_manager = event.client.permission_manager
    users = list({i for _ in permission_manager.module_permissions.values() for i in _})
    if not users:
        return

    success_count = 0
    fail_count = 0
    reply_message = await event.get_reply_message()
    progress_message = await event.reply(t('broadcasting_message'))
    users_count = len(users)

    for user_id in users:
        try:
            await event.client.send_message(user_id, reply_message)
        except FloodWaitError as e:
            await sleep(e.seconds + 1)
            await event.client.send_message(user_id, reply_message)
        except Exception as e:  # noqa: BLE001
            logging.error(f'Error broadcasting message to {user_id}: {e}')
            fail_count += 1

        success_count += 1
        if (success_count + fail_count) % 5 == 0:
            await progress_message.edit(
                t('broadcasting_progress', progress=success_count + fail_count, total=users_count)
            )
        await sleep(0.25)

    await progress_message.edit(
        t(
            'broadcasting_completed',
            success=success_count,
            failed=fail_count,
            total=users_count,
        )
    )


class Admin(ModuleBase):
    name = 'Admin'
    description = t('_admin_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'broadcast': Command(
            handler=broadcast,
            description=t('_broadcast_description'),
            pattern=re.compile(r'^/broadcast$'),
            condition=lambda e, m: is_admin_in_private(e, m) and is_reply_in_private(e, m),
        ),
        'restart': Command(
            handler=restart,
            description=t('_restart_description'),
            pattern=re.compile(r'^/restart$'),
            condition=is_admin_in_private,
        ),
        'update': Command(
            handler=update,
            description=t('_update_description'),
            pattern=re.compile(r'^/update$'),
            condition=is_admin_in_private,
        ),
    }
