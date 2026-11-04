"""Discovery filters v1 BaseSection definitions and mainfile names in the index."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from basesection_migration.actions import activities
from basesection_migration.actions.models import (
    EntryRef,
    FindEntriesInput,
    MigrationEntry,
)


@pytest.fixture
def search(monkeypatch):
    search = Mock(return_value=iter(()))
    monkeypatch.setattr(activities, 'search_iterator', search)
    return search


@pytest.mark.parametrize('upload_ids', [[], ['upload-a', 'upload-b']])
def test_query_filters_by_nested_v1_sections_and_mainfile(search, upload_ids):
    result = activities.find_v1_entries(
        FindEntriesInput(user_id='admin', target_upload_ids=upload_ids)
    )
    assert result == []
    search.assert_called_once()
    kwargs = search.call_args.kwargs
    assert kwargs['owner'] == 'admin'
    assert kwargs['user_id'] == 'admin'
    assert kwargs['required'].include == ['entry_id', 'upload_id', 'mainfile']
    query = kwargs['query'].to_dict()['bool']
    section_pattern = activities.V1_SECTION_NAME_RE
    assert query['filter'] == [
        {'term': {'published': False}},
        {
            'nested': {
                'path': 'section_defs',
                'query': {
                    'regexp': {
                        'section_defs.definition_qualified_name': section_pattern,
                    },
                },
            },
        },
        {'regexp': {'mainfile.keyword': activities.MAINFILE_NAME_RE}},
    ]
    assert query.get('must', []) == (
        [{'terms': {'upload_id': upload_ids}}] if upload_ids else []
    )


def test_returns_migration_entries_and_skips_backed_up_files(search, monkeypatch):
    search.return_value = iter(
        [
            {
                'entry_id': 'eligible',
                'upload_id': 'upload',
                'mainfile': 'example.archive.yaml',
            },
            {
                'entry_id': 'backup',
                'upload_id': 'upload',
                'mainfile': 'migrated.archive.yaml',
            },
        ]
    )
    files = Mock()
    files.raw_path_exists.side_effect = lambda path: path == 'migrated.archive.v1.yaml'
    files.raw_file_object.return_value = SimpleNamespace(os_path='/staging/example')
    get_files = Mock(return_value=files)
    monkeypatch.setattr(activities, 'get_staging_upload_files', get_files)

    result = activities.find_v1_entries(FindEntriesInput(user_id='admin'))

    assert result == [
        MigrationEntry(
            entry_ref=EntryRef(entry_id='eligible', upload_id='upload'),
            mainfile='example.archive.yaml',
            mainfile_os_path='/staging/example',
        )
    ]
    get_files.assert_called_with('upload')
    files.raw_file_object.assert_called_once_with('example.archive.yaml')


def test_staging_validation_failure_stops_discovery(search, monkeypatch):
    search.return_value = iter(
        [
            {
                'entry_id': 'entry',
                'upload_id': 'missing',
                'mainfile': 'example.archive.yaml',
            },
        ]
    )
    get_files = Mock(side_effect=ValueError('Upload has no staging files.'))
    monkeypatch.setattr(activities, 'get_staging_upload_files', get_files)

    with pytest.raises(ValueError, match='Upload has no staging files'):
        activities.find_v1_entries(FindEntriesInput(user_id='admin'))

    get_files.assert_called_once_with('missing')
