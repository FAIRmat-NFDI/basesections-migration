from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

from nomad.datamodel.metainfo import basesections
from nomad.parsing.parser import ArchiveParser

from basesection_migration.migration.migrate import migrate_basesections

if TYPE_CHECKING:
    from nomad.datamodel.datamodel import EntryArchive
    from structlog.stdlib import BoundLogger


V1_BASESECTIONS_PACKAGE = 'nomad.datamodel.metainfo.basesections.v1'


def install_archive_parser_hook() -> bool:
    """Install the migration hook once when the active basesections package is v1.
    Returns True if the hook was installed, False otherwise."""

    if not (
        getattr(getattr(basesections, 'm_package', None), 'name', None)
        == V1_BASESECTIONS_PACKAGE
    ):
        return False

    original_parse = ArchiveParser.parse
    if getattr(original_parse, '_basesection_migration_hook', False):
        # original parse already wrapped with migration
        return True

    @wraps(original_parse)
    def parse_with_migration(
        self: ArchiveParser,
        mainfile: str,
        archive: EntryArchive,
        logger: BoundLogger | None = None,
        child_archives: dict[str, EntryArchive] | None = None,
    ) -> None:
        # Add archive migration here before parsing when the transformation is ready.
        migrate_basesections(mainfile, archive, logger)
        original_parse(self, mainfile, archive, logger, child_archives)

    parse_with_migration._basesection_migration_hook = True  # type: ignore
    ArchiveParser.parse = parse_with_migration

    return True
