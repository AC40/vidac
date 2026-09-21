"""Reconnect originals by content without changing editorial records or derivatives."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from vidac.db import Database
from vidac.media import Check, digest, noop


def fingerprint(path: Path, check: Check = noop) -> str:
    before = path.stat()
    sha = digest(path, check)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
        after.st_dev,
    ):
        raise ValueError(f"File changed while checking it. Wait for the copy to finish: {path}")
    return sha


def relink_file(db: Database, asset_id: str, path: Path, check: Check = noop) -> dict:
    asset = db.asset(asset_id)
    path = path.expanduser().resolve()
    try:
        matches = path.is_file() and fingerprint(path, check) == asset["sha256"]
    except OSError as exc:
        raise ValueError(f"Cannot read original: {path}: {exc}") from exc
    if not matches:
        raise ValueError("Relinking requires the exact original file (SHA-256 must match).")
    check()
    db.execute("UPDATE assets SET path=? WHERE id=?", (str(path), asset_id))
    return db.asset(asset_id)


def search_relinks(
    db: Database,
    root: Path,
    progress: Callable[[float, str], None],
    check: Check,
) -> dict:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Search folder is unavailable: {root}")
    missing = {
        row["sha256"]: row
        for row in db.all("SELECT id,path,name,size,sha256 FROM assets ORDER BY id")
        if not Path(row["path"]).is_file()
    }
    sizes = {row["size"] for row in missing.values()}
    relinked, warnings = [], []
    scanned = 0

    def warn(error):
        if len(warnings) < 30:
            warnings.append(str(error))

    progress(0, "Searching for missing originals")
    # Do not follow directory symlinks: mounted folders may contain cycles.
    for folder, directories, names in os.walk(root, onerror=warn, followlinks=False):
        check()
        directories.sort()
        for name in sorted(names):
            check()
            if not missing:
                break
            path = Path(folder) / name
            scanned += 1
            if scanned % 100 == 0:
                progress(0, f"Searching · {scanned} files · {len(relinked)} relinked")
            try:
                if not path.is_file() or path.stat().st_size not in sizes:
                    continue
                progress(0, f"Checking moved media · {scanned} files · {len(relinked)} relinked")
                sha = fingerprint(path, check)
                asset = missing.get(sha)
                if asset is None:
                    continue
                check()
                # A concurrent manual relink may already have restored this source.
                current = db.asset(asset["id"])
                if not current["online"]:
                    db.execute(
                        "UPDATE assets SET path=? WHERE id=? AND path=?",
                        (str(path.resolve()), asset["id"], current["path"]),
                    )
                    relinked.append(
                        {"asset_id": asset["id"], "name": asset["name"], "path": str(path)}
                    )
                del missing[sha]
            except (OSError, ValueError) as exc:
                warn(exc)
        if not missing:
            break
    unresolved = [
        {"asset_id": asset["id"], "name": asset["name"], "path": asset["path"]}
        for asset in missing.values()
        if not db.asset(asset["id"])["online"]
    ]
    progress(1, f"{len(relinked)} relinked · {len(unresolved)} still missing")
    return {"relinked": relinked, "missing": unresolved, "scanned": scanned, "warnings": warnings}
