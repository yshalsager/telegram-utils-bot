# ruff: noqa: PT027
from asyncio import CancelledError, Event, create_task, gather
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from src.utils.arabi_tools import transcribe_with_arabi

JOB = f'/transcribe/jobs/{"a" * 32}'


class ArabiToolsTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.output_dir = Path(self.stack.enter_context(TemporaryDirectory()))
        self.input_file = self.output_dir / 'درس.mp3'
        self.input_file.write_bytes(b'audio')
        self.states = ['waiting', 'running', 'done']
        self.text = 'نص التفريغ'
        self.requests = []
        self.upload = f'{JOB}/audio'
        self.reserve_status = 200
        self.attempts = []
        self.reserved, self.release = Event(), Event()
        self.release.set()
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', self.handle)
        server = await self.stack.enter_async_context(TestServer(app))
        self.stack.enter_context(
            patch('src.utils.arabi_tools.API_BASE', str(server.make_url('')).rstrip('/'))
        )
        self.probe = self.stack.enter_context(
            patch('src.utils.arabi_tools.run_command', AsyncMock(return_value=('60', 0)))
        )
        self.sleep = self.stack.enter_context(patch('src.utils.arabi_tools.sleep', AsyncMock()))

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append((request.method, request.path))
        if request.path == '/transcribe/jobs':
            attempt = (await request.post())['attempt']
            assert len(attempt) == 32
            self.attempts.append(attempt)
            self.reserved.set()
            await self.release.wait()
            return web.json_response({'upload': self.upload}, status=self.reserve_status)
        if request.path == f'{JOB}/audio':
            form = await request.post()
            assert form['language'] == 'ar'
            assert form['file'].file.read() == b'audio'
            assert form['file'].filename.endswith(('.mp3', '.m4a'))
            return web.json_response({'next': JOB})
        if request.path == JOB:
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return web.Response(text=f'<div class="job" data-state="{state}">')
        assert request.path in (f'{JOB}/transcript.txt', f'{JOB}/transcript.srt')
        return web.Response(text=self.text)

    async def test_upload_polls_job_page_and_downloads_both_outputs(self) -> None:
        await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')

        assert self.requests == [
            ('POST', '/transcribe/jobs'),
            ('POST', f'{JOB}/audio'),
            *[('GET', JOB)] * 3,
            ('GET', f'{JOB}/transcript.txt'),
            ('GET', f'{JOB}/transcript.srt'),
        ]
        assert self.sleep.await_count == 2
        for extension in ('txt', 'srt'):
            assert self.input_file.with_suffix(f'.{extension}').read_text() == self.text

    async def test_converts_video_before_upload(self) -> None:
        self.input_file = self.input_file.rename(self.input_file.with_suffix('.mp4'))

        async def convert(command: str) -> tuple[str, int]:
            if command.startswith('ffprobe'):
                return '60', 0
            assert '-vn -ac 1 -c:a aac -b:a 48k' in command
            (self.output_dir / 'arabi.m4a').write_bytes(b'audio')
            return '', 0

        self.probe.side_effect = convert
        await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert self.probe.await_count == 2

    async def test_rejects_invalid_duration_and_failed_conversion_before_http(self) -> None:
        for duration in ('10801', '0', 'nan'):
            with self.subTest(duration=duration):
                self.probe.return_value = (duration, 0)
                with self.assertRaisesRegex(RuntimeError, '3 hours'):
                    await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        self.input_file = self.input_file.rename(self.input_file.with_suffix('.ogg'))
        self.probe.side_effect = [('60', 0), ('conversion failed', 1)]
        with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
            await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert not self.requests

    async def test_rejects_oversized_converted_audio_before_http(self) -> None:
        (self.output_dir / 'arabi.m4a').write_bytes(b'audio')
        self.probe.side_effect = [('60', 0), ('', 0)]
        with (
            patch('src.utils.arabi_tools.MAX_UPLOAD_BYTES', 4),
            self.assertRaisesRegex(RuntimeError, '99 MB'),
        ):
            await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert not self.requests

    async def test_serializes_concurrent_jobs(self) -> None:
        self.states = ['done']
        await gather(
            *(transcribe_with_arabi(self.input_file, self.output_dir, 'ar') for _ in range(2))
        )
        assert self.requests[:5] == self.requests[5:]
        assert len(self.requests) == 10

    async def test_busy_service_waits_before_uploading(self) -> None:
        self.reserve_status = 429
        self.states = ['done']
        self.sleep.side_effect = lambda _: setattr(self, 'reserve_status', 200)
        await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        self.sleep.assert_awaited_once_with(5)
        assert self.requests[:3] == [('POST', '/transcribe/jobs')] * 2 + [('POST', self.upload)]
        assert self.attempts[0] == self.attempts[1]

    async def test_cancelled_reservation_reuses_attempt_on_next_job(self) -> None:
        self.release.clear()
        task = create_task(transcribe_with_arabi(self.input_file, self.output_dir, 'ar'))
        await self.reserved.wait()
        task.cancel()
        with self.assertRaises(CancelledError):
            await task
        self.release.set()
        self.states = ['done']
        await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert len(self.attempts) == 2
        assert self.attempts[0] == self.attempts[1]

    async def test_refuses_unexpected_upload_url(self) -> None:
        self.upload = 'https://example.com/audio'
        with self.assertRaisesRegex(RuntimeError, 'upload URL'):
            await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert self.requests == [('POST', '/transcribe/jobs')]

    async def test_failed_or_changed_status_page_does_not_download(self) -> None:
        for state in ('failed', 'unknown'):
            with self.subTest(state=state):
                self.states = [state]
                with self.assertRaisesRegex(RuntimeError, 'failed or expired'):
                    await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert not list(self.output_dir.glob('*.txt'))
        assert not list(self.output_dir.glob('*.srt'))

    async def test_silence_keeps_empty_outputs(self) -> None:
        self.states = ['done']
        self.text = ''
        await transcribe_with_arabi(self.input_file, self.output_dir, 'ar')
        assert self.input_file.with_suffix('.txt').read_text() == ''
        assert self.input_file.with_suffix('.srt').read_text() == ''
