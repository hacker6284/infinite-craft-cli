"""Tests for /prune: orphan detection and Infinibrowser fillability checks."""

import asyncio
import sys
import pytest
from unittest.mock import patch, AsyncMock


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestPruneOrphans:
    def test_prunes_unfillable_orphan(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans_async
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=False):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    asyncio.run(_prune_orphans_async(storage))
        assert storage.get_by_name("Ghost") is None
        assert "Pruned 1" in capsys.readouterr().out

    def test_keeps_fillable_orphan(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans_async
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=True):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    asyncio.run(_prune_orphans_async(storage))
        assert storage.get_by_name("Steam") is not None
        captured = capsys.readouterr().out
        assert "Pruned 0" in captured
        assert "1 fillable" in captured

    def test_cancelled_during_sleep_stops_early(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans_async
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        storage.add(name="Phantom", emoji="👻", is_first_discovery=False)

        async def cancel_on_sleep(seconds, step=0.1):
            return True

        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=False):
                with patch(
                    "infinite_craft_cli.cli._sleep_cancellable_async",
                    side_effect=cancel_on_sleep,
                ):
                    asyncio.run(_prune_orphans_async(storage))
        captured = capsys.readouterr()
        assert "Stopped early" in captured.out
        # Cancel on first sleep leaves remaining work (do not require both removed).
        remaining = [
            name
            for name in ("Ghost", "Phantom")
            if storage.get_by_name(name) is not None
        ]
        assert remaining, "expected at least one orphan still present after early stop"

    def test_skips_on_api_error(self, capsys, tmp_path):
        from infinite_craft_cli.cli import _prune_orphans_async
        from infinite_craft_cli.storage import DiscoveryStorage

        path = tmp_path / "d.json"
        storage = DiscoveryStorage(str(path))
        storage.add(name="Ghost", emoji="👻", is_first_discovery=False)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(tmp_path / "r.json")):
            with patch("infinite_craft_cli.cli._ib_can_fill", return_value=None):
                with patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False):
                    asyncio.run(_prune_orphans_async(storage))
        assert storage.get_by_name("Ghost") is not None
        assert "skipped" in capsys.readouterr().out.lower()
