"""Discovery, transformation, and reporting activities for BaseSection migration."""

import json
import re
from pathlib import Path

from elasticsearch.dsl import Q
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
    """Find unpublished entries that contain reference to the migration entries."""
    target_ids = list(dict.fromkeys(data.target_entry_ids))
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
def transform_entry(data: MigrationEntry) -> bool:
    """Transform a resolved mainfile, returning whether it was migrated."""

    # TODO: Populate data.entry_data_m_def of the migration entries when their
    # archive mainfiles are opened. This information will be required for the
    # activity that updates the reference paths in other entries that point
    # to migration entries.

    return migrate_input_archive(data.mainfile_os_path)


@activity.defn
def fix_reference_paths(data: FixReferencePathsInput) -> None:
    """Fix references in one source entry after all target transformations finish.

    Intended to run once per source entry. ``target_entry_m_defs_path`` points
    to a JSON ``dict[str, str]`` mapping target entry IDs to their original,
    pre-migration ``data.m_def`` values.
    """
    raise NotImplementedError('Reference path repair is not implemented yet.')


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
