#!/usr/bin/env python3
"""Safely bind Cara to one private Telegram owner.

Run only while the service is stopped, and pass the chat id you EXPECT:
binding is never implicit. Without an id the helper only LISTS the pending
private chats and exits non-zero — "the sole pending private chat" used to be
enough to become the owner, which handed the bot to whoever /start-ed first.

It reads the update queue with the ONE call the Bot API guarantees consumes
nothing — getUpdates with no offset at all — so the first real poll still sees
everything, and it rewrites /etc/tg-ingest-agent.env atomically with a .bak —
the old truncate-and-write could destroy the bot token and the DO key.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

ENV = Path("/etc/tg-ingest-agent.env")
SERVICE = "tg-ingest-agent"
PAGE_LIMIT = 100
MAX_PAGES = 10
DEEP_FLAG = "--deep-read"
USAGE = ("usage: bootstrap_chat_id.py [--deep-read] <expected_numeric_chat_id>\n"
         "       (no id = list candidates; --deep-read reaches the END of a flooded "
         "queue and DISCARDS everything older — see fetch_updates)")


def api_get(token, method, params, opener=urlopen):
    url = f"https://api.telegram.org/bot{token}/{method}?{urlencode(params)}"
    with opener(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok", True):
        raise ValueError(f"Telegram {method} failed: {payload.get('description')}")
    return payload


def fetch_updates(token, opener=urlopen, limit=PAGE_LIMIT, max_pages=MAX_PAGES, deep=False):
    """Read the pending queue with the call that is guaranteed not to consume it.

    getUpdates documents both halves of this in one paragraph: an update is
    confirmed (dropped) "as soon as getUpdates is called with an offset higher
    than its update_id", AND "the negative offset can be specified to retrieve
    updates starting from -offset update from the end of the updates queue. All
    previous updates will be forgotten." So a negative offset is NOT a free
    read — `getUpdates(offset=-1)` is the standard idiom for DISCARDING the
    pending queue. Only a call with no offset at all is promised to leave the
    queue intact ("by default, updates starting with the earliest unconfirmed
    update are returned"), so that is the default here: one page, no offset.

    That page holds the OLDEST `limit` updates, so a deeper queue hides the
    newest ones. The caller is told (`truncated`) instead of being shown a
    silent subset. `deep=True` — the operator's explicit --deep-read — pages
    backwards with negative offsets to reach the END of a flooded queue and
    will permanently discard everything older than the window it reads.

    Returns (updates ordered by update_id, truncated); `truncated` means the
    queue may hold pending updates this read did not list.
    """
    seen = {}

    def absorb(batch):
        fresh = 0
        for update in batch:
            update_id = update.get("update_id")
            if update_id is None:
                continue
            if int(update_id) not in seen:
                fresh += 1
            seen[int(update_id)] = update
        return fresh

    payload = api_get(token, "getUpdates", {"timeout": 0, "limit": limit}, opener)
    batch = payload.get("result") or []
    absorb(batch)
    # A full page means "there may be more"; there is no non-destructive way to
    # find out, which is exactly why this is reported rather than papered over.
    truncated = len(batch) >= limit
    if truncated and deep:
        for page in range(1, max_pages + 1):
            payload = api_get(token, "getUpdates",
                              {"timeout": 0, "limit": limit, "offset": -(page * limit)}, opener)
            batch = payload.get("result") or []
            fresh = absorb(batch)
            # A short page is the start of the queue; no new ids means the window
            # stopped moving (fewer updates exist than this offset skips back).
            if len(batch) < limit or not fresh:
                truncated = False
                break
    return [seen[key] for key in sorted(seen)], truncated


def candidates(updates):
    """Pending private chats keyed by chat id, valued by a human label.

    A private chat whose sender id equals the chat id is a real person writing
    to the bot directly (never a group, a channel or a forwarded sender).
    """
    found = {}
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        if chat.get("type") != "private" or chat_id is None or sender.get("id") != chat_id:
            continue
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p)
        handle = sender.get("username")
        label = (f"{name} (@{handle})" if handle else name).strip()
        found.setdefault(int(chat_id), label or "(no name)")
    return found


def select_owner(pending, expected):
    """The expected id is MANDATORY: never bind "whoever happens to be here"."""
    if expected is None or str(expected).strip() == "":
        raise ValueError("pass the expected numeric chat id; binding is never implicit")
    expected = int(expected)
    if expected not in pending:
        raise ValueError("the expected owner has no pending private message")
    return expected


def read_token(content):
    match = re.search(r"(?m)^TELEGRAM_BOT_TOKEN=(.*)$", content)
    token = match.group(1).strip() if match else ""
    if not token or token == "REPLACE_ME":
        raise ValueError("token not configured")
    return token


def set_env_value(content, key, value):
    """Replace exactly one `KEY=…` line, or append the line when it is absent.

    A missing line used to make the rewrite a silent no-op. Duplicates are
    refused rather than half-updated: systemd's EnvironmentFile honours the LAST
    assignment, so rewriting only the first would leave the old value in force.
    The replacement is a callable — a secret containing a backslash must not be
    read as a regex escape.
    """
    new, count = re.subn(rf"(?m)^{re.escape(key)}=.*$", lambda _: f"{key}={value}", content)
    if count == 1:
        return new
    if count > 1:
        raise ValueError(f"{key} is assigned {count} times; fix the file by hand")
    return content.rstrip("\n") + f"\n{key}={value}\n"


def write_owner(content, chat_id):
    return set_env_value(content, "ALLOWED_CHAT_IDS", int(chat_id))


def write_env_atomically(path, content):
    """Replace `path` without ever leaving it truncated.

    The env file holds the bot token and the DO key: a plain write() that dies
    mid-way loses both. Temp file (O_EXCL, 0600) -> fsync -> .bak of the old
    content -> os.replace (atomic within the filesystem) -> fsync the directory.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        # O_EXCL is deliberate, but a hard kill between this open and os.replace
        # leaves the temp file behind and every later run would then die on a
        # bare FileExistsError. Say what it is and how to clear it.
        raise SystemExit(f"a stale {tmp} is in the way — an earlier run was killed "
                         f"mid-write. Inspect it, then: rm -f {tmp}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if previous is not None:
            backup = path.with_name(path.name + ".bak")
            bak_fd = os.open(backup, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            # The mode above applies only when the file is CREATED: a .bak left by
            # an earlier hand-made `cp` keeps its own (looser) mode while receiving
            # a full copy of the token and the DO key.
            os.fchmod(bak_fd, 0o600)
            with os.fdopen(bak_fd, "w", encoding="utf-8") as handle:
                handle.write(previous)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    os.chmod(path, 0o600)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:                 # some filesystems refuse a directory fsync
        pass
    return path


def service_is_active(service=SERVICE, runner=subprocess.run):
    """True only when systemd says the poller runs (two pollers = HTTP 409)."""
    try:
        done = runner(["systemctl", "is-active", service],
                      capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False                # no systemd here: nothing of ours can poll
    return (done.stdout or "").strip() == "active"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    deep = DEEP_FLAG in argv
    argv = [arg for arg in argv if arg != DEEP_FLAG]
    if len(argv) > 1 or (argv and not re.fullmatch(r"-?\d+", argv[0])):
        raise SystemExit(USAGE)
    content = ENV.read_text(encoding="utf-8")
    token = read_token(content)
    if service_is_active():
        print(f"refusing: {SERVICE} is active — run `systemctl stop {SERVICE}` first "
              "(two pollers on one token make Telegram answer HTTP 409).")
        raise SystemExit(1)
    try:
        updates, truncated = fetch_updates(token, deep=deep)
    except HTTPError as exc:
        if exc.code == 409:
            print(f"refusing: another poller holds this bot token — stop {SERVICE} first.")
            raise SystemExit(1) from exc
        raise
    pending = candidates(updates)
    if deep:
        print(f"NOTE: {DEEP_FLAG} sent NEGATIVE getUpdates offsets. The Bot API says "
              "\"all previous updates will be forgotten\", so pending updates older than "
              "the window just read are gone.")
    if truncated and deep:
        print(f"WARNING: the deep read stopped after {MAX_PAGES * PAGE_LIMIT} updates; "
              "anything older was neither listed nor kept.")
    elif truncated:
        print(f"WARNING: listed only the OLDEST {PAGE_LIMIT} pending updates — that is the "
              "deepest read Telegram guarantees is non-destructive, so a recent message from "
              f"the owner may sit further down the queue. Let it drain, or re-run with "
              f"{DEEP_FLAG} (which reads from the END and DISCARDS everything older).")
    if not argv:
        print("pending private chats:")
        for chat_id, label in sorted(pending.items()):
            print(f"  {chat_id}  {label}")
        if not pending:
            print("  (none — message the bot from the owner's account, then re-run)")
        print(USAGE)
        raise SystemExit(1)
    try:
        owner = select_owner(pending, argv[0])
    except ValueError as exc:
        print(f"bootstrap refused: {exc}")
        raise SystemExit(1) from exc
    try:
        updated = write_owner(content, owner)
    except ValueError as exc:                   # duplicated ALLOWED_CHAT_IDS=
        raise SystemExit(f"bootstrap refused: {exc}") from exc
    write_env_atomically(ENV, updated)
    print(f"bound ALLOWED_CHAT_IDS={owner} ({pending[owner]}) in {ENV}; "
          f"previous contents kept as {ENV}.bak")


if __name__ == "__main__":
    main()
