"""Temporal workflows for the BaseSection v1 to v2 migration."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

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
        transformed_mainfiles: set[str] = set()

        for entry_id in dict.fromkeys(data.entry_ids):
            entry_ref = EntryRef(entry_id=entry_id, upload_id=data.target_upload_id)
            try:
                result = await workflow.execute_activity(
                    transform_entry,
                    TransformEntryInput(
                        target_upload_id=data.target_upload_id,
                        entry_id=entry_id,
                    ),
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=NO_RETRY,
                )
            except Exception as exc:
                result = TransformEntryResult(
                    entry_ref=entry_ref,
                    status='failed',
                    reason=str(exc),
                )

            if result.status == 'transformed':
                output.transformed.append(result.entry_ref)
                if result.mainfile:
                    transformed_mainfiles.add(result.mainfile)
            elif (
                result.status == 'skipped' and result.mainfile in transformed_mainfiles
            ):
                # Several indexed entry IDs can refer to one archive mainfile.
                output.transformed.append(result.entry_ref)
            elif result.status == 'skipped':
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

        found: list[EntryRef] = []
        transformed: list[EntryRef] = []
        skipped: list[TransformEntryResult] = []
        failed: list[TransformEntryResult] = []
        error: str | None = None

        try:
            found = await workflow.execute_activity(
                find_v1_entries,
                FindEntriesInput(user_id=data.user_id),
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=NO_RETRY,
            )

            entries_by_upload: dict[str, list[str]] = {}
            for entry in found:
                entries_by_upload.setdefault(entry.upload_id, []).append(entry.entry_id)

            for upload_id, entry_ids in entries_by_upload.items():
                try:
                    result = await workflow.execute_child_workflow(
                        TransformUploadWorkflow.run,
                        TransformUploadInput(
                            target_upload_id=upload_id,
                            entry_ids=entry_ids,
                        ),
                        id=f'{workflow.info().workflow_id}-upload-{upload_id}',
                        parent_close_policy=workflow.ParentClosePolicy.TERMINATE,
                        retry_policy=NO_RETRY,
                    )
                    transformed.extend(result.transformed)
                    skipped.extend(result.skipped)
                    failed.extend(result.failed)
                except Exception as exc:
                    failed.extend(
                        TransformEntryResult(
                            entry_ref=EntryRef(entry_id=entry_id, upload_id=upload_id),
                            status='failed',
                            reason=f'Upload workflow failed: {exc}',
                        )
                        for entry_id in dict.fromkeys(entry_ids)
                    )
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'

        report_path = await workflow.execute_activity(
            write_report,
            ReportInput(
                user_id=data.user_id,
                upload_id=data.upload_id,
                workflow_id=workflow.info().workflow_id,
                started_at=workflow.info().start_time.isoformat(),
                found=found,
                transformed=transformed,
                skipped=skipped,
                failed=failed,
                error=error,
            ),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=NO_RETRY,
        )
        if error:
            raise ApplicationError(error)

        return MigrationActionOutput(
            found_count=len(found),
            transformed=transformed,
            skipped_count=len(skipped),
            failed_count=len(failed),
            report_path=report_path,
        )
