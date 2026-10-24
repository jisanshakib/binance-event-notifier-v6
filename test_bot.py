"""End-to-end tests with an in-memory database and fake Telegram/AI/scraper.
Run:  python test_bot.py"""
import asyncio
import os
import sys
import types
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k, v in dict(API_ID="1", API_HASH="x", User_session="", BOT_TOKEN="1:x", ADMIN_ID="42",
                 SUPABASE_URI="postgresql://u:p@localhost:5432/db", GEMINI_API_KEY="x").items():
    os.environ.setdefault(k, v)
from telethon.sessions import StringSession as _SS  # noqa: E402
_s = _SS(); _s.set_dc(2, "149.154.167.51", 443); _s.auth_key = __import__("telethon.crypto", fromlist=["AuthKey"]).AuthKey(b"0" * 256)
os.environ["User_session"] = _s.save()


# ---------- in-memory database replacing the Postgres layer ----------
class FakeData(dict):
    pass


FAKE = types.SimpleNamespace(data=FakeData())


def _install_fake_db(db):
    d = FAKE.data
    def kws(): return d.setdefault("keywords", [])
    db.get_channels = lambda: [r["username"] for r in d.setdefault("source_channels", [])]
    def add_channel(c):
        c = db._norm(c)
        if c not in db.get_channels(): d.setdefault("source_channels", []).append({"username": c})
    def remove_channel(c):
        c = db._norm(c); old = len(db.get_channels())
        d["source_channels"] = [r for r in d.setdefault("source_channels", []) if r["username"] != c]
        return len(db.get_channels()) < old
    db.add_channel, db.remove_channel = add_channel, remove_channel
    db.get_keywords = lambda: [r["word"] for r in kws()]
    def add_keyword(w):
        w = db._clean_kw(w)
        if not w or any(r["word"].lower() == w.lower() for r in kws()): return False
        kws().append({"word": w}); return True
    def remove_keyword(w):
        w = db._clean_kw(w).lower(); before = len(kws())
        d["keywords"] = [r for r in kws() if r["word"].lower() != w]; return len(d["keywords"]) < before
    def get_setting(k, default=None):
        return next((r["value"] for r in d.setdefault("settings", []) if r["key"] == k), default)
    def set_setting(k, v):
        d["settings"] = [r for r in d.setdefault("settings", []) if r["key"] != k] + [{"key": k, "value": str(v)}]
    def claim_post(c, m):
        pp = d.setdefault("processed_posts", [])
        if any(r["source_chat"] == c and r["message_id"] == m for r in pp): return False
        pp.append({"source_chat": c, "message_id": m, "status": "processing"}); return True
    def log_post(c, m, status, output=None, error=None):
        pp = d.setdefault("processed_posts", [])
        r = next((r for r in pp if r["source_chat"] == c and r["message_id"] == m), None)
        if r: r.update(status=status, output=output, error=error)
        else: pp.append({"source_chat": c, "message_id": m, "status": status, "output": output, "error": error})
    db.add_keyword, db.remove_keyword = add_keyword, remove_keyword
    db.get_setting, db.set_setting, db.claim_post, db.log_post = get_setting, set_setting, claim_post, log_post


import main, db, timeutil, ai_formatter  # noqa: E402
_install_fake_db(db)

SENT = []


async def fake_send_message(dest, text, **k): SENT.append((dest, text))
async def fake_send_file(dest, path, caption=None, **k): SENT.append((dest, caption or "<file>"))


main.bot.send_message = fake_send_message
main.bot.send_file = fake_send_file
AI_CALLS = []


async def fake_ask(prompt):
    AI_CALLS.append(prompt)
    return '{"title":"Spot Trading Tournament S6","reward":"Get Up 350 $FF","eligibility":"For All User","token":"FF","pair":"USDT"}'


ai_formatter._ask = fake_ask
main.scrape = lambda url: {"title": "T", "text": "Promotion Period: 2026-10-22 10:00 (UTC) to 2026-10-28 12:00 (UTC)",
                           "join_link": "https://www.binance.com/en/activity/x", "url": url}


def msg(mid, text, entities=None):
    return types.SimpleNamespace(id=mid, raw_text=text, entities=entities, reply_markup=None,
                                 photo=None, document=None, file=None)


def run(c): return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


def reset():
    FAKE.data.clear(); SENT.clear(); AI_CALLS.clear(); main.controls.pending.clear()
    db.set_setting("destination", "@dest")


# ---------------- tests ----------------
def test_schema_has_no_seed_keywords():
    sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")).read().lower()
    assert "create table if not exists keywords" in sql
    assert "insert into keywords" not in sql


def test_keyword_crud_case_insensitive():
    reset()
    assert db.get_keywords() == []
    assert db.add_keyword("  Airdrop ") is True
    assert db.add_keyword("AIRDROP") is False          # no duplicate in other case
    assert db.get_keywords() == ["Airdrop"]
    assert db.add_keyword("100%_Bonus") is True        # % and _ are literal, not wildcards
    assert db.remove_keyword("100x Bonus") is False
    assert db.remove_keyword("airdrop") is True
    assert db.remove_keyword("airdrop") is False
    assert db.get_keywords() == ["100%_Bonus"]


def test_match_keyword_whole_word():
    kw = ["Will List", "airdrop", "$FF"]
    assert main.match_keyword("Binance WILL   list Foo (FOO)", kw)
    assert main.match_keyword("New AIRDROP campaign", kw)
    assert main.match_keyword("Trade $FF now", kw)
    assert not main.match_keyword("Airdrops are fun", ["airdrop"])  # exact word only
    assert not main.match_keyword("Listing soon", ["list"])
    assert not main.match_keyword("anything", [])
    assert not main.match_keyword("", ["x"])


def test_non_matching_post_skips_ai_and_posting():
    reset(); db.add_keyword("Tournament")
    run(main.process_message(msg(1, "BTC price is up today"), "binance"))
    assert AI_CALLS == [] and SENT == [] and FAKE.data.get("processed_posts", []) == []


def test_matching_post_is_posted_once_even_when_duplicated_concurrently():
    reset(); db.add_keyword("tournament")
    m = msg(2, "Spot Trading Tournament S6 https://www.binance.com/en/support/announcement/abc")
    async def both():
        await asyncio.gather(main.process_message(m, "binance"), main.process_message(m, "binance"))
    run(both())
    run(main.process_message(m, "binance"))  # edited / redelivered event
    assert len(SENT) == 1
    text = SENT[0][1]
    assert "**🛒Spot Trading Tournament S6**" in text
    assert "[Open✅](https://www.binance.com/en/activity/x)" in text
    assert "4:00 PM" in text and "28 Oct at 6:00 PM" in text
    assert FAKE.data["processed_posts"][0]["status"] == "posted"


def test_scrape_and_ai_failures_still_post():
    reset(); db.add_keyword("airdrop")
    orig_s, orig_a = main.scrape, ai_formatter._ask
    def boom(url): raise RuntimeError("403 blocked")
    async def ai_boom(p): raise RuntimeError("quota")
    main.scrape, ai_formatter._ask = boom, ai_boom
    try:
        run(main.process_message(msg(3, "HODLer Airdrop: XYZ\nhttps://www.binance.com/en/support/announcement/q"), "binance"))
    finally:
        main.scrape, ai_formatter._ask = orig_s, orig_a
    assert len(SENT) == 1 and "**🛒HODLer Airdrop: XYZ**" in SENT[0][1]


def test_missing_link_and_long_text():
    reset(); db.add_keyword("listing")
    run(main.process_message(msg(4, "New listing " + "x " * 5000), "binance"))
    assert len(SENT) == 1 and "https://www.binance.com" in SENT[0][1]


def test_no_destination_does_not_crash_or_claim():
    reset(); FAKE.data["settings"] = []; db.add_keyword("x")
    run(main.process_message(msg(5, "x"), "binance"))
    assert FAKE.data.get("processed_posts", []) == []


def test_unauthorized_blocked():
    reset()
    e = AdminEvent('/addkw bad', sender=999)
    run(main.controls.on_command(e))
    assert db.get_keywords() == [] and e.answers


def test_command_patterns():
    import re
    p = r"^/addkw(@\w+)?(\s|$)"
    assert re.match(p, "/addkw airdrop") and re.match(p, "/addkw@MyBot airdrop") and re.match(p, "/addkw")
    assert not re.match(p, "/addkwfoo")
    assert not re.match(r"^/dest(@\w+)?\s*$", "/destination")


def test_time_conversion_formats():
    f = timeutil.find_utc_times
    exp = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    for s in ["2026-10-22 10:00 (UTC)", "2026-10-22 10:00:00 UTC", "2026/10/22 10:00 UTC",
              "22 October 2026 10:00 UTC", "October 22, 2026, 10:00 (UTC)", "Oct 22, 2026 at 10:00 UTC"]:
        assert f(s) == [exp], s
    assert f("2026-13-45 10:00 UTC") == []           # invalid date does not crash
    assert f("no dates") == [] and f(None) == []
    now = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
    assert timeutil.fmt_bst(exp, now) == "at Today 4:00 PM"
    assert timeutil.fmt_bst(datetime(2026, 10, 22, 18, 30, tzinfo=timezone.utc), now) == "23 Oct at 12:30 AM"  # crosses midnight
    assert timeutil.fmt_bst(datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc), now) == "28 Oct at 12:00 PM"


def test_config_uses_exactly_seven_env_vars():
    import config
    assert config.REQUIRED == ["API_ID", "API_HASH", "User_session", "BOT_TOKEN", "ADMIN_ID", "SUPABASE_URI", "GEMINI_API_KEY"]


def test_exact_template_and_token_dollar():
    from ai_formatter import build_post, token_symbol
    assert token_symbol("ENA") == "$ENA" and token_symbol("$ENA") == "$ENA"
    p = build_post({"title": "T", "reward": "R", "eligibility": "E", "token": "ENA", "pair": "USDC"},
                   "https://x.com/e", "at Today 4:00 PM", "28 Oct at 4:00 PM")
    plain = p.replace("**", "")
    assert plain.split("\n") == ["🛒T", "🎁 R", "🎉 E", "", "🔗  Link : [Open✅](https://x.com/e)",
        "• Event Goes Live✅", "", "at Today 4:00 PM", "• Event Will Expire", "28 Oct at 4:00 PM ❕",
        "➡️Register Event Page", "➡️Make Volume on $ENA/USDC", "➡️More Volume More Reward", "➡️Done"]
    assert "$$" not in build_post({"token": "$ENA", "pair": "USDC"}, "https://x", None, None)


def test_audit_fixes():
    from datetime import datetime, timezone
    from ai_formatter import pair_text
    import timeutil, scraper
    assert pair_text("ENA/USDC", "") == "$ENA/USDC"
    assert pair_text("$ENA", "ENA/USDC") == "$ENA/USDC"
    assert pair_text("ena", None) == "$ENA/USDT"
    # expiry must come after start even if an earlier date is mentioned second
    txt = "Period: 2026-10-28 10:00 (UTC) to 2026-11-04 10:00 (UTC). Announced 2026-10-20 08:00 (UTC)"
    s, e = timeutil.start_end_bst(txt)
    assert s.startswith("28 Oct") and e == "4 Nov at 4:00 PM"
    assert scraper.find_binance_url("see https://www.binance.com/en/support/announcement/abc.", []) == "https://www.binance.com/en/support/announcement/abc"
    assert scraper.find_binance_url("", ["www.binance.com/en/activity/x"]) == "https://www.binance.com/en/activity/x"


def test_binance_link_placeholders_and_entities():
    import scraper
    links = [("Terms", "https://www.%suffixOrigin%/%locale%/terms"),
             ("Join Now", "https://www.%suffixOrigin%/activity/x")]
    assert scraper.pick_cta(links, "f") == "https://www.binance.com/activity/x"
    assert scraper._clean_text("Hello&nbsp;World") == "Hello World"


class AdminEvent:
    def __init__(self, text='', data=b'', sender=42):
        self.sender_id, self.chat_id, self.is_private = sender, sender, True
        self.raw_text, self.data = text, data
        self.replies, self.edits, self.answers = [], [], []
    async def reply(self, text, **kw): self.replies.append((text, kw))
    async def respond(self, text, **kw): self.replies.append((text, kw))
    async def edit(self, text, **kw): self.edits.append((text, kw))
    async def answer(self, *a, **kw): self.answers.append((a, kw))


class UserDouble:
    def __init__(self): self.joins, self.reads = [], []
    async def __call__(self, request): self.joins.append(request.channel)
    async def get_messages(self, channel, ids):
        self.reads.append((channel, ids))
        return msg(ids, 'Tournament https://www.binance.com/en/support/announcement/abc')


def test_channel_input_forms():
    from channel_refs import normalize_channel, telegram_entity
    for v in ['https://t.me/ChannelName', 't.me/channelname', '@channelname', 'channelname']:
        assert normalize_channel(v) == 'channelname'
    assert normalize_channel('-100123456') == '-100123456'
    assert normalize_channel('123456') == '-100123456'
    assert telegram_entity('-100123456') == -100123456
    for v in ['https://evil.test/channelname', 't.me/channelname/123', 't.me/+invite', '', '0']:
        try: normalize_channel(v)
        except ValueError: pass
        else: raise AssertionError(v)


def test_manual_post_link_validation():
    from channel_refs import parse_post_link
    assert parse_post_link('t.me/channelname/123?single') == ('channelname', 123)
    assert parse_post_link('https://t.me/c/123456/789') == (-100123456, 789)
    for v in ['https://evil.test/channelname/123', 't.me/channelname/0', 't.me/channelname']:
        try: parse_post_link(v)
        except ValueError: pass
        else: raise AssertionError(v)


def test_button_channel_and_keyword_crud_round_trip():
    reset(); c = main.controls; original = c.user; double = UserDouble(); c.user = double
    async def flow():
        start = AdminEvent('/start'); await c.on_command(start)
        assert len(start.replies[0][1]['buttons']) == 3
        await c.on_button(AdminEvent(data=b'm:channels'))
        await c.on_button(AdminEvent(data=b'p:addchannel'))
        await c.on_input(AdminEvent('https://t.me/ChannelName'))
        assert db.get_channels() == ['channelname'] and double.joins == ['channelname']
        listed = AdminEvent(data=b'a:channels'); await c.on_button(listed)
        assert '@channelname' in listed.replies[0][0]
        await c.on_button(AdminEvent(data=b'p:delchannel'))
        await c.on_input(AdminEvent('t.me/channelname'))
        assert db.get_channels() == []
        await c.on_button(AdminEvent(data=b'p:addkw'))
        await c.on_input(AdminEvent('  Tournament  '))
        assert db.get_keywords() == ['Tournament']
        await c.on_button(AdminEvent(data=b'p:delkw'))
        await c.on_input(AdminEvent('tournament'))
        assert db.get_keywords() == []
    try: run(flow())
    finally: c.user = original


def test_destination_button_stores_canonical_link():
    reset(); c = main.controls; old_me, old_perms = c.bot.get_me, c.bot.get_permissions; checked = []
    async def me(): return object()
    async def perms(ent, account):
        checked.append(ent); return types.SimpleNamespace(is_admin=True, post_messages=True)
    c.bot.get_me, c.bot.get_permissions = me, perms
    async def flow():
        await c.on_button(AdminEvent(data=b'p:setdest'))
        await c.on_input(AdminEvent('https://t.me/OutputChannel'))
        assert db.get_setting('destination') == 'outputchannel' and checked == ['outputchannel']
        checked.clear()
        await c.on_command(AdminEvent('/setdest -100123456'))
        assert db.get_setting('destination') == '-100123456' and checked == [-100123456]
    try: run(flow())
    finally: c.bot.get_me, c.bot.get_permissions = old_me, old_perms


def test_buttons_authorization_and_pending_cancellation():
    reset(); c = main.controls
    async def flow():
        denied = AdminEvent(data=b'p:addkw', sender=999); await c.on_button(denied)
        assert denied.answers and not c.pending
        await c.on_button(AdminEvent(data=b'p:addkw'))
        await c.on_input(AdminEvent('bad', sender=999))
        assert db.get_keywords() == [] and 42 in c.pending
        await c.on_command(AdminEvent('/cancel'))
        await c.on_input(AdminEvent('ignored'))
        assert not c.pending and db.get_keywords() == []
        await c.on_button(AdminEvent(data=b'p:addkw'))
        await c.on_button(AdminEvent(data=b'a:status'))
        assert not c.pending
        import time
        c.pending[42] = ('addkw', time.monotonic() - 601)
        await c.on_input(AdminEvent('expired'))
        assert db.get_keywords() == []
    run(flow())


def test_keyword_maximum_100_characters_through_menu():
    reset(); c = main.controls
    async def flow():
        await c.on_button(AdminEvent(data=b'p:addkw')); await c.on_input(AdminEvent('x' * 101))
        assert db.get_keywords() == []
        await c.on_button(AdminEvent(data=b'p:addkw')); await c.on_input(AdminEvent('x' * 100))
        assert db.get_keywords() == ['x' * 100]
    run(flow())


def test_menu_manual_test_publishes_and_records_result():
    reset(); c = main.controls; old = c.user; double = UserDouble(); c.user = double
    async def flow():
        await c.on_button(AdminEvent(data=b'p:test'))
        await c.on_input(AdminEvent('t.me/c/123456/789'))
        assert double.reads == [(-100123456, 789)]
        assert FAKE.data['processed_posts'][0]['status'] == 'posted'
        assert len(SENT) == 1 and SENT[0][0] == 'dest'
    try: run(flow())
    finally: c.user = old


def test_manual_test_failure_is_not_reported_as_success():
    reset(); c = main.controls; old = c.user; c.user = UserDouble()
    async def flow():
        FAKE.data['settings'] = []
        e = AdminEvent('/test t.me/channelname/123'); await c.on_command(e)
        assert FAKE.data.get('processed_posts', []) == []
        assert e.replies[-1][0].startswith('❌')
    try: run(flow())
    finally: c.user = old


def test_channel_list_is_not_cut_off():
    reset()
    for i in range(400): db.add_channel('channel_' + str(i))
    e = AdminEvent(data=b'a:channels'); run(main.controls.on_button(e))
    assert all(len(t.encode('utf-16-le')) // 2 <= 4096 for t, _ in e.replies)
    assert '@channel_399' in ''.join(t for t, _ in e.replies)


if __name__ == "__main__":
    fails = 0
    for n, fn in list(globals().items()):
        if n.startswith("test_"):
            try: fn(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e))
    sys.exit(1 if fails else 0)
