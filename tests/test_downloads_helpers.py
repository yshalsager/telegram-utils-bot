from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from src.utils.downloads import resolve_upload_caption


class UploadCaptionTest(TestCase):
    def event(self, reply_message: object | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            message=SimpleNamespace(
                is_reply=reply_message is not None,
                reply_to=SimpleNamespace(reply_message=reply_message),
            )
        )

    def test_explicit_caption_wins(self) -> None:
        assert resolve_upload_caption(self.event(), Path('out.mp4'), 'done') == 'done'

    def test_reply_caption_is_preserved(self) -> None:
        reply = SimpleNamespace(raw_text='original caption')

        assert resolve_upload_caption(self.event(reply), Path('out.mp4')) == 'original caption'

    def test_reply_filename_falls_back_before_output_name(self) -> None:
        reply = SimpleNamespace(raw_text='', file=SimpleNamespace(name='original.mp4'))

        assert (
            resolve_upload_caption(self.event(reply), Path('out.mp4'))
            == '<code>original.mp4</code>'
        )

    def test_output_filename_is_final_fallback(self) -> None:
        assert resolve_upload_caption(self.event(), Path('out.mp4')) == '<code>out.mp4</code>'
