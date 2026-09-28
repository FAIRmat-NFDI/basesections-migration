from __future__ import annotations

from typing import TYPE_CHECKING

from nomad.parsing.parser import Parser

if TYPE_CHECKING:
    from nomad.datamodel.datamodel import EntryArchive
    from structlog.stdlib import BoundLogger


class BootstrapParser(Parser):
    """A bootstrap parser that installs the migration hook on the ArchiveParser while
    claiming no archive files."""

    def is_mainfile(
        self,
        filename: str,
        mime: str,
        buffer: bytes,
        decoded_buffer: str,
        compression: str | None = None,
    ) -> bool:
        return False

    def parse(
        self,
        mainfile: str,
        archive: EntryArchive,
        logger: BoundLogger | None = None,
        child_archives: dict[str, EntryArchive] | None = None,
    ) -> None:
        raise RuntimeError('Bootstrap parser cannot parse files')
