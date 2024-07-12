"""Bot initialization"""

import logging
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
log_date_format = '%Y-%m-%d %H:%M:%S'
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s [%(module)s.%(funcName)s:%(lineno)d]: %(message)s',
    level=logging.INFO,
    datefmt=log_date_format,
)
