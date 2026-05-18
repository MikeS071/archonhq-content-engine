#!/usr/bin/env python3
"""ArchonHQ Retry & Recovery Module — shared retry/recovery logic for all pipeline scripts.

Features:
  - retry(fn, ...)          — exponential backoff retry wrapper
  - with_recovery(fn, ...)  — run with fallback and optional alert
  - send_alert(stage, ...)  — send Telegram alert via hermes on pipeline failure
  - DeadLetterQueue         — append-only log of failed operations
  - circuit_breaker(...)    — stops retrying after N consecutive failures for a stage
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

# ── Config Integration ─────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))
from config import get_path, get_config

_DLQ_PATH = Path(os.path.expanduser("~/archonhq-content/dead_letter_queue.json"))


# ── Circuit Breaker ────────────────────────────────────────────────────────────

class CircuitBreaker:
    """Stops retrying after N consecutive failures for a named stage.

    After `failure_threshold` consecutive failures, the circuit opens and
    all further calls fail fast until `reset_timeout` seconds have elapsed,
    at which point the circuit half-opens (allows one attempt).

    Args:
        name: Identifier for this circuit (e.g. 'draft_generation').
        failure_threshold: Consecutive failures before opening (default 5).
        reset_timeout: Seconds before attempting reset (default 300).
    """

    # Shared registry so circuit state persists across calls
    _registry: dict[str, "CircuitBreaker"] = {}
    _lock = threading.Lock()

    def __init__(self, name: str, failure_threshold: int = 5, reset_timeout: int = 300):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.state = "closed"  # closed | open | half_open

    @classmethod
    def get(cls, name: str, failure_threshold: int = 5, reset_timeout: int = 300) -> "CircuitBreaker":
        """Get or create a circuit breaker by name (singleton per name)."""
        with cls._lock:
            if name not in cls._registry:
                cls._registry[name] = cls(name, failure_threshold, reset_timeout)
            return cls._registry[name]

    def record_success(self):
        """Record a successful call — reset failure count and close circuit."""
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = None

    def record_failure(self):
        """Record a failed call — increment count, open circuit if threshold reached."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def is_open(self) -> bool:
        """Check if the circuit is open (calls should fail fast).

        If open but reset_timeout has elapsed, transitions to half_open
        and returns False (allows one attempt).
        """
        if self.state == "closed":
            return False
        if self.state == "open":
            if self.last_failure_time and (time.time() - self.last_failure_time) >= self.reset_timeout:
                self.state = "half_open"
                return False  # Allow one attempt
            return True
        # half_open — allow attempt
        return False

    @property
    def status(self) -> dict:
        """Return current circuit breaker status as a dict."""
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure_time": self.last_failure_time,
        }


def circuit_breaker(name: str, failure_threshold: int = 5, reset_timeout: int = 300) -> CircuitBreaker:
    """Convenience function to get/create a circuit breaker by name."""
    return CircuitBreaker.get(name, failure_threshold, reset_timeout)


# ── Retry with Exponential Backoff ─────────────────────────────────────────────

class RetryExhausted(Exception):
    """Raised when all retry attempts have been exhausted."""
    def __init__(self, attempts: int, last_error: Exception):
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"All {attempts} retry attempts exhausted. Last error: {last_error}")


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and the call is rejected."""
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is open — failing fast")


def retry(
    fn: Callable,
    max_attempts: int = 3,
    backoff_base: float = 2,
    timeout: float = 300,
    circuit_name: str | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> Any:
    """Execute fn with exponential backoff retry.

    Args:
        fn: Callable to execute.
        max_attempts: Maximum number of attempts (default 3).
        backoff_base: Base for exponential backoff: delay = backoff_base ** attempt (default 2).
        timeout: Maximum total seconds to spend retrying (default 300).
        circuit_name: If set, check circuit breaker before attempting.
        on_retry: Optional callback(attempt_number, exception) called before each retry.

    Returns:
        The return value of fn() on success.

    Raises:
        RetryExhausted: If all attempts fail.
        CircuitOpenError: If circuit breaker is open.
    """
    # Check circuit breaker if specified
    cb = None
    if circuit_name:
        cb = circuit_breaker(circuit_name)
        if cb.is_open():
            raise CircuitOpenError(circuit_name)

    start_time = time.time()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        # Check timeout
        if time.time() - start_time > timeout:
            if cb:
                cb.record_failure()
            raise RetryExhausted(attempt - 1, last_error or TimeoutError("Retry timeout exceeded"))

        try:
            result = fn()
            if cb:
                cb.record_success()
            return result
        except Exception as e:
            last_error = e
            if on_retry:
                on_retry(attempt, e)
            if attempt < max_attempts:
                delay = backoff_base ** attempt
                # Cap delay so we don't exceed timeout
                elapsed = time.time() - start_time
                remaining = timeout - elapsed
                delay = min(delay, max(0, remaining - 1))
                if delay > 0:
                    time.sleep(delay)

    # All attempts exhausted
    if cb:
        cb.record_failure()
    raise RetryExhausted(max_attempts, last_error)


# ── Alert System ───────────────────────────────────────────────────────────────

def send_alert(stage: str, error: str, article_id: str | None = None) -> bool:
    """Send a Telegram alert via hermes when a pipeline stage fails.

    Uses the hermes CLI to send a Telegram message to the configured chat.

    Args:
        stage: Pipeline stage that failed (e.g. 'draft_generation').
        error: Error message or description.
        article_id: Optional article identifier (e.g. 'M15').

    Returns:
        True if alert was sent successfully, False otherwise.
    """
    article_part = f" | Article: {article_id}" if article_id else ""
    message = (
        f"🚨 **Pipeline Alert**\n"
        f"Stage: `{stage}`{article_part}\n"
        f"Error: {error[:500]}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    try:
        # Try hermes send-message first
        result = subprocess.run(
            ["hermes", "send-message", message],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        # Fallback: try with curl to Telegram API if bot token is available
        env = os.environ
        bot_token = env.get("TELEGRAM_BOT_TOKEN") or env.get("TG_BOT_TOKEN")
        chat_id = env.get("TELEGRAM_CHAT_ID") or env.get("TG_CHAT_ID")
        if bot_token and chat_id:
            import urllib.request
            import urllib.parse
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status == 200
    except Exception:
        pass

    # Final fallback: log to stderr
    print(f"[ALERT] {message}", file=sys.stderr)
    return False


# ── Recovery Wrapper ───────────────────────────────────────────────────────────

def with_recovery(
    fn: Callable,
    fallback: Callable | None = None,
    alert: bool = True,
    stage: str = "",
    article_id: str | None = None,
) -> Any:
    """Run a function, call fallback on failure, optionally send alert.

    Args:
        fn: Primary function to execute.
        fallback: Optional fallback function called if fn raises.
            Receives the exception as its argument. If fallback returns a value,
            that value is returned instead of raising.
        alert: Whether to send a Telegram alert on failure (default True).
        stage: Pipeline stage name for the alert.
        article_id: Optional article ID for the alert.

    Returns:
        Return value of fn(), or return value of fallback() if fn() fails.

    Raises:
        The original exception if fn() fails and no fallback is provided.
    """
    try:
        return fn()
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"

        if alert:
            try:
                send_alert(stage or fn.__name__, error_msg, article_id)
            except Exception:
                pass  # Alert failure should not mask original error

        if fallback is not None:
            try:
                return fallback(e)
            except Exception as fallback_err:
                # Fallback itself failed — log and raise original
                print(f"[RECOVERY] Fallback also failed: {fallback_err}", file=sys.stderr)
                raise e from fallback_err

        raise


# ── Dead Letter Queue ──────────────────────────────────────────────────────────

class DeadLetterQueue:
    """Append-only log of failed operations.

    Stores failed operations as JSON entries with timestamps, stage info,
    error messages, article references, retry counts, and resolution status.

    The queue is persisted to ~/archonhq-content/dead_letter_queue.json.
    """

    _lock = threading.Lock()

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else _DLQ_PATH
        self._ensure_file()

    def _ensure_file(self):
        """Create the DLQ file if it doesn't exist."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]")

    def _read(self) -> list[dict]:
        """Read the current DLQ entries."""
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, entries: list[dict]):
        """Write entries to the DLQ file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=2))

    def add(
        self,
        stage: str,
        error: str,
        article: str | None = None,
        retries: int = 0,
    ) -> str:
        """Add a failed operation to the dead letter queue.

        Args:
            stage: Pipeline stage that failed (e.g. 'draft_generation').
            error: Error message or description.
            article: Optional article identifier (e.g. 'M15').
            retries: Number of retries attempted before giving up.

        Returns:
            The entry ID (ISO timestamp).
        """
        with self._lock:
            entries = self._read()
            entry_id = datetime.now(timezone.utc).isoformat()
            entry = {
                "timestamp": entry_id,
                "stage": stage,
                "error": error[:1000],  # Cap error length
                "article": article or "",
                "retries": retries,
                "resolved": False,
            }
            entries.append(entry)
            self._write(entries)
            return entry_id

    def resolve(self, entry_id: str) -> bool:
        """Mark a DLQ entry as resolved.

        Args:
            entry_id: The timestamp ID of the entry to resolve.

        Returns:
            True if the entry was found and resolved, False otherwise.
        """
        with self._lock:
            entries = self._read()
            for entry in entries:
                if entry.get("timestamp") == entry_id:
                    entry["resolved"] = True
                    self._write(entries)
                    return True
            return False

    def get_unresolved(self) -> list[dict]:
        """Get all unresolved entries from the DLQ.

        Returns:
            List of unresolved entry dicts, sorted by timestamp.
        """
        entries = self._read()
        return [e for e in entries if not e.get("resolved", False)]

    def get_all(self) -> list[dict]:
        """Get all entries from the DLQ.

        Returns:
            List of all entry dicts, sorted by timestamp.
        """
        return self._read()

    def flush_resolved(self) -> int:
        """Remove all resolved entries from the DLQ.

        Returns:
            Number of entries flushed.
        """
        with self._lock:
            entries = self._read()
            original_count = len(entries)
            unresolved = [e for e in entries if not e.get("resolved", False)]
            flushed = original_count - len(unresolved)
            self._write(unresolved)
            return flushed

    def stats(self) -> dict:
        """Get DLQ statistics.

        Returns:
            Dict with total, unresolved, resolved counts and stage breakdowns.
        """
        entries = self._read()
        unresolved = [e for e in entries if not e.get("resolved", False)]
        resolved = [e for e in entries if e.get("resolved", False)]

        # Count by stage
        stage_counts: dict[str, int] = {}
        for e in unresolved:
            stage = e.get("stage", "unknown")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        return {
            "total": len(entries),
            "unresolved": len(unresolved),
            "resolved": len(resolved),
            "stages": stage_counts,
        }


# ── Convenience Decorator ──────────────────────────────────────────────────────

def retried(
    max_attempts: int = 3,
    backoff_base: float = 2,
    timeout: float = 300,
    circuit_name: str | None = None,
):
    """Decorator version of retry(). Apply to any function.

    Usage:
        @retried(max_attempts=3, circuit_name="draft_generation")
        def generate_draft(topic):
            ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return retry(
                lambda: fn(*args, **kwargs),
                max_attempts=max_attempts,
                backoff_base=backoff_base,
                timeout=timeout,
                circuit_name=circuit_name,
            )
        return wrapper
    return decorator


def resilient(
    fallback: Callable | None = None,
    alert: bool = True,
    stage: str = "",
    article_id: str | None = None,
):
    """Decorator version of with_recovery(). Apply to any function.

    Usage:
        @resilient(fallback=lambda e: None, stage="publishing")
        def publish_article(article):
            ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return with_recovery(
                lambda: fn(*args, **kwargs),
                fallback=fallback,
                alert=alert,
                stage=stage or fn.__name__,
                article_id=article_id,
            )
        return wrapper
    return decorator
