#!/usr/bin/env python3
"""
Claude Code PostToolUse hook to log token usage statistics.
Reads session transcript, extracts token counts per model, and saves to daily JSON file.

Token field mapping (API -> Storage -> Display):
- input_tokens        -> input_tokens          -> Input
- output_tokens       -> output_tokens         -> Output
- cache_read_input_tokens    -> cache_read_tokens     -> Cache Read
- cache_creation_input_tokens -> cache_creation_tokens -> Cache Write
"""

import fcntl
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add lib directory to path for shared modules
# Supports both development (../lib) and installed (~/.claude/lib) locations
_script_dir = Path(__file__).resolve().parent
for _lib_path in [_script_dir.parent / "lib", Path.home() / ".claude" / "lib"]:
    if _lib_path.exists():
        sys.path.insert(0, str(_lib_path))
        break

# Try to import from shared modules, fallback to inline definitions for backward compatibility
try:
    from pricing import (
        STATS_DIR, PRICING_FILE,
        load_pricing, get_model_pricing, calculate_cost
    )
    from accounts import detect_account_id, ensure_account_exists
    ACCOUNTS_AVAILABLE = True
except ImportError:
    ACCOUNTS_AVAILABLE = False
    # Fallback for users who haven't reinstalled yet
    STATS_DIR = Path.home() / ".claude" / "stats"
    PRICING_FILE = STATS_DIR / "pricing.json"

    DEFAULT_PRICING = {
        "claude-opus-4-5-20251101": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
        "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_write": 1.00},
    }

    FAMILY_PRICING = {
        "opus": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
        "sonnet": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
        "haiku": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
    }

    def get_family_pricing(model: str) -> dict:
        model_lower = model.lower()
        if "opus" in model_lower:
            return FAMILY_PRICING["opus"]
        elif "haiku" in model_lower:
            return FAMILY_PRICING["haiku"]
        return FAMILY_PRICING["sonnet"]

    def load_pricing() -> dict:
        pricing = DEFAULT_PRICING.copy()
        if PRICING_FILE.exists():
            try:
                with open(PRICING_FILE, "r") as f:
                    pricing.update(json.load(f))
            except (json.JSONDecodeError, FileNotFoundError, PermissionError):
                pass
        return pricing

    def get_model_pricing(model: str, pricing: dict) -> dict:
        if model in pricing:
            return pricing[model]
        for key in pricing:
            if key in model or model in key:
                return pricing[key]
        return get_family_pricing(model)

    def calculate_cost(tokens: dict, model: str, pricing: dict) -> float:
        mp = get_model_pricing(model, pricing)
        cost = 0.0
        cost += (tokens.get("input_tokens", 0) / 1_000_000) * mp.get("input", 0)
        cost += (tokens.get("output_tokens", 0) / 1_000_000) * mp.get("output", 0)
        cost += (tokens.get("cache_read_tokens", 0) / 1_000_000) * mp.get("cache_read", 0)
        cost += (tokens.get("cache_creation_tokens", 0) / 1_000_000) * mp.get("cache_write", 0)
        return cost

# Fallback account functions for backward compatibility
if not ACCOUNTS_AVAILABLE:
    def detect_account_id() -> str:
        if override := os.environ.get("CLAUDE_STATS_ACCOUNT"):
            return override
        if api_key := os.environ.get("ANTHROPIC_API_KEY"):
            return "api_" + hashlib.sha256(api_key[:20].encode()).hexdigest()[:12]
        return "default"

    def ensure_account_exists(account_id: str, fetch_org: bool = False) -> dict:
        return {"name": account_id, "type": "unknown"}

DEBUG_LOG = STATS_DIR / "debug.log"
LOCK_FILE = STATS_DIR / ".stats.lock"
TIMESERIES_FILE = STATS_DIR / "timeseries.json"
TIMESERIES_WINDOW_MINUTES = 35  # 30 min display + 5 min buffer


def debug_log(msg: str):
    """Write debug message to log file."""
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEBUG_LOG, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")


def parse_iso_timestamp(ts: str) -> datetime:
    """Parse ISO timestamp string to datetime."""
    # Handle both 'Z' suffix and '+00:00' timezone formats
    ts = ts.replace("Z", "+00:00")
    try:
        # Python 3.11+ can handle timezone directly
        return datetime.fromisoformat(ts)
    except ValueError:
        # Fallback: strip timezone for older Python
        if "+" in ts:
            ts = ts.split("+")[0]
        elif ts.endswith("Z"):
            ts = ts[:-1]
        return datetime.fromisoformat(ts)


def parse_transcript(transcript_path: str) -> dict:
    """Parse transcript JSONL and extract token usage per model.

    For each request_id, tracks the maximum value seen for each token type
    independently to handle streaming responses correctly.
    Also tracks timestamps to calculate request duration and tokens/second.
    """
    request_usage = {}
    request_timing = {}  # Track first/last timestamps per request

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "assistant":
                        message = entry.get("message", {})
                        usage = message.get("usage", {})
                        request_id = entry.get("requestId")
                        model = message.get("model", "unknown")
                        timestamp = entry.get("timestamp")

                        # Validate we have the required fields
                        if not usage or not request_id:
                            continue

                        # Extract token counts with validation
                        input_tokens = usage.get("input_tokens", 0) or 0
                        output_tokens = usage.get("output_tokens", 0) or 0
                        cache_read_tokens = usage.get("cache_read_input_tokens", 0) or 0
                        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0) or 0

                        if request_id not in request_usage:
                            # First time seeing this request
                            request_usage[request_id] = {
                                "model": model,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cache_read_tokens": cache_read_tokens,
                                "cache_creation_tokens": cache_creation_tokens,
                            }
                        else:
                            # Update each token type to max seen value independently
                            existing = request_usage[request_id]
                            existing["input_tokens"] = max(existing["input_tokens"], input_tokens)
                            existing["output_tokens"] = max(existing["output_tokens"], output_tokens)
                            existing["cache_read_tokens"] = max(existing["cache_read_tokens"], cache_read_tokens)
                            existing["cache_creation_tokens"] = max(existing["cache_creation_tokens"], cache_creation_tokens)
                            # Update model if we get a more specific one
                            if model != "unknown":
                                existing["model"] = model

                        # Track timestamps for TPS calculation
                        if timestamp and request_id:
                            if request_id not in request_timing:
                                request_timing[request_id] = {
                                    "first_ts": timestamp,
                                    "last_ts": timestamp
                                }
                            else:
                                # Update last timestamp (streaming creates multiple entries)
                                request_timing[request_id]["last_ts"] = timestamp
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return {"by_model": {}, "totals": {}, "completed_requests": []}

    pricing = load_pricing()

    by_model = {}
    for req_data in request_usage.values():
        model = req_data["model"]
        if model not in by_model:
            by_model[model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "request_count": 0,
            }
        by_model[model]["input_tokens"] += req_data["input_tokens"]
        by_model[model]["output_tokens"] += req_data["output_tokens"]
        by_model[model]["cache_read_tokens"] += req_data["cache_read_tokens"]
        by_model[model]["cache_creation_tokens"] += req_data["cache_creation_tokens"]
        by_model[model]["request_count"] += 1

    # Calculate cost per model
    for model, stats in by_model.items():
        stats["cost"] = calculate_cost(stats, model, pricing)

    totals = {
        "input_tokens": sum(m["input_tokens"] for m in by_model.values()),
        "output_tokens": sum(m["output_tokens"] for m in by_model.values()),
        "cache_read_tokens": sum(m["cache_read_tokens"] for m in by_model.values()),
        "cache_creation_tokens": sum(m["cache_creation_tokens"] for m in by_model.values()),
        "request_count": len(request_usage),
        "cost": sum(m["cost"] for m in by_model.values()),
    }
    totals["total_tokens"] = (
        totals["input_tokens"] + totals["output_tokens"] +
        totals["cache_read_tokens"] + totals["cache_creation_tokens"]
    )

    # Calculate TPS for completed requests
    completed_requests = []
    for req_id, usage in request_usage.items():
        timing = request_timing.get(req_id, {})
        first_ts = timing.get("first_ts")
        last_ts = timing.get("last_ts")

        if first_ts and last_ts:
            try:
                first_dt = parse_iso_timestamp(first_ts)
                last_dt = parse_iso_timestamp(last_ts)
                duration = (last_dt - first_dt).total_seconds()

                # Only include if we have meaningful duration (> 0.1s and < 1 hour)
                # Upper bound prevents bad data from clock skew or malformed timestamps
                if 0.1 < duration < 3600:
                    output_tokens = usage["output_tokens"]
                    total_tokens = (
                        usage["input_tokens"] + usage["output_tokens"] +
                        usage["cache_read_tokens"] + usage["cache_creation_tokens"]
                    )
                    # Calculate cost for this request
                    req_cost = calculate_cost(usage, usage["model"], pricing)
                    completed_requests.append({
                        "request_id": req_id,
                        "timestamp": last_ts,
                        "duration_seconds": duration,
                        "output_tokens": output_tokens,
                        "total_tokens": total_tokens,
                        "output_tps": output_tokens / duration,
                        "total_tps": total_tokens / duration,
                        "cost": req_cost,
                    })
            except (ValueError, TypeError):
                continue

    return {"by_model": by_model, "totals": totals, "completed_requests": completed_requests}


def load_daily_stats(stats_file: Path) -> dict:
    """Load existing daily stats or create new structure.

    If the file exists but is corrupted, backs it up and starts fresh.
    """
    if stats_file.exists():
        try:
            with open(stats_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            # Back up corrupted file instead of silently losing data
            backup = stats_file.with_suffix(f".corrupted.{datetime.now().strftime('%H%M%S')}.json")
            shutil.copy2(stats_file, backup)
            debug_log(f"Corrupted stats file backed up to {backup}: {e}")
        except IOError as e:
            debug_log(f"IO error reading stats file: {e}")

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "sessions": [],
        "daily_totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "session_count": 0,
            "cost": 0.0,
        },
        "by_model": {}
    }


def recalculate_daily_totals(daily_stats: dict) -> None:
    """Recalculate daily totals from all sessions."""
    totals = daily_stats["daily_totals"]
    totals["input_tokens"] = 0
    totals["output_tokens"] = 0
    totals["cache_read_tokens"] = 0
    totals["cache_creation_tokens"] = 0
    totals["total_tokens"] = 0
    totals["request_count"] = 0
    totals["cost"] = 0.0

    daily_stats["by_model"] = {}

    for session in daily_stats["sessions"]:
        totals["input_tokens"] += session.get("input_tokens", 0)
        totals["output_tokens"] += session.get("output_tokens", 0)
        totals["cache_read_tokens"] += session.get("cache_read_tokens", 0)
        totals["cache_creation_tokens"] += session.get("cache_creation_tokens", 0)
        totals["total_tokens"] += session.get("total_tokens", 0)
        totals["request_count"] += session.get("request_count", 0)
        totals["cost"] += session.get("cost", 0.0)

        for model, model_stats in session.get("by_model", {}).items():
            if model not in daily_stats["by_model"]:
                daily_stats["by_model"][model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "request_count": 0,
                    "cost": 0.0,
                }
            for key in ["input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "request_count", "cost"]:
                daily_stats["by_model"][model][key] += model_stats.get(key, 0)

    totals["session_count"] = len(daily_stats["sessions"])


def save_stats(stats_file: Path, stats: dict):
    """Save stats to file atomically with pretty formatting."""
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    # Write to temp file first, then rename (atomic on POSIX)
    temp_file = stats_file.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        json.dump(stats, f, indent=2)
    temp_file.rename(stats_file)


def load_timeseries() -> dict:
    """Load timeseries data or create new structure."""
    if TIMESERIES_FILE.exists():
        try:
            with open(TIMESERIES_FILE, "r") as f:
                data = json.load(f)
                # Validate structure
                if "version" in data and "buckets" in data:
                    return data
        except (json.JSONDecodeError, FileNotFoundError, PermissionError):
            pass
    return {
        "version": 1,
        "buckets": {},
        "seen_request_ids": []
    }


def update_timeseries(data: dict, completed_requests: list, session_id: str) -> dict:
    """Update timeseries with new request data, aggregating into minute buckets.

    Uses absolute minute keys (e.g., "2026-01-15T11:14") so historical values
    are frozen once a minute passes.
    """
    now = datetime.now()
    cutoff = now - timedelta(minutes=TIMESERIES_WINDOW_MINUTES)

    # Prune old buckets
    buckets = data.get("buckets", {})
    data["buckets"] = {
        k: v for k, v in buckets.items()
        if parse_iso_timestamp(k + ":00") > cutoff
    }

    # Prune old seen_request_ids (keep last 1000 to prevent unbounded growth)
    # Use set for O(1) lookup during deduplication
    seen_ids = data.get("seen_request_ids", [])
    if len(seen_ids) > 1000:
        seen_ids = seen_ids[-900:]  # Keep 90% instead of 50% for smoother pruning
    seen_ids_set = set(seen_ids)
    data["seen_request_ids"] = seen_ids
    data["_seen_ids_set"] = seen_ids_set  # Temporary set for fast lookup

    # Process new completed requests
    for req in completed_requests:
        req_id = req.get("request_id")
        if not req_id or req_id in seen_ids_set:
            continue  # Skip duplicates (O(1) set lookup)

        timestamp = req.get("timestamp")
        if not timestamp:
            continue

        try:
            ts_dt = parse_iso_timestamp(timestamp)
        except (ValueError, TypeError):
            continue

        # Create minute bucket key (e.g., "2026-01-15T11:14")
        bucket_key = ts_dt.strftime("%Y-%m-%dT%H:%M")

        # Initialize bucket if needed
        if bucket_key not in data["buckets"]:
            data["buckets"][bucket_key] = {
                "output_tokens": 0,
                "total_tokens": 0,
                "total_duration": 0.0,
                "request_count": 0,
                "output_tps": 0.0,
                "total_tps": 0.0,
                "cost": 0.0,
            }

        bucket = data["buckets"][bucket_key]
        bucket["output_tokens"] += req.get("output_tokens", 0)
        bucket["total_tokens"] += req.get("total_tokens", 0)
        bucket["total_duration"] += req.get("duration_seconds", 0.0)
        bucket["request_count"] += 1
        bucket["cost"] = bucket.get("cost", 0.0) + req.get("cost", 0.0)

        # Recalculate average TPS for the bucket
        if bucket["total_duration"] > 0:
            bucket["output_tps"] = bucket["output_tokens"] / bucket["total_duration"]
            bucket["total_tps"] = bucket["total_tokens"] / bucket["total_duration"]

        # Mark request as seen
        data["seen_request_ids"].append(req_id)
        seen_ids_set.add(req_id)

    # Clean up temporary set before returning (not needed in JSON)
    data.pop("_seen_ids_set", None)
    return data


def save_timeseries(data: dict):
    """Save timeseries atomically."""
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = TIMESERIES_FILE.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=2)
    temp_file.rename(TIMESERIES_FILE)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        debug_log(f"Invalid JSON input from hook: {e}")
        sys.exit(0)

    transcript_path = hook_input.get("transcript_path")
    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", "")

    if not transcript_path:
        sys.exit(0)

    parsed = parse_transcript(transcript_path)
    session_tokens = parsed["totals"]
    by_model = parsed["by_model"]
    completed_requests = parsed.get("completed_requests", [])

    if session_tokens.get("total_tokens", 0) == 0:
        sys.exit(0)

    # Detect account and ensure it's registered (fetches org info once for new accounts)
    account_id = detect_account_id()
    ensure_account_exists(account_id, fetch_org=True)

    today = datetime.now().strftime("%Y-%m-%d")
    stats_file = STATS_DIR / f"{today}.json"

    # Use file locking to prevent race conditions between concurrent sessions
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)  # Exclusive lock
        try:
            daily_stats = load_daily_stats(stats_file)

            session_entry = {
                "session_id": session_id,
                "account_id": account_id,
                "date": today,  # Track which date this session entry belongs to
                "last_updated": datetime.now().isoformat(),
                "project": cwd,
                "by_model": by_model,
                **session_tokens
            }

            session_index = next(
                (i for i, s in enumerate(daily_stats["sessions"]) if s["session_id"] == session_id),
                None
            )

            if session_index is not None:
                session_entry["started"] = daily_stats["sessions"][session_index].get("started", session_entry["last_updated"])
                daily_stats["sessions"][session_index] = session_entry
            else:
                session_entry["started"] = session_entry["last_updated"]
                daily_stats["sessions"].append(session_entry)

            recalculate_daily_totals(daily_stats)
            save_stats(stats_file, daily_stats)

            # Update timeseries for TPS graph (only if we have new requests)
            if completed_requests:
                ts_data = load_timeseries()
                ts_data = update_timeseries(ts_data, completed_requests, session_id)
                save_timeseries(ts_data)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)  # Release lock

    sys.exit(0)


if __name__ == "__main__":
    main()
