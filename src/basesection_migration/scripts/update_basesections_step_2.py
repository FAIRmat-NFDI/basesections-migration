"""transform renamed entries into the new schema, keep the old version as well"""

import importlib
import json
import pathlib
from copy import deepcopy

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


def create_transformer(path_prefix: pathlib.Path) -> Transformer:
    """Create a transformer configured with all base-section migration rules.

    Returns:
        A NOMAD ``Transformer`` containing one mapping per section in
        ``BASE_SECTIONS_V1_LIST``. Expected relative file path for each section is
        ``transformation_rules/rules_<Section Name>.json``
    """
    rules = {}
    for section in BASE_SECTIONS_V1_LIST:
        rule_path = (
            path_prefix / 'src/basesection_migration/scripts/'
            / f'transformation_rules/rules_{section}.json'
        )
        rules_json = json.loads(rule_path.read_text())
        rules[f'{section}_transformation'] = Rules(**rules_json)

    transformer = Transformer(rules)
    return transformer


def get_schema_from_source_data(source_data: dict) -> tuple[str | None, dict | None]:
    """Load the class and JSON schema identified by ``m_def`` key of a source data
    dictionary.

    Args:
        source_data: ``data`` section of a NOMAD ``.archive.json`` file.

    Returns:
        A tuple containing the ``m_def`` value and its loaded JSON schema.
        If ``m_def`` is missing or cannot be imported, the schema is empty.
    """
    data_m_def: str | None = source_data.get('m_def', None)

    if data_m_def is None:
        print(f'missing m_def for {path}')
        return None, {}

    module_name, separator, class_name = data_m_def.rpartition('.')
    if separator:
        try:
            data_m_def_class = getattr(importlib.import_module(module_name), class_name)
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
    else:
        source_data_schema = None
        print('Cannot load the data schema')

    return data_m_def, source_data_schema


def validate_and_get_subdict_refs(  # noqa: PLR0915
    source_data: dict, source_data_schema: dict
) -> list[tuple[tuple[str | int, ...], str | None]]:
    """Validate ``source_data`` by ``source_data_schema``, then walk both
    and generate paths to nested subsections and their ``m_def``.

    If multiple different ``m_def`` are allowed per a nested subsection by schema,
    check if ``m_def`` is provided by data and is allowed by schema. Use the data
    ``m_def`` if the check is passed, otherwise fall back to the last ``m_def``
    allowed by schema (should be the most general one).

    Args:
        source_data: ``data`` section of a NOMAD ``.archive.json`` file.
        source_data_schema: JSON schema corresponding to the ``source_data``.

    Returns:
        a list of records, every record corresponds to a subsection of the
        ``source_data`` walked depth-first. Every record is a tuple, where the first
        element is a path to subsection and the second is an ``m_def`` string
        corresponding to the subsection. The path is a tuple with elements that are
        either strings (key for the dict) or integers (indices for the lists) in order
        from root to subsection.
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
        """Yield a schema and the schemas reachable through refs/compositions."""
        if '$ref' in schema:
            resolved_schema = resolver.lookup(schema['$ref']).contents
            yield from schema_views(resolved_schema)
            return
        yield schema
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for subschema in schema.get(keyword, []):
                yield from schema_views(subschema)

    def normalize_ref(reference: str) -> str:
        """Reduce a schema reference to its definition name."""
        return reference.split('@', 1)[0].rsplit('/', 1)[-1]

    def schema_refs(schema: dict) -> list[str]:
        """Collect normalized references from a schema composition."""
        if '$ref' in schema:
            return [normalize_ref(schema['$ref'])]
        references = []
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for subschema in schema.get(keyword, []):
                references.extend(schema_refs(subschema))
        return references

    def schema_ref(schema: dict, data: dict | None = None) -> str | None:
        """Choose the data-matching reference or the final available option."""
        references = schema_refs(schema)
        if not references:
            return None
        if data is not None and data.get('m_def'):
            for reference in references:
                if reference == data['m_def']:
                    return reference
        return references[-1]

    def object_properties(schema: dict) -> dict:
        """Collect object properties from all visible schema views."""
        properties = {}
        for schema_view in schema_views(schema):
            properties.update(schema_view.get('properties', {}))
        return properties

    def array_items(schema: dict) -> dict | None:
        """Return the schema describing items in an array, if present."""
        for schema_view in schema_views(schema):
            items = schema_view.get('items')
            if isinstance(items, dict):
                return items
        return None

    def walk(data: dict | list, schema: dict, path: tuple[str | int, ...]) -> None:
        """Recursively collect paths to nested dictionaries in source data."""
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
    """Find the class MRO for a section definition (``m_def``).

    Args:
        section_definition: Fully qualified Python class name from ``m_def``.

    Returns:
        The class's method resolution order, or ``None`` if the class cannot
        be imported or resolved.
    """
    module_name, separator, class_name = section_definition.rpartition('.')
    if separator:
        try:
            m_def_class = getattr(importlib.import_module(module_name), class_name)
            return m_def_class.mro()
        except (AttributeError, ImportError, TypeError, ValueError) as e:
            print(f'Failed to parse section_definition {section_definition}: {e}')
            return None


def transform_section(
    source_section: dict, section_definition: str, transformer: Transformer
) -> dict:
    """Apply all relevant base-section transformations to one archive section in
    reverse-MRO order (first general, then specialized).

    Args:
        source_section: The section data to transform.
        section_definition: Fully qualified Python class name from ``m_def``.
        transformer: Configured NOMAD transformer containing migration rules.

    Returns:
        The transformed section, or the original section when no applicable
        base-section transformation exists. The original section remains unchanged.
    """
    print(f'### transforming section {section_definition}')
    section_mro = find_mro(section_definition=section_definition)
    if not isinstance(section_mro, list):
        print(f'unexpected mro: {section_mro}')
        return source_section

    result_section = deepcopy(source_section)

    for inherited_class in reversed(section_mro):
        if inherited_class.__name__ in BASE_SECTIONS_V1_LIST:
            print(
                f'###### applying {inherited_class.__name__}'
                + f' transformation to section {section_definition}'
            )
            result_section = transformer.transform(
                source_data=source_section,
                mapping_name=f'{inherited_class.__name__.rsplit(".", maxsplit=1)[-1]}'
                + '_transformation',
                target_data=result_section,
                inplace=False,
                array_rules=True,
                delete_sources=True,
            )

    return result_section


if __name__ == '__main__':
    path_prefix = pathlib.Path(__file__).parents[3]
    transformer = create_transformer(path_prefix)

    for path in (path_prefix / TEMP_FOLDER).rglob('*ELNSubstance.archive.v1.json'):
        print(f'Transforming {path}')
        source_data_full = json.loads(path.read_text())
        source_data: dict = source_data_full.get('data', {})

        data_m_def, source_data_schema = get_schema_from_source_data(source_data)

        if data_m_def is None or source_data_schema is None:
            continue

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
                print(
                    f'Failed to reach correct subsection for path = {section_path}, {e}'
                )
                continue

            if section_definition is not None and isinstance(old_section, dict):
                new_section = transform_section(
                    old_section, section_definition, transformer
                )
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
