"""Find v1 basesection archives and remove them from the regular archive scan."""

import importlib
import json
import pathlib
import shutil

from nomad.datamodel.metainfo.basesections.v1 import BaseSection

PROJECT_FOLDER_INPUT = 'tests/data/ExampleELN_BSv1'
TEMP_FOLDER = 'tests/data/ExampleELN_BSv1_temp'


def _inherits_from_v1_basesection(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        m_def = value['data']['m_def']
    except (KeyError, TypeError):
        return False

    if not isinstance(m_def, str):
        return False
    module_name, separator, class_name = m_def.rpartition('.')
    if not separator:
        return False
    try:
        m_def_class = getattr(importlib.import_module(module_name), class_name)
        return isinstance(m_def_class, type) and issubclass(m_def_class, BaseSection)
    except (AttributeError, ImportError, TypeError, ValueError):
        return False


def inherits_from_v1_basesection(path: str | pathlib.Path) -> bool:
    """Return whether an archive contains a v1 basesection in an ``m_def`` field."""
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return _inherits_from_v1_basesection(data)


def rename_v1_archives(project_folder: str | pathlib.Path) -> list[pathlib.Path]:
    """Rename matching archives from ``.archive.json`` to ``.archive.v1.json``."""
    renamed_paths = []
    for path in pathlib.Path(project_folder).rglob('*.archive.json'):
        print(f'Checking {path}')
        if not inherits_from_v1_basesection(path):
            continue
        renamed_path = path.with_name(
            f'{path.name.removesuffix(".archive.json")}.archive.v1.json'
        )
        path.rename(renamed_path)
        renamed_paths.append(renamed_path)
    return renamed_paths


if __name__ == '__main__':
    shutil.copytree(PROJECT_FOLDER_INPUT, TEMP_FOLDER, dirs_exist_ok=True)
    rename_v1_archives(TEMP_FOLDER)
