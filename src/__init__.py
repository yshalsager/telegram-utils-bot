"""Bot initialization"""

import logging.config
from os import getenv
from pathlib import Path

# paths
WORK_DIR = Path(__package__)
PARENT_DIR = WORK_DIR.parent

# bot config
IS_DEBUG: bool = getenv('DEBUG', '').lower() in ('true', '1')
BOT_TOKEN = getenv('BOT_TOKEN')
API_ID = getenv('API_ID')
API_HASH = getenv('API_HASH')
BOT_ADMINS = [
    int(admin_str.strip()) for admin_str in getenv('BOT_ADMINS', '').split(',') if admin_str
] or []

# Logging
log_file_path = PARENT_DIR / 'bot.log'
logging_config = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'detailed': {
            'format': '%(asctime)s [%(levelname)s] %(name)s [%(module)s.%(funcName)s:%(lineno)d]: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'level': 'INFO',
            'formatter': 'detailed',
            'filename': log_file_path,
            'when': 'midnight',
            'interval': 1,
            'backupCount': 7,
        },
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'detailed',
            'stream': 'ext://sys.stdout',
        },
    },
    'loggers': {
        '': {  # root logger
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
logging.config.dictConfig(logging_config)
