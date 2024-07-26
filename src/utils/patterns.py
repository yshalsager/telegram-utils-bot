YOUTUBE_URL_PATTERN = (
    r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)'
    r'\/(?:watch\?v=)?(?:embed\/)?(?:v\/)?(?:shorts\/)?(?:live\/)?'
    r'(?:(?:watch\?)?(?:time_continue=(?:\d+))?\&?(?:v=))?([^\s&]+)'
)
HTTP_URL_PATTERN = (
    r'https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}'
    r'\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)'
)
