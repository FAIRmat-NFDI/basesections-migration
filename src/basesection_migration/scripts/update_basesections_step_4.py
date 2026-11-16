"""Find archives referencing basesections v1 and
remove them from the regular archive scan."""

import pathlib
import shutil

PROJECT_FOLDER_INPUT = 'tests/data/ExampleELN_referencingBSv1'
TEMP_FOLDER = 'tests/data/ExampleELN_referencingBSv1_temp'


def rename_referencing_v1_archives(
    project_folder: str | pathlib.Path,
) -> list[pathlib.Path]:
    """Rename matching archives from ``.archive.json`` to ``.archive.v1.json``."""
    renamed_paths = []
    for path in pathlib.Path(project_folder).rglob('*.archive.json'):
        print(f'Checking {path}')
        print(f'Renaming {path}')
        renamed_path = path.with_name(
            f'{path.name.removesuffix(".archive.json")}.archive.v1.json'
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
    rename_referencing_v1_archives(path_prefix / TEMP_FOLDER)
