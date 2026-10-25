from asyncio import Lock, sleep, timeout
from pathlib import Path
from shlex import quote
from uuid import uuid4

import aiohttp
import regex as re

from src.utils.run import run_command

API_BASE = 'https://arabi.tools'
MAX_UPLOAD_BYTES = 99_000_000
arabi_lock = Lock()
arabi_attempt = uuid4().hex


async def transcribe_with_arabi(input_file: Path, output_dir: Path, language: str) -> None:  # noqa: C901
    duration, code = await run_command(
        f'ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 {quote(str(input_file))}'
    )
    if code or not 0 < float(duration) <= 3 * 60 * 60:
        raise RuntimeError(duration if code else 'arabi.tools accepts recordings up to 3 hours.')

    audio = input_file
    if audio.suffix.lower() not in ('.mp3', '.m4a') or audio.stat().st_size > MAX_UPLOAD_BYTES:
        audio = output_dir / 'arabi.m4a'
        output, code = await run_command(
            f'ffmpeg -hide_banner -y -i {quote(str(input_file))} -vn -ac 1 -c:a aac -b:a 48k {quote(str(audio))}'
        )
        if code:
            raise RuntimeError(output)
    if audio.stat().st_size > MAX_UPLOAD_BYTES:
        raise RuntimeError('arabi.tools accepts files up to 99 MB.')

    async with (
        arabi_lock,
        timeout(60 * 60),
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session,
    ):
        while True:
            async with session.post(
                f'{API_BASE}/transcribe/jobs', data={'attempt': arabi_attempt}
            ) as response:
                if response.status != 429:
                    response.raise_for_status()
                    upload = (await response.json())['upload']
                    break
            await sleep(5)
        if not re.fullmatch(r'/transcribe/jobs/[a-f0-9]{32}/audio', upload):
            raise RuntimeError('Unexpected arabi.tools upload URL.')
        job = upload.removesuffix('/audio')
        form = aiohttp.FormData()
        form.add_field('language', language)
        with audio.open('rb') as source:
            form.add_field('file', source, filename=audio.name)
            async with session.post(f'{API_BASE}{upload}', data=form) as response:
                response.raise_for_status()
                await response.read()

        while True:
            async with session.get(f'{API_BASE}{job}') as response:
                response.raise_for_status()
                page = await response.text()
            state = re.search(r'class="job" data-state="(waiting|running|done|failed)"', page)
            if not state or state[1] == 'failed':
                raise RuntimeError('arabi.tools transcription failed or expired.')
            if state[1] == 'done':
                break
            await sleep(5)

        for extension in ('txt', 'srt'):
            async with session.get(f'{API_BASE}{job}/transcript.{extension}') as response:
                response.raise_for_status()
                (output_dir / f'{input_file.stem}.{extension}').write_text(await response.text())
