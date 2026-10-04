"""Independent Temporal worker process for the real interruption test."""

import asyncio
import json
import os
from datetime import timedelta
from functools import partial
from pathlib import Path
from unittest.mock import patch

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker._interceptor import (
    Interceptor,
    WorkflowInboundInterceptor,
    WorkflowOutboundInterceptor,
)

import app.worker as worker_module
from app import ledger
from app.settings import settings


class ShortApplyTimeout(Interceptor):
    def workflow_interceptor_class(self, input):
        return ShortApplyInbound


class ShortApplyInbound(WorkflowInboundInterceptor):
    def init(self, outbound):
        super().init(ShortApplyOutbound(outbound))


class ShortApplyOutbound(WorkflowOutboundInterceptor):
    def start_activity(self, input):
        if input.activity == "run_operation_phase" and input.args[1] == "apply":
            assert input.start_to_close_timeout == timedelta(minutes=30)
            input.start_to_close_timeout = timedelta(seconds=5)
        return super().start_activity(input)


async def main():
    if receipt := os.environ.get("FORGEAPI_TEST_LATE_RECEIPT"):
        original_finish = ledger.finish

        def recording_finish(op, state, **result):
            original_finish(op, state, **result)
            target = Path(receipt)
            pending = target.with_suffix(".tmp")
            pending.write_text(json.dumps({
                "operation_id": op["id"],
                "requested_state": state,
                "resulting_state": ledger.get(op["id"])["state"],
            }))
            pending.replace(target)

        ledger.finish = recording_finish
    client = await Client.connect(settings.temporal_address)
    with patch.object(worker_module, "Worker", partial(Worker, interceptors=[ShortApplyTimeout()])):
        worker = worker_module.build_worker(client)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
