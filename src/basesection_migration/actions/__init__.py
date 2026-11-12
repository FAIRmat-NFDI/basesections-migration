"""NOMAD action entry point for migrating BaseSection v1 archives."""

from nomad.actions import TaskQueue
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nomad.config import config as nomad_config
    from nomad.config.models.plugins import ActionEntryPoint


class MigrateBaseSectionsActionEntryPoint(ActionEntryPoint):
    def load(self):
        from nomad.actions import Action

        from basesection_migration.actions.activities import (
            cleanup,
            find_referencing_entries,
            find_v1_entries,
            fix_reference_paths,
            transform_entry,
            validate_input,
            write_report,
            write_transformed_entries,
        )
        from basesection_migration.actions.workflows import (
            MigrateBaseSectionsWorkflow,
            TransformReferencesWorkflow,
            TransformUploadWorkflow,
        )

        return Action(
            task_queue=self.task_queue,
            workflow=MigrateBaseSectionsWorkflow,
            child_workflows=[TransformUploadWorkflow, TransformReferencesWorkflow],
            activities=[
                validate_input,
                find_v1_entries,
                find_referencing_entries,
                transform_entry,
                write_transformed_entries,
                fix_reference_paths,
                write_report,
                cleanup,
            ],
        )


migrate_basesections = MigrateBaseSectionsActionEntryPoint(
    name='Migrate BaseSections v1 to v2',
    description=(
        'Migrate archive mainfiles from v1 to v2 and report '
        'the entries requiring reprocessing.'
    ),
    task_queue=TaskQueue.CPU,
    users=[nomad_config.services.admin_user_id],
)  # type: ignore
