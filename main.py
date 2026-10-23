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
        await notify_admin("⚠️ Destination channel not set. Use /setdest @channel")
        return
    lock = _locks.setdefault((chat_name, msg.id), asyncio.Lock())
    async with lock:
        try:
            if force:
                await asyncio.to_thread(db.log_post, chat_name, msg.id, "processing")
            elif not await asyncio.to_thread(db.claim_post, chat_name, msg.id):
                return  # already posted (or being posted) -> never publish twice
            await _publish(msg, text, chat_name, dest)
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
        dest_entity = int(dest) if dest.lstrip("-").isdigit() else dest
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
    except Exception as e:
        traceback.print_exc()
        try:
            await asyncio.to_thread(db.log_post, chat_name, msg.id, "error", None, str(e)[:2000])
        except Exception:
            traceback.print_exc()
        await notify_admin(f"❌ Error processing {chat_name}/{msg.id}: {str(e)[:500]}\nRetry with /test")


async def notify_admin(text):
    try:
        await bot.send_message(config.ADMIN_ID, text)
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


# ---------------- admin commands (bot) ----------------
HELP = """🤖 Admin Commands
/channels – list monitored channels
/addchannel @name – monitor channel
/delchannel @name – stop monitoring
/keywords – list keywords
/addkw word – add keyword
/delkw word – remove keyword
/setdest @channel_or_-100id – set destination
/dest – show destination & check bot admin rights
/test https://t.me/channel/123 – process one post now
/status – bot status"""


def admin_only(func):
    async def wrapper(event):
        if event.sender_id != config.ADMIN_ID:
            await event.reply("⛔️ Unauthorized")
            return
        try:
            await func(event)
        except Exception as e:
            traceback.print_exc()
            await event.reply(f"Error: {str(e)[:500]}")
    wrapper.__name__ = func.__name__
    return wrapper


def arg(event):
    parts = event.raw_text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@bot.on(events.NewMessage(pattern=r"^/(start|help)(@\w+)?(\s|$)"))
@admin_only
async def cmd_help(e): await e.reply(HELP)


@bot.on(events.NewMessage(pattern=r"^/channels(@\w+)?(\s|$)"))
@admin_only
async def cmd_channels(e): await e.reply("📡 " + ("\n".join("@" + c for c in (await asyncio.to_thread(db.get_channels))) or "none"))


@bot.on(events.NewMessage(pattern=r"^/addchannel(@\w+)?(\s|$)"))
@admin_only
async def cmd_addch(e):
    a = arg(e)
    if not a: return await e.reply("Usage: /addchannel @name")
    (await asyncio.to_thread(db.add_channel, a))
    try:
        from telethon.tl.functions.channels import JoinChannelRequest
        await user(JoinChannelRequest(a.lstrip("@")))
    except Exception:
        pass
    await e.reply(f"✅ Monitoring {a}")


@bot.on(events.NewMessage(pattern=r"^/delchannel(@\w+)?(\s|$)"))
@admin_only
async def cmd_delch(e):
    a = arg(e)
    if not a: return await e.reply("Usage: /delchannel @name")
    await e.reply("🗑 Removed" if (await asyncio.to_thread(db.remove_channel, a)) else f"Not found: {a}")


@bot.on(events.NewMessage(pattern=r"^/keywords(@\w+)?(\s|$)"))
@admin_only
async def cmd_kws(e):
    kws = (await asyncio.to_thread(db.get_keywords))
    if not kws: return await e.reply("🔑 No keywords yet. Add one with /addkw word")
    body = "\n".join(f"{i}. {k}" for i, k in enumerate(kws, 1))
    await e.reply(f"🔑 Keywords ({len(kws)}):\n{body}"[:4000])


@bot.on(events.NewMessage(pattern=r"^/addkw(@\w+)?(\s|$)"))
@admin_only
async def cmd_addkw(e):
    a = arg(e)
    if not a: return await e.reply("Usage: /addkw word")
    if len(a) > 100: return await e.reply("Keyword too long (max 100 characters).")
    await e.reply(f"✅ Keyword added: {a}" if (await asyncio.to_thread(db.add_keyword, a)) else f"ℹ️ Already exists: {a}")


@bot.on(events.NewMessage(pattern=r"^/delkw(@\w+)?(\s|$)"))
@admin_only
async def cmd_delkw(e):
    a = arg(e)
    if not a: return await e.reply("Usage: /delkw word")
    await e.reply(f"🗑 Removed: {a}" if (await asyncio.to_thread(db.remove_keyword, a)) else f"Not found: {a}")


@bot.on(events.NewMessage(pattern=r"^/setdest(@\w+)?(\s|$)"))
@admin_only
async def cmd_setdest(e):
    a = arg(e)
    if not a: return await e.reply("Usage: /setdest @channel or -100123...")
    (await asyncio.to_thread(db.set_setting, "destination", a))
    await cmd_dest(e)


@bot.on(events.NewMessage(pattern=r"^/dest(@\w+)?\s*$"))
@admin_only
async def cmd_dest(e):
    dest = (await asyncio.to_thread(db.get_setting, "destination"))
    if not dest: return await e.reply("Destination not set.")
    try:
        ent = int(dest) if dest.lstrip("-").isdigit() else dest
        me = await bot.get_me()
        perms = await bot.get_permissions(ent, me)
        ok = perms.is_admin and perms.post_messages is not False
        await e.reply(f"🎯 Destination: {dest}\nBot admin with post rights: {'✅ Yes' if ok else '❌ No'}")
    except Exception as ex:
        await e.reply(f"🎯 Destination: {dest}\n❌ Cannot verify: {ex}\nAdd the bot as channel admin.")


@bot.on(events.NewMessage(pattern=r"^/test(@\w+)?(\s|$)"))
@admin_only
async def cmd_test(e):
    a = arg(e).split("?")[0].rstrip("/")
    try:
        parts = a.split("/")
        ch, mid = parts[-2], int(parts[-1])
        if len(parts) >= 3 and parts[-3] == "c":
            ch = int("-100" + parts[-2])  # private link t.me/c/123/45
    except Exception:
        return await e.reply("Usage: /test https://t.me/channel/123")
    msg = await user.get_messages(ch, ids=mid)
    if not msg: return await e.reply("Message not found.")
    await e.reply("⏳ Processing...")
    await process_message(msg, str(ch).lower(), force=True)
    await e.reply("✅ Done (check destination).")


@bot.on(events.NewMessage(pattern=r"^/status(@\w+)?(\s|$)"))
@admin_only
async def cmd_status(e):
    await e.reply(f"✅ Running\nChannels: {len((await asyncio.to_thread(db.get_channels)))}\nKeywords: {len((await asyncio.to_thread(db.get_keywords)))}\nAI: Gemini ({config.GEMINI_MODEL})")


async def main():
    threading.Thread(target=run_web, daemon=True).start()
    await asyncio.to_thread(db.init_schema)  # creates tables automatically if missing
    await bot.start(bot_token=config.BOT_TOKEN)
    await user.start()
    # make sure the user account has joined all channels
    from telethon.tl.functions.channels import JoinChannelRequest
    for ch in await asyncio.to_thread(db.get_channels):
        try:
            await user(JoinChannelRequest(ch))
        except Exception as ex:
            print("join failed", ch, ex)
    print("Bot running...")
    await notify_admin("🚀 Bot started. Send /help")
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())
