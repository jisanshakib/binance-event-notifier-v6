"""Bengali Telegram controls. No imports from main, no additional settings."""
import asyncio
import time
import traceback
from telethon import Button, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.functions.channels import JoinChannelRequest
import config
import db
from channel_refs import display_channel, normalize_channel, parse_post_link, telegram_entity

HELP = '''❓ সাহায্য ও নির্দেশনা
/start – প্রধান মেনু
/channels – নজরে থাকা চ্যানেলের তালিকা
/addchannel @name – চ্যানেল যোগ করুন
/delchannel @name – চ্যানেল বাদ দিন
/keywords – কিওয়ার্ডের তালিকা
/addkw শব্দ – কিওয়ার্ড যোগ করুন
/delkw শব্দ – কিওয়ার্ড বাদ দিন
/setdest @channel – গন্তব্য চ্যানেল ঠিক করুন
/dest – গন্তব্য ও বটের অনুমতি দেখুন
/test https://t.me/channel/123 – একটি পোস্ট পরীক্ষা করুন
/status – বটের অবস্থা
/cancel – চলমান কাজ বাতিল করুন

বাটন চাপুন, তারপর চাওয়া তথ্য পাঠান। চ্যানেল হিসেবে @username, t.me/channelname, https://t.me/channelname বা -100… আইডি দিন। ব্যক্তিগত চ্যানেলে আপনার Telegram অ্যাকাউন্ট আগে যুক্ত থাকতে হবে।
গন্তব্য চ্যানেলে বটকে Admin বানিয়ে Post Messages অনুমতি দিন। অন্তত একটি কিওয়ার্ড যোগ করুন; কিওয়ার্ড না থাকলে স্বয়ংক্রিয় পোস্ট বন্ধ থাকবে।
ম্যানুয়াল টেস্ট কিওয়ার্ড ফিল্টার এড়ায় এবং আগে পাঠানো পোস্ট আবার পাঠায়।'''
PROMPTS = {
    'addchannel': '📢 যোগ করার জন্য চ্যানেলের @username, t.me লিংক বা আইডি পাঠান।',
    'delchannel': '🗑 বাদ দেওয়ার জন্য চ্যানেলের @username, t.me লিংক বা আইডি পাঠান।',
    'addkw': '🔑 নতুন কিওয়ার্ড পাঠান (সর্বোচ্চ ১০০ অক্ষর)।',
    'delkw': '🗑 যে কিওয়ার্ড বাদ দিতে চান সেটি পাঠান।',
    'setdest': '🎯 গন্তব্য চ্যানেলের @username, t.me লিংক বা -100… আইডি পাঠান।',
    'test': '🧪 পোস্টের লিংক পাঠান: https://t.me/channelname/123',
}


def button(label, data): return Button.inline(label, data.encode())
def back(): return [button('⬅️ মূল মেনু', 'm:main')]
def main_menu():
    return [[button('📢 চ্যানেল', 'm:channels'), button('🔑 কিওয়ার্ড', 'm:keywords')],
            [button('🎯 গন্তব্য চ্যানেল', 'm:dest'), button('📊 বটের অবস্থা', 'a:status')],
            [button('🧪 ম্যানুয়াল টেস্ট', 'p:test'), button('❓ সাহায্য', 'a:help')]]


def section_menu(section):
    if section == 'dest':
        return [[button('✏️ নির্ধারণ করুন', 'p:setdest'), button('✅ যাচাই করুন', 'a:dest')], back()]
    add, delete = ('addchannel', 'delchannel') if section == 'channels' else ('addkw', 'delkw')
    return [[button('📋 তালিকা', 'a:' + section)],
            [button('➕ যোগ করুন', 'p:' + add), button('➖ বাদ দিন', 'p:' + delete)], back()]


class AdminControls:
    def __init__(self, bot, user, process_message):
        self.bot, self.user, self.process_message = bot, user, process_message
        self.pending = {}

    async def send(self, event, text, buttons=None):
        # Split safely for Telegram's UTF-16 text limit; never lose list entries.
        chunks = [text[i:i + 1800] for i in range(0, len(text), 1800)] or ['']
        for i, chunk in enumerate(chunks):
            await event.respond(chunk, buttons=buttons if i == len(chunks) - 1 else None, parse_mode=None)

    async def authorized(self, event):
        if event.sender_id != config.ADMIN_ID:
            if hasattr(event, 'answer'): await event.answer('⛔️ আপনার অনুমতি নেই', alert=True)
            else: await event.reply('⛔️ আপনার অনুমতি নেই')
            return False
        if not event.is_private:
            text = 'ব্যবস্থাপনার জন্য বটের ব্যক্তিগত চ্যাটে /start পাঠান।'
            if hasattr(event, 'answer'): await event.answer(text, alert=True)
            else: await event.reply(text)
            return False
        return True

    async def prompt(self, event, action):
        self.pending[event.sender_id] = (action, time.monotonic())
        await self.send(event, PROMPTS[action] + '\nবাতিল করতে /cancel লিখুন।', [back()])

    async def show(self, event, name):
        if name == 'help': return await self.send(event, HELP, [back()])
        if name == 'channels':
            values = await asyncio.to_thread(db.get_channels)
            text = '📢 নজরে থাকা চ্যানেল:\n' + ('\n'.join(display_channel(c) for c in values) or 'কোনো চ্যানেল যোগ করা হয়নি।')
        elif name == 'keywords':
            values = await asyncio.to_thread(db.get_keywords)
            text = '🔑 কিওয়ার্ড:\n' + ('\n'.join(f'{i}. {k}' for i, k in enumerate(values, 1)) or 'এখনো কোনো কিওয়ার্ড নেই। “যোগ করুন” চাপুন।')
        elif name == 'dest':
            dest = await asyncio.to_thread(db.get_setting, 'destination')
            if not dest: return await self.send(event, '🎯 গন্তব্য চ্যানেল এখনো ঠিক করা হয়নি।', section_menu('dest'))
            text = f'🎯 গন্তব্য চ্যানেল: {display_channel(dest)}'
            try:
                perms = await self.bot.get_permissions(telegram_entity(dest), await self.bot.get_me())
                ok = perms.is_admin and perms.post_messages is not False
                text += '\nপোস্ট করার Admin অনুমতি: ' + ('✅ আছে' if ok else '❌ নেই')
            except Exception as ex:
                text += f'\n❌ অনুমতি যাচাই করা যায়নি: {str(ex)[:300]}\nবটকে চ্যানেলের Admin বানান।'
        elif name == 'status':
            channels, kws = await asyncio.gather(asyncio.to_thread(db.get_channels), asyncio.to_thread(db.get_keywords))
            dest = await asyncio.to_thread(db.get_setting, 'destination')
            text = f'📊 বটের অবস্থা\n✅ চালু আছে\nচ্যানেল: {len(channels)}টি\nকিওয়ার্ড: {len(kws)}টি\nগন্তব্য: {display_channel(dest) if dest else "ঠিক করা হয়নি"}\nAI: Gemini ({config.GEMINI_MODEL})'
        else: return
        await self.send(event, text, section_menu(name) if name in ('channels', 'keywords', 'dest') else [back()])

    async def action(self, event, name, value):
        if name in ('addchannel', 'delchannel', 'setdest'):
            try: channel = normalize_channel(value)
            except ValueError as ex: return await self.send(event, f'⚠️ {ex}', [back()])
            if name == 'setdest':
                await asyncio.to_thread(db.set_setting, 'destination', channel)
                return await self.show(event, 'dest')
            if name == 'addchannel':
                await asyncio.to_thread(db.add_channel, channel)
                text = f'✅ চ্যানেল যোগ হয়েছে: {display_channel(channel)}'
                try: await self.user(JoinChannelRequest(telegram_entity(channel)))
                except Exception: text += '\nℹ️ আপনার Telegram অ্যাকাউন্ট চ্যানেলে যুক্ত হতে পারেনি; প্রয়োজনে নিজে যোগ দিন।'
            else:
                ok = await asyncio.to_thread(db.remove_channel, channel)
                text = f'🗑 চ্যানেল বাদ দেওয়া হয়েছে: {display_channel(channel)}' if ok else 'ℹ️ চ্যানেলটি তালিকায় নেই।'
            return await self.send(event, text, section_menu('channels'))
        if name in ('addkw', 'delkw'):
            value = ' '.join((value or '').split())
            if not value: return await self.send(event, '⚠️ একটি কিওয়ার্ড লিখুন।', section_menu('keywords'))
            if name == 'addkw':
                if len(value) > 100: return await self.send(event, '⚠️ কিওয়ার্ড অনেক বড় (সর্বোচ্চ ১০০ অক্ষর)।', section_menu('keywords'))
                ok = await asyncio.to_thread(db.add_keyword, value)
                text = f'✅ কিওয়ার্ড যোগ হয়েছে: {value}' if ok else f'ℹ️ কিওয়ার্ডটি আগেই আছে: {value}'
            else:
                ok = await asyncio.to_thread(db.remove_keyword, value)
                text = f'🗑 কিওয়ার্ড বাদ দেওয়া হয়েছে: {value}' if ok else f'ℹ️ কিওয়ার্ডটি পাওয়া যায়নি: {value}'
            return await self.send(event, text, section_menu('keywords'))
        if name == 'test':
            try: channel, mid = parse_post_link(value)
            except ValueError as ex: return await self.send(event, f'⚠️ {ex}', [back()])
            msg = await self.user.get_messages(telegram_entity(str(channel)), ids=mid)
            if not msg: return await self.send(event, '❌ পোস্টটি পাওয়া যায়নি।', [back()])
            await self.send(event, '⏳ পোস্টটি প্রক্রিয়া করা হচ্ছে...')
            ok = await self.process_message(msg, str(channel).lower(), force=True)
            await self.send(event, '✅ পোস্ট পাঠানো হয়েছে। গন্তব্য চ্যানেল দেখুন।' if ok else '❌ পোস্ট পাঠানো যায়নি। গন্তব্য চ্যানেল ও বটের অনুমতি যাচাই করুন।', [back()])

    async def on_command(self, event):
        if not await self.authorized(event): return
        self.pending.pop(event.sender_id, None)
        parts = event.raw_text.split(maxsplit=1)
        command = parts[0].split('@')[0][1:]
        value = parts[1].strip() if len(parts) > 1 else ''
        try:
            if command in ('start', 'menu', 'cancel'):
                return await self.send(event, '❎ কাজ বাতিল করা হয়েছে।' if command == 'cancel' else '🤖 প্রধান মেনু\nকী করতে চান, বেছে নিন।', main_menu())
            if command in PROMPTS:
                if value: return await self.action(event, command, value)
                return await self.prompt(event, command)
            await self.show(event, command)
        except Exception as ex:
            traceback.print_exc()
            await self.send(event, f'❌ সমস্যা হয়েছে: {str(ex)[:500]}', [back()])

    async def on_input(self, event):
        if not event.is_private or event.sender_id != config.ADMIN_ID or event.sender_id not in self.pending: return
        if (event.raw_text or '').startswith('/'): return
        action, started = self.pending.pop(event.sender_id)
        if time.monotonic() - started > 600:
            return await self.send(event, '⌛ আগের কাজের সময় শেষ হয়েছে। /start থেকে আবার বেছে নিন।', main_menu())
        try: await self.action(event, action, (event.raw_text or '').strip())
        except Exception as ex:
            traceback.print_exc()
            await self.send(event, f'❌ সমস্যা হয়েছে: {str(ex)[:500]}', [back()])

    async def on_button(self, event):
        if not await self.authorized(event): return
        await event.answer()
        try:
            kind, name = event.data.decode().split(':', 1)
            self.pending.pop(event.sender_id, None)
            if kind == 'p' and name in PROMPTS: return await self.prompt(event, name)
            if kind == 'a': return await self.show(event, name)
            if kind == 'm':
                text = {'main': '🤖 প্রধান মেনু\nকী করতে চান, বেছে নিন।', 'channels': '📢 চ্যানেল ব্যবস্থাপনা', 'keywords': '🔑 কিওয়ার্ড ব্যবস্থাপনা', 'dest': '🎯 গন্তব্য চ্যানেল'}
                if name not in text: return
                try: await event.edit(text[name], buttons=main_menu() if name == 'main' else section_menu(name), parse_mode=None)
                except MessageNotModifiedError: pass
        except Exception as ex:
            traceback.print_exc()
            await self.send(event, f'❌ সমস্যা হয়েছে: {str(ex)[:500]}', [back()])

    def register(self):
        self.bot.add_event_handler(self.on_command, events.NewMessage(pattern=r'^/(start|menu|cancel|help|channels|addchannel|delchannel|keywords|addkw|delkw|setdest|dest|test|status)(@\w+)?(\s|$)'))
        self.bot.add_event_handler(self.on_input, events.NewMessage(func=lambda e: e.is_private and not (e.raw_text or '').startswith('/')))
        self.bot.add_event_handler(self.on_button, events.CallbackQuery(pattern=rb'^[map]:'))
        return self
