#!/usr/bin/env python3
"""Account management for Claude Token Stats.

Supports multiple accounts (API keys, org logins) with automatic detection
and user-configurable display names.
"""

import fcntl
import hashlib
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

STATS_DIR = Path.home() / ".claude" / "stats"
ACCOUNTS_FILE = STATS_DIR / "accounts.json"
ACCOUNTS_LOCK_FILE = STATS_DIR / ".accounts.lock"
CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"


def detect_account_id() -> str:
    """Detect the active account from environment.

    Priority:
    1. Manual override via CLAUDE_STATS_ACCOUNT env var
    2. API key hash (first 20 chars of key, SHA256 truncated to 12 hex)
    3. Org UUID from Claude settings (forceLoginOrgUUID)
    4. Default account for legacy/unknown sessions
    """
    # 1. Manual override
    if override := os.environ.get("CLAUDE_STATS_ACCOUNT"):
        return override

    # 2. API key hash
    if api_key := os.environ.get("ANTHROPIC_API_KEY"):
        key_hash = hashlib.sha256(api_key[:20].encode()).hexdigest()[:12]
        return f"api_{key_hash}"

    # 3. Org UUID from Claude settings
    try:
        if CLAUDE_SETTINGS_FILE.exists():
            with open(CLAUDE_SETTINGS_FILE, "r") as f:
                settings = json.load(f)
            if org_uuid := settings.get("forceLoginOrgUUID"):
                return f"org_{org_uuid[:12]}"
    except (json.JSONDecodeError, IOError, KeyError):
        pass

    return "default"


def get_key_hint(api_key: str) -> str:
    """Generate a safe hint for an API key (first 7 + last 4 chars)."""
    if len(api_key) >= 15:
        return f"{api_key[:7]}...{api_key[-4:]}"
    return "***"


def _get_ssl_context():
    """Get SSL context, handling macOS certificate issues.

    By default, uses verified SSL. If verification fails and
    CLAUDE_STATS_ALLOW_INSECURE_SSL=1 is set, falls back to unverified.
    """
    ctx = ssl.create_default_context()

    # Quick test to see if certs work
    try:
        import socket
        with socket.create_connection(("api.anthropic.com", 443), timeout=2) as sock:
            with ctx.wrap_socket(sock, server_hostname="api.anthropic.com"):
                pass
        return ctx
    except Exception as e:
        # SSL verification failed - only allow insecure fallback if explicitly opted in
        if os.environ.get("CLAUDE_STATS_ALLOW_INSECURE_SSL") == "1":
            print(
                f"WARNING: SSL certificate verification failed ({e}). "
                "Falling back to unverified connection because CLAUDE_STATS_ALLOW_INSECURE_SSL=1 is set.",
                file=sys.stderr
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        else:
            # Don't silently disable SSL verification - raise instead
            raise ssl.SSLError(
                f"SSL certificate verification failed: {e}. "
                "To allow insecure connections (not recommended), set CLAUDE_STATS_ALLOW_INSECURE_SSL=1"
            ) from e


def get_claude_code_credentials() -> dict:
    """Get Claude Code credentials from macOS keychain.

    Returns dict with available info: local_user, subscription_type, org_id.
    """
    result = {}
    try:
        import subprocess
        # Get the keychain password (JSON blob)
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            creds = json.loads(proc.stdout.strip())
            oauth = creds.get("claudeAiOauth", {})
            if sub := oauth.get("subscriptionType"):
                result["subscription"] = sub
            if tier := oauth.get("rateLimitTier"):
                result["rate_limit_tier"] = tier

        # Also get the keychain account name (local username)
        proc2 = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True, text=True, timeout=5
        )
        if proc2.returncode == 0:
            # Parse output to find "acct"<blob>="username"
            for line in proc2.stdout.split("\n"):
                if '"acct"<blob>=' in line:
                    # Extract value between quotes
                    import re
                    match = re.search(r'"acct"<blob>="([^"]+)"', line)
                    if match:
                        result["local_user"] = match.group(1)
                    break
    except Exception:
        pass
    return result


def fetch_org_info(api_key: str) -> dict:
    """Fetch organization info from Anthropic API.

    For Admin API keys (sk-ant-admin...), fetches org name from /v1/organizations/me.
    For regular API keys, makes minimal call to get org-id from response headers.

    Returns dict with 'org_id' and optionally 'org_name'.
    """
    result = {}

    try:
        ctx = _get_ssl_context()
    except Exception:
        return result  # Can't establish SSL, skip org fetch

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    # Try Admin API first if it looks like an admin key
    if api_key.startswith("sk-ant-admin"):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/organizations/me",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                data = json.loads(resp.read())
                result["org_id"] = data.get("id")
                result["org_name"] = data.get("name")
                return result
        except Exception:
            pass  # Fall through to regular API call

    # For regular keys, make minimal API call to get org-id from headers
    try:
        data = json.dumps({
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            org_id = resp.headers.get("anthropic-organization-id")
            if org_id:
                result["org_id"] = org_id
    except Exception:
        pass

    return result


def load_accounts() -> dict:
    """Load accounts configuration from file."""
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, "r") as f:
                data = json.load(f)
                if data.get("version") == 1 and "accounts" in data:
                    return data
        except (json.JSONDecodeError, IOError):
            pass

    # Return default structure
    return {
        "version": 1,
        "accounts": {}
    }


def save_accounts(data: dict) -> None:
    """Save accounts configuration to file atomically with file locking."""
    STATS_DIR.mkdir(parents=True, exist_ok=True)

    # Use file locking to prevent race conditions between concurrent sessions
    with open(ACCOUNTS_LOCK_FILE, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)  # Exclusive lock
        try:
            temp_file = ACCOUNTS_FILE.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)
            temp_file.rename(ACCOUNTS_FILE)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)  # Release lock


def ensure_account_exists(account_id: str, fetch_org: bool = False) -> dict:
    """Ensure an account exists in the registry, creating it if needed.

    Args:
        account_id: The account identifier
        fetch_org: If True and this is a new API key account, fetch org info
                   from the Anthropic API (makes one minimal API call)

    Returns the account data.

    Note: Uses file locking to prevent race conditions when multiple
    sessions try to create/update accounts simultaneously.
    """
    STATS_DIR.mkdir(parents=True, exist_ok=True)

    # Use file locking for the entire read-modify-write operation
    with open(ACCOUNTS_LOCK_FILE, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)  # Exclusive lock
        try:
            data = load_accounts()
            now = datetime.now().isoformat()

            if account_id not in data["accounts"]:
                # Determine account type and name
                if account_id == "default":
                    account_type = "unknown"
                    name = "Legacy"
                elif account_id.startswith("api_"):
                    account_type = "token"
                    # Try to get key hint from current environment
                    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                    key_hint = get_key_hint(api_key) if api_key else None
                    name = "API Key"
                elif account_id.startswith("org_"):
                    account_type = "org"
                    name = "Organization"
                else:
                    account_type = "custom"
                    name = account_id

                account_data = {
                    "name": name,
                    "type": account_type,
                    "created": now,
                    "last_seen": now,
                }

                # Add key hint for API accounts
                if account_type == "token" and api_key:
                    account_data["key_hint"] = get_key_hint(api_key)

                    # Optionally fetch org info from API and keychain
                    if fetch_org:
                        # Get org info from API
                        org_info = fetch_org_info(api_key)
                        if org_info.get("org_id"):
                            account_data["org_id"] = org_info["org_id"]
                        if org_info.get("org_name"):
                            account_data["org_name"] = org_info["org_name"]
                            account_data["name"] = org_info["org_name"]

                        # Get additional info from keychain (macOS only)
                        kc_info = get_claude_code_credentials()
                        if kc_info.get("subscription"):
                            account_data["subscription"] = kc_info["subscription"]
                        if kc_info.get("local_user"):
                            account_data["local_user"] = kc_info["local_user"]
                            # Use local_user + subscription as name if no org_name
                            if not org_info.get("org_name"):
                                sub = kc_info.get("subscription", "")
                                if sub:
                                    account_data["name"] = f"{kc_info['local_user']} ({sub})"
                                else:
                                    account_data["name"] = kc_info["local_user"]

                data["accounts"][account_id] = account_data
                _save_accounts_unlocked(data)
            else:
                # Update last_seen
                data["accounts"][account_id]["last_seen"] = now
                _save_accounts_unlocked(data)

            return data["accounts"][account_id]
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)  # Release lock


def _save_accounts_unlocked(data: dict) -> None:
    """Save accounts configuration atomically. Caller must hold lock."""
    temp_file = ACCOUNTS_FILE.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=2)
    temp_file.rename(ACCOUNTS_FILE)


def get_account_name(account_id: str) -> str:
    """Get the display name for an account."""
    data = load_accounts()

    if account_id in data["accounts"]:
        return data["accounts"][account_id].get("name", account_id)

    # Generate default name for unknown accounts
    if account_id == "default":
        return "Legacy"
    elif account_id.startswith("api_"):
        return "API Key"
    elif account_id.startswith("org_"):
        return "Organization"
    return account_id


def set_account_name(account_id: str, name: str) -> None:
    """Set the display name for an account.

    Uses file locking to prevent race conditions.
    """
    STATS_DIR.mkdir(parents=True, exist_ok=True)

    with open(ACCOUNTS_LOCK_FILE, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            data = load_accounts()

            if account_id not in data["accounts"]:
                # Create a minimal account entry
                data["accounts"][account_id] = {
                    "name": name,
                    "type": "custom",
                    "created": datetime.now().isoformat(),
                    "last_seen": datetime.now().isoformat(),
                }
            else:
                data["accounts"][account_id]["name"] = name

            _save_accounts_unlocked(data)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def list_accounts() -> list[tuple[str, str]]:
    """List all known accounts as (id, name) tuples, sorted by last_seen."""
    data = load_accounts()
    accounts = []

    for account_id, info in data["accounts"].items():
        name = info.get("name", account_id)
        last_seen = info.get("last_seen", "")
        accounts.append((account_id, name, last_seen))

    # Sort by last_seen descending (most recent first)
    accounts.sort(key=lambda x: x[2], reverse=True)

    return [(acc[0], acc[1]) for acc in accounts]


def get_account_info(account_id: str) -> dict:
    """Get full account info dict."""
    data = load_accounts()
    return data["accounts"].get(account_id, {})


def refresh_account_org_info(account_id: str, api_key: str = None) -> dict:
    """Fetch org info from API and update account record.

    Args:
        account_id: The account to update
        api_key: API key to use (defaults to ANTHROPIC_API_KEY env var)

    Returns updated account data, or empty dict if failed.

    Uses file locking to prevent race conditions.
    """
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}

    org_info = fetch_org_info(api_key)
    if not org_info:
        return {}

    STATS_DIR.mkdir(parents=True, exist_ok=True)

    with open(ACCOUNTS_LOCK_FILE, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            data = load_accounts()
            if account_id not in data["accounts"]:
                return {}

            if org_info.get("org_id"):
                data["accounts"][account_id]["org_id"] = org_info["org_id"]
            if org_info.get("org_name"):
                data["accounts"][account_id]["org_name"] = org_info["org_name"]
                # Update display name to org name if it's still generic
                if data["accounts"][account_id].get("name") in ("API Key", "Organization", account_id):
                    data["accounts"][account_id]["name"] = org_info["org_name"]

            _save_accounts_unlocked(data)
            return data["accounts"][account_id]
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
