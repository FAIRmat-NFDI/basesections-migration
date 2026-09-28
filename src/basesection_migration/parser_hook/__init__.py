from nomad.config.models.plugins import ParserEntryPoint
from pydantic import Field


class MigrationHookEntryPoint(ParserEntryPoint):
    migration_hook_installed: bool = Field(
        False,
        description='Whether the migration hook is installed on the ArchiveParser.',
    )

    def load(self):
        from basesection_migration.parser_hook.bootstrap_parser import BootstrapParser
        from basesection_migration.parser_hook.hook import install_archive_parser_hook

        self.migration_hook_installed = install_archive_parser_hook()

        return BootstrapParser()


archive_parser_migration_hook = MigrationHookEntryPoint(
    name='Basesections migration hook',
    description='Entry point that hooks basesection migration '
    'into the ArchiveParser.parse when active basesections package is v1.',
)  # type: ignore
