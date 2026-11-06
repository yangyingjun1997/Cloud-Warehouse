from __future__ import annotations

import hashlib

from .models import ImportBatch, ImportIssue


def create_import_batch(*, actor, filename, content, mode, records, issues, summary):
    batch = ImportBatch.objects.create(
        source_filename=filename[:255],
        source_sha256=hashlib.sha256(content).hexdigest(),
        mode=mode,
        status=ImportBatch.Status.PREVIEWED,
        summary=summary,
        created_by=actor,
    )
    ImportIssue.objects.bulk_create([ImportIssue(batch=batch, **issue) for issue in issues])
    return batch
