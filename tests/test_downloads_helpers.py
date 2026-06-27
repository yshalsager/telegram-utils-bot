import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock

from src.utils.downloads import get_filename_from_url, resolve_upload_caption


class UploadCaptionTest(TestCase):
    def event(self, reply_message: object | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            message=SimpleNamespace(
                is_reply=reply_message is not None,
                get_reply_message=AsyncMock(return_value=reply_message),
            )
        )

    def caption(
        self, event: SimpleNamespace, output_file: str = 'out.mp4', caption: str = ''
    ) -> str:
        return asyncio.run(resolve_upload_caption(event, Path(output_file), caption))

    def test_explicit_caption_wins(self) -> None:
        assert self.caption(self.event(), caption='done') == 'done'

    def test_reply_caption_is_preserved(self) -> None:
        reply = SimpleNamespace(raw_text='original caption')

        assert self.caption(self.event(reply)) == 'original caption'

    def test_reply_filename_falls_back_before_output_name(self) -> None:
        reply = SimpleNamespace(raw_text='', file=SimpleNamespace(name='original.mp4'))

        assert self.caption(self.event(reply)) == '<code>original.mp4</code>'

    def test_bot_prompt_reply_is_not_used_as_caption(self) -> None:
        reply = SimpleNamespace(raw_text='Please provide a new filename.', out=True)

        assert self.caption(self.event(reply), 'renamed.epub') == '<code>renamed.epub</code>'

    def test_output_filename_is_final_fallback(self) -> None:
        assert self.caption(self.event()) == '<code>out.mp4</code>'


class UrlFilenameTest(TestCase):
    def test_url_filename_is_decoded(self) -> None:
        assert (
            get_filename_from_url(
                'https://example.com/files/%D9%85%D9%84%D9%81-%D8%B9%D8%B1%D8%A8%D9%8A.pdf'
            )
            == 'ملف-عربي.pdf'
        )
