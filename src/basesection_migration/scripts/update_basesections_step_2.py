### transform renamed entries into the new schema, keep the old version as well
import json
import pathlib

from nomad.datamodel.metainfo.annotations import Rules
from nomad.utils.json_transformer import Transformer

TEMP_FOLDER = 'tests/data/ExampleELN_BSv1_temp'
RULE_JSON_PATH = 'transformation_rules.json'

if __name__ == '__main__':
    rules_json = json.loads(pathlib.Path(RULE_JSON_PATH).read_text())
    rules = {'basesections_transformation': Rules(rules_json)}

    transformer = Transformer(rules)

    for path in pathlib.Path(TEMP_FOLDER).rglob('*.archive.v1.json'):
        print(f'Transforming {path}')
        source_data = json.loads(path.read_text())

        try:
            transformed_json = transformer.transform(
                source_data, 'basesections_transformation'
            )
        except Exception as e:
            print(f'Error transforming {path}: {e}')
            continue

        transformed_path = path.with_name(
            f'{path.name.removesuffix(".archive.v1.json")}.archive.v2.json'
        )
        transformed_path.write_text(json.dumps(transformed_json))
