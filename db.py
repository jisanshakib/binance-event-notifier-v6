"""Supabase Postgres storage via the SUPABASE_URI connection string (psycopg 3)."""
import threading
import psycopg
import config

SCHEMA = """
create table if not exists source_channels (id bigserial primary key, username text unique not null, created_at timestamptz default now());
create table if not exists keywords (id bigserial primary key, word text unique not null, created_at timestamptz default now());
create table if not exists settings (key text primary key, value text);
create table if not exists processed_posts (
  id bigserial primary key, source_chat text not null, message_id bigint not null,
  status text, output text, error text, created_at timestamptz default now(),
  unique (source_chat, message_id));
"""

_lock = threading.RLock()
_conn = None


def _connect():
    uri = config.SUPABASE_URI
    if "sslmode=" not in uri:
        uri += ("&" if "?" in uri else "?") + "sslmode=require"
    # prepare_threshold=None -> works with Supabase's pgbouncer pooler (port 6543)
    return psycopg.connect(uri, autocommit=True, prepare_threshold=None, connect_timeout=15)


def _q(sql, params=None, fetch=True):
    """Run a query; reconnect once if the connection dropped."""
    global _conn
    with _lock:
        for attempt in (1, 2):
            try:
                if _conn is None or _conn.closed:
                    _conn = _connect()
                with _conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall() if fetch and cur.description else cur.rowcount
            except (psycopg.OperationalError, psycopg.InterfaceError):
                try:
                    _conn and _conn.close()
                except Exception:
                    pass
                _conn = None
                if attempt == 2:
                    raise


def init_schema():
    for stmt in [s for s in SCHEMA.split(";") if s.strip()]:
        _q(stmt, fetch=False)
    if not get_setting("seeded") and not get_channels():
        for ch in ("binance_announcements", "binance_wallet_channel"):
            add_channel(ch)
    set_setting("seeded", "1")


def _norm(ch: str) -> str:
    ch = (ch or "").strip()
    for p in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if ch.startswith(p):
            ch = ch[len(p):]
    return ch.strip("/").lower()


def _clean_kw(w: str) -> str:
    return " ".join((w or "").split())


def get_channels() -> list[str]:
    return [r[0] for r in _q("select username from source_channels order by id")]


def add_channel(ch):
    _q("insert into source_channels(username) values (%s) on conflict (username) do nothing", (_norm(ch),), fetch=False)


def remove_channel(ch) -> bool:
    return _q("delete from source_channels where username=%s", (_norm(ch),), fetch=False) > 0


def get_keywords() -> list[str]:
    return [r[0] for r in _q("select word from keywords order by id")]


def add_keyword(w) -> bool:
    """False if it already exists (any letter case)."""
    w = _clean_kw(w)
    if not w or _q("select 1 from keywords where lower(word)=lower(%s)", (w,)):
        return False
    _q("insert into keywords(word) values (%s) on conflict (word) do nothing", (w,), fetch=False)
    return True


def remove_keyword(w) -> bool:
    return _q("delete from keywords where lower(word)=lower(%s)", (_clean_kw(w),), fetch=False) > 0


def get_setting(key, default=None):
    r = _q("select value from settings where key=%s", (key,))
    return r[0][0] if r else default


def set_setting(key, value):
    _q("insert into settings(key,value) values (%s,%s) on conflict (key) do update set value=excluded.value",
       (key, str(value)), fetch=False)


def claim_post(chat, msg_id) -> bool:
    """Atomically reserve a post; the unique constraint guarantees it is posted only once."""
    return _q("insert into processed_posts(source_chat,message_id,status) values (%s,%s,'processing') "
              "on conflict (source_chat,message_id) do nothing", (chat, msg_id), fetch=False) > 0


def log_post(chat, msg_id, status, output=None, error=None):
    _q("insert into processed_posts(source_chat,message_id,status,output,error) values (%s,%s,%s,%s,%s) "
       "on conflict (source_chat,message_id) do update set status=excluded.status, output=excluded.output, error=excluded.error",
       (chat, msg_id, status, output, error), fetch=False)
