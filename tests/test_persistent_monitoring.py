import pytest

import monitoring
from metrics_repository import ensure_schema, increment_counter, load_counters, record_event


@pytest.mark.asyncio
async def test_persistent_counter_survives_in_memory_reset(db):
    await ensure_schema()
    await increment_counter("production_probe", 3)

    monitoring._COUNTERS.pop("production_probe", None)
    await monitoring.initialize_persistent_monitoring()

    assert monitoring.get_metrics()["production_probe"] == 3


@pytest.mark.asyncio
async def test_persistent_event_can_be_written_and_counters_are_read(db):
    await ensure_schema()
    await record_event("test_event", '{"ok": true}')
    await increment_counter("event_probe", 1)

    counters = await load_counters()
    assert counters["event_probe"] == 1
