"""Temporal workflows for the BaseSection v1 to v2 migration."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
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
    from basesection_migration.actions.models import (
        FindEntriesInput,
        FindReferencingEntriesInput,
        FixReferencePathsInput,
        MigrationActionInput,
        MigrationActionOutput,
        MigrationEntry,
        ReportInput,
        TransformEntryResult,
        TransformReferencesInput,
        TransformUploadInput,
        TransformUploadResult,
    )


NO_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn
class TransformUploadWorkflow:
    """Transform the selected mainfiles in one staging upload."""

    @workflow.run
    async def run(self, data: TransformUploadInput) -> TransformUploadResult:
        output = TransformUploadResult()
        outcomes: dict[str, TransformEntryResult] = {}
        for entry in data.entries:
            path = entry.mainfile_os_path
            if path not in outcomes:
                try:
                    outcomes[path] = await workflow.execute_activity(
                        transform_entry,
                        entry,
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=NO_RETRY,
                    )
                except Exception as exc:
                    outcomes[path] = TransformEntryResult(
                        entry_ref=entry, status='failed', reason=str(exc)
                    )

            outcome = outcomes[path]
            entry.entry_data_m_def = outcome.entry_ref.entry_data_m_def
            result = TransformEntryResult(
                entry_ref=entry,
                status=outcome.status,
                reason=outcome.reason,
            )
            if result.status == 'transformed':
                output.transformed.append(result.entry_ref)
            elif result.status == 'skipped':
                output.skipped.append(result)
            else:
                output.failed.append(result)

        return output


@workflow.defn
class TransformReferencesWorkflow:
    """Find source entries and repair their references in one activity."""

    @workflow.run
    async def run(self, data: TransformReferencesInput) -> None:
        source_entries = await workflow.execute_activity(
            find_referencing_entries,
            FindReferencingEntriesInput(
                user_id=data.user_id,
                transformed_entries_path=data.transformed_entries_path,
            ),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=NO_RETRY,
        )

        if source_entries:
            await workflow.execute_activity(
                fix_reference_paths,
                FixReferencePathsInput(
                    source_entries=source_entries,
                    transformed_entries_path=data.transformed_entries_path,
                ),
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=NO_RETRY,
            )


@workflow.defn
class MigrateBaseSectionsWorkflow:
    """Migrate staging entries, repair references, and save a run report."""

    @workflow.run
    async def run(self, data: MigrationActionInput) -> MigrationActionOutput:
        await workflow.execute_activity(
            validate_input,
            data,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=NO_RETRY,
        )

        found_migration_entries = await workflow.execute_activity(
            find_v1_entries,
            FindEntriesInput(
                user_id=data.user_id, target_upload_ids=data.target_upload_ids
            ),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=NO_RETRY,
        )
        transformed: list[MigrationEntry] = []
        skipped: list[TransformEntryResult] = []
        failed: list[TransformEntryResult] = []

        entries_by_upload: dict[str, list[MigrationEntry]] = {}
        for entry in found_migration_entries:
            entries_by_upload.setdefault(entry.upload_id, []).append(entry)

        for upload_id, entries in entries_by_upload.items():
            result = await workflow.execute_child_workflow(
                TransformUploadWorkflow.run,
                TransformUploadInput(
                    target_upload_id=upload_id,
                    entries=entries,
                ),
                id=f'{workflow.info().workflow_id}-upload-{upload_id}',
                parent_close_policy=workflow.ParentClosePolicy.TERMINATE,
                retry_policy=NO_RETRY,
            )
            transformed.extend(result.transformed)
            skipped.extend(result.skipped)
            failed.extend(result.failed)

        transformed_entry_m_defs = {
            entry.entry_id: entry.entry_data_m_def
            for entry in transformed
            if entry.entry_data_m_def is not None
        }
        transformed_entries_path = await workflow.execute_activity(
            write_transformed_entries,
            transformed_entry_m_defs,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=NO_RETRY,
        )

        try:
            if transformed_entry_m_defs:
                await workflow.execute_child_workflow(
                    TransformReferencesWorkflow.run,
                    TransformReferencesInput(
                        user_id=data.user_id,
                        transformed_entries_path=transformed_entries_path,
                    ),
                    id=f'{workflow.info().workflow_id}-transform-references',
                    parent_close_policy=workflow.ParentClosePolicy.TERMINATE,
                    retry_policy=NO_RETRY,
                )

            report_path = await workflow.execute_activity(
                write_report,
                ReportInput(
                    user_id=data.user_id,
                    upload_id=data.upload_id,
                    workflow_id=workflow.info().workflow_id,
                    started_at=workflow.info().start_time.isoformat(),
                    found=found_migration_entries,
                    transformed=transformed,
                    skipped=skipped,
                    failed=failed,
                ),
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=NO_RETRY,
            )
            return MigrationActionOutput(
                found_count=len(found_migration_entries),
                transformed=transformed,
                skipped_count=len(skipped),
                failed_count=len(failed),
                report_path=report_path,
            )
        finally:
            await workflow.execute_activity(
                cleanup,
                [transformed_entries_path],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=NO_RETRY,
            )
