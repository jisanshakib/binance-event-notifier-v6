"""AI parsing + strict Telegram Markdown formatting (all bold except link)."""
import asyncio
import json
import re
import httpx
import config

PROMPT = """You extract crypto campaign details from a Binance announcement.
Return ONLY valid JSON with these keys:
{{"title": short campaign title (e.g. "Spot Trading Tournament S6"),
 "reward": the reward line copied from the post (e.g. "Get Up to 400 BNB", "1,000,000 FDUSD Rewards"),
 "eligibility": who can join (e.g. "For All User"),
 "token": the campaign token symbol (e.g. "ENA"),
 "pair": the quote asset of the trading pair (e.g. "USDC"; use "USDT" if unclear)}}
Do not include dates. Keep the title under 60 characters and the reward under 100.

STRICT NUMBER RULES for "reward":
- Copy every number EXACTLY as written in the text, including commas: "500,000",
  "1,000,000". Never drop digits, never split a number at a comma, never round
  or shorten it, and never abbreviate (never "500K", "1M", "0.5M").
- Copy the reward asset as its COMPLETE symbol: "BNB", "FDUSD", "USDT". Never
  truncate it or drop letters (never "B" for "BNB", never "F" for "FDUSD").

STRICT TOKEN RULE for "token":
- "token" is the COMPLETE campaign token symbol exactly as written in the text
  (e.g. "ENA", "BNB"). Never truncate it or miss any letters.

Telegram post:
{post}

Announcement title: {title}
Announcement text:
{text}
"""


def _parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s or "", re.S)
    d = json.loads(m.group(0)) if m else {}
    if not isinstance(d, dict):
        return {}
    if not isinstance(d.get("steps"), list):
        d["steps"] = []
    return d


_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def _ask(prompt: str) -> str:
    """Google Gemini (REST) - the only AI provider. One retry on rate-limit/server errors."""
    for attempt in (1, 2):
        r = await _ask_once(prompt)
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            await asyncio.sleep(4)
            continue
        break
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


async def _ask_once(prompt: str):
    async with httpx.AsyncClient(timeout=27) as client:
        return await client.post(
            _GEMINI_URL.format(model=config.GEMINI_MODEL),
            headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
        )


def _clean(s: str) -> str:
    # remove characters that would break **bold** markdown
    return str(s or "").replace("*", "").replace("[", "(").replace("]", ")").replace("_", " ").replace("`", "'").replace("~~", "~").replace("||", "|").strip()


def _safe_url(u: str) -> str:
    return (u or "https://www.binance.com").replace(")", "%29").replace("(", "%28").replace(" ", "%20")


# ---- deterministic repair: never post a truncated number or token symbol ----
# The AI may shorten "500,000" to "500" (comma split) or "BNB" to "B". These
# helpers restore such values using ONLY pairs that literally appear in the
# announcement text, so nothing is ever guessed or invented.
_REWARD_LINE = re.compile(r"(?i)reward|prize|pool|share|airdrop|giveaway|up\s+to|worth|total")
_REWARD_PAIR = re.compile(
    r"(?<![\d.,])(\$?\s?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|\$?\s?[0-9]+(?:\.[0-9]+)?)"
    r"\s*(?:worth\s+of\s+|in\s+|worth\s+)?\$?([A-Z][A-Z0-9]{1,11})\b")
_NOT_ASSET = {"ALPHA", "POINT", "POINTS", "USER", "USERS", "PARTICIPANTS", "WINNERS",
              "DAY", "DAYS", "HOUR", "HOURS", "TRADERS", "UTC", "GMT", "REWARD", "REWARDS"}
_REWARD_NUM = re.compile(r"(?<![\w.,])([0-9][0-9,]*(?:\.[0-9]+)?)(?![\d,])")
_TAIL_TOKEN = re.compile(r"^\s*(?:(?:worth\s+of|in|of)\s+)?\$?([A-Za-z][A-Za-z0-9]{0,11})?")


def _reward_candidates(text):
    """Amount+asset pairs that literally appear in reward-related lines of the text."""
    out = []
    for line in (text or "").splitlines():
        if not _REWARD_LINE.search(line):
            continue
        for m in _REWARD_PAIR.finditer(line):
            num, tok = m.group(1).lstrip("$").strip(), m.group(2)
            if tok and tok.upper() not in _NOT_ASSET:
                out.append((num, tok))
    return out


def _digits(s: str) -> str:
    return (s or "").replace(",", "")


def _number_truncated(ai_num: str, full_num: str) -> bool:
    """True when ai_num is full_num with whole ',000' groups cut off (comma split)."""
    a, b = _digits(ai_num), _digits(full_num)
    if len(b) <= len(a) or not b.startswith(a):
        return False
    lost = b[len(a):]
    return len(lost) % 3 == 0 and lost.strip("0") == ""


def _repair_reward(reward, candidates):
    """Repair an AI reward line against amounts found in the source text.
    Restores digits cut at a comma ('500' -> '500,000' when the text says
    '500,000') and completes a truncated asset symbol ('B' -> 'BNB').
    Returns (reward, ai_token, full_token); full_token is set only when the
    asset itself was truncated, so it is safe evidence for fixing the token."""
    reward = " ".join(str(reward or "").split()).strip()
    if not reward or not candidates:
        return reward, "", ""
    m = _REWARD_NUM.search(reward)
    if not m:
        return reward, "", ""
    ai_num = m.group(1)
    tail = _TAIL_TOKEN.match(reward[m.end():])
    ai_tok = (tail.group(1) or "") if tail else ""
    prefix = reward[:m.start()].strip()
    best = None  # (num, tok, rank): 0 exact token, 1 no token given, 2 truncated token
    for num, tok in candidates:
        if _digits(ai_num) != _digits(num) and not _number_truncated(ai_num, num):
            continue
        if not ai_tok:
            best = (num, tok, 1)
            break
        if tok.upper() == ai_tok.upper():
            best = (num, tok, 0)
            break
        if tok.upper().startswith(ai_tok.upper()) and best is None:
            best = (num, tok, 2)
    if best is None:
        return reward, ai_tok, ""
    num, tok, _ = best
    fixed = (prefix + " " if prefix else "") + num + (f" {tok}" if tok else "")
    if fixed == reward:
        return reward, ai_tok, ""
    return fixed, ai_tok, tok


def _apply_reward_repair(data, source_text):
    """Safety net for the truncation bugs, applied to every AI result."""
    candidates = _reward_candidates(source_text)
    if not candidates:
        return data
    fixed, ai_tok, tok_used = _repair_reward(data.get("reward"), candidates)
    if fixed:
        data["reward"] = fixed
    if not data.get("reward"):  # AI gave no usable reward -> take the one from the text
        num, tok = candidates[0]
        data["reward"] = f"{num} {tok}"
    # Complete a campaign token the AI cut short ('B' for 'BNB'), but only when
    # the reward's own asset was truncated the same way in the same line.
    tok = str(data.get("token") or "").strip().lstrip("$")
    if tok and tok_used and ai_tok and len(tok) < len(tok_used) \
            and tok_used.upper().startswith(tok.upper()) \
            and tok_used.upper().startswith(ai_tok.upper()) and len(ai_tok) <= len(tok):
        data["token"] = tok_used
    return data


def b(s: str) -> str:
    s = _clean(s)
    return f"**{s}**" if s else ""


def token_symbol(tok: str) -> str:
    """'ENA' -> '$ENA'; '$ENA' stays '$ENA'."""
    tok = re.sub(r"[^A-Za-z0-9$]", "", str(tok or "")).upper().lstrip("$")
    return f"${tok}" if tok else ""


def pair_text(tok: str, pair: str) -> str:
    tok, pair = str(tok or ""), str(pair or "")
    if "/" in tok:  # Gemini returned the whole pair as the token
        tok, _, p2 = tok.partition("/")
        pair = pair or p2
    if "/" in pair:  # pair returned as 'ENA/USDC'
        pair = pair.rsplit("/", 1)[1]
    sym = token_symbol(tok)
    quote = re.sub(r"[^A-Za-z0-9]", "", str(pair or "")).upper() or "USDT"
    return f"{sym}/{quote}" if sym else ""


def build_post(d: dict, link: str, start: str | None, end: str | None, link_label: str = "Open✅") -> str:
    pair = pair_text(d.get("token"), d.get("pair"))
    lines = [
        b(f"🛒{d.get('title', 'New Binance Event')}"),
        b(f"🎁 {d.get('reward', 'Check Rewards')}"),
        b(f"🎉 {d.get('eligibility', 'For All User')}"),
        "",
        f"{b('🔗  Link :')} [{link_label}]({_safe_url(link)})",
        b("• Event Goes Live✅"),
        "",
        b(start or "at Today Now"),
        b("• Event Will Expire"),
        b(f"{end or 'Check Announcement'} ❕"),
        b("➡️Register Event Page"),
        b(f"➡️Make Volume on {pair}" if pair else "➡️Make Volume"),
        b("➡️More Volume More Reward"),
        b("➡️Done"),
    ]
    return "\n".join(lines)


async def format_post(post_text: str, page: dict, start, end) -> str:
    try:
        raw = await asyncio.wait_for(
            _ask(PROMPT.format(post=post_text[:3000], title=page.get("title", ""), text=page.get("text", "")[:15000])),
            timeout=60,
        )
        data = _parse_json(raw)
    except Exception as e:
        print("AI error:", repr(e))
        data = {}
    # Restore any truncated reward numbers / token symbols before building the post.
    data = _apply_reward_repair(data, (post_text or "") + "\n" + (page.get("text") or ""))
    if not data.get("title"):  # AI failed or returned nothing -> safe fallback, never crash
        first = next((l.strip() for l in (post_text or "").splitlines() if l.strip()), "")
        data["title"] = (page.get("title") or first or "New Binance Event")[:60]
    return build_post(data, page.get("join_link") or "https://www.binance.com", start, end)
