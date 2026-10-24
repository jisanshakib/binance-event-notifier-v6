"""Regression tests for the two extraction bugs:
Bug 1: token tickers truncated ("Up to 400 BNB" -> "Up to 400 B").
Bug 2: comma numbers truncated ("500,000" -> "500", "1,000,000" -> "1,000").
Run:  python -m pytest test_reward_fixes.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k, v in dict(API_ID="1", API_HASH="x", User_session="x", BOT_TOKEN="1:x", ADMIN_ID="42",
                 SUPABASE_URI="postgresql://u:p@localhost:5432/db", GEMINI_API_KEY="x").items():
    os.environ.setdefault(k, v)
import ai_formatter as A


def format_with(ai_answer: str, post: str, page: dict) -> str:
    async def fake_ask(prompt):
        return ai_answer
    orig, A._ask = A._ask, fake_ask
    try:
        return asyncio.run(A.format_post(post, page, "at Today 4:00 PM", "28 Oct at 4:00 PM"))
    finally:
        A._ask = orig


PAGE_BNB = ("BNB Booster Campaign\n"
            "Trade to share a total reward pool of up to 400 BNB!\n"
            "Eligible users: all users with 2+ Alpha Points.")


# ---- Bug 1: token ticker must never be truncated ----
def test_bnb_ticker_restored_in_reward_and_token():
    ai = '{"title":"BNB Booster","reward":"Get Up to 400 B","eligibility":"For All User","token":"B","pair":"USDT"}'
    out = format_with(ai, "BNB Booster https://x", {"title": "t", "text": PAGE_BNB, "join_link": "https://x"})
    assert "Get Up to 400 BNB" in out
    assert "Make Volume on $BNB/USDT" in out
    assert "$B/" not in out


def test_correct_ticker_is_left_alone():
    ai = '{"title":"BNB Booster","reward":"Get Up to 400 BNB","eligibility":"For All User","token":"BNB","pair":"USDT"}'
    out = format_with(ai, "BNB Booster https://x", {"title": "t", "text": PAGE_BNB, "join_link": "https://x"})
    assert "Get Up to 400 BNB" in out and "$BNB/USDT" in out


def test_reward_asset_and_campaign_token_differ():
    # Reward asset (USDT) must not be copied into the campaign token (ENA).
    page = "ENA Campaign\nTotal reward pool: 500,000 USDT for all users"
    ai = '{"title":"T","reward":"Get 500 USDT","eligibility":"For All User","token":"ENA","pair":"USDT"}'
    out = format_with(ai, "ENA Campaign https://x", {"title": "t", "text": page, "join_link": "https://x"})
    assert "Get 500,000 USDT" in out and "Make Volume on $ENA/USDT" in out


# ---- Bug 2: comma numbers must be restored in full ----
def test_five_digit_pool_restored():
    page = "ENA Campaign\nTotal reward pool: 500,000 USDT for all users"
    ai = '{"title":"T","reward":"Get 500 USDT","eligibility":"For All User","token":"ENA","pair":"USDT"}'
    out = format_with(ai, "ENA Campaign https://x", {"title": "t", "text": page, "join_link": "https://x"})
    assert "Get 500,000 USDT" in out


def test_seven_digit_pool_restored():
    page = "FDUSD Event\nShare a total reward pool of 1,000,000 FDUSD in rewards!"
    ai = '{"title":"T","reward":"Get 1,000 FDUSD","eligibility":"For All User","token":"FDUSD","pair":"USDT"}'
    out = format_with(ai, "FDUSD Event https://x", {"title": "t", "text": page, "join_link": "https://x"})
    assert "Get 1,000,000 FDUSD" in out


def test_ai_that_copies_the_full_number_is_untouched():
    page = "FDUSD Event\nShare a total reward pool of 1,000,000 FDUSD in rewards!"
    ai = '{"title":"T","reward":"Get 1,000,000 FDUSD","eligibility":"For All User","token":"FDUSD","pair":"USDT"}'
    out = format_with(ai, "FDUSD Event https://x", {"title": "t", "text": page, "join_link": "https://x"})
    assert "Get 1,000,000 FDUSD" in out
    assert "Get 1,000,000,000" not in out


# ---- the repair never invents values ----
def test_no_repair_without_evidence_in_text():
    ai = '{"title":"T","reward":"Get Up 350 $FF","eligibility":"For All User","token":"FF","pair":"USDT"}'
    page = {"title": "T", "text": "Promotion Period: 2026-10-22 10:00 (UTC)", "join_link": "https://x"}
    out = format_with(ai, "Spot Tournament https://x", page)
    assert "Get Up 350 $FF" in out and "$FF/USDT" in out


def test_wrong_number_is_not_rewritten():
    # AI number not related to any number in the text -> left as the AI wrote it.
    page = "ENA Campaign\nTotal reward pool: 500,000 USDT for all users"
    ai = '{"title":"T","reward":"Get 42 USDT","eligibility":"For All User","token":"ENA","pair":"USDT"}'
    out = format_with(ai, "ENA Campaign https://x", {"title": "t", "text": page, "join_link": "https://x"})
    assert "Get 42 USDT" in out


# ---- unit behaviour of the helpers ----
def test_reward_candidates_extraction():
    text = ("Share a reward pool of 100,000 USDC in prizes!\n"
            "Up to 400 BNB for traders\n"
            "$50,000 worth of FLAP in airdrops\n"
            "Runs for 30 days with 61 winners")
    assert A._reward_candidates(text) == [("100,000", "USDC"), ("400", "BNB"), ("50,000", "FLAP")]


def test_number_truncated_detection():
    assert A._number_truncated("500", "500,000")
    assert A._number_truncated("1,000", "1,000,000")
    assert not A._number_truncated("50", "500,000")   # not a comma split
    assert not A._number_truncated("600", "500,000")
    assert not A._number_truncated("500,000", "500,000")


def test_missing_reward_falls_back_to_text_value():
    page = "ENA Campaign\nTotal reward pool: 500,000 USDT for all users"
    ai = '{"title":"T","reward":null,"eligibility":"For All User","token":"ENA","pair":"USDT"}'
    out = format_with(ai, "ENA Campaign https://x", {"title": "t", "text": page, "join_link": "https://x"})
    assert "🎁 500,000 USDT" in out


def test_booster_clean_reward_keeps_full_comma_number():
    import booster as B
    long_line = "2,000,000 TMX shared across all eligible participants this round only"
    assert B.clean_reward(long_line) == long_line          # no 60-char cut
    assert B.clean_reward("1,000,000 USDT") == "1,000,000 USDT"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))


# Bug 3: truncated header number chosen over the full 🎁 reward line.
FF_POST = ("🛒Spot Trading Tournament Season 6: Trade to Share Up to 2,300\n"
           "🎁 2,300,000 FF\n🎉 For All User")


def _ff(reward):
    import json
    return format_with(json.dumps({"title": "Spot Trading Tournament S6", "reward": reward,
                                   "eligibility": "For All User", "token": "FF", "pair": "USDT"}),
                       FF_POST, {"text": ""})


def test_gift_line_beats_truncated_header():
    for r in ["Trade to Share Up to 2,300", "Up to 2,300", "2,300 FF", "", "2,300,000 FF"]:
        out = _ff(r)
        assert "2,300,000 FF" in out, (r, out)
        assert "2,300**" not in out and "2,300 " not in out.replace("2,300,000", "")


def test_ai_reward_kept_when_it_matches_gift_line():
    d = A._apply_reward_repair({"reward": "Get Up to 400 BNB"}, "🎁 400 BNB\nTrade now")
    assert d["reward"] == "Get Up to 400 BNB"
