"""Temporal workflows for the BaseSection v1 to v2 migration."""

from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from basesection_migration.actions.activities import (
        find_v1_entries,
        transform_entry,
        validate_report_upload,
        write_report,
    )
    from basesection_migration.actions.models import (
        EntryRef,
        FindEntriesInput,
        MigrationActionInput,
        MigrationActionOutput,
        MigrationEntry,
        ReportInput,
        TransformEntryInput,
        TransformEntryResult,
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
        outcomes: dict[
            str, tuple[Literal['transformed', 'skipped', 'failed'], str | None]
        ] = {}
        for entry in data.entries:
            path = entry.mainfile_os_path
            if path not in outcomes:
                try:
                    transformed = await workflow.execute_activity(
                        transform_entry,
                        TransformEntryInput(mainfile_os_path=path),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=NO_RETRY,
                    )
                    if transformed:
                        outcomes[path] = ('transformed', None)
                    else:
                        outcomes[path] = (
                            'skipped',
                            'Archive is ineligible or already has a v1 backup',
                        )
                except Exception as exc:
                    outcomes[path] = ('failed', str(exc))

            status, reason = outcomes[path]
            result = TransformEntryResult(
                entry_ref=entry.entry_ref,
                mainfile=entry.mainfile,
                status=status,
                reason=reason,
            )
            if status == 'transformed':
                output.transformed.append(result.entry_ref)
            elif status == 'skipped':
                output.skipped.append(result)
            else:
                output.failed.append(result)

        return output


@workflow.defn
class MigrateBaseSectionsWorkflow:
    """Find staging entries, migrate them by upload, and save a run report."""

    @workflow.run
    async def run(self, data: MigrationActionInput) -> MigrationActionOutput:
        await workflow.execute_activity(
            validate_report_upload,
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
        transformed: list[EntryRef] = []
        skipped: list[TransformEntryResult] = []
        failed: list[TransformEntryResult] = []

        entries_by_upload: dict[str, list[MigrationEntry]] = {}
        for entry in found_migration_entries:
            entries_by_upload.setdefault(entry.entry_ref.upload_id, []).append(entry)

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

        report_path = await workflow.execute_activity(
            write_report,
            ReportInput(
                user_id=data.user_id,
                upload_id=data.upload_id,
                workflow_id=workflow.info().workflow_id,
                started_at=workflow.info().start_time.isoformat(),
                found=[entry.entry_ref for entry in found_migration_entries],
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
