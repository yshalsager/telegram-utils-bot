from types import SimpleNamespace

from auditok import AudioRegion
from tafrigh.cli import main as tafrigh_main


def patch_auditok_audio_region_meta() -> None:
    if hasattr(AudioRegion, 'meta'):
        return

    @property
    def meta(self: AudioRegion) -> SimpleNamespace:
        start = self.start or 0
        return SimpleNamespace(start=start, end=start + self.seconds.len)

    type.__setattr__(AudioRegion, 'meta', meta)


def main() -> int | None:
    patch_auditok_audio_region_meta()

    return tafrigh_main()


if __name__ == '__main__':
    raise SystemExit(main())
