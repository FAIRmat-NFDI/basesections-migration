"""Discovery, transformation, and reporting activities for BaseSection migration."""

import json
import re
from pathlib import Path

from elasticsearch.dsl import Q
from nomad.actions.manager import action_instance_artifacts_dir
from nomad.app.v1.models.models import MetadataRequired
from nomad.config import config
from nomad.search import search_iterator
from temporalio import activity

from basesection_migration.actions.models import (
    EntryRef,
    FindEntriesInput,
    FindReferencingEntriesInput,
    FixReferencePathsInput,
    MigrationActionInput,
    MigrationEntry,
    ReportInput,
    TransformEntryResult,
)
from basesection_migration.actions.utils import get_staging_upload_files
from basesection_migration.scripts.migrate import migrate_input_archive

MAINFILE_NAME_RE = r'.*(archive|metainfo)\.(json|yaml|yml)'

## Optional regex that matches with all nomad.datamodel.metainfo.basesections.v1
## sections, even if they are not inheriting BaseSection:
# V1_SECTION_NAME_RE = r'nomad\.datamodel\.metainfo\.basesections\.v1\..+'

V1_SECTION_NAME_RE = r'nomad\.datamodel\.metainfo\.basesections\.v1\.BaseSection'


@activity.defn
def validate_input(data: MigrationActionInput) -> None:
    """Before the migration workflow starts, fail if

    - user is not admin
    - upload_id does not resolve to a staging upload for saving the report
    - target_upload_ids does not resolve to staging uploads
    """
    if data.user_id != config.services.admin_user_id:
        raise PermissionError(
            'Only the configured NOMAD administrator can run this action.'
        )

    for upload_id in [data.upload_id, *data.target_upload_ids]:
        get_staging_upload_files(upload_id)


@activity.defn
def find_v1_entries(data: FindEntriesInput) -> list[MigrationEntry]:
    """Find unpublished entries that contain or inherit v1 BaseSection for migration.

    Entries that contain or inherit ArchiveSection available in
    `nomad.datamodel.metainfo.basesections.v1` are ignored for now.
    """
    query = Q(
        'bool',
        filter=[
            Q('term', published=False),
            Q(
                'nested',
                path='section_defs',
                query=Q(
                    'regexp',
                    **{'section_defs.definition_qualified_name': V1_SECTION_NAME_RE},
                ),
            ),
            Q('regexp', **{'mainfile.keyword': MAINFILE_NAME_RE}),
        ],
    )
    if data.target_upload_ids:
        query &= Q('terms', upload_id=data.target_upload_ids)

    entries = search_iterator(
        owner='admin',
        user_id=data.user_id,
        query=query,
        required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),  # type: ignore
    )
    migration_entries = []
    for entry in entries:
        entry_id = entry['entry_id']
        upload_id = entry['upload_id']
        mainfile = entry['mainfile']

        # skip if a backup mainfile exists, i.e., the mainfile was already transformed
        upload_files = get_staging_upload_files(upload_id)
        mainfile_path = Path(mainfile)
        suffix = mainfile_path.suffix
        backup_mainfile_path = mainfile_path.with_name(
            f'{mainfile_path.stem}.v1{suffix}'
        ).as_posix()
        if upload_files.raw_path_exists(backup_mainfile_path):
            continue

        migration_entries.append(
            MigrationEntry(
                entry_id=entry_id,
                upload_id=upload_id,
                mainfile=mainfile,
                mainfile_os_path=upload_files.raw_file_object(mainfile).os_path,
            )
        )
    return migration_entries


@activity.defn
def find_referencing_entries(
    data: FindReferencingEntriesInput,
) -> list[EntryRef]:
    """Load transformed target IDs from the mapping and find unpublished sources."""
    with Path(data.transformed_entries_path).open(encoding='utf-8') as file:
        target_ids = list(json.load(file))
    if not target_ids:
        return []

    query = Q(
        'bool',
        filter=[
            Q('term', published=False),
            Q('regexp', **{'mainfile.keyword': MAINFILE_NAME_RE}),
            Q(
                'nested',
                path='entry_references',
                query=Q('terms', **{'entry_references.target_entry_id': target_ids}),
            ),
        ],
    )
    entries = search_iterator(
        owner='admin',
        user_id=data.user_id,
        query=query,
        required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),  # type: ignore
    )
    referencing_entries = []
    for entry in entries:
        mainfile = entry['mainfile']
        upload_files = get_staging_upload_files(entry['upload_id'])
        referencing_entries.append(
            EntryRef(
                entry_id=entry['entry_id'],
                upload_id=entry['upload_id'],
                mainfile=mainfile,
                mainfile_os_path=upload_files.raw_file_object(mainfile).os_path,
            )
        )
    return referencing_entries


@activity.defn
def transform_entry(data: MigrationEntry) -> TransformEntryResult:
    """Transform a mainfile and return its outcome with the original ``data.m_def``."""

    try:
        transformed, entry_data_m_def = migrate_input_archive(data.mainfile_os_path)
        data.entry_data_m_def = entry_data_m_def
        if transformed:
            status, reason = ('transformed', None)
        else:
            status, reason = (
                'skipped',
                'Archive is ineligible or already has a v1 backup',
            )
    except Exception as exc:
        status, reason = ('failed', str(exc))

    return TransformEntryResult(entry_ref=data, status=status, reason=reason)


@activity.defn
def write_transformed_entries(data: dict[str, str]) -> str:
    """Save transformed entries as an action artifact.

    The mapping contains entry IDs returned by child workflows and their original,
    pre-migration ``data.m_def`` values.
    """
    artifact_dir = Path(action_instance_artifacts_dir(activity.info().workflow_id))
    output_path = artifact_dir / 'transformed_entries.json'
    with output_path.open('w', encoding='utf-8') as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    return output_path.as_posix()


def _fix_reference_paths_for_entry(
    source_entry: EntryRef, transformed_entries_path: str
) -> None:
    """Placeholder for repairing references in one source archive."""
    raise NotImplementedError('Reference path repair is not implemented yet.')


@activity.defn
def fix_reference_paths(data: FixReferencePathsInput) -> None:
    """Fix references in all source entries after target transformations finish.

    ``source_entries`` includes resolved mainfile paths. Process entries in order,
    continuing after individual failures. ``transformed_entries_path`` points to a
    JSON ``dict[str, str]`` mapping transformed entry IDs to their original,
    pre-migration ``data.m_def`` values.
    """
    for entry in data.source_entries:
        try:
            _fix_reference_paths_for_entry(entry, data.transformed_entries_path)
        except Exception:
            pass


@activity.defn
def cleanup(paths: list[str]) -> None:
    """Remove temporary artifact files, ignoring those that are already missing."""
    for path in paths:
        Path(path).unlink(missing_ok=True)


@activity.defn
def write_report(data: ReportInput) -> str:
    """Write a durable JSON report into the administrator's staging upload."""
    upload_files = get_staging_upload_files(data.upload_id)
    safe_workflow_id = re.sub(r'[^A-Za-z0-9._-]', '_', data.workflow_id)
    report_path = f'basesection_migration_{safe_workflow_id}.json'
    report = {
        'workflow_id': data.workflow_id,
        'started_at': data.started_at,
        'found_count': len(data.found),
        'transformed_count': len(data.transformed),
        'skipped_count': len(data.skipped),
        'failed_count': len(data.failed),
        'error': data.error,
        'found': [entry.model_dump() for entry in data.found],
        'transformed': [entry.model_dump() for entry in data.transformed],
        'skipped': [result.model_dump() for result in data.skipped],
        'failed': [result.model_dump() for result in data.failed],
    }
    with upload_files.raw_file(report_path, 'wt', encoding='utf-8') as file:
        json.dump(report, file, indent=2, ensure_ascii=False)
    return report_path
