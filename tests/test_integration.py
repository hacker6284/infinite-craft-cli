"""Integration tests that hit the real neal.fun API. Skipped by default.

Run with: bazel test //tests:test_integration --test_env=INTEGRATION_TESTS=1
"""

import asyncio
import os
import sys
import pytest

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Set INTEGRATION_TESTS=1 to run integration tests",
)


def run_async(coro):
    return asyncio.run(coro)


class TestClientIntegration:
    def test_session_starts(self):
        from infinite_craft_cli.client import InfiniteCraftClient

        async def _test():
            async with InfiniteCraftClient(rate_limit=10) as client:
                assert client._session is not None

        run_async(_test())

    def test_pair_water_fire_makes_steam(self):
        from infinite_craft_cli.client import InfiniteCraftClient

        async def _test():
            async with InfiniteCraftClient(rate_limit=10) as client:
                result = await client.pair("Water", "Fire")
                assert result.name == "Steam"
                assert result.emoji is not None

        run_async(_test())

    def test_pair_nothing_result(self):
        from infinite_craft_cli.client import InfiniteCraftClient

        async def _test():
            async with InfiniteCraftClient(rate_limit=10) as client:
                result = await client.pair("Water", "Water")
                # Water + Water typically returns something, but we just verify
                # the response is a valid Element (name is str or None)
                assert result.name is None or isinstance(result.name, str)

        run_async(_test())


class TestStorageIntegration:
    def test_round_trip(self, tmp_path):
        from infinite_craft_cli.storage import DiscoveryStorage

        path = str(tmp_path / "test_discoveries.json")
        storage = DiscoveryStorage(path)
        assert len(storage.get_all()) == 4  # starters

        storage.add(name="Steam", emoji="💨", is_first_discovery=False)
        assert storage.get_by_name("Steam") is not None

        # Reload from disk
        storage2 = DiscoveryStorage(path)
        assert storage2.get_by_name("Steam") is not None
        assert len(storage2.get_all()) == 5
