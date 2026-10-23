#!/usr/bin/env python3
"""
Telegram String Session Generator
Generates a Telethon StringSession for the Binance TG Bot listener.

Run: python generate_session.py
"""

import os
import sys
import getpass
from pathlib import Path

# Try loading .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        SessionPasswordNeededError,
        PhoneCodeInvalidError,
        PhoneCodeExpiredError,
        PasswordHashInvalidError,
        ApiIdInvalidError,
        PhoneNumberInvalidError,
        FloodWaitError,
        PhoneNumberBannedError
    )
except ImportError:
    print("\n❌ Error: Telethon is not installed!")
    print("Please install required libraries first by running:")
    print("    pip install telethon python-dotenv\n")
    sys.exit(1)


def clean_str(val: str) -> str:
    """Strips quotes and extra whitespace."""
    if not val:
        return ""
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val


def get_api_credentials():
    """Gets API_ID and API_HASH from .env or interactive user input."""
    env_api_id = clean_str(os.getenv("API_ID", ""))
    env_api_hash = clean_str(os.getenv("API_HASH", ""))

    print("=" * 60)
    print("   TELEGRAM STRING SESSION GENERATOR")
    print("   (Used by the Binance Bot to listen to Telegram channels)")
    print("=" * 60)

    # If already in .env
    if env_api_id and env_api_hash:
        masked_hash = env_api_hash[:4] + "..." + env_api_hash[-4:] if len(env_api_hash) > 8 else "***"
        print(f"\nFound existing credentials in .env:")
        print(f"  API_ID:   {env_api_id}")
        print(f"  API_HASH: {masked_hash}")
        use_env = input("Use these credentials? [Y/n]: ").strip().lower()
        if use_env in ("", "y", "yes"):
            try:
                return int(env_api_id), env_api_hash
            except ValueError:
                print("❌ API_ID in .env is not a valid number. Let's enter them manually.")

    print("\nGet your API_ID and API_HASH for FREE from Telegram:")
    print("  1. Go to: https://my.telegram.org")
    print("  2. Log in with your phone number")
    print("  3. Click 'API development tools'")
    print("  4. Create an app (any name/title is fine)\n")

    while True:
        raw_id = input("Enter your API_ID (digits only, e.g. 12345678): ").strip()
        raw_id = clean_str(raw_id)
        if raw_id.isdigit():
            api_id = int(raw_id)
            break
        print("❌ Invalid API_ID. It must be numeric digits only. Please try again.")

    while True:
        api_hash = clean_str(input("Enter your API_HASH (32 characters): "))
        if len(api_hash) >= 10:
            break
        print("❌ Invalid API_HASH. Please copy the full API_HASH from my.telegram.org.")

    return api_id, api_hash


def save_to_env(api_id: int, api_hash: str, session_string: str):
    """Saves or updates .env file with the generated credentials."""
    env_path = Path(".env")
    example_path = Path(".env.example")

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    elif example_path.exists():
        existing_lines = example_path.read_text(encoding="utf-8").splitlines()

    updated = {}
    updated["API_ID"] = str(api_id)
    updated["API_HASH"] = api_hash
    updated["User_session"] = session_string

    new_lines = []
    seen = set()
    for line in existing_lines:
        line_stripped = line.strip()
        matched_key = None
        for k in updated:
            if line_stripped.startswith(f"{k}=") or line_stripped.startswith(f"# {k}="):
                matched_key = k
                break
        if matched_key:
            new_lines.append(f"{matched_key}={updated[matched_key]}")
            seen.add(matched_key)
        else:
            new_lines.append(line)

    for k, v in updated.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("\n✅ Successfully updated your local .env file with User_session, API_ID, and API_HASH!")


def main():
    try:
        api_id, api_hash = get_api_credentials()
    except (KeyboardInterrupt, EOFError):
        print("\n\nOperation cancelled.")
        return

    print("\n" + "-" * 60)
    print("NOTE: Enter your personal phone number (with country code).")
    print("Do NOT enter a bot token. The listener must run as a user.")
    print("Example: +8801712345678 or +12025550123")
    print("-" * 60)

    while True:
        try:
            phone = clean_str(input("\nEnter Phone Number: "))
            if not phone:
                continue
            if not phone.startswith("+"):
                phone = "+" + phone
            break
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled.")
            return

    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        client.connect()
    except ApiIdInvalidError:
        print("\n❌ Error: Invalid API_ID or API_HASH! Please double-check them on https://my.telegram.org.")
        return
    except Exception as e:
        print(f"\n❌ Connection error: {e}")
        return

    # Request code
    print("\n⏳ Sending login code to Telegram...")
    try:
        sent_code = client.send_code_request(phone)
    except PhoneNumberInvalidError:
        print("\n❌ Error: Invalid phone number format! Make sure to include the country code (e.g. +880...).")
        client.disconnect()
        return
    except PhoneNumberBannedError:
        print("\n❌ Error: This phone number is banned by Telegram.")
        client.disconnect()
        return
    except FloodWaitError as e:
        print(f"\n❌ Telegram flood wait: You made too many attempts. Please wait {e.seconds} seconds.")
        client.disconnect()
        return
    except ApiIdInvalidError:
        print("\n❌ Error: Invalid API_ID or API_HASH.")
        client.disconnect()
        return
    except Exception as e:
        print(f"\n❌ Error sending code: {e}")
        client.disconnect()
        return

    print("\n" + "=" * 60)
    print("📩 Verification code has been sent!")
    print("⚠️  IMPORTANT:")
    print("   Check the official 'Telegram' app chat on your phone or PC!")
    print("   Telegram almost always sends the code inside the app, NOT via SMS.")
    print("=" * 60)

    # Prompt for code with retry
    signed_in = False
    for attempt in range(3):
        try:
            code = clean_str(input("\nEnter the code received in Telegram: "))
            if not code:
                continue
            # Remove hyphens or spaces if user pasted "123-45"
            code = code.replace("-", "").replace(" ", "")

            try:
                client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
                signed_in = True
                break
            except SessionPasswordNeededError:
                # 2FA enabled
                print("\n🔐 Two-Step Verification (2FA) Cloud Password is enabled on this account.")
                for pw_attempt in range(3):
                    try:
                        password = getpass.getpass("Enter your 2FA Cloud Password: ")
                        client.sign_in(password=password)
                        signed_in = True
                        break
                    except PasswordHashInvalidError:
                        print("❌ Incorrect 2FA password. Please try again.")
                    except FloodWaitError as e:
                        print(f"❌ Flood wait: Please wait {e.seconds} seconds.")
                        break
                if signed_in:
                    break
                else:
                    print("❌ Failed to authenticate with 2FA password.")
                    client.disconnect()
                    return
            except PhoneCodeInvalidError:
                print(f"❌ Incorrect code (attempt {attempt + 1}/3). Please try again.")
            except PhoneCodeExpiredError:
                print("❌ The code has expired. Please run the script again to get a fresh code.")
                client.disconnect()
                return
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled.")
            client.disconnect()
            return

    if not signed_in:
        print("\n❌ Failed to verify code after 3 attempts.")
        client.disconnect()
        return

    session_string = client.session.save()
    client.disconnect()

    print("\n" + "=" * 60)
    print("🎉 SUCCESS! YOUR SESSION STRING HAS BEEN GENERATED:")
    print("=" * 60)
    print(f"\nUser_session={session_string}\n")
    print("=" * 60)

    # Ask to save to .env
    try:
        save_choice = input("Do you want to automatically save this to your .env file? [Y/n]: ").strip().lower()
        if save_choice in ("", "y", "yes"):
            save_to_env(api_id, api_hash, session_string)
    except (KeyboardInterrupt, EOFError):
        pass

    print("\n📌 NEXT STEPS:")
    print("1. If deploying to Render, go to Render Dashboard > Environment > Add:")
    print(f"   Key:   User_session")
    print(f"   Value: {session_string}")
    print("2. Make sure your account has joined the Binance Telegram channels you want to monitor.")
    print("3. Start the bot and use /status in Telegram to check health!\n")


if __name__ == "__main__":
    main()
