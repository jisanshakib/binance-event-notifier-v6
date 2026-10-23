"""AI parsing + strict Telegram Markdown formatting (all bold except link)."""
import asyncio
import json
import re
import httpx
import config

PROMPT = """You extract crypto campaign details from a Binance announcement.
Return ONLY valid JSON with these keys:
{{"title": short campaign title (e.g. "Spot Trading Tournament S6"),
 "reward": short reward line (e.g. "Get Up 350 $FF"),
 "eligibility": who can join (e.g. "For All User"),
 "token": the campaign token symbol (e.g. "ENA"),
 "pair": the quote asset of the trading pair (e.g. "USDC"; use "USDT" if unclear)}}
Do not include dates. Keep every value under 60 characters.

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
    if not data.get("title"):  # AI failed or returned nothing -> safe fallback, never crash
        first = next((l.strip() for l in (post_text or "").splitlines() if l.strip()), "")
        data["title"] = (page.get("title") or first or "New Binance Event")[:60]
    return build_post(data, page.get("join_link") or "https://www.binance.com", start, end)
