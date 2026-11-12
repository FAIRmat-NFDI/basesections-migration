"""Administrator validation must precede all migration work."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from basesection_migration.actions import activities, workflows
from basesection_migration.actions.models import MigrationActionInput


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
    assert [
        call.args[0] for call in staging_upload_files.call_args_list
    ] == checked_uploads
    child_workflow.assert_not_awaited()


@pytest.mark.parametrize('target_upload_ids', [[], ['target-a', 'target-b']])
def test_validation_checks_report_and_target_uploads(
    staging_upload_files, target_upload_ids
):
    activities.validate_input(
        MigrationActionInput(
            user_id='admin',
            upload_id='report-upload',
            target_upload_ids=target_upload_ids,
        )
    )

    assert [call.args[0] for call in staging_upload_files.call_args_list] == [
        'report-upload',
        *target_upload_ids,
    ]
