"""Original target definitions survive transformation and workflow boundaries."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from basesection_migration.actions import activities, workflows
from basesection_migration.actions.models import (
    MigrationEntry,
    TransformEntryResult,
    TransformUploadInput,
)
from basesection_migration.scripts import migrate

ORIGINAL_M_DEF = 'example.v1.Schema'
MIGRATED_M_DEF = 'example.v2.Schema'


@pytest.fixture
def entry(tmp_path):
    return MigrationEntry(
        entry_id='entry',
        upload_id='upload',
        mainfile='entry.archive.json',
        mainfile_os_path=str(tmp_path / 'entry.archive.json'),
    )


@pytest.mark.parametrize('suffix', ['json', 'yaml'])
def test_transform_entry_returns_original_definition(
    entry, tmp_path, monkeypatch, suffix
):
    mainfile = f'entry.archive.{suffix}'
    path = tmp_path / mainfile
    entry.mainfile = mainfile
    entry.mainfile_os_path = str(path)
    original = {'data': {'m_def': ORIGINAL_M_DEF}}
    original_bytes = (
        json.dumps(original) if suffix == 'json' else yaml.safe_dump(original)
    ).encode('utf-8')
    path.write_bytes(original_bytes)

    def transform(archive):
        archive['data']['m_def'] = MIGRATED_M_DEF
        return True

    monkeypatch.setattr(migrate, '_transform_archive', transform)

    result = activities.transform_entry(entry)

    assert result.status == 'transformed'
    assert result.entry_ref.entry_data_m_def == ORIGINAL_M_DEF
    assert yaml.safe_load(path.read_text())['data']['m_def'] == MIGRATED_M_DEF
    assert (tmp_path / f'entry.archive.v1.{suffix}').read_bytes() == original_bytes


def test_ineligible_archive_preserves_original_definition(entry, tmp_path, monkeypatch):
    path = tmp_path / entry.mainfile
    original_bytes = json.dumps({'data': {'m_def': ORIGINAL_M_DEF}}).encode('utf-8')
    path.write_bytes(original_bytes)
    monkeypatch.setattr(migrate, '_transform_archive', Mock(return_value=False))

    result = activities.transform_entry(entry)

    assert result.status == 'skipped'
    assert result.entry_ref.entry_data_m_def == ORIGINAL_M_DEF
    assert path.read_bytes() == original_bytes
    assert not (tmp_path / 'entry.archive.v1.json').exists()


def test_existing_backup_skips_without_using_migrated_definition(entry, tmp_path):
    path = tmp_path / entry.mainfile
    original_bytes = json.dumps({'data': {'m_def': ORIGINAL_M_DEF}}).encode('utf-8')
    migrated_bytes = json.dumps({'data': {'m_def': MIGRATED_M_DEF}}).encode('utf-8')
    path.write_bytes(migrated_bytes)
    backup_path = tmp_path / 'entry.archive.v1.json'
    backup_path.write_bytes(original_bytes)

    result = activities.transform_entry(entry)

    assert result.status == 'skipped'
    assert result.entry_ref.entry_data_m_def is None
    assert path.read_bytes() == migrated_bytes
    assert backup_path.read_bytes() == original_bytes


@pytest.mark.parametrize('original_bytes', [b'{', b'{"data": {}}', b'{"data": null}'])
def test_invalid_archive_is_reported_without_modifying_files(
    entry, tmp_path, original_bytes
):
    path = tmp_path / entry.mainfile
    path.write_bytes(original_bytes)

    result = activities.transform_entry(entry)

    assert result.status == 'failed'
    assert result.reason
    assert path.read_bytes() == original_bytes
    assert not (tmp_path / 'entry.archive.v1.json').exists()


def test_backup_race_is_reported_as_skipped(entry, tmp_path, monkeypatch):
    path = tmp_path / entry.mainfile
    original_bytes = json.dumps({'data': {'m_def': ORIGINAL_M_DEF}}).encode('utf-8')
    path.write_bytes(original_bytes)
    backup_path = tmp_path / 'entry.archive.v1.json'

    def transform(archive):
        backup_path.write_bytes(b'concurrent backup')
        return True

    monkeypatch.setattr(migrate, '_transform_archive', transform)

    result = activities.transform_entry(entry)

    assert result.status == 'skipped'
    assert result.entry_ref.entry_data_m_def == ORIGINAL_M_DEF
    assert path.read_bytes() == original_bytes
    assert backup_path.read_bytes() == b'concurrent backup'


def test_child_deduplicates_mainfiles_and_continues_after_failure(entry, monkeypatch):
    duplicate = entry.model_copy(update={'entry_id': 'duplicate'})
    other = entry.model_copy(
        update={
            'entry_id': 'other',
            'mainfile': 'other.archive.json',
            'mainfile_os_path': '/staging/upload/raw/other.archive.json',
        }
    )
    outcome = TransformEntryResult(
        entry_ref=other.model_copy(update={'entry_data_m_def': ORIGINAL_M_DEF}),
        status='transformed',
    )
    execute_activity = AsyncMock(
        side_effect=[
            RuntimeError('Activity timed out'),
            TransformEntryResult.model_validate_json(outcome.model_dump_json()),
        ]
    )
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)

    result = asyncio.run(
        workflows.TransformUploadWorkflow().run(
            TransformUploadInput(
                target_upload_id='upload',
                entries=[
                    entry,
                    duplicate,
                    other,
                    other.model_copy(update={'entry_id': 'other-duplicate'}),
                ],
            )
        )
    )

    assert [call.args[1].entry_id for call in execute_activity.await_args_list] == [
        'entry',
        'other',
    ]
    assert [outcome.entry_ref.entry_id for outcome in result.failed] == [
        'entry',
        'duplicate',
    ]
    assert [outcome.reason for outcome in result.failed] == [
        'Activity timed out',
        'Activity timed out',
    ]
    assert result.transformed == [
        outcome.entry_ref,
        outcome.entry_ref.model_copy(update={'entry_id': 'other-duplicate'}),
    ]
    assert result.skipped == []
