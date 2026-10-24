"""Booster extraction regressions. Run: python -m unittest test_booster_extraction.py"""
import json
import unittest
from unittest.mock import patch
import test_bot as fixtures
import booster as B


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def extract_answer(self, text, answer):
        async def fake(prompt):
            return json.dumps(answer) if not isinstance(answer, str) else answer
        with patch.object(B, 'ask_gemini', fake):
            return await B.extract(text)

    async def test_request_uses_json_schema_and_flash(self):
        requests = []
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                requests.append((url, kwargs['json']))
                return fixtures.types.SimpleNamespace(status_code=200, json=lambda: {
                    'candidates': [{'content': {'parts': [{'text': '{"target_name":"SWELL","event_type":null,"alpha_points":"61"}'}]}}]})
        with patch.object(B.httpx, 'AsyncClient', Client):
            response = await B.ask_gemini('post')
        self.assertEqual(json.loads(response)['target_name'], 'SWELL')
        self.assertTrue(requests[0][0].endswith('gemini-2.5-flash:generateContent'))
        generation = requests[0][1]['generationConfig']
        self.assertEqual(generation['responseMimeType'], 'application/json')
        self.assertEqual(set(generation['responseSchema']['required']), {'target_name', 'event_type', 'alpha_points'})
        self.assertTrue(generation['responseSchema']['properties']['event_type']['nullable'])

    async def test_dollar_ticker(self):
        d = await self.extract_answer('$SWELL Booster, 61 Alpha Points', {'target_name': '$SWELL', 'alpha_points': '61'})
        self.assertEqual(d['token'], 'SWELL')

    async def test_hash_ticker(self):
        d = await self.extract_answer('#ENA Campaign', {'target_name': '#ENA'})
        self.assertEqual(d['token'], 'ENA')

    async def test_service_product_and_company_names_preserved(self):
        for name in ('Megadrop', 'Launchpool', 'Web3 Wallet', 'Binance Wallet', 'Acme Labs'):
            with self.subTest(name=name):
                d = await self.extract_answer(name + ' Campaign', {'target_name': name})
                self.assertEqual(d['token'], name)

    async def test_pair_base_target(self):
        d = await self.extract_answer('SWELL/USDT Listing Rewards', {'target_name': 'SWELL'})
        self.assertEqual(d['token'], 'SWELL')

    async def test_campaign_name_without_ticker_is_preserved(self):
        name = 'Binance Wallet Booster Campaign'
        d = await self.extract_answer(name + ': join today', {'target_name': name})
        self.assertEqual(d['token'], name)

    async def test_mixed_case_project_name_has_no_forced_dollar(self):
        d = await self.extract_answer('Swell Booster Campaign', {'target_name': 'Swell'})
        self.assertEqual(d['token'], 'Swell')
        self.assertEqual(B.build_post(d).split(' × ', 1)[0], 'Swell')

    async def test_formatter_removes_marker_without_altering_campaign_name(self):
        for name in ('Swell', 'Binance Wallet Booster Campaign'):
            with self.subTest(name=name):
                self.assertEqual(B.build_post({'token': '$' + name}).split(' × ', 1)[0], name)

    async def test_raw_campaign_name_publish_flow(self):
        fixtures.reset()
        B.add_keyword('Booster')
        name = 'Binance Wallet Booster Campaign'
        async def fake(prompt):
            return json.dumps({'target_name': name, 'event_type': None, 'alpha_points': '61'})
        with patch.object(B, 'ask_gemini', fake):
            success = await fixtures.BOOST.process(fixtures.msg(912, name + ': 61 Alpha Points'), 'boostch')
        self.assertTrue(success)
        self.assertEqual(fixtures.FAKE.data['b_posts'][('boostch', 912)], 'posted')
        self.assertEqual(len(fixtures.SENT), 1)
        self.assertEqual(fixtures.SENT[0][1].split(' × ', 1)[0], name)

    async def test_exact_dynamic_points(self):
        for points in ('1', '2', '61', '987'):
            with self.subTest(points=points):
                d = await self.extract_answer(f'SWELL Booster requires {points} Alpha Points', {'target_name': 'SWELL', 'alpha_points': points})
                self.assertEqual(d['alpha_points'], points)

    async def test_absent_points_are_na_not_rewards(self):
        d = await self.extract_answer('$SWELL Booster: 61 users win 100 tokens', {'target_name': 'SWELL', 'alpha_points': 'N/A'})
        self.assertEqual(d['alpha_points'], 'N/A')

    async def test_points_fallback_before_and_after_label(self):
        for text in ('$SWELL requires 61 Binance Alpha Points', '$SWELL Alpha Points requirement: 2', '$SWELL Alpha Points: 1'):
            d = await self.extract_answer(text, {'target_name': 'SWELL', 'alpha_points': 'N/A'})
            self.assertEqual(d['alpha_points'], '61' if '61' in text else '2' if '2' in text else '1')

    async def test_explicit_fcfs(self):
        for wording in ('FCFS users Event', 'first-come-first-served'):
            d = await self.extract_answer('$SWELL Booster ' + wording, {'target_name': 'SWELL', 'event_type': 'FCFS users Event'})
            self.assertEqual(d['event_type'], 'FCFS users Event')

    async def test_explicit_all_users(self):
        d = await self.extract_answer('$ENA Booster All user Event', {'target_name': 'ENA', 'event_type': 'All user Event'})
        self.assertEqual(d['event_type'], 'All user Event')

    async def test_event_not_guessed(self):
        for event in ('FCFS users Event', 'All user Event', None, '', 'first reward', 'allotment'):
            d = await self.extract_answer('$SWELL Booster 61 Alpha Points', {'target_name': 'SWELL', 'event_type': event})
            self.assertEqual(d['event_type'], '')

    async def test_fallback_marked_tickers(self):
        for text, target in (('$SWELL Airdrop', 'SWELL'), ('#ENA Rewards', 'ENA')):
            d = await self.extract_answer(text, {'target_name': 'N/A'})
            self.assertEqual(d['token'], target)

    async def test_fallback_near_keywords(self):
        for text in ('SWELL Booster', 'Booster: SWELL', 'SWELL new Campaign', 'Campaign for SWELL'):
            d = await self.extract_answer(text, {'target_name': None})
            self.assertEqual(d['token'], 'SWELL')

    async def test_fallback_on_ai_error(self):
        async def fail(prompt): raise RuntimeError('offline')
        with patch.object(B, 'ask_gemini', fail):
            d = await B.extract('$SWELL Booster requires 61 Alpha Points')
        self.assertEqual(d, {'token': 'SWELL', 'event_type': '', 'alpha_points': '61'})

    async def test_malformed_or_non_object_json_uses_fallback(self):
        for answer in ('not JSON', 'null', '[]', '{broken', '```json\n{}\n```'):
            d = await self.extract_answer('#ENA Booster', answer)
            self.assertEqual(d['token'], 'ENA')

    async def test_no_false_target_when_no_ticker(self):
        for text in ('Booster with 5 Alpha Points', 'FCFS Booster UTC', '#USDT Campaign', '#TOOLONGTICKER Campaign', '#A Campaign', 'Booster https://example.org/$ENA'):
            d = await self.extract_answer(text, {'target_name': 'N/A'})
            self.assertEqual(d['token'], '')

    async def test_ambiguous_fallback_does_not_guess(self):
        d = await self.extract_answer('$ENA and $SWELL Rewards', {'target_name': 'N/A'})
        self.assertEqual(d['token'], '')

    async def test_full_post_sent_without_old_4000_character_cutoff(self):
        seen = []
        async def fake(prompt):
            seen.append(prompt)
            return '{"target_name":"SWELL","event_type":null,"alpha_points":"61"}'
        with patch.object(B, 'ask_gemini', fake):
            d = await B.extract('x ' * 2500 + '$SWELL Booster 61 Alpha Points')
        self.assertIn('$SWELL Booster 61 Alpha Points', seen[0])
        self.assertEqual(d['token'], 'SWELL')

    async def test_fallback_publish_flow(self):
        fixtures.reset()
        B.add_keyword('Booster')
        async def fake(prompt): return '{"target_name":"N/A","event_type":"All user Event","alpha_points":"N/A"}'
        with patch.object(B, 'ask_gemini', fake):
            success = await fixtures.BOOST.process(fixtures.msg(911, '#SWELL Booster 61 Alpha Points'), 'boostch')
        self.assertTrue(success)
        self.assertEqual(fixtures.FAKE.data['b_posts'][('boostch', 911)], 'posted')
        self.assertEqual(len(fixtures.SENT), 1)
        self.assertIn('SWELL ×', fixtures.SENT[0][1])
        self.assertIn('at least 61 Binance Alpha Points', fixtures.SENT[0][1])
        self.assertNotIn('All user Event', fixtures.SENT[0][1])


if __name__ == '__main__':
    unittest.main()


# ---- audit additions: Gemini missing/broken fields must fall back to the post text ----
def _run_extract(raw, post):
    import asyncio, booster
    async def fake(_p):
        if raw is None:
            raise RuntimeError("Gemini down")
        return raw
    orig, booster.ask_gemini = booster.ask_gemini, fake
    try:
        return asyncio.run(booster.extract(post))
    finally:
        booster.ask_gemini = orig


def test_audit_fcfs_tag_survives_missing_gemini_field():
    assert _run_extract("{}", "Swell Booster Campaign. 61 Alpha Points. FCFS.")["event_type"] == "FCFS users Event"


def test_audit_all_users_tag_when_gemini_down():
    assert _run_extract(None, "ENA Booster Campaign open to all users, 2 Alpha Points")["event_type"] == "All user Event"


def test_audit_no_tag_when_post_has_none():
    assert _run_extract("{}", "ENA Booster Campaign, 2 Alpha Points")["event_type"] == ""


def test_audit_mixed_case_name_when_gemini_down_no_dollar():
    d = _run_extract(None, "Swell Booster Campaign is live! Requires 61 Alpha Points.")
    assert d["token"] == "Swell" and d["alpha_points"] == "61"
    import booster
    assert booster.build_post(d).startswith("Swell × BinanceWallet Booster Campaign")


# ---- name must never be "not found": heading fallback + generic default ----
def test_heading_used_when_no_booster_keyword_and_gemini_down():
    assert _run_extract(None, "📢 NEW: Lorenzo Protocol on Binance Wallet\n2 Alpha Points")["token"] == "Lorenzo Protocol"


def test_filler_words_never_become_the_name():
    assert _run_extract(None, "New Booster Campaign is LIVE!\nKelp DAO rewards for all users")["token"] == "Kelp DAO"


def test_post_without_any_name_still_gets_clean_header():
    import booster
    d = _run_extract("{}", "🚀🚀🚀\nhttps://x.com")
    assert d["token"] == ""
    assert booster.build_post(d).startswith("BinanceWallet Booster Campaign")
