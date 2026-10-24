"""Shared Telegram reference validation."""
import re
from urllib.parse import urlsplit


def normalize_channel(value):
    value = (value or '').strip()
    if re.fullmatch(r'-?[1-9]\d*', value):
        return value if value.startswith('-') else '-100' + value
    if value.startswith('@'):
        value = value[1:]
    elif re.match(r'^(?:https?://)?(?:www\.)?t\.me/', value, re.I):
        parsed = urlsplit(value if '://' in value else 'https://' + value)
        if parsed.hostname not in ('t.me', 'www.t.me') or parsed.username or parsed.port:
            raise ValueError('চ্যানেলের সঠিক Telegram লিংক দিন।')
        value = parsed.path.strip('/')
        if value.startswith('s/'): value = value[2:]
        if re.fullmatch(r'c/[1-9]\d*', value): return '-100' + value.split('/')[1]
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', value):
        raise ValueError('চ্যানেলের @username, t.me/channelname লিংক অথবা সংখ্যার আইডি দিন; পোস্ট বা আমন্ত্রণ লিংক নয়।')
    return value.lower()


def telegram_entity(value):
    value = normalize_channel(value)
    return int(value) if value.startswith('-') else value


def display_channel(value):
    return value if value.lstrip('-').isdigit() else '@' + value.lstrip('@')


def parse_post_link(value):
    value = (value or '').strip()
    if not re.match(r'^(?:https?://)?(?:www\.)?t\.me/', value, re.I):
        raise ValueError('পোস্টের লিংক দিন: https://t.me/channelname/123')
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    if parsed.hostname not in ('t.me', 'www.t.me') or parsed.username or parsed.port:
        raise ValueError('পোস্টের সঠিক Telegram লিংক দিন।')
    path = parsed.path.strip('/')
    if path.startswith('s/'): path = path[2:]
    private = re.fullmatch(r'c/([1-9]\d*)/([1-9]\d*)', path)
    if private: return int('-100' + private[1]), int(private[2])
    public = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]{3,31})/([1-9]\d*)', path)
    if public: return normalize_channel(public[1]), int(public[2])
    raise ValueError('একটি পোস্টের লিংক দিন: https://t.me/channelname/123 অথবা https://t.me/c/123456/789')


def short_hash(value):
    """8-char fingerprint so a ❌ button only deletes the exact item it was shown for."""
    import hashlib
    return hashlib.sha1(str(value).lower().encode()).hexdigest()[:8]
