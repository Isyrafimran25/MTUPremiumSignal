# -*- coding: utf-8 -*-
# Generate Telegram session string (JALANKAN SEKALI SAHAJA, di komputer sendiri)
# ---------------------------------------------------------------------------
# Skrip ni akan login guna akaun Telegram peribadi anda dan cetak satu
# "session string". Salin string tu dan letak dalam environment variable
# TELEGRAM_SESSION di server (Railway/Heroku/dll).
#
# Cara guna (di terminal komputer anda):
#   1. pip install telethon
#   2. Dapatkan API_ID & API_HASH dari https://my.telegram.org -> API development tools
#   3. python generate_session.py
#   4. Masukkan API ID, API HASH, no. telefon, dan kod OTP yang Telegram hantar.
#   5. Salin session string yang dicetak -> set sebagai TELEGRAM_SESSION.
#
# ⚠️ JANGAN kongsi session string ni dengan sesiapa -- ia akses penuh ke akaun anda.
# ---------------------------------------------------------------------------

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

print("=== Generate Telegram Session String ===")
print("Dapatkan API ID & HASH dari https://my.telegram.org\n")

api_id   = int(input("API ID   : ").strip())
api_hash = input("API HASH : ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session_string = client.session.save()
    me = client.get_me()
    print("\n" + "=" * 60)
    print(f"Login berjaya sebagai: {me.first_name} (@{me.username})")
    print("=" * 60)
    print("\nTELEGRAM_SESSION (salin baris di bawah):\n")
    print(session_string)
    print("\n⚠️  Rahsiakan string ni. Jangan commit ke git / kongsi sesiapa.")
