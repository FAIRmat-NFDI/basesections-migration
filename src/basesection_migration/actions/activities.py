"""Discovery, transformation, and reporting activities for BaseSection migration."""

import json
import re

from nomad.app.v1.models.models import MetadataRequired
from nomad.config import config
from nomad.datamodel.metainfo.basesections.v1 import BaseSection
from nomad.files import StagingUploadFiles
from nomad.processing.data import Entry, Upload
from nomad.search import search_iterator
from temporalio import activity

from basesection_migration.actions.models import (
    EntryRef,
    FindEntriesInput,
    MigrationActionInput,
    ReportInput,
    TransformEntryInput,
    TransformEntryResult,
)
from basesection_migration.scripts.migrate import migrate_input_archive

MAINFILE_NAME_RE = r'.*(archive|metainfo)\.(json|yaml|yml)$'


@activity.defn
def find_v1_entries(data: FindEntriesInput) -> list[EntryRef]:
    """Find staging archive mainfiles whose indexed schema includes v1 BaseSection."""
    if data.user_id != config.services.admin_user_id:
        raise PermissionError(
            'Only the configured NOMAD administrator can run this action.'
        )

    entries = search_iterator(
        owner='admin',
        user_id=data.user_id,
        query={
            'published': False,
            'section_defs.definition_qualified_name': (
                BaseSection.m_def.qualified_name()
            ),
        },
        required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),
    )
    mainfile_name_re = re.compile(MAINFILE_NAME_RE)
    return [
        EntryRef(entry_id=entry['entry_id'], upload_id=entry['upload_id'])
        for entry in entries
        if isinstance(entry.get('mainfile'), str)
        and mainfile_name_re.fullmatch(entry['mainfile'])
    ]


@activity.defn
def transform_entry(data: TransformEntryInput) -> TransformEntryResult:  # noqa: PLR0911
    """Transform a staged entry's mainfile, returning its outcome for the report."""
    entry_ref = EntryRef(entry_id=data.entry_id, upload_id=data.target_upload_id)
    mainfile = None

    def result(status: str, reason: str | None = None) -> TransformEntryResult:
        return TransformEntryResult(
            entry_ref=entry_ref,
            mainfile=mainfile,
            status=status,
            reason=reason,
        )

    try:
        entry = Entry.get(data.entry_id)
        mainfile = entry.mainfile
        if entry.upload_id != data.target_upload_id:
            return result('failed', 'Entry belongs to a different upload')

        upload = Upload.get(data.target_upload_id)
        if upload.published:
            return result('skipped', 'Upload is published')
        if not StagingUploadFiles.exists_for(data.target_upload_id):
            return result('skipped', 'Upload no longer has staging files')
        if not isinstance(mainfile, str) or not mainfile.endswith(
            ('archive.json', 'archive.yaml')
        ):
            return result('skipped', 'Entry mainfile is not a supported input archive')

        upload_files = StagingUploadFiles(data.target_upload_id)
        if not upload_files.raw_isfile(mainfile):
            return result('failed', 'Entry mainfile is missing')
        path = upload_files.raw_file_object(mainfile).os_path
        if migrate_input_archive(path):
            return result('transformed')
        return result('skipped', 'Archive is ineligible or already has a v1 backup')
    except Exception as exc:
        return result('failed', f'{type(exc).__name__}: {exc}')


def _report_upload(data: MigrationActionInput) -> StagingUploadFiles:
    if data.user_id != config.services.admin_user_id:
        raise PermissionError(
            'Only the configured NOMAD administrator can migrate entries.'
        )

    upload = Upload.get(data.upload_id)
    if upload is None:
        raise ValueError(f'Report upload {data.upload_id} does not exist.')
    if upload.main_author != data.user_id:
        raise PermissionError('The report upload must be owned by the administrator.')
    if upload.publish_time is not None or not StagingUploadFiles.exists_for(
        data.upload_id
    ):
        raise ValueError('The report upload must be an unpublished staging upload.')
    return StagingUploadFiles(data.upload_id)


@activity.defn
def validate_report_upload(data: MigrationActionInput) -> None:
    """Fail before migration starts if the report cannot be saved."""
    _report_upload(data)


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
