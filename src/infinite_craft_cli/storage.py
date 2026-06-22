"""Persistent storage for discovered elements."""

import contextlib
import json
import os
import tempfile

from infinite_craft_cli.element import Element

_STARTERS = [
    {"name": "Water", "emoji": "💧", "is_first_discovery": False},
    {"name": "Fire", "emoji": "🔥", "is_first_discovery": False},
    {"name": "Wind", "emoji": "🌬️", "is_first_discovery": False},
    {"name": "Earth", "emoji": "🌍", "is_first_discovery": False},
]


class DiscoveryStorage:
    """Manages the discoveries JSON file and provides in-memory lookups."""

    def __init__(self, path: str):
        self._path = path
        self._elements: list[Element] = []
        self._index: dict[str, Element] = {}

        if os.path.exists(path):
            self._load()
        else:
            self._init_starters()

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            # Attempt simple repair for common truncation (e.g. interrupted atomic write)
            try:
                with open(self._path, encoding="utf-8") as f:
                    c = f.read().rstrip()
                raw = json.loads(c + "\n]\n")
                # rewrite clean version
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
                self._save()
                return
            except Exception:
                raise ValueError(
                    f"discoveries file is corrupted ({e}). "
                    f"Back up {self._path}, repair or delete it, then retry."
                ) from e
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
        dir_name = os.path.dirname(self._path) or "."
        os.makedirs(dir_name, exist_ok=True)
        raw = [
            {"name": e.name, "emoji": e.emoji, "is_first_discovery": e.is_first_discovery}
            for e in self._elements
        ]
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def get_all(self) -> list[Element]:
        return list(self._elements)

    def get_by_name(self, name: str) -> Element | None:
        return self._index.get(name)

    def add(self, *, name: str, emoji: str | None = None, is_first_discovery: bool | None = None) -> Element | None:
        """Add an element. Returns the Element if newly added, None if already exists."""
        if name in self._index:
            return None
        elem = Element(name=name, emoji=emoji, is_first_discovery=is_first_discovery)
        self._elements.append(elem)
        self._index[name] = elem
        self._save()
        return elem

    def add_batch(
        self,
        items: list[tuple[str, str | None, bool | None]],
    ) -> int:
        """Add multiple elements with a single disk write. Returns new count."""
        added = 0
        for name, emoji, is_discovery in items:
            if name in self._index:
                continue
            elem = Element(name=name, emoji=emoji, is_first_discovery=is_discovery)
            self._elements.append(elem)
            self._index[name] = elem
            added += 1
        if added:
            self._save()
        return added

    def remove(self, name: str) -> bool:
        """Remove an element by name. Returns True if removed."""
        if name not in self._index:
            return False
        del self._index[name]
        self._elements = [e for e in self._elements if e.name != name]
        self._save()
        return True

    def reload(self):
        """Re-read discoveries from disk."""
        self._load()
