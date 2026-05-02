from unittest import TestCase

from auditok import AudioRegion
from src.utils.tafrigh_compat import patch_auditok_audio_region_meta


class TafriighCompatTest(TestCase):
    def test_patch_auditok_audio_region_meta_adds_tafrigh_expected_bounds(self) -> None:
        patch_auditok_audio_region_meta()

        region = AudioRegion(b'\0' * 16000, 16000, 2, 1, start=2.5)
        meta = object.__getattribute__(region, 'meta')

        assert meta.start == 2.5
        assert meta.end == 3.0
