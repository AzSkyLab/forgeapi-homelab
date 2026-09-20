"""Local Temporal dev server without installing the CLI: `uv run python -m app.devserver`.

The SDK downloads the official dev-server binary on first use. History is in-memory; it is a
development convenience, equivalent to `temporal server start-dev`.
"""

import asyncio
import contextlib

from temporalio.testing import WorkflowEnvironment


async def main() -> None:
    async with await WorkflowEnvironment.start_local(port=7233, ui=True) as _:
        print("Temporal dev server on localhost:7233, UI on http://localhost:8233 (Ctrl-C to stop)")
        await asyncio.Event().wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
