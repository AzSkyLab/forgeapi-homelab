"""Local Temporal dev server without installing the CLI: `uv run python -m app.devserver`.

The SDK downloads the official dev-server binary on first use. History persists under DATA_DIR.
This is a development convenience, not a production Temporal deployment.
"""

import asyncio
import contextlib

from temporalio.testing import WorkflowEnvironment

from app.settings import settings


async def main() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        port=7233,
        ui=True,
        dev_server_database_filename=str(settings.data_dir.resolve() / "temporal.db"),
    ):
        print("Temporal dev server on localhost:7233, UI on http://localhost:8233 (Ctrl-C to stop)")
        await asyncio.Event().wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
