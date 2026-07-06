# Channel Forwarder — Panduan Setup

Automation ini akan **copy setiap post baru dari Channel A (sumber) ke Channel B (destinasi)** secara automatik, tanpa tag "Forwarded from".

Sebab Channel A milik orang lain (anda subscriber sahaja), Bot API biasa **tak boleh** baca mesej di situ. Jadi kita guna **userbot** (Telethon) yang login guna **akaun Telegram peribadi anda**.

---

## 1. Dapatkan API ID & API HASH

1. Pergi ke https://my.telegram.org
2. Login guna nombor telefon Telegram anda.
3. Klik **API development tools**.
4. Isi borang (nama app apa saja, contoh `mtu-forwarder`).
5. Salin **api_id** dan **api_hash**.

## 2. Jana Session String (buat di komputer sendiri, sekali sahaja)

```bash
pip install telethon
python generate_session.py
```

- Masukkan **API ID**, **API HASH**, **nombor telefon**, dan **kod OTP** yang Telegram hantar.
- Kalau akaun anda ada 2FA, masukkan juga password.
- Skrip akan cetak satu **session string** yang panjang. **Salin string itu.**

> ⚠️ Session string = akses penuh ke akaun Telegram anda. **Jangan** commit ke git, **jangan** kongsi sesiapa.

## 3. Set Environment Variables (di Railway / server)

| Variable            | Nilai / Contoh                                  | Wajib |
|---------------------|-------------------------------------------------|-------|
| `TELEGRAM_API_ID`   | `1234567` (dari langkah 1)                       | ✅ |
| `TELEGRAM_API_HASH` | `abcd1234...` (dari langkah 1)                   | ✅ |
| `TELEGRAM_SESSION`  | session string (dari langkah 2)                  | ✅ |
| `SOURCE_CHANNELS`   | Channel A — `@channelsumber` atau `-1001234567890` | ✅ |
| `DEST_CHANNEL`      | Channel B — `@channelanda` atau `-1009876543210`   | ✅* |

\* Kalau `DEST_CHANNEL` tak diset, ia akan guna `TELEGRAM_CHANNEL_ID` (channel bot signal sedia ada).

**Nota:**
- `SOURCE_CHANNELS` boleh lebih dari satu channel — pisahkan dengan koma:
  `@channelA1, @channelA2, -1001111111111`
- Akaun anda **mesti dah subscribe/join Channel A** supaya boleh baca postnya.
- Akaun anda **mesti ada kebenaran hantar mesej di Channel B** (jadi admin, atau owner channel).
- Kalau channel private (tiada username), guna ID numerik `-100...`.
  Cara dapat ID: forward satu post channel tu ke bot [@userinfobot](https://t.me/userinfobot) atau [@JsonDumpBot](https://t.me/JsonDumpBot).

## 4. Jalankan

**Railway / Heroku (guna Procfile):**
Sudah ditambah proses `forwarder` dalam `Procfile`:
```
forwarder: python channel_forwarder.py
```
Aktifkan/scale proses `forwarder` di dashboard Railway (macam proses `worker`).

**Local / manual:**
```bash
pip install -r requirements.txt
python channel_forwarder.py
```

Bila jalan, ia akan cetak:
```
Login sebagai: ...
Forwarder aktif. Menunggu post baru dari Channel A...
```

Lepas ni, setiap post baru di Channel A akan muncul di Channel B secara automatik (teks, gambar, video, dokumen, sticker, album — semua disokong).

---

## Soalan Lazim

**Q: Kenapa tak guna bot biasa je?**
Bot Telegram cuma boleh baca mesej channel kalau bot itu **admin** channel tu. Channel A milik orang lain — anda tak boleh jadikan bot sebagai admin di situ. Userbot (akaun peribadi) boleh baca apa saja channel yang anda dah join.

**Q: Adakah ini melanggar terma Telegram?**
Automasi guna akaun peribadi dibenarkan untuk kegunaan biasa. Elak spam / volume ekstrem. Guna secara bertanggungjawab.

**Q: Post lama pun kena copy?**
Tidak. Ia hanya copy **post baru** yang keluar selepas forwarder mula berjalan.

**Q: Boleh copy ke banyak channel B?**
Versi sekarang copy ke satu `DEST_CHANNEL`. Kalau perlu banyak destinasi, boleh diubah suai — beritahu saya.
