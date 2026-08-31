# System Checks

`django-rebac` registers a Django [system check](https://docs.djangoproject.com/en/stable/topics/checks/) suite. Every misconfiguration the framework can have is statically detectable at startup — and it is reported at startup, not as a 500 on request #1 (or, before the T1.1 fix, not as a silent world-readable collection).

Run it explicitly:

```console
$ python manage.py check
```

or rely on it running automatically with every management command (`runserver`, `migrate`, `collectstatic`, ...).

## The checks

| Code | Level | What it catches |
|------|-------|-----------------|
| `rebac.E001` | ERROR | OpenFGA backend selected but `STORE_ID` is missing — the client can never initialize. |
| `rebac.E002` | ERROR | A `RebacViewConfig` declares `object_type` but **no relations at all**. Since T1.1 an unconfigured relation *denies*, so such a config protects nothing *and* allows nothing — the T1.1 failure mode, caught before the first request. |
| `rebac.E003` | ERROR | A `RebacModelConfig` references a field that does not exist on its model (e.g., `local_field="creator_id"` with no `creator_id` column). The row would fail in the background worker and surface days later as "my list is empty". |
| `rebac.E004` | ERROR | `LOCAL_DEV_FALLBACK.USE_DJANGO_USER` is `True` while `DEBUG` is `False` — a local-development identity fallback enabled in production. |
| `rebac.W001` | WARNING | No Celery beat entry runs the outbox drain. The post-write dispatch is best-effort (a broker hiccup is swallowed by design, T2.9); without a periodic sweeper, rows would sit `PENDING` forever. |
| `rebac.W002` | WARNING | The default database backend lacks `select_for_update(skip_locked=True)` (needs Postgres, MySQL ≥ 8.0.1, or MariaDB ≥ 10.6) — every outbox drain would crash at claim time. |

## Example output

A misconfigured project:

```console
$ python manage.py check
SystemCheckError: System check identified some issues:

?: ERROR: REBAC_CONFIG['BACKEND_OPTIONS'] is missing 'STORE_ID' — the OpenFGA
backend cannot be initialized, and every authorization call will fail at
request time. (rebac.E001)
?: ERROR: RebacViewConfig(object_type='invoice') declares no relations at all
(read/update/delete/list/action_relations are all empty). ... (rebac.E002)
?: WARNING: CELERY_BEAT_SCHEDULE is defined but no entry runs the ReBAC outbox
drain task 'process_rebac_outbox_batch'. ... (rebac.W001)
```

A healthy project:

```console
$ python manage.py check
System check identified no issues (0 silenced).
```

## What is *not* checked

- **Relations vs. the DSL.** Verifying that a configured relation exists in your OpenFGA authorization model requires talking to the store (and a pinned `AUTHORIZATION_MODEL_ID`). `rebac_reconcile` is the tool for that class of drift.
- **The identity proxy.** `REBAC_CONFIG['REQUEST_HEADER_MAPPINGS']` must still be fronted by a trusting proxy (Traefik forward-auth) or `TRUSTED_PROXIES` — that is an infrastructure fact no code check can verify. See the security note in the middleware docs.
