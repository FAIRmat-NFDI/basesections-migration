from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from importlib import resources
from pathlib import Path

import yaml
from nomad.datamodel.metainfo.annotations import Rules
from nomad.utils.json_transformer import Transformer

from basesection_migration.scripts.update_basesections_step_1 import (
    _inherits_from_v1_basesection,
)
from basesection_migration.scripts.update_basesections_step_2 import (
    BASE_SECTIONS_V1_LIST,
    get_schema_from_source_data,
    transform_section,
    validate_and_get_subdict_refs,
)


def _create_transformer() -> Transformer:
    rules_dir = resources.files('basesection_migration').joinpath(
        'scripts', 'transformation_rules'
    )
    rules = {
        f'{section}_transformation': Rules(
            **json.loads(
                rules_dir.joinpath(f'rules_{section}.json').read_text(encoding='utf-8')
            )
        )
        for section in BASE_SECTIONS_V1_LIST
    }
    return Transformer(rules)


def _transform_archive(source_archive: dict) -> bool:
    """Transform v1 sections in memory and report whether any were found."""
    source_data = source_archive.get('data')
    if not isinstance(source_data, dict):
        raise ValueError('Archive data must be a mapping')
    if not isinstance(source_data.get('m_def'), str):
        raise ValueError('Archive data is missing a valid m_def')

    data_m_def, source_data_schema = get_schema_from_source_data(source_data)
    if data_m_def is None or source_data_schema is None:
        raise ValueError('Cannot load the v1 archive data schema')

    sections = validate_and_get_subdict_refs(source_data, source_data_schema)[::-1]
    sections.append(((), data_m_def))
    transformer = None
    transformed = False

    for section_path, section_definition in sections:
        parent = source_archive
        key: str | int = 'data'
        for step in section_path:
            parent = parent[key]
            key = step
        section = parent[key]
        if not isinstance(section, dict):
            continue
        definition = section.get('m_def', section_definition)
        if not isinstance(definition, str) or not _inherits_from_v1_basesection(
            {'data': {'m_def': definition}}
        ):
            continue
        if transformer is None:
            transformer = _create_transformer()
        parent[key] = transform_section(section, definition, transformer)
        transformed = True

    return transformed


def _replace_with_backup(
    input_path: Path, backup_path: Path, original_bytes: bytes, migrated_bytes: bytes
) -> bool:
    """Create the exact original backup and atomically replace the mainfile."""
    input_mode = stat.S_IMODE(input_path.stat().st_mode)

    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f'.{input_path.name}.', suffix='.tmp', dir=input_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, 'wb') as temporary_file:
            temporary_file.write(migrated_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.chmod(input_mode)
        try:
            with backup_path.open('xb') as backup_file:
                try:
                    backup_file.write(original_bytes)
                    backup_file.flush()
                    os.fsync(backup_file.fileno())
                except BaseException:
                    backup_path.unlink()
                    raise
        except FileExistsError:
            return False

        try:
            shutil.copystat(input_path, backup_path)
            os.replace(temporary_path, input_path)
        except BaseException:
            backup_path.unlink()
            raise
    finally:
        temporary_path.unlink(missing_ok=True)

    return True


def migrate_input_archive(path: str | Path) -> bool:
    """Migrate one JSON/YAML input archive in place, keeping an exact v1 backup.

    Return ``True`` when migrated and ``False`` for an ineligible archive or one
    with an existing v1 backup. Malformed archives and failed transformations raise
    an error before altering the source. Run with v1 definitions available; the
    updated archive should be parsed only after the v2 upgrade.
    """
    input_path = Path(path)
    if input_path.name.endswith('archive.json'):
        format_suffix = '.json'
    elif input_path.name.endswith('archive.yaml'):
        format_suffix = '.yaml'
    else:
        return False

    original_bytes = input_path.read_bytes()
    backup_path = input_path.with_name(
        f'{input_path.name.removesuffix(format_suffix)}.v1{format_suffix}'
    )
    if backup_path.exists():
        return False

    try:
        if format_suffix == '.json':
            source_archive = json.loads(original_bytes)
        else:
            source_archive = yaml.safe_load(original_bytes)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f'Cannot parse archive {input_path}') from e
    if not isinstance(source_archive, dict):
        raise ValueError(f'Archive root must be a mapping: {input_path}')
    if not _transform_archive(source_archive):
        return False

    if format_suffix == '.json':
        migrated_bytes = json.dumps(source_archive).encode('utf-8')
    else:
        migrated_bytes = yaml.safe_dump(
            source_archive, sort_keys=False, allow_unicode=True
        ).encode('utf-8')
    return _replace_with_backup(input_path, backup_path, original_bytes, migrated_bytes)
