import asyncio
import re
import os
import tempfile
import threading
import traceback

import uvicorn
from fastapi import FastAPI
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

import config
import db
from channel_refs import telegram_entity
from scraper import find_binance_url, scrape
from timeutil import start_end_bst
from ai_formatter import format_post

_NET = dict(connection_retries=None, retry_delay=5, auto_reconnect=True, request_retries=10)
try:
    _user_session = StringSession(config.USER_SESSION)
except ValueError:
    raise SystemExit("❌ User_session is not a valid Telethon string session. Generate it with: python generate_session.py")
user = TelegramClient(_user_session, config.API_ID, config.API_HASH, **_NET)
bot = TelegramClient(StringSession(), config.API_ID, config.API_HASH, **_NET)
_locks: dict = {}

# ---------------- health server for Render ----------------
app = FastAPI()


@app.get("/")
def health():
    return {"status": "ok"}


def run_web():
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")


# ---------------- helpers ----------------
def entity_urls(msg) -> list[str]:
    out = []
    for ent in msg.entities or []:
        if isinstance(ent, MessageEntityTextUrl):
            out.append(ent.url)
        elif isinstance(ent, MessageEntityUrl):
            out.append(msg.raw_text[ent.offset: ent.offset + ent.length])
    if msg.reply_markup and getattr(msg.reply_markup, "rows", None):
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if getattr(btn, "url", None):
                    out.append(btn.url)
    return out


def match_keyword(text: str, keywords: list[str]) -> bool:
    """Case-insensitive whole-word/phrase match: 'list' matches 'Will LIST' but not 'Listing'."""
    t = " ".join((text or "").split())
    for k in keywords:
        k = " ".join((k or "").split())
        if not k:
            continue
        pat = r"\s+".join(re.escape(w) for w in k.split(" "))
        pre = r"(?<!\w)" if (k[0].isalnum() or k[0] == "_") else ""
        post = r"(?!\w)" if (k[-1].isalnum() or k[-1] == "_") else ""
        if re.search(pre + pat + post, t, re.I):
            return True
    return False


async def process_message(msg, chat_name: str, force=False):
    text = msg.raw_text or ""
    if not text.strip() and not force:
        return  # sticker / service message / media without caption
    # keyword check happens BEFORE any scraping or AI call -> zero credits spent on non-matches
    if not force and not match_keyword(text, await asyncio.to_thread(db.get_keywords)):
        return
    dest = await asyncio.to_thread(db.get_setting, "destination")
    if not dest:
        await notify_admin("⚠️ গন্তব্য চ্যানেল ঠিক করা হয়নি। /start মেনু থেকে 🎯 গন্তব্য চ্যানেল নির্ধারণ করুন।")
        return
    lock = _locks.setdefault((chat_name, msg.id), asyncio.Lock())
    async with lock:
        try:
            if force:
                await asyncio.to_thread(db.log_post, chat_name, msg.id, "processing")
            elif not await asyncio.to_thread(db.claim_post, chat_name, msg.id):
                return  # already posted (or being posted) -> never publish twice
            return await _publish(msg, text, chat_name, dest)
        finally:
            _locks.pop((chat_name, msg.id), None)


async def _publish(msg, text, chat_name, dest):
    try:
        url = find_binance_url(text, entity_urls(msg))
        page = {"title": "", "text": "", "join_link": url or "https://www.binance.com", "url": url or ""}
        if url:
            try:
                page = await asyncio.wait_for(asyncio.to_thread(scrape, url), timeout=45)
            except Exception as ex:  # article down / blocked -> still post using the channel text
                print("scrape failed:", url, repr(ex))
        start, end = start_end_bst(page["text"] + "\n" + text)
        caption = await format_post(text, page, start, end)
        dest_entity = telegram_entity(dest)
        if msg.photo or (msg.document and msg.file and (msg.file.mime_type or "").startswith(("image", "video"))):
            with tempfile.TemporaryDirectory() as d:
                path = await user.download_media(msg, file=os.path.join(d, "media"))
                if len(caption) <= 1024:
                    await bot.send_file(dest_entity, path, caption=caption, parse_mode="md")
                else:
                    await bot.send_file(dest_entity, path)
                    await bot.send_message(dest_entity, caption, parse_mode="md", link_preview=False)
        else:
            await bot.send_message(dest_entity, caption, parse_mode="md", link_preview=False)
        await asyncio.to_thread(db.log_post, chat_name, msg.id, "posted", caption)
        print(f"Posted from {chat_name}/{msg.id}")
        return True
    except Exception as e:
        traceback.print_exc()
        try:
            await asyncio.to_thread(db.log_post, chat_name, msg.id, "error", None, str(e)[:2000])
        except Exception:
            traceback.print_exc()
        await notify_admin(f"❌ {chat_name}/{msg.id} পোস্টটি প্রক্রিয়া করা যায়নি: {str(e)[:500]}\n/start মেনুর 🧪 ম্যানুয়াল টেস্ট দিয়ে আবার চেষ্টা করুন।")

        return False


async def notify_admin(text):
    try:
        await bot.send_message(config.ADMIN_ID, text, parse_mode=None)
    except Exception:
        pass


# ---------------- listener ----------------
@user.on(events.NewMessage())
@user.on(events.MessageEdited())
async def on_channel_post(event):
    # edited posts: if an edit adds a keyword it gets posted once; already-posted ones are skipped by dedup
    try:
        if not event.is_channel or not event.message:
            return
        chat = await event.get_chat()
        uname = (getattr(chat, "username", None) or str(event.chat_id)).lower()
        channels = await asyncio.to_thread(db.get_channels)
        if uname not in channels and str(event.chat_id) not in channels:
            return
        await process_message(event.message, uname)
    except Exception:
        traceback.print_exc()  # never let one bad event kill the listener


# ---------------- Bengali Telegram admin interface ----------------
from admin_controls import AdminControls
controls = AdminControls(bot, user, process_message).register()


async def main():
    threading.Thread(target=run_web, daemon=True).start()
    await asyncio.to_thread(db.init_schema)  # creates tables automatically if missing
    await bot.start(bot_token=config.BOT_TOKEN)
    await user.start()
    # make sure the user account has joined all channels
    from telethon.tl.functions.channels import JoinChannelRequest
    for ch in await asyncio.to_thread(db.get_channels):
        try:
            await user(JoinChannelRequest(telegram_entity(ch)))
        except Exception as ex:
            print("join failed", ch, ex)
    print("Bot running...")
    await notify_admin("🚀 বট চালু হয়েছে। মেনু খুলতে /start পাঠান।")
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())
