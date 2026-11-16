"""Reference discovery returns source file identities for later reference updates."""

import json
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


def discover(tmp_path, *targets):
    path = tmp_path / 'transformed_entries.json'
    path.write_text(
        json.dumps(dict.fromkeys(targets, 'example.v1.Schema')), encoding='utf-8'
    )
    return activities.find_referencing_entries(
        FindReferencingEntriesInput(user_id='admin', transformed_entries_path=str(path))
    )


def test_empty_mapping_avoids_search(search, tmp_path):
    assert discover(tmp_path) == []
    search.assert_not_called()


def test_collective_query_matches_targets_from_file(search, tmp_path):
    assert discover(tmp_path, 'target-a', 'target-b') == []
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
    search, monkeypatch, tmp_path
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

    assert discover(tmp_path, 'target-a', 'target-b') == [
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
