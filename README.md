# Multi-Purpose Telegram Bot

[![Open Source Love](https://badges.frapsoft.com/os/v1/open-source.png?v=103)](https://github.com/ellerbrock/open-source-badges/)
[![made-with-python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)

[![PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?style=flat&labelColor=00457C&logo=PayPal&logoColor=white&link=https://www.paypal.me/yshalsager)](https://www.paypal.me/yshalsager)
[![Patreon](https://img.shields.io/badge/Patreon-Support-F96854?style=flat&labelColor=F96854&logo=Patreon&logoColor=white&link=https://www.patreon.com/XiaomiFirmwareUpdater)](https://www.patreon.com/XiaomiFirmwareUpdater)
[![Liberapay](https://img.shields.io/badge/Liberapay-Support-F6C915?style=flat&labelColor=F6C915&logo=Liberapay&logoColor=white&link=https://liberapay.com/yshalsager)](https://liberapay.com/yshalsager)

A versatile multilingual modular Telegram bot with multiple features.

## Features

### Bot Management

- User and permission management
- Plugin system with enable/disable functionality
- Task management and cancellation
- Help command for instructions
- Bot restart and update functionality
- Broadcast messages to all users

### File Management

- Download files from URLs using aria2
- Download / upload Telegram files
- Upload files as documents or media
- Rename Telegram files
- Create and extract zip archives
- List archive contents

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
- Create a video from audio, and photo or subtitles

### Web Interactions

- Search and retrieve Quran ayahs and Sunnah Hadiths
- Search web using DuckDuckGo
- Search Wikipedia in multiple languages
- YouTube (and all sites supported by ytdlp) interactions (download full/segment from audio/video, playlists, subtitles)

### Text, Document, and Audio Processing

- OCR (Optical Character Recognition) using Tesseract and Gemini
- PDF processing: text extraction, compression, page extraction, cropping, splitting, and merging
- Transcription (Wit/Whisper/Vosk + Gemini)
- Custom Gemini prompt on a replied file

### Utility Functions

- Execute shell commands
- View Telegram messages as JSON
- Generate MD5 hashes of Telegram files
- Create direct download links (planned)
- Instant Preview of web articles
- Readability extraction for web pages

## Usage

- Start the bot by sending `/start` in private or adding it to a group.
- Use `/help` to get a list of available commands and their usage.
- For file conversions, simply send a file to the bot and follow the prompts.
- Use inline queries for web searches by typing `@your_bot_username` followed by your search query. You can list all
  inline command using `@your_bot_username help`.

## User plugins

You can drop your own plugins (or `git clone` them) into `state/plugins/` and they will be loaded on startup.

- Put plugin files directly in the folder: `state/plugins/my_plugin.py`
- Plugin filenames must be valid Python module names (letters/numbers/underscore), e.g. `my_plugin.py`

Plugin modules should define `ModuleBase` subclasses with `IS_MODULE = True` (same pattern as built-in plugins in
`src/modules/plugins/`).

## Setup

Before setting up the bot:

1. Create a `.env` and fill in the required information as defined in `mise.toml`:

```dotenv
   API_ID="1234567"
   API_HASH="0123456789abcdef0123456789abcdef"
   BOT_TOKEN="1234567890:abcdefghijklmnopqrstuvwxyz0123456789"
   BOT_ADMINS='123456,123456,123456'
   # Optional (AI/Gemini)
   LLM_GEMINI_KEY="..."
   # Optional (Whisper via Groq)
   GROQ_API_KEY="..."
   # Optional (tafrigh wit)
   WIT_CLIENT_ACCESS_TOKENS="..."
```

### Using Docker (Recommended)

1. Make sure you have Docker and Docker Compose installed.
2. Clone this repository.
3. Run the following command in the project directory:

```bash
docker compose up --build -d
```

### Without Docker

1. Install system dependencies:
    - FFmpeg
    - aria2
    - Any other system-level dependencies (refer to the Dockerfile for a complete list)
2. Install tools + Python (recommended, Python 3.13+):
    - `mise install`
3. Install Python dependencies:
    - `uv sync --dev`
4. Run the bot:

```bash
uv run -m src
```

## Acknowledgements

### Libraries, Tools, etc

- [Telethon Library](https://github.com/LonamiWebs/Telethon/)
- [FFmpeg](https://ffmpeg.org/)
- [aria2](https://aria2.github.io/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [orjson](https://github.com/ijl/orjson/)
- [regex](https://github.com/mrabarnett/mrab-regex)
- [search-engine-parser](https://github.com/bisohns/search-engine-parser)
- [Vosk](https://github.com/alphacep/vosk-api)
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF)
- [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF)
- [tafrigh](https://github.com/ieasybooks/tafrigh)
- [tahweel](https://github.com/ieasybooks/tahweel)
- [Plate](https://github.com/delivrance/plate)

## Resources

- [Quran.com](https://quran.com/)
- [Sunnah.one](https://sunnah.one/)
- [Exchange Rate API](https://exchangerate-api.com/)

## Development

This project uses several tools to streamline the development process:

### mise

[mise](https://mise.jdx.dev/) is used for managing project-level dependencies and environment variables. mise helps
ensure consistent development environments across different machines.

To get started with mise:

1. Install mise by following the instructions on the [official website](https://mise.jdx.dev/).
2. Run `mise install` in the project root to set up the development environment.

### UV

[UV](https://docs.astral.sh/uv/) is used for dependency management and packaging. It provides a clean,
version-controlled way to manage project dependencies.

To set up the project with UV:

1. Install UV by following the instructions on the [official website](https://docs.astral.sh/uv/getting-started/installation/).
2. Run `uv sync` to install project dependencies.

### Jurigged for Live Reload

[Jurigged](https://github.com/breuleux/jurigged) is used for live code reloading during development. This allows you to
see changes in your code immediately without manually restarting the application.

To use Jurigged:

1. Make sure you have installed the project dependencies using UV, including dev
   dependencies `uv sync --dev`.
2. Run the bot with Jurigged:

```bash
uv run jurigged -v -m src
```

## Internationalization (i18n)

- [Plate](https://github.com/delivrance/plate) library is used to translate the bot's messages.
- Translations are stored as JSON files in the `src/i18n/locales` directory, the default locale is `en_US`.
- To add a new language, create a new JSON file in the `src/i18n/locales` directory, with the corresponding language
  code, and translate the messages to that language.
- Set the `BOT_LANGUAGE` environment variable to the desired language code.
