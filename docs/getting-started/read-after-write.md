# 📡 Read-After-Write Consistency

`django-rebac` writes authorization relationships **asynchronously** via the Transactional Outbox:

1. Your model is saved and the relationship tuples are enqueued into the `rebac_syncoutbox` table **inside the same database transaction** as your write.
2. After commit, the outbox is drained and the tuples are pushed to OpenFGA.
3. Only then does a `check()` / `list_objects()` reflect the new state.

That leaves a short window (typically well under a second with a live worker) in which a relationship you just wrote is **not yet visible** to an authorization query. This is expected behavior, not a bug — but it has a very visible consequence you will hit on day one.

## The symptom you will hit first

```python
# 1. Create the object (enqueues a WRITE tuple, commits)
Document.objects.create(title="Q3 plan", creator_id="user:1")

# 2. Immediately ask "can this user read it?"
#    The tuple may not be in OpenFGA yet -> the creator is briefly locked out.
check(user="user:1", relation="viewer", object="document:123")  # can be False for a moment
```

In HTTP terms: `POST /documents/` returns `201`, and the very next `GET /documents/123/` can return `403` until the outbox drains. Integration tests that create-then-read fail non-deterministically — fast enough to pass locally, slow enough to fail in CI.

## Mitigations

### 1. Synchronous drain for local development and CI (recommended)

Set `SYNC_MODE="INLINE"` in `REBAC_CONFIG`. The outbox row is still written durably first; the difference is that the drain runs **in-process** (`.apply()`) right after commit instead of being enqueued to Celery (`.delay()`). Create-then-read now works within a single request:

```python
# settings.py
REBAC_CONFIG = {
    "SYNC_MODE": "INLINE",  # drain in-process after commit (dev / CI)
    "BACKEND_OPTIONS": {"STORE_ID": "...", "API_URL": "..."},
}
```

Keep `"ASYNC"` (the default) in production where a broker + worker pool already provides throughput; the periodic Celery beat sweeper remains the load-bearing safety net either way. Losing the immediate dispatch (e.g. a broker blip) only costs latency — the durable outbox row is picked up by the sweeper — so a request never 500s on a write that already committed.

### 2. OpenFGA read consistency preference

Once the tuple **has** been written, OpenFGA may still serve reads from a replica with a small replication lag. Set `CONSISTENCY` in `BACKEND_OPTIONS` to control that window:

```python
REBAC_CONFIG = {
    "BACKEND_OPTIONS": {
        "STORE_ID": "...",
        "API_URL": "...",
        "CONSISTENCY": "HIGHER_CONSISTENCY",  # default: strong freshness
        # "CONSISTENCY": "MINIMAL_CONSISTENCY",  # lower latency on hot list endpoints
    },
}
```

Leave it unset (`None`) to use the OpenFGA server default. `HIGHER_CONSISTENCY` narrows the post-write replication window at the cost of some latency; `MINIMAL_CONSISTENCY` is the opposite trade and suits high-traffic `list_objects` endpoints.

> **Note:** `CONSISTENCY` only shrinks the *replication* window. It does **not** close the outbox drain window — if the tuple hasn't been written yet, no consistency preference will make it visible. For a zero window, use `SYNC_MODE="INLINE"`.

### 3. Client-side tolerance

If you must stay fully async and cannot accept the window, retry the authorization decision on `403`/empty results, or poll until the object appears. This is the least preferred option because it pushes a framework concern into your application code.
