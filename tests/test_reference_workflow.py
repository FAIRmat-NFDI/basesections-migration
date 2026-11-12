"""The reference child discovers sources and repairs each with the shared mapping."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from basesection_migration.actions import activities, workflows
from basesection_migration.actions.models import (
    EntryRef,
    FindReferencingEntriesInput,
    FixReferencePathsInput,
    TransformReferencesInput,
)


@pytest.fixture
def data():
    return TransformReferencesInput(
        user_id='admin',
        transformed_entries_path='/actions/migration/artifacts/transformed_entries.json',
    )


@pytest.mark.parametrize(
    'source_specs',
    [
        pytest.param([], id='no-sources'),
        pytest.param(
            [('b1', 'upload-b'), ('a1', 'upload-a'), ('b2', 'upload-b')],
            id='discovery-order',
        ),
    ],
)
def test_child_schedules_one_repair_activity(data, monkeypatch, source_specs):
    sources = [
        EntryRef(
            entry_id=entry_id,
            upload_id=upload_id,
            mainfile=f'{entry_id}.archive.json',
            mainfile_os_path=f'/staging/{upload_id}/raw/{entry_id}.archive.json',
        )
        for entry_id, upload_id in source_specs
    ]
    execute_activity = AsyncMock(side_effect=[sources, None])
    monkeypatch.setattr(workflows.workflow, 'execute_activity', execute_activity)

    asyncio.run(workflows.TransformReferencesWorkflow().run(data))

    expected_calls = [
        (
            activities.find_referencing_entries,
            FindReferencingEntriesInput(
                user_id='admin', transformed_entries_path=data.transformed_entries_path
            ),
        )
    ]
    if sources:
        expected_calls.append(
            (
                activities.fix_reference_paths,
                FixReferencePathsInput(
                    source_entries=sources,
                    transformed_entries_path=data.transformed_entries_path,
                ),
            )
        )
    assert [call.args for call in execute_activity.await_args_list] == expected_calls


def test_activity_continues_after_reference_repair_failure(data, monkeypatch):
    sources = [
        EntryRef(
            entry_id=entry_id,
            upload_id=upload_id,
            mainfile=f'{entry_id}.archive.json',
            mainfile_os_path=f'/staging/{upload_id}/raw/{entry_id}.archive.json',
        )
        for entry_id, upload_id in [
            ('a1', 'upload-a'),
            ('b1', 'upload-b'),
            ('a2', 'upload-a'),
        ]
    ]
    repair = Mock(side_effect=[RuntimeError('Reference repair failed'), None, None])
    monkeypatch.setattr(activities, '_fix_reference_paths_for_entry', repair)

    activities.fix_reference_paths(
        FixReferencePathsInput(
            source_entries=sources,
            transformed_entries_path=data.transformed_entries_path,
        )
    )

    assert [call.args for call in repair.call_args_list] == [
        (entry, data.transformed_entries_path) for entry in sources
    ]
