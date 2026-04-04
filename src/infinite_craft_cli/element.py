"""Element dataclass for Infinite Craft."""

from dataclasses import dataclass


@dataclass(frozen=True, eq=False)
class Element:
    """An Infinite Craft element."""

    name: str | None = None
    emoji: str | None = None
    is_first_discovery: bool | None = None

    def __str__(self) -> str:
        if self.emoji:
            return f"{self.emoji} {self.name}"
        return self.name or ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Element):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __bool__(self) -> bool:
        return self.name is not None and self.emoji is not None and self.is_first_discovery is not None
