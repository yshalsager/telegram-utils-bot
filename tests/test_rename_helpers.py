from src.modules.plugins.rename import is_valid_filename


def test_filename_validation() -> None:
    assert is_valid_filename('renamed.epub')
    assert not is_valid_filename('../renamed.epub')
    assert not is_valid_filename('dir/renamed.epub')
    assert not is_valid_filename('dir\\renamed.epub')
    assert not is_valid_filename('x' * 256)
