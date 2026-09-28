from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nomad.datamodel.datamodel import EntryArchive
    from structlog.stdlib import BoundLogger


def migrate_basesections(
    mainfile: str, archive: EntryArchive, logger: BoundLogger | None = None
):
    if logger is not None:
        logger.warning('Migrating archive from basesection v1 to v2')
