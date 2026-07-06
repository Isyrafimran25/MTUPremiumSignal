# -*- coding: utf-8 -*-
# Channel Forwarder -- MTU Premium
# ---------------------------------------------------------------------------
# Auto-copy setiap post baru dari Channel A (sumber) ke Channel B (destinasi).
#
# Sebab Channel A milik orang lain (anda subscriber sahaja), Bot API biasa
# TAK boleh baca mesej di situ. Jadi kita guna USERBOT (Telethon) yang login
# guna akaun Telegram peribadi anda -- akaun tu boleh baca semua channel yang
# anda subscribe.
#
# Mesej dihantar sebagai COPY (re-send) -- TIADA tag "Forwarded from".
# Sokongan: teks, gambar, video, dokumen, sticker, dan album (grouped media).
#
# Setup: baca FORWARDER_SETUP.md
# ---------------------------------------------------------------------------

import os
import sys
import asyncio

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage


# ── Secrets / Config ────────────────────────────────────────────────────────
def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"[FATAL] Environment variable '{name}' tak diset. Baca FORWARDER_SETUP.md")
        sys.exit(1)
    return val

API_ID      = int(_require("TELEGRAM_API_ID"))
API_HASH    = _require("TELEGRAM_API_HASH")
SESSION_STR = _require("TELEGRAM_SESSION")

# Channel A (sumber) -- boleh lebih dari satu, pisah dengan koma.
# Guna @username, atau ID numerik (contoh -1001234567890), atau link t.me/xxx.
SOURCE_CHANNELS_RAW = _require("SOURCE_CHANNELS")

# Channel B (destinasi) -- tempat post akan di-copy.
# Fallback ke TELEGRAM_CHANNEL_ID supaya boleh guna balik channel bot signal.
DEST_CHANNEL_RAW = os.environ.get("DEST_CHANNEL", "").strip() \
    or os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
if not DEST_CHANNEL_RAW:
    print("[FATAL] Set DEST_CHANNEL (atau TELEGRAM_CHANNEL_ID). Baca FORWARDER_SETUP.md")
    sys.exit(1)


def _parse_channel(raw: str):
    """Tukar string channel kepada bentuk yang Telethon boleh guna.

    Terima: @username, username, https://t.me/username, atau ID numerik.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Link t.me/xxx -> ambil bahagian akhir
    if "t.me/" in raw:
        raw = raw.split("t.me/")[-1].strip("/")
    # ID numerik (channel biasanya -100...)
    if raw.lstrip("-").isdigit():
        return int(raw)
    # Username
    return raw if raw.startswith("@") else "@" + raw


SOURCE_CHANNELS = [_parse_channel(c) for c in SOURCE_CHANNELS_RAW.split(",") if c.strip()]
DEST_CHANNEL    = _parse_channel(DEST_CHANNEL_RAW)

print("Channel Forwarder -- MTU Premium")
print(f"  Sumber (A) : {SOURCE_CHANNELS}")
print(f"  Destinasi(B): {DEST_CHANNEL}")


# ── Telethon client ─────────────────────────────────────────────────────────
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)


async def _copy_single(message):
    """Copy satu mesej (bukan album) ke Channel B tanpa tag forward."""
    has_media = message.media is not None and not isinstance(message.media, MessageMediaWebPage)

    if has_media:
        # Re-upload media + caption. Guna formatting_entities supaya bold/link kekal.
        await client.send_file(
            DEST_CHANNEL,
            message.media,
            caption=message.message or "",
            formatting_entities=message.entities or None,
        )
    elif message.message:
        # Teks sahaja (link preview dikekalkan kalau ada webpage preview).
        await client.send_message(
            DEST_CHANNEL,
            message.message,
            formatting_entities=message.entities or None,
            link_preview=isinstance(message.media, MessageMediaWebPage),
        )
    else:
        # Tiada teks & tiada media yang boleh dicopy -- langkau.
        print("  (langkau: mesej tiada teks/media yang boleh dicopy)")


@client.on(events.Album(chats=SOURCE_CHANNELS))
async def on_album(event):
    """Album (beberapa gambar/video dalam satu post) -- hantar sekali gus."""
    try:
        media   = [m.media for m in event.messages if m.media]
        caption = next((m.message for m in event.messages if m.message), "")
        entities = next((m.entities for m in event.messages if m.message and m.entities), None)
        await client.send_file(
            DEST_CHANNEL,
            media,
            caption=caption,
            formatting_entities=entities,
        )
        print(f"[OK] Album ({len(media)} item) di-copy ke Channel B")
    except Exception as e:
        print(f"[ERR] Gagal copy album: {e}")


@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def on_new_message(event):
    """Post baru (bukan album) dari Channel A -> copy ke Channel B."""
    # Album dikendalikan oleh handler khas di atas -- elak hantar dua kali.
    if event.message.grouped_id:
        return
    try:
        await _copy_single(event.message)
        preview = (event.message.message or "[media]")[:60].replace("\n", " ")
        print(f"[OK] Post di-copy: {preview}")
    except Exception as e:
        print(f"[ERR] Gagal copy post: {e}")


async def main():
    await client.start()
    me = await client.get_me()
    print(f"Login sebagai: {me.first_name} (@{me.username})")

    # Sahkan semua channel boleh diakses (bagi error awal kalau salah id/username).
    for ch in SOURCE_CHANNELS:
        try:
            ent = await client.get_entity(ch)
            print(f"  Sumber OK   : {getattr(ent, 'title', ch)}")
        except Exception as e:
            print(f"  [WARN] Tak jumpa channel sumber '{ch}': {e}")
    try:
        dest_ent = await client.get_entity(DEST_CHANNEL)
        print(f"  Destinasi OK: {getattr(dest_ent, 'title', DEST_CHANNEL)}")
    except Exception as e:
        print(f"  [WARN] Tak jumpa channel destinasi '{DEST_CHANNEL}': {e}")

    print("Forwarder aktif. Menunggu post baru dari Channel A...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        print("\nForwarder dihentikan.")
