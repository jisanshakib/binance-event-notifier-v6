"""Configuration. The bot uses EXACTLY these 7 environment variables:
API_ID, API_HASH, User_session, BOT_TOKEN, ADMIN_ID, SUPABASE_URI, GEMINI_API_KEY
(PORT is set automatically by Render for the health check; it is not something you configure.)"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

REQUIRED = ["API_ID", "API_HASH", "User_session", "BOT_TOKEN", "ADMIN_ID", "SUPABASE_URI", "GEMINI_API_KEY"]


def _get(name: str) -> str:
    return (os.environ.get(name) or "").strip().strip('"').strip("'")


_missing = [k for k in REQUIRED if not _get(k)]
if _missing:
    sys.exit("❌ Missing environment variables: " + ", ".join(_missing)
             + "\nSet them in Render → Environment (names are case-sensitive).")

try:
    API_ID = int(_get("API_ID"))
    ADMIN_ID = int(_get("ADMIN_ID"))
except ValueError:
    sys.exit("❌ API_ID and ADMIN_ID must be numbers.")

API_HASH = _get("API_HASH")
USER_SESSION = _get("User_session")
BOT_TOKEN = _get("BOT_TOKEN")
SUPABASE_URI = _get("SUPABASE_URI")
GEMINI_API_KEY = _get("GEMINI_API_KEY")

if not SUPABASE_URI.startswith(("postgres://", "postgresql://")):
    sys.exit("❌ SUPABASE_URI must be the Postgres connection string from Supabase "
             "(Project Settings → Database → Connection string → URI), e.g. postgresql://postgres.xxx:PASSWORD@aws-0-...pooler.supabase.com:6543/postgres")

# fixed internal values (not environment variables)
GEMINI_MODEL = "gemini-2.5-flash"
PORT = int(os.environ.get("PORT") or 10000)
