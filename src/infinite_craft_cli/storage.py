"""Persistent storage for discovered elements."""

import json
import os

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False  # Windows

from infinite_craft_cli.element import Element

_STARTERS = [
    {"name": "Water", "emoji": "💧", "is_first_discovery": False},
    {"name": "Fire", "emoji": "🔥", "is_first_discovery": False},
    {"name": "Wind", "emoji": "🌬️", "is_first_discovery": False},
    {"name": "Earth", "emoji": "🌍", "is_first_discovery": False},
]


class DiscoveryStorage:
    """Manages the discoveries JSON file and provides in-memory lookups.

    Uses file locking (fcntl) on Unix to support concurrent access from
    multiple processes sharing the same discoveries file.
    """

    def __init__(self, path: str):
        self._path = path
        self._lock_path = path + ".lock"
        self._elements: list[Element] = []
        self._index: dict[str, Element] = {}

        if os.path.exists(path):
            self._load()
        else:
            self._init_starters()

    def _lock(self, exclusive: bool):
        """Acquire a file lock. Returns the lock file handle (caller must close)."""
        if not _HAS_FCNTL:
            return None
        os.makedirs(os.path.dirname(self._lock_path) or ".", exist_ok=True)
        lock = open(self._lock_path, "w")
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return lock

    def _unlock(self, lock):
        """Release a file lock."""
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def _load(self):
        lock = self._lock(exclusive=False)
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        finally:
            self._unlock(lock)
        self._elements = []
        self._index = {}
        for d in raw:
            elem = Element(
                name=d.get("name"),
                emoji=d.get("emoji"),
                is_first_discovery=d.get("is_first_discovery"),
            )
            self._elements.append(elem)
            if elem.name is not None:
                self._index[elem.name] = elem

    def _init_starters(self):
        self._elements = []
        self._index = {}
        for d in _STARTERS:
            elem = Element(**d)
            self._elements.append(elem)
            self._index[elem.name] = elem
        self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        raw = [
            {"name": e.name, "emoji": e.emoji, "is_first_discovery": e.is_first_discovery}
            for e in self._elements
        ]
        lock = self._lock(exclusive=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
        finally:
            self._unlock(lock)

    def get_all(self) -> list[Element]:
        return list(self._elements)

    def get_by_name(self, name: str) -> Element | None:
        return self._index.get(name)

    def add(self, *, name: str, emoji: str | None = None, is_first_discovery: bool | None = None) -> Element | None:
        """Add an element. Returns the Element if newly added, None if already exists.

        Re-reads from disk first to pick up changes from other processes.
        """
        self._load()
        if name in self._index:
            return None
        elem = Element(name=name, emoji=emoji, is_first_discovery=is_first_discovery)
        self._elements.append(elem)
        self._index[name] = elem
        self._save()
        return elem

    def reload(self):
        """Re-read discoveries from disk."""
        self._load()
