"""transform renamed entries referencing basesections into the new schema,
keep the old version as well"""

import ast
import json
import pathlib
import re
from copy import deepcopy

from basesection_migration.scripts.update_basesections_step_2 import find_mro

TEMP_FOLDER = 'tests/data/ExampleELN_referencingBSv1_temp'
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

basesection_lookup = {
    'rRIGMEFTVvWPEbaUXT90ga3VRJR2': 'parser_level_test.schema_packages.schema_package.AnyPureSubstance',  # noqa: E501
    'HyZy_SZ5ldcBbMs91G55ajqiXrRs': 'nomad_pld_unileipzig.schema_packages.pld.UniLeipzigPLDSample',  # noqa: E501
}


def create_regex_pattern(jmespath_notation: str) -> str:
    """
    Creates a regex from the jmespath notation of the transformer rule to match to.

    Returns:
        A regex string matching to the end of the reference.
    """
    regex_notation = jmespath_notation.replace('.', r'/')
    regex_notation = re.sub(r'\[\w+\]', r'\\[(\\d+)\\]', regex_notation)
    return regex_notation + '$'


def create_regex_replace(jmespath_notation: str) -> str:
    """
    Creates a regex from the jmespath notation of the transformer
    rule to replace the v1 reference with.

    Returns:
        A regex string in basesectoin v2 notation.
    """
    regex_notation = jmespath_notation.replace('.', r'/')
    i = 1
    while re.search(r'\[\w+\]', regex_notation):
        regex_notation = re.sub(r'\[\w+\]', rf'[\\{i}]', regex_notation, count=1)
        i += 1
    return regex_notation


def create_reference_replacements(path_prefix: pathlib.Path) -> dict:
    """Create a dict configured with all reference migration rules
    infered from the basesection migration rules.

    Returns:
        A dict containing a dict of regexes with the replacements in the references
        per section in ``BASE_SECTIONS_V1_LIST``. ``
    """
    reference_replacement = {}
    for section in BASE_SECTIONS_V1_LIST:
        single_ref = {}
        rule_path = (
            path_prefix
            / 'src/basesection_migration/scripts/'
            / f'transformation_rules/rules_{section}.json'
        )
        rules_json = json.loads(rule_path.read_text())['rules']
        for key in rules_json.keys():
            if 'source' in rules_json[key] and 'target' in rules_json[key]:
                if rules_json[key]['source'] == rules_json[key]['target']:
                    continue
                single_ref[key] = {
                    'pattern': create_regex_pattern(rules_json[key]['source']),
                    'replace': create_regex_replace(rules_json[key]['target']),
                }

        rule_path_references = (
            path_prefix
            / 'src/basesection_migration/scripts/'
            / f'reference_rules/reference_rules_{section}.json'
        )
        if rule_path_references.exists():
            rules_json = json.loads(rule_path_references.read_text())['rules']
            for key in rules_json.keys():
                if 'source' in rules_json[key] and 'target' in rules_json[key]:
                    if rules_json[key]['source'] == rules_json[key]['target']:
                        continue
                    single_ref[key] = {
                        'pattern': create_regex_pattern(rules_json[key]['source']),
                        'replace': create_regex_replace(rules_json[key]['target']),
                    }

        reference_replacement[section] = single_ref
    return reference_replacement


def find_all_references(data):
    """Create a list with all references in the data.

    Args:
        data: Source data to be checked for references.

    Returns:
        A list of all references in the source.
    """
    # TODO: Take care of internal references
    reference_regex = re.compile(
        r'..\/uploads\/[a-zA-Z0-9_-]+\/archive\/[a-zA-Z0-9_-]+#data(?:[\/\[\]a-zA-Z0-9_-]+)'
    )
    return reference_regex.findall(str(data))


def identify_referenced_basesections(reference_list, basesection_lookup):
    """Analyses all references for their respective basesection type, if it is any.

    Args:
        reference_list: All references to be checked.
        basesection_lookup: A dict linking references to basesections and inheriting
        classes.

    Returns:
        A list of all references, that inherit from basesections. A list with the
        class of the respective entry.
    """
    basesection_ref = []
    basesection_type = []
    for ref in reference_list:
        entryid = re.search(r'archive/([\w]+)', ref).group(1)
        if entryid in basesection_lookup.keys():
            basesection_ref.append(ref)
            basesection_type.append(basesection_lookup[entryid])
    return basesection_ref, basesection_type


def transform_references(
    source_section: dict,
    transformer: dict,
    basesection_references: list,
    basesection_types: list,
) -> dict:
    """Transforms all references pointing to basesections from the source section.

    Args:
        source_section: The section data to transform references.
        transformer: The reference transformation rules.
        basesection_references: List of all references pointing to basesections.
        basesection_types: List of the classes of these basesections.

    Returns:
       A dict, which is the source dict with transformed references.
    """
    for i in range(len(basesection_references)):
        section_mro = find_mro(section_definition=basesection_types[i])
        if not isinstance(section_mro, list):
            print(f'unexpected mro: {section_mro}')
            return source_section

        result_section = deepcopy(source_section)

        for inherited_class in reversed(section_mro):
            if inherited_class.__name__ in BASE_SECTIONS_V1_LIST:
                print(
                    f'###### applying {inherited_class.__name__}'
                    + ' reference transformation'
                )
                for key in transformer[inherited_class.__name__]:
                    if re.search(
                        transformer[inherited_class.__name__][key]['pattern'],
                        basesection_references[i],
                    ):
                        new_ref = re.sub(
                            transformer[inherited_class.__name__][key]['pattern'],
                            transformer[inherited_class.__name__][key]['replace'],
                            basesection_references[i],
                        )
                        new_ref = new_ref.strip('/')
                        break

        result_section = ast.literal_eval(
            str(result_section).replace(basesection_references[i], new_ref)
        )

    return result_section


if __name__ == '__main__':
    path_prefix = pathlib.Path(__file__).parents[3]

    transformer = create_reference_replacements(path_prefix)

    for path in (path_prefix / TEMP_FOLDER).rglob('*'):
        print(f'Transforming reference {path}')
        source_data_full = json.loads(path.read_text())
        source_data: dict = source_data_full.get('data', {})

        all_references = find_all_references(source_data)

        all_basesection_references, basesection_types = (
            identify_referenced_basesections(all_references, basesection_lookup)
        )

        if len(all_basesection_references) == 0:
            continue

        new_source_data = transform_references(
            source_data, transformer, all_basesection_references, basesection_types
        )

        source_data_full['data'] = new_source_data

        output_path = path.with_name(
            path.name.replace('.archive.v1.json', '.archive.v2.json')
        )
        with open(output_path, 'w') as f:
            json.dump(source_data_full, f)
