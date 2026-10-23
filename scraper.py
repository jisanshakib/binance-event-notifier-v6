"""Fetch a Binance announcement and extract the 'Join Now' style campaign link."""
import html
import json
import re
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "clienttype": "web",
    "lang": "en",
}
CTA = re.compile(r"(join\s*now|register\s*now|participate|join\s*the|register\s*here|join\s*here|landing\s*page|claim|click\s*here|go\s*to\s*event|start\s*now)", re.I)
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
ARTICLE_CODE = re.compile(r"/(?:announcement/detail|support/announcement)/(?:[\w-]+-)?([0-9a-f]{32})", re.I)


def _norm_url(u: str) -> str:
    u = (u or "").strip().rstrip(".,;:!?…")
    if u and not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    return u


def find_binance_url(text: str, entity_urls: list[str]) -> str | None:
    urls = [u for u in (_norm_url(x) for x in entity_urls + URL_RE.findall(text or "")) if u and not u.startswith(("https://t.me", "http://t.me"))]
    for u in urls:
        if "binance" in u and ("announcement" in u or "/support/" in u):
            return u
    for u in urls:
        if "binance" in u:
            return u
    return urls[0] if urls else None


def _walk_json_content(node, links, texts):
    """Binance CMS body is a JSON tree (nodes with 'child', 'attr', 'text')."""
    if isinstance(node, dict):
        if node.get("node") == "text" and node.get("text"):
            texts.append(node["text"])
        attr = node.get("attr") or {}
        href = attr.get("href")
        if href:
            sub_texts = []
            _walk_json_content(node.get("child", []), [], sub_texts)
            links.append((" ".join(sub_texts).strip(), href))
            texts.extend(sub_texts)
        else:
            _walk_json_content(node.get("child", []), links, texts)
    elif isinstance(node, list):
        for n in node:
            _walk_json_content(n, links, texts)


def _from_cms_api(client: httpx.Client, url: str):
    m = ARTICLE_CODE.search(url)
    if not m:
        return None
    r = client.get(
        "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query",
        params={"articleCode": m.group(1)},
    )
    if r.status_code != 200:
        return None
    data = (r.json() or {}).get("data") or {}
    body = data.get("body")
    if not body:
        return None
    links, texts = [], []
    try:
        _walk_json_content(json.loads(body), links, texts)
    except Exception:
        soup = BeautifulSoup(body, "html.parser")
        links = [(a.get_text(" ", strip=True), a["href"]) for a in soup.find_all("a", href=True)]
        texts = [soup.get_text("\n", strip=True)]
    return data.get("title", ""), "\n".join(texts), links


def _from_html(client: httpx.Client, url: str):
    r = client.get(url)
    if r.status_code >= 400:
        raise RuntimeError(f"page returned {r.status_code}")
    if re.search(r"(captcha|access denied|attention required|verify you are human)", r.text[:5000], re.I):
        raise RuntimeError("page blocked by anti-bot protection")
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    links = [(a.get_text(" ", strip=True), urljoin(url, a["href"])) for a in soup.find_all("a", href=True)]
    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    return title, soup.get_text("\n", strip=True), links


def fix_url(u: str) -> str:
    """Binance CMS links contain placeholders like https://www.%suffixOrigin%/%locale%/..."""
    return (u or "").replace("%suffixOrigin%", "binance.com").replace("%locale%", "en").strip()


def _clean_text(t: str) -> str:
    t = html.unescape(t or "").replace("\xa0", " ")
    return "\n".join(" ".join(l.split()) for l in t.splitlines() if l.strip())


def pick_cta(links, fallback):
    links = [(t, fix_url(h)) for t, h in links]
    for text, href in links:
        if CTA.search(text or "") and href.startswith("http"):
            return href
    for text, href in links:  # activity / campaign pages
        if re.search(r"binance\.com/.*/(activity|campaign|events?|trading-competition|launchpool)", href):
            return href
    return fallback


def scrape(url: str) -> dict:
    with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as client:
        res = None
        try:
            res = _from_cms_api(client, url)
        except Exception:
            res = None
        if not res:
            res = _from_html(client, url)
    title, text, links = res
    text = _clean_text(text)
    return {"title": html.unescape(title or ""), "text": text[:20000], "join_link": pick_cta(links, url), "url": url}
