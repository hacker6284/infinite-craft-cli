"""Infinite Craft CLI — combine elements from the terminal."""

try:
    from importlib.metadata import version, PackageNotFoundError
    __version__ = version("infinite-craft-cli")
except (ImportError, PackageNotFoundError):
    __version__ = "0.0.0"
