"""Shared test fixtures for infinite-craft-cli tests."""

import asyncio
import queue
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import contextlib


class MockElement:
    """Lightweight mock for Element."""

    def __init__(self, name, emoji="", is_first_discovery=False):
        self.name = name
        self.emoji = emoji
        self.is_first_discovery = is_first_discovery

    def __str__(self):
        if self.emoji:
            return f"{self.emoji} {self.name}"
        return self.name

    def __eq__(self, other):
        return isinstance(other, MockElement) and self.name == other.name

    def __hash__(self):
        return hash(self.name)


def make_mock_storage(discoveries=None):
    """Create a mock DiscoveryStorage object."""
    if discoveries is None:
        discoveries = [
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
        ]

    storage = MagicMock()
    storage.get_all.return_value = list(discoveries)

    def get_by_name(name):
        for e in discoveries:
            if e.name == name:
                return e
        return None

    storage.get_by_name.side_effect = get_by_name
    storage.add.return_value = None
    storage.reload.return_value = None

    return storage


def make_mock_client():
    """Create a mock InfiniteCraftClient object."""
    client = AsyncMock()
    client.pair = AsyncMock()
    return client


# Keep for backwards compat
def make_mock_game(discoveries=None):
    """Deprecated: use make_mock_storage() and make_mock_client() instead."""
    return make_mock_storage(discoveries)


@pytest.fixture(autouse=True)
def reset_cli_globals(request):
    """Reset module-level CLI state that can leak between tests.

    Uses cli._reset_test_state() for comprehensive cleanup. A finalizer
    ensures best-effort reset even if the test body does not complete normally.
    Also cancels any stray api worker tasks.
    """
    import infinite_craft_cli.cli as cli

    def _full_reset():
        try:
            cli._reset_test_state()
        except Exception:
            # Best effort; don't let cleanup itself fail the suite
            pass

    _full_reset()
    request.addfinalizer(_full_reset)
    yield


@pytest.fixture
def mock_storage():
    return make_mock_storage()


@pytest.fixture
def mock_client():
    return make_mock_client()


@pytest.fixture
def mock_storage_with_extras():
    """Storage with more discoveries for testing search/match."""
    return make_mock_storage(
        [
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Steam", "💨"),
            MockElement("Lava", "🌋"),
            MockElement("Mud", ""),
            MockElement("Dust", ""),
            MockElement("Waterfall", "🏞️", is_first_discovery=True),
            MockElement("Firewall", "🧱", is_first_discovery=True),
        ]
    )


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory and patch data.py paths."""
    return tmp_path


@pytest.fixture
def recipes_file(tmp_path):
    """Create a temporary recipes file."""
    path = tmp_path / "recipes.json"
    return path


@pytest.fixture
def discoveries_file(tmp_path):
    """Create a temporary discoveries file."""
    path = tmp_path / "discoveries.json"
    return path


@pytest.fixture
def repl_harness():
    """Provide a REPLTestHarness with auto cleanup."""
    h = REPLTestHarness()
    with h:
        yield h
    # __exit__ did cleanup


# ---------------------------------------------------------------------------
# Overhauled test harness for interactive TUI: less fragile, full featured.
# Replaces ad-hoc patching, SIGALRM, sleep hacks, deprecated loops.
# Supports strong assertions on prompts, outputs, queue/chrome state, ordering.
# ---------------------------------------------------------------------------


class REPLTestHarness:
    """Reliable harness to drive interactive_mode.

    Uses _test_*_hook for deterministic input (no real tty/select/pipes).
    Queue for scripts avoids pop+ fragile sleeps/races.
    Owns patches via ExitStack for guaranteed restore.
    - Auto cleanup: cancels worker tasks, resets state, undoes chrome patches.
    - Records prompt calls (via .prompt_calls) for assertions. Output: rely on capsys (no stdout patch).
    - Supports local cmds during long ops via feed (queues enable interleaving; see legacy TimedPrompt for Event gates).
    - For special keys/ESC/CSI: use enable_tty_mode() + feed_tty_bytes(). (Incremental migration of _Pipe tests ongoing; see TestREPLHarnessEdges.)
    """

    def __init__(self):
        self.cli = None  # set on enter
        self.input_q: queue.Queue[str] = queue.Queue()
        self.prompt_calls: list[tuple[str, str]] = []
        self.captured_lines: list[str] = []
        self._patches = contextlib.ExitStack()
        self._mock_client: AsyncMock | None = None
        self._mock_storage = None
        self._tty_byte_q: queue.Queue[str] | None = None
        self._tty_mode = False
        self._running = False
        self._interactive_task: asyncio.Task | None = None
        self._ensure_task: asyncio.Task | None = None

    def __enter__(self):
        import infinite_craft_cli.cli as cli

        self.cli = cli
        # Force full reset at start
        self._full_cli_reset()
        # Do not patch stdout (rely on capsys for output capture in tests); only isatty for tty paths
        self._patches.enter_context(patch("sys.stdout.isatty", return_value=True))
        self._patches.enter_context(
            patch(
                "infinite_craft_cli.cli.DISCOVERIES_PATH",
                "/tmp/nonexistent_for_test.json",
            )
        )
        # Will set client/storage per run
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *a):
        self.cleanup()

    def _full_cli_reset(self):
        # Delegate to single-source cli reset (covers seams, workers, chrome, queue etc)
        try:
            self.cli._reset_test_state()
        except Exception:
            pass
        self._interactive_task = None
        self._ensure_task = None

    def cleanup(self):
        """Ensure no leaked tasks, fds, handlers, patches. Idempotent."""
        # Cancel tracked tasks (no cross-loop asyncio.run; avoids dead-loop attach)
        for t in (self._interactive_task, self._ensure_task):
            if t and not t.done():
                t.cancel()
        # Full reap for api worker: use threadsafe if loop avail, else best effort + reset will drain
        if self.cli and self.cli._api_worker_task and not self.cli._api_worker_task.done():
            coro = None
            try:
                loop = asyncio.get_running_loop()
                coro = self.cli._cancel_and_await_worker()
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                fut.result(timeout=1)
            except Exception:
                # best effort if no loop or would deadlock
                if coro is not None:
                    try:
                        coro.close()
                    except Exception:
                        pass
                try:
                    if self.cli._api_worker_task:
                        self.cli._api_worker_task.cancel()
                except Exception:
                    pass
                self.cli._api_worker_task = None
        # clear to restore prod paths (delegated to _full_cli_reset which calls single-source _reset_test_state)
        if self.cli:
            try:
                if self.cli._chrome_enabled:
                    self.cli._chrome_disable()
            except Exception:
                pass
            self._full_cli_reset()
        try:
            self._patches.close()
        except Exception:
            pass
        self._tty_byte_q = None
        self._running = False
        self._interactive_task = None
        self._ensure_task = None

    def set_mock_client(self, client: AsyncMock | None = None):
        """Prepare a mock client (pair etc)."""
        self._mock_client = client or AsyncMock()
        if not hasattr(self._mock_client, "pair"):
            self._mock_client.pair = AsyncMock(return_value=MagicMock(name=None))
        return self._mock_client

    def set_storage_elems(self, elems=None):
        self._mock_storage = make_mock_storage(elems)
        return self._mock_storage

    def _make_input_provider(self):
        """Return sync hook for _test_prompt_input_hook (runs in to_thread)."""

        def provider(prompt: str) -> str:
            self.prompt_calls.append((prompt, ""))
            # Blocking get is safe: this runs inside asyncio.to_thread(_read)
            try:
                line = self.input_q.get(timeout=2.0)
            except Exception:
                line = "/quit"
            self.prompt_calls[-1] = (prompt, line)
            self.captured_lines.append(f"PROMPT:{prompt} -> {line}")
            return line

        return provider

    def feed(self, line: str) -> None:
        """Feed next line for the next _prompt_input. Safe from any thread."""
        if self.cli and self.cli._test_prompt_input_hook is None:
            # auto install if not yet
            self.cli._test_prompt_input_hook = self._make_input_provider()
        self.input_q.put(line)

    def feed_tty_bytes(self, data: str | bytes) -> None:
        """For tty sim mode: feed raw bytes (e.g. '\x1b[A' for up)."""
        if not self._tty_mode or self._tty_byte_q is None:
            raise RuntimeError("call enable_tty_mode() first")
        if isinstance(data, bytes):
            data = data.decode("latin-1")
        for ch in data:
            self._tty_byte_q.put(ch)

    def enable_tty_mode(self):
        """Enable low-level byte hook for CSI/ESC/arrows testing."""
        self._tty_mode = True
        self._tty_byte_q = queue.Queue()

        def byte_hook(timeout: float) -> str | None:
            try:
                # block up to timeout
                return self._tty_byte_q.get(timeout=max(0.001, timeout or 0.1))
            except queue.Empty:
                return None

        if self.cli:
            self.cli._tty_read_byte_hook = byte_hook
            # mark as if tty available, mock termios to not touch real fds
            self._patches.enter_context(
                patch.object(self.cli, "_tty_input_available", return_value=True)
            )
            term_p = patch("infinite_craft_cli.cli.termios")
            mt = self._patches.enter_context(term_p)
            mt.tcgetattr.return_value = []
            mt.TCSADRAIN = 1
            self._patches.enter_context(patch("infinite_craft_cli.cli.tty"))
        return self

    def _make_tty_byte_hook(self):
        def byte_hook(timeout: float) -> str | None:
            try:
                return (
                    self._tty_byte_q.get(timeout=max(0.001, timeout or 0.1))
                    if self._tty_byte_q
                    else None
                )
            except queue.Empty:
                return None

        return byte_hook

    async def run_until_quit(
        self, *, auto_feed_quit: bool = True, client=None, storage=None
    ) -> str:
        """Drive full interactive_mode until it exits. Returns captured stdout."""
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import interactive_mode

        if client is None:
            client = self.set_mock_client()
        if storage is None:
            storage = self.set_storage_elems()
        # Re-setup patches for this run (in case __enter__ was light)
        # stdout patch already in stack from enter
        p_client = patch("infinite_craft_cli.cli.InfiniteCraftClient")
        p_storage = patch(
            "infinite_craft_cli.cli.DiscoveryStorage", return_value=storage
        )
        p_client_ctx = self._patches.enter_context(p_client)
        self._patches.enter_context(p_storage)
        p_client_ctx.return_value.__aenter__ = AsyncMock(return_value=client)
        p_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        if not self._tty_mode:
            cli._test_prompt_input_hook = self._make_input_provider()
        else:
            if self._tty_byte_q is None:
                self.enable_tty_mode()
            cli._tty_read_byte_hook = self._make_tty_byte_hook()
        if auto_feed_quit:
            # ensure we eventually exit even if script underfed
            async def _ensure_quit():
                await asyncio.sleep(0.02)
                if self.input_q.empty():
                    if (
                        self._tty_mode
                        and getattr(self, "_tty_byte_q", None) is not None
                    ):
                        # feed as individual chars so _tty_read_line byte_hook consumes correctly
                        for c in "/quit\n":
                            self._tty_byte_q.put(c)
                    else:
                        self.input_q.put("/quit")

            self._ensure_task = asyncio.create_task(_ensure_quit())
        self._running = True
        try:
            self._interactive_task = asyncio.create_task(interactive_mode())
            await self._interactive_task
        finally:
            self._running = False
            if self._ensure_task and not self._ensure_task.done():
                self._ensure_task.cancel()
            # ensure worker fully reaped even on direct harness paths
            try:
                await cli._cancel_and_await_worker()
            except Exception:
                pass
            # drain any leftover
            while True:
                try:
                    self.input_q.get_nowait()
                except Exception:
                    break
            cli._test_prompt_input_hook = None
            cli._tty_read_byte_hook = None
        # Output capture intentionally not performed here (capsys sees writes; avoids patch conflicts)
        out = ""
        self.captured_lines.append("OUTPUT:\n" + out)
        return out

    def get_captured(self) -> str:
        # Use capsys for output (see class doc). Kept for API compat, always ''.
        return ""

    def assert_prompt_count(self, n: int):
        assert len([c for c in self.prompt_calls if c[1]]) >= n

    def last_prompts(self) -> list[str]:
        return [p for p, a in self.prompt_calls]

    def answers(self) -> list[str]:
        return [a for p, a in self.prompt_calls]
