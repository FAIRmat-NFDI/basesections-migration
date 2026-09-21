import argparse
import json
import pathlib
from pathlib import Path

TEMP_FOLDER = 'tests/data/ExampleELN_BSv1_temp'


def main() -> None:
    parser = argparse.ArgumentParser(description='Print a JSON file with indentation.')
    parser.add_argument('file_name', type=Path)
    args = parser.parse_args()
    print(TEMP_FOLDER + '/' + str(args.file_name))
    data = json.loads(pathlib.Path(TEMP_FOLDER + '/' + str(args.file_name)).read_text())
    print(json.dumps(data, indent=4, ensure_ascii=False))


if __name__ == '__main__':
    main()
