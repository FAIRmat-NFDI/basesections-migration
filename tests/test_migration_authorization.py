"""Administrator validation must precede all migration work."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nomad.common import now

from basesection_migration.actions import activities, workflows
from basesection_migration.actions.models import (
    MigrationActionInput,
    MigrationActionOutput,
    MigrationEntry,
    TransformUploadInput,
    TransformUploadResult,
)


@pytest.fixture
def staging_upload_files(monkeypatch):
    monkeypatch.setattr(activities.config.services, 'admin_user_id', 'admin')
    get_upload_files = Mock()
    monkeypatch.setattr(activities, 'get_staging_upload_files', get_upload_files)
    return get_upload_files


@pytest.mark.parametrize(
    'user_id,invalid_upload,checked_uploads',
    [
        pytest.param('other', None, [], id='non-admin'),
        pytest.param('admin', 'report-upload', ['report-upload'], id='invalid-report'),
        pytest.param(
            'admin',
            'target-b',
            ['report-upload', 'target-a', 'target-b'],
            id='invalid-target',
        ),
    ],
)
def test_workflow_stops_on_invalid_input(
    monkeypatch, staging_upload_files, user_id, invalid_upload, checked_uploads
):
    def get_upload_files(upload_id):
        if upload_id == invalid_upload:
            raise ValueError('Invalid staging upload.')

    staging_upload_files.side_effect = get_upload_files

    async def execute(activity, argument, **kwargs):
        assert activity is activities.validate_input
        return activity(argument)

    execute_activity = AsyncMock(side_effect=execute)
    child_workflow = AsyncMock()
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)
    monkeypatch.setattr(workflows.workflow, 'execute_child_workflow', child_workflow)

    data = MigrationActionInput(
        user_id=user_id,
        upload_id='report-upload',
        target_upload_ids=['target-a', 'target-b'],
    )
    error = PermissionError if user_id != 'admin' else ValueError
    with pytest.raises(error):
        asyncio.run(workflows.MigrateBaseSectionsWorkflow().run(data))

    execute_activity.assert_awaited_once()
    call = execute_activity.await_args
    assert call.args == (activities.validate_input, data)
    assert call.kwargs['retry_policy'].maximum_attempts == 1
    assert [
        call.args[0] for call in staging_upload_files.call_args_list
    ] == checked_uploads
    child_workflow.assert_not_awaited()


@pytest.mark.parametrize('target_upload_ids', [[], ['target-a', 'target-b']])
def test_workflow_validates_input_before_discovery(
    monkeypatch, staging_upload_files, target_upload_ids
):
    entry = MigrationEntry(
        entry_id='entry',
        upload_id='target-a',
        mainfile='entry.archive.json',
        mainfile_os_path='/staging/target-a/raw/entry.archive.json',
        entry_data_m_def='nomad.datamodel.metainfo.basesections.v1.BaseSection',
    )

    async def execute(activity, argument, **kwargs):
        if activity is activities.validate_input:
            return activity(argument)
        if activity is activities.find_v1_entries:
            return [entry]
        if activity is activities.transform_entry:
            assert isinstance(argument, MigrationEntry)
            assert (
                MigrationEntry.model_validate_json(argument.model_dump_json()) == entry
            )
            return True
        if activity is activities.write_report:
            return 'report.json'
        raise AssertionError(f'Unexpected activity: {activity.__name__}')

    async def execute_child(workflow, argument, **kwargs):
        assert workflow is workflows.TransformUploadWorkflow.run
        child_input = TransformUploadInput.model_validate_json(
            argument.model_dump_json()
        )
        child_result = await workflows.TransformUploadWorkflow().run(child_input)
        return TransformUploadResult.model_validate_json(child_result.model_dump_json())

    execute_activity = AsyncMock(side_effect=execute)
    child_workflow = AsyncMock(side_effect=execute_child)
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)
    monkeypatch.setattr(workflows.workflow, 'execute_child_workflow', child_workflow)
    monkeypatch.setattr(
        workflows.workflow,
        'info',
        lambda: SimpleNamespace(workflow_id='migration', start_time=now()),
    )
    data = MigrationActionInput(
        user_id='admin', upload_id='report-upload', target_upload_ids=target_upload_ids
    )
    result = asyncio.run(workflows.MigrateBaseSectionsWorkflow().run(data))

    assert [call.args[0] for call in execute_activity.await_args_list] == [
        activities.validate_input,
        activities.find_v1_entries,
        activities.transform_entry,
        activities.write_report,
    ]
    assert execute_activity.await_args_list[0].args == (activities.validate_input, data)
    assert [call.args[0] for call in staging_upload_files.call_args_list] == [
        'report-upload',
        *target_upload_ids,
    ]
    assert result.report_path == 'report.json'
    assert result.found_count == 1
    assert result.transformed == [entry]
    assert MigrationActionOutput.model_validate_json(result.model_dump_json()) == result
    child_workflow.assert_awaited_once()
    child_input = child_workflow.await_args.args[1]
    assert child_input.target_upload_id == entry.upload_id
    assert child_input.entries == [entry]
    report_input = execute_activity.await_args_list[-1].args[1]
    assert report_input.found == [entry]
    assert report_input.transformed == [entry]
    assert report_input.model_dump()['found'][0]['entry_data_m_def'] == (
        entry.entry_data_m_def
    )
    assert report_input.model_dump()['transformed'][0]['entry_data_m_def'] == (
        entry.entry_data_m_def
    )
