"""Temporal worker entrypoint: `uv run python -m app.worker`."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app import activities
from app.settings import settings
from app.workflows import DeployWorkflow, DestroyWorkflow


def build_worker(client: Client) -> Worker:
    return Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[DeployWorkflow, DestroyWorkflow],
        activities=activities.ALL,
        activity_executor=ThreadPoolExecutor(max_workers=4),
    )


async def main() -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    print(f"worker polling {settings.task_queue!r} on {settings.temporal_address}")
    await build_worker(client).run()


if __name__ == "__main__":
    asyncio.run(main())
