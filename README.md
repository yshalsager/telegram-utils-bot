# Multi-Purpose Telegram Bot

[![Open Source Love](https://badges.frapsoft.com/os/v1/open-source.png?v=103)](https://github.com/ellerbrock/open-source-badges/)
[![made-with-python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)

[![PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?style=flat&labelColor=00457C&logo=PayPal&logoColor=white&link=https://www.paypal.me/yshalsager)](https://www.paypal.me/yshalsager)
[![Patreon](https://img.shields.io/badge/Patreon-Support-F96854?style=flat&labelColor=F96854&logo=Patreon&logoColor=white&link=https://www.patreon.com/XiaomiFirmwareUpdater)](https://www.patreon.com/XiaomiFirmwareUpdater)
[![Liberapay](https://img.shields.io/badge/Liberapay-Support-F6C915?style=flat&labelColor=F6C915&logo=Liberapay&logoColor=white&link=https://liberapay.com/yshalsager)](https://liberapay.com/yshalsager)

A versatile modular Telegram bot with multiple features.

## Features

### Bot Management

- User and permission management
- Plugin system with enable/disable functionality
- Task management and cancellation
- Help command for instructions
- Bot restart and update functionality

### File Management

- Download files from URLs using aria2
- Download / upload Telegram files
- Upload files as documents or media
- Rename Telegram files

### Audio Processing

- Convert audio to voice messages
- Convert between audio formats
- Compress audio files
- Cut, split, and merge audio files
- Increase audio volume
- Set title and artist metadata
- Remove silence from audio

### Video Processing

- Remove audio from videos
- Display video information
- Compress videos
- Encode videos to x265 format
- Extract video thumbnails
- Scale and resize videos
- Cut, split, and merge videos
- Extract subtitles from videos
- Replace audio in videos
- Convert videos to different formats

### Web Interactions

- Search and retrieve Quran ayahs and Sunnah Hadiths
- Search web using DuckDuckGo
- Search Wikipedia in multiple languages
- YouTube interactions (download audio/video, playlists, subtitles - planned)

### Text, Document, and Audio Processing

- OCR (Optical Character Recognition) using Tesseract and Google (planned)
- PDF processing: text extraction, compression, page extraction (planned)
- Transcription (planned)

### Utility Functions

- Execute shell commands
- View Telegram messages as JSON
- Generate MD5 hashes of Telegram files
- Create direct download links (planned)

## Usage

- Start the bot by sending `/start` in private or adding it to a group.
- Use `/commands` to get a list of available commands and their usage.
- For file conversions, simply send a file to the bot and follow the prompts.
- Use inline queries for web searches by typing `@your_bot_username` followed by your search query. You can list all
  inline command using `@your_bot_username commands`.

## Setup

Before setting up the bot:

1. Create a `.env` and fill in the required information as defined in [mise.toml] env section:

```dotenv
   API_ID="1234567"
   API_HASH="0123456789abcdef0123456789abcdef"
   BOT_TOKEN="1234567890:abcdefghijklmnopqrstuvwxyz0123456789"
   BOT_ADMINS='123456,123456,123456'
```

### Using Docker (Recommended)

1. Make sure you have Docker and Docker Compose installed.
2. Clone this repository.
3. Run the following command in the project directory:

```bash
docker-compose up --build -d
```

### Without Docker

1. Ensure you have Python 3.12+ and pip v19+ or poetry installed.
2. Clone the repository.
3. Install dependencies:
    - Using poetry: `poetry install`
    - Using pip: `pip install .`
4. Install system dependencies:
    - FFmpeg
    - aria2
    - Any other system-level dependencies (refer to the Dockerfile for a complete list)
5. Run the bot:

```bash
python3 -m src
```

## Acknowledgements

### Libraries, Tools, etc

- [Telethon Library](https://github.com/LonamiWebs/Telethon/)
- [FFmpeg](https://ffmpeg.org/)
- [aria2](https://aria2.github.io/)

## Resources

- [Quran.com](https://quran.com/)
- [Sunnah.one](https://sunnah.one/)
- [Exchange Rate API](https://exchangerate-api.com/)
