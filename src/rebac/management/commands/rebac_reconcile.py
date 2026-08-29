# rebac/management/commands/rebac_reconcile.py
"""The "verify" (and optional "repair") half of build / verify / repair.

Reads what the ReBAC store actually holds for every row of a model (via the
`read_tuples` contract method) and diffs it against the tuples Django state
expects. By default it only REPORTS drift — a reconciler must never mutate state
on a hunch. With `--apply`, the repairs (WRITE for missing tuples, DELETE for
unexpected/orphaned ones) are queued through the transactional outbox, so they
still flow through the normal idempotent worker pipeline instead of being
sent ad hoc.
"""

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import models

from rebac.backends.base.exceptions import RebacError
from rebac.core.adapters import RebacTupleAdapter
from rebac.models.mixins import RebacModelSyncMixin
from rebac.models.outbox import RebacSyncOutbox
from rebac.services import RebacTupleIngestionService
from rebac.utils import get_rebac_client, resolve_rebac_model

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Diff the ReBAC store against Django state for a model. Reports "
        "drift by default; with --apply, queues repairs (WRITE missing / DELETE "
        "unexpected) through the transactional outbox."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "model",
            help="Django model label, e.g. 'myapp.Folder' or 'Folder'.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Queue the drift repairs into the outbox (WRITE missing, DELETE unexpected).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows per iterator chunk / repair flush (default: %(default)s).",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Skip the worker dispatch after applying repairs (the sweeper will drain).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        model = self._resolve_rebac_model(options["model"])
        batch_size = max(1, options["batch_size"])
        apply = options["apply"]

        client = get_rebac_client()

        rows = 0
        drifted = 0
        missing = 0
        unexpected = 0
        enqueued = 0
        buffered: list[dict[str, Any]] = []

        try:
            for instance in model.objects.iterator(chunk_size=batch_size):
                config = instance._get_validated_config()
                object_id = f"{config.object_type}:{instance.pk}"
                expected = RebacTupleAdapter.generate_tuples(instance, config)

                stored = client.read_tuples(object_id)

                expected_keys = {(t["user"], t["relation"]) for t in expected}
                stored_keys = {(t["user"], t["relation"]) for t in stored}

                gone = [t for t in expected if (t["user"], t["relation"]) not in stored_keys]
                extra = [t for t in stored if (t["user"], t["relation"]) not in expected_keys]

                rows += 1
                if gone or extra:
                    drifted += 1
                    missing += len(gone)
                    unexpected += len(extra)
                    self.stdout.write(f"DRIFT {object_id}:")
                    for t in gone:
                        self.stdout.write(f"  missing    (WRITE)    {t['user']} {t['relation']}")
                    for t in extra:
                        self.stdout.write(f"  unexpected (DELETE)   {t['user']} {t['relation']}")

                    if apply:
                        for t in gone:
                            buffered.append({"action": RebacSyncOutbox.Action.WRITE, **t})
                        for t in extra:
                            buffered.append({"action": RebacSyncOutbox.Action.DELETE, **t})
                        if len(buffered) >= batch_size:
                            enqueued += self._flush(buffered)
                            buffered.clear()
        except RebacError as e:
            # The store state could not be fully verified; abort before any partial
            # repair report can mislead the operator into trusting it.
            raise CommandError(
                f"Reconcile aborted after {rows} row(s) of '{model.__name__}': {e}"
            ) from e

        if apply and buffered:
            enqueued += self._flush(buffered)
            buffered.clear()
            if not options["no_sync"]:
                RebacTupleIngestionService.trigger_sync()

        if drifted == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"OK: {rows} row(s) of '{model.__name__}' match the store — no drift."
                )
            )
        elif apply:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Repaired {drifted}/{rows} drifted row(s) of '{model.__name__}': "
                    f"{enqueued} outbox row(s) enqueued ({missing} WRITE, {unexpected} DELETE)."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"{drifted}/{rows} row(s) of '{model.__name__}' have drifted "
                    f"({missing} missing tuple(s), {unexpected} unexpected tuple(s)). "
                    "Re-run with --apply to queue the repairs through the outbox."
                )
            )
        logger.info(
            "rebac_reconcile %s: %d row(s) scanned, %d drifted, %d repair(s) enqueued.",
            model.__name__,
            rows,
            drifted,
            enqueued,
        )

    def _flush(self, buffered: list[dict[str, Any]]) -> int:
        """Bulk-enqueues a buffered repair chunk (one INSERT, no per-row savepoint)."""
        return RebacTupleIngestionService.queue_tuples(tuples=buffered, trigger_worker=False)

    @staticmethod
    def _resolve_rebac_model(label: str) -> type[models.Model]:
        try:
            model = resolve_rebac_model(label)
        except LookupError as e:
            raise CommandError(
                f"Unknown model '{label}'. Use the 'app.Model' form (e.g. 'myapp.Folder')."
            ) from e

        if not (isinstance(model, type) and issubclass(model, RebacModelSyncMixin)):
            raise CommandError(
                f"'{label}' must inherit from RebacModelSyncMixin (it must define a "
                "'rebac_config') to be reconciled."
            )
        return model
