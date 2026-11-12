"""Reference-repair metadata is written once all target uploads are transformed."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nomad.common import now

from basesection_migration.actions import activities, workflows
from basesection_migration.actions.models import (
    FindEntriesInput,
    MigrationActionInput,
    MigrationEntry,
    TransformEntryResult,
    TransformUploadResult,
)


@pytest.fixture
def action_context(tmp_path, monkeypatch):
    monkeypatch.setattr(activities.config.fs, 'actions', str(tmp_path / 'actions'))
    files = Mock()
    files.raw_file.side_effect = lambda name, *args, **kwargs: (tmp_path / name).open(
        *args, **kwargs
    )
    monkeypatch.setattr(
        activities, 'get_staging_upload_files', Mock(return_value=files)
    )
    info = SimpleNamespace(workflow_id='migration', start_time=now())
    monkeypatch.setattr(activities.activity, 'info', lambda: info)
    monkeypatch.setattr(workflows.workflow, 'info', lambda: info)
    return info


def test_mapping_files_are_isolated_by_action(action_context):
    first_mapping = {'target-a': 'example.v1.Schema'}
    first_path = Path(activities.write_transformed_entries(first_mapping))
    assert first_path == (
        Path(activities.action_instance_artifacts_dir(action_context.workflow_id))
        / 'transformed_entries.json'
    )

    action_context.workflow_id = 'other-migration'
    other_mapping = {'target-b': 'example.v1.Other'}
    other_path = Path(activities.write_transformed_entries(other_mapping))

    assert first_path != other_path
    assert json.loads(first_path.read_text(encoding='utf-8')) == first_mapping
    assert json.loads(other_path.read_text(encoding='utf-8')) == other_mapping


def test_cleanup_removes_selected_files_and_tolerates_missing_paths(action_context):
    path = Path(activities.write_transformed_entries({}))
    temporary_artifact = path.with_name('temporary.json')
    temporary_artifact.write_text('{}', encoding='utf-8')
    other_artifact = path.with_name('other.json')
    other_artifact.write_text('{}', encoding='utf-8')
    paths = [str(path.with_name('missing.json')), str(path), str(temporary_artifact)]

    activities.cleanup(paths)
    activities.cleanup(paths)
    activities.cleanup([])

    assert not path.exists()
    assert not temporary_artifact.exists()
    assert other_artifact.read_text(encoding='utf-8') == '{}'


@pytest.mark.parametrize(
    'upload_ids,report_fails',
    [
        pytest.param([], False, id='empty'),
        pytest.param(['upload-a', 'upload-b'], False, id='multiple-uploads'),
        pytest.param(['upload-a', 'upload-b'], True, id='report-failure'),
    ],
)
def test_parent_cleans_artifacts_only_on_success(
    action_context, monkeypatch, tmp_path, upload_ids, report_fails
):
    found = []
    children = {}
    expected_mapping = {}
    for upload_id in upload_ids:
        entries = [
            MigrationEntry(
                entry_id=f'{upload_id}-{name}',
                upload_id=upload_id,
                mainfile=f'{name}.archive.json',
                mainfile_os_path=f'/staging/{upload_id}/raw/{name}.archive.json',
                entry_data_m_def=m_def,
            )
            for name, m_def in [
                ('transformed', 'example.v1.Schema'),
                ('skipped', 'example.v1.Skipped'),
                ('failed', 'example.v1.Failed'),
            ]
        ]
        transformed, skipped, failed = entries
        found.extend(
            entry.model_copy(update={'entry_data_m_def': None}) for entry in entries
        )
        expected_mapping[transformed.entry_id] = transformed.entry_data_m_def
        children[upload_id] = TransformUploadResult(
            transformed=[transformed],
            skipped=[TransformEntryResult(entry_ref=skipped, status='skipped')],
            failed=[TransformEntryResult(entry_ref=failed, status='failed')],
        )

    events = []
    mapping_paths = []

    async def execute(activity, argument, **kwargs):
        events.append(activity.__name__)
        if activity is activities.validate_input:
            return None
        if activity is activities.find_v1_entries:
            assert argument == FindEntriesInput(
                user_id='admin', target_upload_ids=upload_ids
            )
            return found
        if activity is activities.write_transformed_entries:
            path = activity(argument)
            mapping_paths.append(Path(path))
            return path
        if activity is activities.write_report:
            assert json.loads(mapping_paths[0].read_text(encoding='utf-8')) == (
                expected_mapping
            )
            if report_fails:
                raise RuntimeError('Report failed')
            return activity(argument)
        if activity is activities.cleanup:
            assert argument == [str(mapping_paths[0])]
            return activity(argument)
        raise AssertionError(f'Unexpected activity: {activity.__name__}')

    async def execute_child(workflow, argument, **kwargs):
        if workflow is workflows.TransformReferencesWorkflow.run:
            assert argument.model_dump() == {
                'user_id': 'admin',
                'transformed_entries_path': str(mapping_paths[0]),
            }
            events.append('references')
            return None
        assert workflow is workflows.TransformUploadWorkflow.run
        assert argument.entries == [
            entry for entry in found if entry.upload_id == argument.target_upload_id
        ]
        events.append(argument.target_upload_id)
        return TransformUploadResult.model_validate_json(
            children[argument.target_upload_id].model_dump_json()
        )

    monkeypatch.setattr(
        workflows.workflow, 'execute_activity', AsyncMock(side_effect=execute)
    )
    monkeypatch.setattr(
        workflows.workflow,
        'execute_child_workflow',
        AsyncMock(side_effect=execute_child),
    )

    run = workflows.MigrateBaseSectionsWorkflow().run(
        MigrationActionInput(
            user_id='admin', upload_id='report-upload', target_upload_ids=upload_ids
        )
    )

    if report_fails:
        with pytest.raises(RuntimeError, match='Report failed'):
            asyncio.run(run)
    else:
        result = asyncio.run(run)
        assert 'transformed_entries_path' not in result.model_dump()
        report = json.loads((tmp_path / result.report_path).read_text(encoding='utf-8'))
        assert result.transformed == [
            entry for child in children.values() for entry in child.transformed
        ]
        assert report['transformed'] == [
            entry.model_dump() for entry in result.transformed
        ]
        assert report['found_count'] == result.found_count == len(found)
        assert report['skipped_count'] == result.skipped_count == len(upload_ids)
        assert report['failed_count'] == result.failed_count == len(upload_ids)

    assert events == [
        'validate_input',
        'find_v1_entries',
        *upload_ids,
        'write_transformed_entries',
        *(['references'] if upload_ids else []),
        'write_report',
        *([] if report_fails else ['cleanup']),
    ]
    assert mapping_paths[0].exists() == report_fails


def test_parent_preserves_artifacts_when_reference_child_fails(
    action_context, monkeypatch
):
    entry = MigrationEntry(
        entry_id='target',
        upload_id='upload',
        mainfile='target.archive.json',
        mainfile_os_path='/staging/upload/raw/target.archive.json',
        entry_data_m_def='example.v1.Schema',
    )
    mapping = {entry.entry_id: entry.entry_data_m_def}
    path = activities.write_transformed_entries(mapping)
    execute_activity = AsyncMock(side_effect=[None, [entry], path])
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)
    monkeypatch.setattr(
        workflows.workflow,
        'execute_child_workflow',
        AsyncMock(
            side_effect=[
                TransformUploadResult(transformed=[entry]),
                RuntimeError('Reference child failed'),
            ]
        ),
    )

    with pytest.raises(RuntimeError, match='Reference child failed'):
        asyncio.run(
            workflows.MigrateBaseSectionsWorkflow().run(
                MigrationActionInput(user_id='admin', upload_id='report-upload')
            )
        )

    assert [call.args[0] for call in execute_activity.await_args_list] == [
        activities.validate_input,
        activities.find_v1_entries,
        activities.write_transformed_entries,
    ]
    assert json.loads(Path(path).read_text(encoding='utf-8')) == mapping
