# Async assessment and parallel futures

Large IFC assessments overlap schema/EXPRESS validation in a spawned Python process with the existing geometry and material calculation. IfcOpenShell's native geometry iterator continues to use its configured worker threads. Small files avoid the extra process startup.

The regular assessment endpoint already calls `assess_ifc`, so the parallel path applies to regular calculations. `assessment_futures.assess_ifc_async` additionally provides an awaitable facade for async callers using `asyncio.to_thread`; it does not introduce a background job queue or change the page response contract.

Each process opens its own IFC model. Native IFC instances never cross the process boundary. Only the source hash and plain diagnostics return from the validation future. The final report waits for validation, verifies source hashes, preserves nonfatal diagnostics and recalculates diagnostic groups. Process startup/failure falls back to serial validation. A semaphore limits validation to one subprocess per application process. This adds a second in-memory IFC instance during concurrent validation.

Configuration:

- `IFC_PARALLEL_VALIDATION`: enabled by default; set environment variable to `false` to disable.
- `IFC_PARALLEL_VALIDATION_MIN_BYTES`: default 2,000,000 bytes.
- Existing `IFC_GEOMETRY_THREADS`: default four native geometry workers.
- Python API `parallel_validation=False` forces serial assessment; `True` forces the future on machines with multiple CPUs.

Async usage:

```python
from plugins.bim_model_manager.ifc_extractor.assessment_futures import assess_ifc_async

report = await assess_ifc_async(path, reference, options)
```

Standalone scripts using spawned workers must call assessments under an `if __name__ == '__main__':` guard.

Validation checks cover exact serial/parallel report equivalence for valid and invalid IFC fixtures, source integrity, process-failure fallback, configuration and CPU limits, event-loop responsiveness, native parallel geometry and regular report integration. Actual A–D model benchmark results are in `IFC_ASYNC_FUTURES_BENCHMARK.json`; building values are compared against the recorded actual-house calculations, schema occurrence counts are preserved and input hashes are checked. House A has both serial and parallel timings; other houses have parallel timings only. Measurements are a single local run, not a guaranteed deployment speedup.
