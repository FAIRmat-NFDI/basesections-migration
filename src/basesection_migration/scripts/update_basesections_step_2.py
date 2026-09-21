### transform renamed entries into the new schema, keep the old version as well
import importlib
import json
import pathlib

from nomad.datamodel.metainfo.annotations import Rules
from nomad.metainfo.util import metainfo_to_json_schema
from nomad.utils.json_transformer import Transformer

TEMP_FOLDER = 'tests/data/ExampleELN_BSv1_temp'
BASE_SECTIONS_V1_LIST = [
    'BaseSection',
    'Entity',
    'Collection',
    'System',
    'CompositeSystem',
    'PureSubstance',
    'Activity',
    'Experiment',
    'Process',
    'SynthesisMethod',
    'Analysis',
    'Measurement',
]
# corresponding rules are in f'rules_{BASE_SECTIONS_V1_LIST[i]}.json'


def import_classes_from_basesections_list():
    base_sections_v1_list_classes = []
    for section in BASE_SECTIONS_V1_LIST:
        module_name = 'nomad.datamodel.metainfo.basesections.v1'
        try:
            base_sections_v1_list_classes.append(
                getattr(importlib.import_module(module_name), section)
            )
        except ImportError as e:
            print(f'Failed to import {module_name} {section}: {e}')
    return base_sections_v1_list_classes


def validate_and_get_subdict_refs(
    source_data: dict, source_data_schema: dict
) -> list[tuple[tuple[str | int, ...], str | None]]:
    """Validate data and return paths to nested dictionaries and their refs.

    Paths are tuples of dictionary keys and list indices, relative to
    ``source_data``. The second item in each pair is the final part of the
    schema ``$ref`` (after the last ``/`` and before ``@``), or ``None`` when
    there is no ref. ``jsonschema.ValidationError`` is raised before walking
    if the data does not conform to the schema.
    """

    from jsonschema.validators import validator_for
    from referencing import Registry, Resource

    validator_class = validator_for(source_data_schema)
    # validator_class.check_schema(source_data_schema)
    schema_uri = source_data_schema.get('$id', 'urn:source-data-schema')
    registry = Registry().with_resource(
        schema_uri, Resource.from_contents(source_data_schema)
    )
    validator = validator_class(source_data_schema, registry=registry)
    validator.validate(source_data)
    resolver = registry.resolver(base_uri=schema_uri)
    subdict_refs: list[tuple[tuple[str | int, ...], str | None]] = []

    def schema_views(schema: dict):
        if '$ref' in schema:
            resolved_schema = resolver.lookup(schema['$ref']).contents
            yield from schema_views(resolved_schema)
            return
        yield schema
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for subschema in schema.get(keyword, []):
                yield from schema_views(subschema)

    def normalize_ref(reference: str) -> str:
        return reference.split('@', 1)[0].rsplit('/', 1)[-1]

    def schema_refs(schema: dict) -> list[str]:
        if '$ref' in schema:
            return [normalize_ref(schema['$ref'])]
        references = []
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for subschema in schema.get(keyword, []):
                references.extend(schema_refs(subschema))
        return references

    def schema_ref(schema: dict, data: dict | None = None) -> str | None:
        references = schema_refs(schema)
        if not references:
            return None
        if data is not None and data.get('m_def'):
            for reference in references:
                if reference == data['m_def']:
                    return reference
        return references[-1]

    def object_properties(schema: dict) -> dict:
        properties = {}
        for schema_view in schema_views(schema):
            properties.update(schema_view.get('properties', {}))
        return properties

    def array_items(schema: dict) -> dict | None:
        for schema_view in schema_views(schema):
            items = schema_view.get('items')
            if isinstance(items, dict):
                return items
        return None

    def walk(data: dict | list, schema: dict, path: tuple[str | int, ...]) -> None:
        if isinstance(data, dict):
            properties = object_properties(schema)
            for key, value in data.items():
                child_schema = properties.get(key)
                if child_schema is None or not isinstance(value, (dict, list)):
                    continue
                if isinstance(value, dict):
                    subdict_refs.append(
                        (path + (key,), schema_ref(child_schema, value))
                    )
                walk(value, child_schema, path + (key,))
        elif isinstance(data, list):
            item_schema = array_items(schema)
            if item_schema is not None:
                for index, value in enumerate(data):
                    item_path = path + (index,)
                    if isinstance(value, dict):
                        subdict_refs.append((item_path, schema_ref(item_schema, value)))
                    if isinstance(value, (dict, list)):
                        walk(value, item_schema, item_path)

    walk(source_data, source_data_schema, ())
    return subdict_refs


def find_mro(section_definition: str) -> list[type] | None:
    """Find the method resolution order (MRO) of a given m_def"""
    module_name, separator, class_name = section_definition.rpartition('.')
    if separator:
        try:
            m_def_class = getattr(importlib.import_module(module_name), class_name)
            return m_def_class.mro()
        except (AttributeError, ImportError, TypeError, ValueError) as e:
            print(f'Failed to parse section_definition {section_definition}: {e}')
            return None


def apply_single_transformation(
    input_section: dict, target_section: dict, class_name: str, transformer: Transformer
) -> dict:
    return transformer.transform(
        source_data=input_section,
        mapping_name=f'{class_name.rsplit(".", maxsplit=1)[-1]}_transformation',
        target_data=target_section,
        inplace=False,
        array_rules=True,
        delete_sources=False,
    )


def transform_section(
    source_section: dict, section_definition: str, transformer: Transformer
) -> dict:
    print(f'### transforming section {section_definition}')
    section_mro = find_mro(section_definition=section_definition)
    if not isinstance(section_mro, list):
        print(f'unexpected mro: {section_mro}')
        return source_section

    result_section = {}
    flag_no_transformation = True

    for inherited_class in reversed(section_mro):
        if inherited_class.__name__ in BASE_SECTIONS_V1_LIST:
            print(
                f'###### applying {inherited_class.__name__}'
                + f' transformation to section {section_definition}'
            )
            # print(f'&&&&&& before = {json.dumps(source_section, indent=2)}')
            result_section = apply_single_transformation(
                source_section, result_section, inherited_class.__name__, transformer
            )
            flag_no_transformation = False
            # print(f'&&&&&& after = {json.dumps(result_section, indent=2)}')

    if flag_no_transformation:
        result_section = source_section

    return result_section


if __name__ == '__main__':
    base_sections_v1_list_classes = import_classes_from_basesections_list()

    rules = {}
    for section in BASE_SECTIONS_V1_LIST:
        rule_path = (
            'src/basesection_migration/scripts/'
            + f'transformation_rules/rules_{section}.json'
        )
        rules_json = json.loads(pathlib.Path(rule_path).read_text())
        rules[f'{section}_transformation'] = Rules(**rules_json)

    # print(f'### rules = {rules}')
    transformer = Transformer(rules)

    for path in pathlib.Path(TEMP_FOLDER).rglob('*ELNSubstance.archive.v1.json'):
        print(f'Transforming {path}')
        source_data_full = json.loads(path.read_text())
        source_data: dict = source_data_full.get('data', {})

        data_m_def: str | None = source_data.get('m_def', None)

        if data_m_def is None:
            print(f'missing m_def for {path}')
            continue

        module_name, separator, class_name = data_m_def.rpartition('.')
        if separator:
            try:
                data_m_def_class = getattr(
                    importlib.import_module(module_name), class_name
                )
            except (AttributeError, ImportError, TypeError, ValueError) as e:
                data_m_def_class = None
                print(f'Failed to import m_def {data_m_def}: {e}')

        if data_m_def_class is not None:
            source_data_schema = metainfo_to_json_schema(
                m_def=data_m_def_class.m_def,
                add_unit_value=False,
                add_section_subtypes=True,
                add_property_subtypes=True,
            )

        list_of_subsections = validate_and_get_subdict_refs(
            source_data, source_data_schema
        )[::-1]
        list_of_subsections.append(((), data_m_def))

        for subsection_information in list_of_subsections:
            print(subsection_information)
            section_path = subsection_information[0]
            section_definition = subsection_information[1]
            old_section = source_data
            parent_section = source_data_full
            previous_key = 'data'
            try:
                for section_path_step in section_path:
                    old_section = old_section[section_path_step]
                    parent_section = parent_section[previous_key]
                    previous_key = section_path_step
            except (AttributeError, TypeError, ValueError) as e:
                print(f'Failed to reach correct subsection, {e}')
                continue

            if section_definition is not None and isinstance(old_section, dict):
                new_section = transform_section(
                    old_section, section_definition, transformer
                )
                print(f'&&& {new_section}')
                if isinstance(parent_section, dict):
                    parent_section.update({previous_key: new_section})
                elif isinstance(parent_section, list) and isinstance(previous_key, int):
                    parent_section[previous_key] = new_section
                else:
                    raise TypeError

        output_path = path.with_name(
            path.name.replace('.archive.v1.json', '.archive.v2.json')
        )
        with open(output_path, 'w') as f:
            json.dump(source_data_full, f)

        # print(json.dumps(source_data, indent=2))
        # print('###############')
        # print('###############')
        # print('###############')
        # print(json.dumps(source_data_schema, indent=2))

        # m_contents

        # mro_of_source_data = find_mro(source_data)

        # if mro_of_source_data is None:
        #     print(f'Failed to find MRO for {path}')
        #     continue

        # for inherited_class in reversed(mro_of_source_data):
        #     if inherited_class in base_sections_v1_list_classes:
        #         pass

        # transform_recursively(source_data, transformer)

        # try:
        #     transformed_json = transformer.transform(
        #         source_data, f'{section}_transformation'
        #     )
        # except Exception as e:
        #     print(f'Error transforming {path}: {e}')
        #     continue

        # transformed_path = path.with_name(
        #     f'{path.name.removesuffix(".archive.v1.json")}.archive.v2.json'
        # )
        # transformed_path.write_text(json.dumps(transformed_json))
