# rebac/management/commands/rebac_backfill.py
"""The "build" half of build / verify / repair for the ReBAC derived index.

Adopting django-rebac on a table that already has rows has no other supported path:
the sync mixin only fires on `save()`. This command walks the existing rows, derives
the tuples Django state expects, and bulk-enqueues them as WRITE rows into the
transactional outbox (idempotent under the conflict options — replaying the
same tuple is a no-op, so re-running a backfill is always safe).
"""

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import models

from rebac.core.adapters import RebacTupleAdapter
from rebac.models.mixins import RebacModelSyncMixin
from rebac.models.outbox import RebacSyncOutbox
from rebac.services import RebacTupleIngestionService
from rebac.utils import resolve_rebac_model

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "`bulk-enqueue` WRITE tuples for every existing row of a ReBAC model "
        "(builds the ReBAC derived index for rows created before adoption)."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "model",
            help="Django model label, e.g. 'myapp.Folder' or 'Folder'.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows per iterator chunk / bulk_create flush (default: %(default)s).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows/tuples would be enqueued without touching the outbox.",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Skip the worker dispatch after enqueuing (the periodic sweeper will drain).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        model = self._resolve_rebac_model(options["model"])
        batch_size = max(1, options["batch_size"])

        buffered: list[dict[str, Any]] = []
        rows = 0
        tuples = 0
        enqueued = 0

        for instance in model.objects.iterator(chunk_size=batch_size):
            config = instance._get_validated_config()
            expected = RebacTupleAdapter.generate_tuples(instance, config)
            rows += 1
            tuples += len(expected)

            if options["dry_run"]:
                continue

            for t in expected:
                buffered.append({"action": RebacSyncOutbox.Action.WRITE, **t})

            if len(buffered) >= batch_size:
                enqueued += self._flush(buffered)
                buffered.clear()

        if buffered:
            enqueued += self._flush(buffered)
            buffered.clear()

        # One dispatch for the whole backfill, not one per chunk.
        if enqueued and not options["no_sync"]:
            RebacTupleIngestionService.trigger_sync()

        if options["dry_run"]:
            self.stdout.write(
                self.style.NOTICE(
                    f"dry-run: {rows} row(s), {tuples} tuple(s) would be enqueued for "
                    f"'{model.__name__}'. Re-run without --dry-run to apply."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backfill complete: {rows} row(s) scanned, {enqueued} tuple(s) "
                    f"enqueued for '{model.__name__}'."
                )
            )
            if enqueued and options["no_sync"]:
                self.stdout.write(
                    self.style.WARNING(
                        "Worker dispatch skipped (--no-sync); the periodic sweeper "
                        "will drain the enqueued rows."
                    )
                )
        logger.info(
            "rebac_backfill %s: %d row(s), %d tuple(s) enqueued.",
            model.__name__,
            rows,
            enqueued,
        )

    def _flush(self, buffered: list[dict[str, Any]]) -> int:
        """Bulk-enqueues a buffered chunk (one INSERT, no per-row savepoint)."""
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
                "'rebac_config') to be backfilled."
            )
        return model
