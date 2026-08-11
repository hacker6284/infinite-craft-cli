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
    def test_pair_water_fire_makes_steam(self):
        from infinite_craft_cli.client import InfiniteCraftClient

        async def _test():
            async with InfiniteCraftClient(rate_limit=10) as client:
                result = await client.pair("Water", "Fire")
                assert result.name == "Steam"
                assert result.emoji is not None

        run_async(_test())
