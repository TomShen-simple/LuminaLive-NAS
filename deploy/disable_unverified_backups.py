"""One-time, idempotent VPS publisher patch; preserves an existing backup.

The legacy VPS publisher is outside this repository. It used to emit up to
three unverified candidate playlists, which APTV could auto-select after the
verified NAS official line timed out. This patch leaves the subscribed files
valid but empty by default. Run with --check first, then --apply on the VPS.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile


MARKER = '    # Keep subscribed backup URLs valid but empty by default.\n'
ANCHOR = (
    '    playlists = [[header] for _ in range(MAX_BACKUP_LISTS)]\n'
    '    counts = [0] * MAX_BACKUP_LISTS\n'
    '    blocked = blocked_urls()\n'
)
REPLACEMENT = (
    '    playlists = [[header] for _ in range(MAX_BACKUP_LISTS)]\n'
    '    counts = [0] * MAX_BACKUP_LISTS\n'
    + MARKER
    + '    # APTV auto-switches on primary timeout, so never use unchecked\n'
    + '    # third-party candidates as automatic failover.\n'
    + '    if os.environ.get("LUMINA_ALLOW_UNVERIFIED_BACKUPS") != "1":\n'
    + '        return [(header + "\\n", 0) for _ in range(MAX_BACKUP_LISTS)]\n'
    + '    blocked = blocked_urls()\n'
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "apply"))
    parser.add_argument("--path", type=Path, default=Path("/usr/local/bin/publish-nas-playlist.py"))
    args = parser.parse_args()
    path = args.path
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print("already patched")
        return
    if source.count(ANCHOR) != 1:
        raise RuntimeError("publisher structure changed; refused to patch")
    updated = source.replace(ANCHOR, REPLACEMENT, 1)
    compile(updated, str(path), "exec")
    if args.mode == "check":
        print("patch applicable")
        return
    stat = path.stat()
    fd, tmp = tempfile.mkstemp(prefix=".publish-nas-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, stat.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print("patched")


if __name__ == "__main__":
    main()
