"""Discovery, transformation, and reporting activities for BaseSection migration."""

import json
import re
from pathlib import Path

from nomad.app.v1.models.models import MetadataRequired
from nomad.config import config
from nomad.datamodel.metainfo.basesections.v1 import BaseSection as BaseSectionV1
from nomad.files import StagingUploadFiles
from nomad.processing.data import Upload
from nomad.search import search_iterator
from temporalio import activity

from basesection_migration.actions.models import (
    EntryRef,
    FindEntriesInput,
    MigrationActionInput,
    MigrationEntry,
    ReportInput,
    TransformEntryInput,
)
from basesection_migration.scripts.migrate import migrate_input_archive

MAINFILE_NAME_RE = r'.*(archive|metainfo)\.(json|yaml|yml)$'


@activity.defn
def validate_admin(user_id: str) -> None:
    """Reject unauthorized users before the migration workflow does any work."""
    if user_id != config.services.admin_user_id:
        raise PermissionError(
            'Only the configured NOMAD administrator can run this action.'
        )


def _report_upload(data: MigrationActionInput) -> StagingUploadFiles:
    upload = Upload.get(data.upload_id)
    if upload is None:
        raise ValueError(f'Report upload {data.upload_id} does not exist.')
    if upload.published or not StagingUploadFiles.exists_for(data.upload_id):
        raise ValueError('The report upload must be an unpublished staging upload.')
    return StagingUploadFiles(data.upload_id)


@activity.defn
def validate_report_upload(data: MigrationActionInput) -> None:
    """Fail before migration starts if the report cannot be saved."""
    _report_upload(data)


@activity.defn
def find_v1_entries(data: FindEntriesInput) -> list[MigrationEntry]:
    """Find staging archive mainfiles whose indexed schema includes v1 BaseSection."""
    query = {
        'published': False,
        'section_defs.definition_qualified_name': BaseSectionV1.m_def.qualified_name(),
    }
    if data.target_upload_ids:
        query['upload_id:any'] = data.target_upload_ids

    entries = search_iterator(
        owner='admin',
        user_id=data.user_id,
        query=query,
        required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),
    )
    mainfile_name_re = re.compile(MAINFILE_NAME_RE)
    migration_entries = []
    for entry in entries:
        upload_id = entry.get('upload_id')
        mainfile = entry.get('mainfile')
        if (
            not StagingUploadFiles.exists_for(upload_id)
            or not mainfile
            or not mainfile_name_re.fullmatch(mainfile)
        ):
            continue

        # skip if a backup mainfile exists, i.e., the mainfile was already transformed
        upload_files = StagingUploadFiles.get(upload_id)
        mainfile_path = Path(mainfile)
        suffix = mainfile_path.suffix
        backup_mainfile_path = f'{mainfile_path.name.removesuffix(suffix)}.v1{suffix}'
        if upload_files.raw_path_exists(backup_mainfile_path):
            continue

        migration_entries.append(
            MigrationEntry(
                entry_ref=EntryRef(
                    entry_id=entry['entry_id'], upload_id=entry['upload_id']
                ),
                mainfile=mainfile,
                mainfile_os_path=upload_files.raw_file_object(mainfile).os_path,
            )
        )
    return migration_entries


@activity.defn
def transform_entry(data: TransformEntryInput) -> bool:
    """Transform a resolved mainfile, returning whether it was migrated."""
    return migrate_input_archive(data.mainfile_os_path)


@activity.defn
def write_report(data: ReportInput) -> str:
    """Write a durable JSON report into the administrator's staging upload."""
    upload_files = _report_upload(
        MigrationActionInput(user_id=data.user_id, upload_id=data.upload_id)
    )
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
