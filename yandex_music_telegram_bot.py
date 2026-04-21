from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from html import escape
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

try:
    from yandex_music import Client
except ImportError:
    Client = None


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)
URL_RE = re.compile(r"(?:https?://)?[^\s<>]+", re.IGNORECASE)
YANDEX_TRACK_ENDPOINT = "https://music.yandex.ru/handlers/track.jsx"
YANDEX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


@dataclass(frozen=True)
class TrackLink:
    track_id: str
    album_id: str

    @property
    def yandex_music_id(self) -> str:
        return f"{self.track_id}:{self.album_id}"


@dataclass(frozen=True)
class TrackInfo:
    title: str
    artists: str
    duration_ms: int


class YandexMusicService:
    def __init__(self, token: str | None = None) -> None:
        self._token = token
        self._client: Any | None = None
        self._lock = Lock()

    def _fetch_from_public_endpoint(self, link: TrackLink) -> TrackInfo:
        url = f"{YANDEX_TRACK_ENDPOINT}?{urlencode({'track': link.yandex_music_id})}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": YANDEX_USER_AGENT,
            },
        )

        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        track = payload.get("track")
        if not isinstance(track, dict):
            raise LookupError("Yandex Music response does not contain track data")

        title = track.get("title") or "Без названия"
        artists = ", ".join(
            artist.get("name", "") for artist in track.get("artists", []) if isinstance(artist, dict) and artist.get("name")
        )
        duration_ms = int(track.get("durationMs") or 0)

        return TrackInfo(
            title=title,
            artists=artists or "Неизвестный артист",
            duration_ms=duration_ms,
        )

    def _get_client(self) -> Any:
        if Client is None:
            raise RuntimeError("yandex-music package is not installed")

        if self._client is None:
            self._client = (Client(self._token) if self._token else Client()).init()
        return self._client

    def _fetch_from_library(self, link: TrackLink) -> TrackInfo:
        with self._lock:
            tracks = self._get_client().tracks([link.yandex_music_id])

        if not tracks:
            raise LookupError("Yandex Music did not return a track for this link")

        track = tracks[0]
        artists = ", ".join(artist.name for artist in (track.artists or []) if artist.name) or "Неизвестный артист"
        return TrackInfo(
            title=track.title or "Без названия",
            artists=artists,
            duration_ms=track.duration_ms or 0,
        )

    def fetch_track_info(self, link: TrackLink) -> TrackInfo:
        try:
            return self._fetch_from_public_endpoint(link)
        except Exception:
            LOGGER.warning("Yandex Music public endpoint failed", exc_info=True)

        try:
            return self._fetch_from_library(link)
        except Exception as library_error:
            LOGGER.warning("Yandex Music library fallback failed", exc_info=True)
            raise LookupError("Failed to fetch track from Yandex Music") from library_error


def extract_track_link(text: str) -> TrackLink | None:
    for match in URL_RE.finditer(text):
        raw_url = match.group(0).strip(".,!?;:()[]{}\"'")
        if "yandex." not in raw_url.lower() or "track" not in raw_url.lower():
            continue

        parsed = urlparse(raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}")
        if not parsed.netloc or "yandex." not in parsed.netloc.lower():
            continue

        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].lower() == "music":
            parts = parts[1:]

        for index in range(len(parts) - 3):
            if parts[index].lower() == "album" and parts[index + 2].lower() == "track":
                album_id = parts[index + 1]
                track_id = parts[index + 3]
                if album_id.isdigit() and track_id.isdigit():
                    return TrackLink(track_id=track_id, album_id=album_id)

    return None


def format_duration(duration_ms: int) -> str:
    total_seconds = round(duration_ms / 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def render_track_info(track: TrackInfo) -> str:
    return (
        f"<b>{escape(track.title)}</b>\n"
        f"Артист: {escape(track.artists)}\n"
        f"Длительность: {format_duration(track.duration_ms)}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message is None:
        return

    await update.effective_message.reply_text(
        "Пришлите ссылку на трек Яндекс.Музыки, например:\n"
        "https://music.yandex.ru/album/1193829/track/10994777"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return

    link = extract_track_link(message.text)
    if link is None:
        await message.reply_text("Не нашёл ссылку на трек Яндекс.Музыки. Нужна ссылка вида /album/.../track/...")
        return

    await message.chat.send_action(ChatAction.TYPING)
    service: YandexMusicService = context.application.bot_data["yandex_music_service"]

    try:
        track = await asyncio.to_thread(service.fetch_track_info, link)
    except Exception:
        LOGGER.exception("Failed to fetch Yandex Music track")
        await message.reply_text("Не получилось получить информацию о треке. Проверьте ссылку и попробуйте ещё раз.")
        return

    await message.reply_text(render_track_info(track), parse_mode=ParseMode.HTML)


def build_application(telegram_token: str) -> Application:
    builder = (
        Application.builder()
        .token(telegram_token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .get_updates_pool_timeout(30)
    )

    telegram_proxy_url = os.getenv("TELEGRAM_PROXY_URL")
    if telegram_proxy_url:
        builder = builder.proxy(telegram_proxy_url).get_updates_proxy(telegram_proxy_url)

    application = builder.build()
    application.bot_data["yandex_music_service"] = YandexMusicService(os.getenv("YANDEX_MUSIC_TOKEN"))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application


def main() -> None:
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable before starting the bot")

    build_application(telegram_token).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
