"""rename new entries so that they are picked up by the archive.json parser"""

import pathlib

TEMP_FOLDER = 'tests/data/ExampleELN_BSv1_temp'


def rename_v2_archives():
    """Rename all ``*.archive.v2.json`` files in a folder if corresponding ``v1`` files
    exists.

    Returns:
        tuple of list of all renamed paths and list of all ``v2`` paths that were
        unchanged due to corresponding ``v1`` entries missing.
    """
    renamed_paths = []
    unchanged_paths = []
    path_prefix = pathlib.Path(__file__).parents[3]
    for path in (path_prefix / TEMP_FOLDER).rglob('*.archive.v2.json'):
        path_original_entry = path.with_name(
            path.name.replace('.archive.v2.json', '.archive.v1.json')
        )
        if path_original_entry.exists():
            renamed_path = path.with_name(
                path.name.replace('.archive.v2.json', '.archive.json')
            )
            path.rename(renamed_path)
            renamed_paths.append(path)
        else:
            unchanged_paths.append(path)

    return renamed_paths, unchanged_paths


if __name__ == '__main__':
    renamed_paths, unchanged_paths = rename_v2_archives()
    if unchanged_paths:
        print(
            'Warning: some files has not been renamed '
            + f'due to missing v1 source files: {unchanged_paths}'
        )
