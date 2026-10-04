"""Test API process that can pause precisely after durable acceptance."""

import asyncio
import os
import sys
from pathlib import Path

import uvicorn

from app.dispatch import get_dispatcher
from app.main import app

if marker := os.environ.get("FORGEAPI_TEST_DISPATCH_MARKER"):
    class BlockedDispatcher:
        async def dispatch(self, operation_id, phase):
            pending = Path(marker).with_suffix(".tmp")
            pending.write_text(f"{operation_id} {phase}\n")
            pending.replace(marker)
            await asyncio.Event().wait()

    app.dependency_overrides[get_dispatcher] = BlockedDispatcher


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
