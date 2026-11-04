"""Reference discovery returns source file identities for later reference updates."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from basesection_migration.actions import activities
from basesection_migration.actions.models import (
    EntryRef,
    FindReferencingEntriesInput,
)


@pytest.fixture
def search(monkeypatch):
    search = Mock(return_value=iter(()))
    monkeypatch.setattr(activities, 'search_iterator', search)
    return search


def discover(*targets):
    return activities.find_referencing_entries(
        FindReferencingEntriesInput(user_id='admin', target_entry_ids=list(targets))
    )


def test_empty_input_avoids_search(search):
    assert discover() == []
    search.assert_not_called()


def test_collective_query_matches_any_target_without_schema_restriction(search):
    assert discover('target-a', 'target-b', 'target-a') == []
    search.assert_called_once()
    kwargs = search.call_args.kwargs
    assert kwargs['owner'] == 'admin'
    assert kwargs['user_id'] == 'admin'
    assert kwargs['required'].include == ['entry_id', 'upload_id', 'mainfile']
    assert kwargs['query'].to_dict() == {
        'bool': {
            'filter': [
                {'term': {'published': False}},
                {'regexp': {'mainfile.keyword': activities.MAINFILE_NAME_RE}},
                {
                    'nested': {
                        'path': 'entry_references',
                        'query': {
                            'terms': {
                                'entry_references.target_entry_id': [
                                    'target-a',
                                    'target-b',
                                ],
                            },
                        },
                    },
                },
            ],
        },
    }


def test_source_identity_preserves_subdirectories_without_backup_exclusion(
    search, monkeypatch
):
    mainfile = 'nested/source.archive.json'
    path = f'/staging/upload/raw/{mainfile}'
    search.return_value = iter(
        [{'entry_id': 'source', 'upload_id': 'upload', 'mainfile': mainfile}]
    )
    files = Mock()
    files.raw_path_exists.return_value = True
    files.raw_file_object.return_value = SimpleNamespace(os_path=path)
    get_files = Mock(return_value=files)
    monkeypatch.setattr(activities, 'get_staging_upload_files', get_files)

    assert discover('target-a', 'target-b') == [
        EntryRef(
            entry_id='source',
            upload_id='upload',
            mainfile=mainfile,
            mainfile_os_path=path,
        ),
    ]
    get_files.assert_called_once_with('upload')
    files.raw_file_object.assert_called_once_with(mainfile)
    files.raw_path_exists.assert_not_called()
