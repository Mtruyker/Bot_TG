from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from html import escape
from threading import Lock
from urllib.parse import urlparse

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from yandex_music import Client


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)
URL_RE = re.compile(r"(?:https?://)?[^\s<>]+", re.IGNORECASE)


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
        self._client: Client | None = None
        self._lock = Lock()

    def _get_client(self) -> Client:
        if self._client is None:
            self._client = (Client(self._token) if self._token else Client()).init()
        return self._client

    def fetch_track_info(self, link: TrackLink) -> TrackInfo:
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
