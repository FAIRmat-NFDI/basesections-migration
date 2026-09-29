import inspect
import json

import pytest
from nomad.datamodel import Context, EntryArchive
from nomad.datamodel.metainfo import basesections
from nomad.datamodel.metainfo.basesections import v1, v2
from nomad.parsing.parser import ArchiveParser
from nomad.parsing.parsers import match_parser

from basesection_migration.parser_hook import archive_parser_migration_hook


@pytest.fixture
def original_archive_parse(monkeypatch):
    original_parse = inspect.unwrap(ArchiveParser.parse)
    monkeypatch.setattr(ArchiveParser, 'parse', original_parse)
    return original_parse


def test_hook_is_only_installed_for_v1(monkeypatch, original_archive_parse):
    monkeypatch.setattr(basesections, 'm_package', v2.m_package)
    bootstrap = archive_parser_migration_hook.load()

    assert ArchiveParser.parse is original_archive_parse
    assert not bootstrap.is_mainfile('sample.archive.json', 'application/json', b'', '')

    monkeypatch.setattr(basesections, 'm_package', v1.m_package)
    bootstrap = archive_parser_migration_hook.load()
    patched_parse = ArchiveParser.parse

    assert patched_parse is not original_archive_parse
    assert patched_parse.__wrapped__ is original_archive_parse
    assert not bootstrap.is_mainfile('sample.archive.json', 'application/json', b'', '')

    archive_parser_migration_hook.load()
    assert ArchiveParser.parse is patched_parse


@pytest.mark.parametrize('basesections_package', [v2.m_package])
def test_builtin_archive_parser_matches_and_parses_v2(
    tmp_path, monkeypatch, original_archive_parse, basesections_package
):
    monkeypatch.setattr(basesections, 'm_package', basesections_package)
    archive_parser_migration_hook.load()
    archive_data = {
        'definitions': {
            'section_definitions': [
                {
                    'name': 'TestSection',
                    'base_sections': ['nomad.datamodel.data.EntryData'],
                    'quantities': [{'name': 'test_quantity', 'type': 'str'}],
                }
            ]
        },
        'data': {
            'm_def': '#/definitions/section_definitions/0',
            'test_quantity': 'test_value',
        },
    }
    mainfile = tmp_path / 'sample.archive.json'
    mainfile.write_text(json.dumps(archive_data))
    parser, mainfile_keys = match_parser(str(mainfile))
    archive = EntryArchive(m_context=Context())

    assert type(parser) is ArchiveParser
    assert parser.name == 'parsers/archive'
    assert mainfile_keys is None
    parser.parse(str(mainfile), archive)

    assert archive.data.test_quantity == 'test_value'
