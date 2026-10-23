"""Find UTC timestamps in announcement text and convert to BST (UTC+6)."""
import re
from datetime import datetime, timedelta, timezone

BST = timezone(timedelta(hours=6))

# 2026-10-22 08:00 (UTC) | 2026-10-22 08:00:00 UTC
_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T,]+(\d{1,2}):(\d{2})(?::\d{2})?\s*\(?UTC\)?", re.I)
# 22 October 2026 08:00 UTC | October 22, 2026, 08:00 (UTC)
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DMY = re.compile(rf"(\d{{1,2}})\s+({_MONTHS})[a-z]*,?\s+(\d{{4}}),?\s+(?:at\s+)?(\d{{1,2}}):(\d{{2}})\s*\(?UTC\)?", re.I)
_MDY = re.compile(rf"({_MONTHS})[a-z]*\s+(\d{{1,2}}),?\s+(\d{{4}}),?\s+(?:at\s+)?(\d{{1,2}}):(\d{{2}})\s*\(?UTC\)?", re.I)
_MIDX = {m: i + 1 for i, m in enumerate(_MONTHS.split("|"))}


def _safe(found, pos, y, mo, d, h, mi):
    try:
        found.append((pos, datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=timezone.utc)))
    except ValueError:
        pass  # impossible date like 2026-13-40 or 25:00 -> skip instead of crashing


def find_utc_times(text: str) -> list[datetime]:
    text = text or ""
    found = []
    for m in _ISO.finditer(text):
        y, mo, d, h, mi = m.groups()
        _safe(found, m.start(), y, mo, d, h, mi)
    for m in _DMY.finditer(text):
        d, mon, y, h, mi = m.groups()
        _safe(found, m.start(), y, _MIDX[mon[:3].lower()], d, h, mi)
    for m in _MDY.finditer(text):
        mon, d, y, h, mi = m.groups()
        _safe(found, m.start(), y, _MIDX[mon[:3].lower()], d, h, mi)
    found.sort(key=lambda x: x[0])
    out = []
    for _, dt in found:
        if dt not in out:
            out.append(dt)
    return out


def fmt_bst(dt: datetime, now: datetime | None = None) -> str:
    """'at Today 4:00 PM' for today, otherwise '28 Oct at 4:00 PM' (Bangladesh time, UTC+6)."""
    local = dt.astimezone(BST)
    now = (now or datetime.now(timezone.utc)).astimezone(BST)
    h12 = local.hour % 12 or 12
    t = f"{h12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    if local.date() == now.date():
        return f"at Today {t}"
    return f"{local.day} {_MONTHS.split('|')[local.month - 1].capitalize()} at {t}"


def start_end_bst(text: str):
    times = find_utc_times(text)
    start = fmt_bst(times[0]) if times else None
    later = [t for t in times[1:] if t > times[0]] if times else []
    end = fmt_bst(later[0]) if later else None
    return start, end
