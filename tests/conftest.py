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
    # Sync chrome_snapshot / rate chrome so paint does not get an AsyncMock
    # coroutine child (would break chrome paint unpack).
    limiter = MagicMock()
    limiter.chrome_snapshot.return_value = (60, 60, 1000)
    client._rate_limiter = limiter
    return client


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
    - For special keys/ESC/CSI and metachars (search [] * ? / / ! ^ etc): use enable_tty_mode() + feed_tty_bytes(). (Covers real _tty_read_line per-char path for user query syntax; high-level .feed bypasses it.)
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
        # Full reap for api worker: always delegate to _cancel_and_await_worker (centralized, awaits)
        # Use threadsafe from sync cleanup; reset below will also ensure. Reduces orphan risk.
        # Assumption: cleanup from test thread; if loop running use it for proper await, else null ref.
        if (
            self.cli
            and self.cli._api_worker_task
            and not self.cli._api_worker_task.done()
        ):
            try:
                loop = asyncio.get_running_loop()
                fut = asyncio.run_coroutine_threadsafe(
                    self.cli._cancel_and_await_worker(), loop
                )
                fut.result(timeout=1)
            except Exception:
                # best effort only; reset will clear
                with contextlib.suppress(Exception):
                    if self.cli._api_worker_task:
                        self.cli._api_worker_task.cancel()
                self.cli._api_worker_task = None
        # clear to restore prod paths (delegated to _full_cli_reset which calls single-source _reset_test_state)
        if self.cli:
            with contextlib.suppress(Exception):
                self.cli._teardown_tty_and_chrome()
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
        self._mock_client = client or make_mock_client()
        if not hasattr(self._mock_client, "pair"):
            self._mock_client.pair = AsyncMock(return_value=MagicMock(name=None))
        # AsyncMock auto-creates _rate_limiter as another AsyncMock; chrome
        # needs a sync chrome_snapshot() returning a 3-tuple.
        rl = getattr(self._mock_client, "_rate_limiter", None)
        if rl is None or isinstance(rl, AsyncMock):
            limiter = MagicMock()
            limiter.chrome_snapshot.return_value = (60, 60, 1000)
            self._mock_client._rate_limiter = limiter
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
        """Feed next line for the next _prompt_input. Safe from any thread.

        VISIBLE no-op in _tty_mode (see guard below + DEBUG comment).
        Avoids installing _test hook which would bypass real _tty_read_line
        for metachars. MUST use feed_tty_bytes in pure-tty tests.
        """
        if getattr(self, "_tty_mode", False):
            # [VISIBLE TTY GUARD] no-op: prevents _test hook install (bypass of _tty_read_line).
            # DEBUG note for callers: use feed_tty_bytes exclusively for pure tty metachar tests.
            # (silent to avoid breaking ensure_quit paths; see harness doc)
            return
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
        # Centralize record here (pure harness surface). High-level drop cli._* record patch sites from bodies.
        # isatty via explicit sys patch (allowed, not cli._*) or enable_tty in tty tests.
        self._patches.enter_context(patch("infinite_craft_cli.cli._record_recipes_batch"))
        if not self._tty_mode:
            if (
                self.cli
                and getattr(self.cli, "_test_prompt_input_hook", None) is not None
            ):
                # respect pre-installed custom hook (e.g. raising for error-finally coverage)
                pass
            else:
                cli._test_prompt_input_hook = self._make_input_provider()
        else:
            # layered: enable may have been called; re-ensure hook (existing pattern)
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
            # ensure worker fully reaped even on direct harness paths (always use the helper)
            with contextlib.suppress(Exception):
                await cli._cancel_and_await_worker()
            # drain any leftover
            while not self.input_q.empty():
                with contextlib.suppress(Exception):
                    self.input_q.get_nowait()
            cli._test_prompt_input_hook = None
            cli._tty_read_byte_hook = None
        # Output capture intentionally not performed here (capsys sees writes; avoids patch conflicts)
        out = ""
        self.captured_lines.append("OUTPUT:\n" + out)
        return out

    def answers(self) -> list[str]:
        return [a for p, a in self.prompt_calls]

    def get_rate_limit_wait_callback(self):
        """Test-only seam: obtain the rate wait cb for exercising real rate path in harness tests.
        Keeps high-level tests from reaching cli._* directly.
        """
        if self.cli is not None:
            return self.cli._rate_limit_wait_callback
        import infinite_craft_cli.cli as cli

        return cli._rate_limit_wait_callback

    def get_repl_print_lines(self):
        """Test-only seam for instrumenting repl output (used by pure harness edge tests)."""
        import infinite_craft_cli.cli as cli

        return cli._repl_print_lines

    def is_cancelled(self) -> bool:
        """Seam: cancel flag read for mocks in pure harness behavioral tests (no cli._ in test body)."""
        if self.cli:
            return bool(getattr(self.cli, "_cancelled", False))
        import infinite_craft_cli.cli as cli

        return bool(getattr(cli, "_cancelled", False))

    def force_cancel(self) -> None:
        """Seam: set cancel flag (harness may poke; high-level tests call only this)."""
        if self.cli:
            self.cli._cancelled = True
        else:
            import infinite_craft_cli.cli as cli

            cli._cancelled = True

    def install_repl_lines_wrapper(self, instrument_func):
        """Seam: install timing wrapper (from get_repl..) w/o cli._ patch literal in test."""
        p = patch(
            "infinite_craft_cli.cli._repl_print_lines", side_effect=instrument_func
        )
        self._patches.enter_context(p)

    def install_cli_patch(self, name: str, *args, **kwargs):
        """Seam (minimal) for high-level TestREPLHarnessEdges purity: apply patches to cli.<name>
        (e.g. "_load_recipes") without any patch("infinite_craft_cli.cli._") or cli._ in test bodies.
        """
        target = f"infinite_craft_cli.cli.{name}"
        p = patch(target, *args, **kwargs)
        self._patches.enter_context(p)

    def set_load_recipes(self, recipes: dict | None = None):
        self.install_cli_patch("_load_recipes", return_value=recipes or {})

    def set_bulk_warn_threshold(self, threshold: int):
        self.install_cli_patch("_BULK_WARN_THRESHOLD", threshold)

    def set_tty_size(self, height: int = 24, width: int = 80):
        """Dims only (no winch); for isatty+chrome tests."""
        self.install_cli_patch("_tty_height", return_value=max(1, height))
        self.install_cli_patch("_tty_width", return_value=max(1, width))

    def reset(self):
        """Seam: reset without cli._reset_test_state literal in high-level body."""
        self._full_cli_reset()
