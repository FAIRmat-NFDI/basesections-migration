"""Transform renamed v1 BaseSection entries into the v2 schema.

The script keeps the original v1 files and writes transformed copies with v2
suffixes for JSON and YAML archive or Metainfo data.
"""

import importlib
import json
import pathlib
from copy import deepcopy

import yaml
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


def create_transformer(path_prefix: pathlib.Path) -> tuple[Transformer, list[str]]:
    """Create a transformer for the available BaseSection migration rules.

    Args:
        path_prefix: Repository or package root containing the migration scripts.

    Returns:
        A tuple with a configured NOMAD ``Transformer`` and the names of the
        v1 BaseSection classes for which rule files were found.
    """

    directory = path_prefix / 'src/basesection_migration/scripts/transformation_rules'
    rules_files = [
        path for path in directory.glob("rules_*.json")
        if path.is_file()
    ]
    base_sections_v1_list = [path.name[6:-5] for path in rules_files]

    rules = {}
    for rule_path, section in zip(rules_files, base_sections_v1_list):
        rules_json = json.loads(rule_path.read_text())
        rules_json['rules']['delete_m_def_id'] = {
            'target': 'm_def_id',
            'action': 'delete'
        }
        rules[f'{section}_transformation'] = Rules(**rules_json)

    transformer = Transformer(rules)
    return transformer, base_sections_v1_list


def get_schema_from_source_data(source_data: dict) -> tuple[str | None, dict | None]:
    """Resolve a source section's ``m_def`` and generate its JSON schema.

    Args:
        source_data: Data mapping from a NOMAD archive.

    Returns:
        A tuple containing the resolved ``m_def`` string and its generated JSON
        schema. If ``m_def`` is missing or cannot be imported, the first or
        second value is ``None`` as appropriate and an explanatory message is
        printed.
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
    """Validate source data and collect paths and definitions for nested sections.

    Schema compositions may expose multiple allowed ``m_def`` values. When the
    section data provides an allowed value, that value is retained; otherwise,
    the last allowed normalized reference is used.

    Args:
        source_data: Data mapping from a NOMAD archive.
        source_data_schema: JSON schema describing ``source_data``.

    Returns:
        A depth-first list of subsection records. Each record contains a tuple
        locating the subsection from the root and the subsection's normalized
        ``m_def`` string, if one can be determined. Path elements are mapping keys
        or list indices.

    Raises:
        jsonschema.exceptions.ValidationError: If ``source_data`` does not conform
            to ``source_data_schema``.
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
        """Yield a schema and every schema reachable through refs or compositions.

        Args:
            schema: JSON schema or subschema to inspect.

        Yields:
            Resolved schema mappings from the input and its compositions.
        """
        if '$ref' in schema:
            resolved_schema = resolver.lookup(schema['$ref']).contents
            yield from schema_views(resolved_schema)
            return
        yield schema
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for subschema in schema.get(keyword, []):
                yield from schema_views(subschema)

    def normalize_ref(reference: str) -> str:
        """Reduce a JSON schema reference to a normalized definition name.

        Args:
            reference: Schema reference string.

        Returns:
            The portion before ``@`` and after the final slash.
        """
        return reference.split('@', 1)[0].rsplit('/', 1)[-1]

    def schema_refs(schema: dict) -> list[str]:
        """Collect normalized references from a schema or composition.

        Args:
            schema: JSON schema or subschema to inspect.

        Returns:
            Normalized references found directly or in allOf, anyOf, or oneOf.
        """
        if '$ref' in schema:
            return [normalize_ref(schema['$ref'])]
        references = []
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for subschema in schema.get(keyword, []):
                references.extend(schema_refs(subschema))
        return references

    def schema_ref(schema: dict, data: dict | None = None) -> str | None:
        """Choose the data-matching schema reference or the final available one.

        Args:
            schema: JSON schema or subschema describing a subsection.
            data: Subsection data used to match an allowed ``m_def``.

        Returns:
            The matching or fallback normalized reference, or ``None`` if none exists.
        """
        references = schema_refs(schema)
        if not references:
            return None
        if data is not None and data.get('m_def'):
            for reference in references:
                if reference == data['m_def']:
                    return reference
        return references[-1]

    def object_properties(schema: dict) -> dict:
        """Collect object properties from all visible schema views.

        Args:
            schema: JSON schema or subschema to inspect.

        Returns:
            A mapping of property names to their schemas.
        """
        properties = {}
        for schema_view in schema_views(schema):
            properties.update(schema_view.get('properties', {}))
        return properties

    def array_items(schema: dict) -> dict | None:
        """Find the schema describing items in an array.

        Args:
            schema: JSON schema or subschema to inspect.

        Returns:
            The first item schema found in the visible schema views, or ``None``.
        """
        for schema_view in schema_views(schema):
            items = schema_view.get('items')
            if isinstance(items, dict):
                return items
        return None

    def walk(data: dict | list, schema: dict, path: tuple[str | int, ...]) -> None:
        """Recursively append nested subsection locations and definitions.

        Args:
            data: Mapping or list currently being traversed.
            schema: JSON schema for ``data``.
            path: Keys and indices locating ``data`` from the source root.
        """
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
    """Resolve the method resolution order for a section definition.

    Args:
        section_definition: Fully qualified Python class name from ``m_def``.

    Returns:
        The class's method resolution order, or ``None`` if the definition is not
        importable or otherwise cannot be resolved.
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
    source_section: dict,
    section_definition: str,
    transformer: Transformer,
    base_sections_v1_list: list[str]
) -> dict:
    """Apply applicable v1 BaseSection rules to one archive section.

    Rules are applied in reverse method resolution order, from the most general
    inherited BaseSection to the most specific class.

    Args:
        source_section: Mapping for the section to transform.
        section_definition: Fully qualified Python class name from ``m_def``.
        transformer: Configured NOMAD transformer containing migration rules.
        base_sections_v1_list: Names of v1 BaseSection classes with rule mappings.

    Returns:
        A transformed copy of ``source_section``, or ``source_section`` itself when
        its MRO cannot be resolved. The input mapping is not modified.
    """
    print(f'### transforming section {section_definition}')
    section_mro = find_mro(section_definition=section_definition)
    if not isinstance(section_mro, list):
        print(f'unexpected mro: {section_mro}')
        return source_section

    result_section = deepcopy(source_section)

    for inherited_class in reversed(section_mro):
        if inherited_class.__name__ in base_sections_v1_list:
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
    transformer, base_sections_v1_list = create_transformer(path_prefix)

    for v1_suffix, v2_suffix in zip(SUFFIX_V1, SUFFIX_V2):
        for path in (path_prefix / TEMP_FOLDER).rglob(f'*{v1_suffix}'):
            print(f'Transforming {path}')
            text = path.read_text()
            source_data_full = (
                yaml.load(text, Loader=yaml.SafeLoader)
                if path.name.endswith('.yaml') or path.name.endswith('.yml')
                else json.loads(text)
            )
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
                        'Failed to reach correct subsection for path = '
                        + f'{section_path}, {e}'
                    )
                    continue

                if section_definition is not None and isinstance(old_section, dict):
                    new_section = transform_section(
                        old_section,
                        section_definition,
                        transformer,
                        base_sections_v1_list
                    )
                    if isinstance(parent_section, dict):
                        parent_section.update({previous_key: new_section})
                    elif isinstance(parent_section, list) and isinstance(
                        previous_key, int
                    ):
                        parent_section[previous_key] = new_section
                    else:
                        raise TypeError

            output_path = path.with_name(path.name.replace(v1_suffix, v2_suffix))
            with open(output_path, 'w') as f:
                if path.name.endswith('.yaml') or path.name.endswith('.yml'):
                    yaml.safe_dump(source_data_full, f, sort_keys=False)
                else:
                    json.dump(source_data_full, f)
