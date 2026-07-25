import regex as re

YOUTUBE_URL_PATTERN = (
    r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)'
    r'\/(?:watch\?v=)?(?:embed\/)?(?:v\/)?(?:shorts\/)?(?:live\/)?'
    r'(?:(?:watch\?)?(?:time_continue=(?:\d+))?\&?(?:v=))?([^\s&]+)'
)
HTTP_URL_PATTERN = r'https?://[^\s<>"\']+'


def extract_urls(text: str) -> list[str]:
    return [match.group(0).rstrip('.,،)') for match in re.finditer(HTTP_URL_PATTERN, text)]
