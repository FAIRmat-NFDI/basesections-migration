"""rename new entries so that they are picked up by the archive.json parser"""

import pathlib

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
SUFFIX_V2 = [
    '.archive.v2.json',
    '.archive.v2.yaml',
    '.archive.v2.yml',
    '.metainfo.v2.json',
    '.metainfo.v2.yaml',
    '.metainfo.v2.yml',
]


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
    for default_suffix, v1_suffix, v2_suffix in zip(
        SUFFIX_DEFAULT, SUFFIX_V1, SUFFIX_V2
    ):
        for path in (path_prefix / TEMP_FOLDER).rglob(f'*{v2_suffix}'):
            path_original_entry = path.with_name(
                path.name.replace(v2_suffix, v1_suffix)
            )
            if path_original_entry.exists():
                renamed_path = path.with_name(
                    path.name.replace(v2_suffix, default_suffix)
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
