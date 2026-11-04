"""Administrator validation must precede all migration work."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nomad.common import now

from basesection_migration.actions import activities, migrate_basesections, workflows
from basesection_migration.actions.models import MigrationActionInput


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(activities.config.services, 'admin_user_id', 'admin')
    return 'admin'


def test_validate_admin_accepts_administrator(admin):
    assert activities.validate_admin(admin) is None


def test_validate_admin_rejects_other_users(admin):
    with pytest.raises(PermissionError, match='administrator'):
        activities.validate_admin('other')


def test_workflow_stops_before_work_for_non_admin(admin, monkeypatch):
    async def execute(activity, argument, **kwargs):
        return activity(argument)

    execute_activity = AsyncMock(side_effect=execute)
    child_workflow = AsyncMock()
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)
    monkeypatch.setattr(workflows.workflow, 'execute_child_workflow', child_workflow)

    with pytest.raises(PermissionError, match='administrator'):
        asyncio.run(
            workflows.MigrateBaseSectionsWorkflow().run(
                MigrationActionInput(user_id='other', upload_id='report-upload')
            )
        )

    execute_activity.assert_awaited_once()
    call = execute_activity.await_args
    assert call.args == (activities.validate_admin, 'other')
    assert call.kwargs['retry_policy'].maximum_attempts == 1
    child_workflow.assert_not_awaited()


def test_workflow_validates_admin_before_report_and_discovery(admin, monkeypatch):
    async def execute(activity, argument, **kwargs):
        if activity is activities.validate_admin:
            return activity(argument)
        if activity is activities.find_v1_entries:
            return []
        if activity is activities.write_report:
            return 'report.json'
        return None

    execute_activity = AsyncMock(side_effect=execute)
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)
    monkeypatch.setattr(
        workflows.workflow,
        'info',
        lambda: SimpleNamespace(workflow_id='migration', start_time=now()),
    )
    result = asyncio.run(
        workflows.MigrateBaseSectionsWorkflow().run(
            MigrationActionInput(user_id=admin, upload_id='report-upload')
        )
    )

    assert [call.args[0] for call in execute_activity.await_args_list] == [
        activities.validate_admin,
        activities.validate_report_upload,
        activities.find_v1_entries,
        activities.write_report,
    ]
    assert result.report_path == 'report.json'
    assert result.found_count == 0


@pytest.mark.parametrize(
    'owner,published,staging,error',
    [
        ('other', False, True, PermissionError),
        ('admin', True, True, ValueError),
        ('admin', False, False, ValueError),
    ],
)
def test_report_upload_constraints_remain(
    monkeypatch, owner, published, staging, error
):
    monkeypatch.setattr(
        activities.Upload,
        'get',
        Mock(return_value=SimpleNamespace(main_author=owner, published=published)),
    )
    monkeypatch.setattr(
        activities.StagingUploadFiles, 'exists_for', Mock(return_value=staging)
    )
    with pytest.raises(error):
        activities.validate_report_upload(
            MigrationActionInput(user_id='admin', upload_id='report-upload')
        )


def test_admin_validation_registered():
    assert activities.validate_admin in migrate_basesections.load().activities
