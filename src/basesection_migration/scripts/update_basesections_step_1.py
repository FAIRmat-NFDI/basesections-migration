"""Find v1 basesection archives and remove them from the regular archive scan."""

import importlib
import json
import pathlib
import shutil

import yaml
from nomad.datamodel.metainfo.basesections.v1 import BaseSection

PROJECT_FOLDER_INPUT = 'tests/data/ExampleELN_BSv1'
TEMP_FOLDER = 'tests/data/ExampleELN_BSv1_temp'

SUFFIX_DEFAULT = [
    '.archive.json',
    '.archive.yaml',
    '.archive.yml',
    '.metainfo.json',
    '.metainfo.yaml',
    '.metainfo.yml',
]
SUFFIX_V1 = [
    '.archive.v1.json',
    '.archive.v1.yaml',
    '.archive.v1.yml',
    '.metainfo.v1.json',
    '.metainfo.v1.yaml',
    '.metainfo.v1.yml',
]


def _inherits_from_v1_basesection(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        m_def = value['data']['m_def']
    except (KeyError, TypeError):
        print('m_def of the root section is missing!')
        return False

    try:
        m_def_id = value['data']['m_def_id']
    except (KeyError, TypeError):
        print('m_def_id of the root section is missing!')
        m_def_id = None

    if not isinstance(m_def, str):
        return False
    module_name, separator, class_name = m_def.rpartition('.')
    if not separator:
        return False
    try:
        m_def_class = getattr(importlib.import_module(module_name), class_name)
        m_def_id_from_class = m_def_class.m_def.hash().hexdigest()
        return (
            isinstance(m_def_class, type)
            and issubclass(m_def_class, BaseSection)
            and (m_def_id == m_def_id_from_class or m_def_id is None)
        )
    except (AttributeError, ImportError, TypeError, ValueError):
        return False


def inherits_from_v1_basesection(path: str | pathlib.Path) -> bool:
    """Return whether an archive contains a v1 basesection in an ``m_def`` field."""
    try:
        archive_path = pathlib.Path(path)
        text = archive_path.read_text()
        data = (
            yaml.safe_load(text)
            if archive_path.name.endswith('.yaml') or archive_path.name.endswith('.yml')
            else json.loads(text)
        )
    except (OSError, json.JSONDecodeError, yaml.YAMLError):
        return False
    return _inherits_from_v1_basesection(data)


def rename_v1_archives(project_folder: str | pathlib.Path) -> list[pathlib.Path]:
    """Rename matching archives from ``.archive.json`` and ``.archive.yaml`` to
    ``.archive.v1.json`` and ``.archive.yaml`` correspondingly."""
    renamed_paths = []
    for default_suffix, v1_suffix in zip(SUFFIX_DEFAULT, SUFFIX_V1):
        for path in pathlib.Path(project_folder).rglob(f'*{default_suffix}'):
            print(f'Checking {path}')
            if not inherits_from_v1_basesection(path):
                print(f'Skipping {path}')
                continue
            print(f'Renaming {path}')
            renamed_path = path.with_name(
                f'{path.name.removesuffix(default_suffix)}{v1_suffix}'
            )
            path.rename(renamed_path)
            renamed_paths.append(renamed_path)
    return renamed_paths


if __name__ == '__main__':
    path_prefix = pathlib.Path(__file__).parents[3]
    shutil.copytree(
        path_prefix / PROJECT_FOLDER_INPUT,
        path_prefix / TEMP_FOLDER,
        dirs_exist_ok=True,
    )
    rename_v1_archives(path_prefix / TEMP_FOLDER)
