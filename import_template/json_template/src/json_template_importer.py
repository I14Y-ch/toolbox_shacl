import json

from rdflib import Graph, URIRef, Literal, Namespace
from rdflib.namespace import RDF, RDFS, XSD, DCTERMS, OWL

from security_limits import (
    ConversionLimitError,
    MAX_IDENTIFIER_CHARS,
    MAX_JSON_CLASSES,
    MAX_JSON_MEMBERS,
    MAX_LANGUAGES_PER_FIELD,
    MAX_TEXT_CHARS,
)


def _validate_identifier(value, field_name):
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_CHARS:
        raise ConversionLimitError(f'Invalid {field_name}')


def _validate_text(value, field_name):
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
        raise ConversionLimitError(f'Invalid {field_name}')


def _validate_multilingual(value, field_name):
    if value is None:
        return
    if not isinstance(value, dict) or len(value) > MAX_LANGUAGES_PER_FIELD:
        raise ConversionLimitError(f'Invalid {field_name}')
    for language, text in value.items():
        if not isinstance(language, str) or len(language) > 35:
            raise ConversionLimitError(f'Invalid {field_name} language')
        _validate_text(text, field_name)


def _validate_member(member, field_name):
    if not isinstance(member, dict):
        raise ConversionLimitError(f'Invalid {field_name}')
    _validate_identifier(member.get('identifier'), f'{field_name} identifier')
    _validate_multilingual(member.get('names', {}), f'{field_name} names')
    _validate_multilingual(member.get('descriptions', {}), f'{field_name} descriptions')

    constraints = member.get('constraints', {})
    if not isinstance(constraints, dict) or len(constraints) > 20:
        raise ConversionLimitError(f'Invalid {field_name} constraints')
    for name, value in constraints.items():
        _validate_text(str(name), f'{field_name} constraint')
        _validate_text(str(value), f'{field_name} constraint')

    for key in ('class', 'conformsTo', 'datatype'):
        if key in member:
            _validate_text(member[key], f'{field_name} {key}')


def _validate_json_data(data):
    if not isinstance(data, dict):
        raise ConversionLimitError('JSON root must be an object')

    classes = data.get('classes', [])
    if not isinstance(classes, list) or len(classes) > MAX_JSON_CLASSES:
        raise ConversionLimitError('JSON has too many classes')

    member_count = 0
    identifiers = set()
    for cls in classes:
        if not isinstance(cls, dict):
            raise ConversionLimitError('Invalid JSON class')
        identifier = cls.get('identifier')
        _validate_identifier(identifier, 'class identifier')
        if identifier in identifiers:
            raise ConversionLimitError('Duplicate class identifier')
        identifiers.add(identifier)

        _validate_multilingual(cls.get('names', {}), 'class names')
        _validate_multilingual(cls.get('descriptions', {}), 'class descriptions')
        for key in ('modified', 'created'):
            if key in cls:
                _validate_text(cls[key], f'class {key}')

        properties = cls.get('properties', [])
        relations = cls.get('relations', [])
        if not isinstance(properties, list) or not isinstance(relations, list):
            raise ConversionLimitError('Invalid class members')
        member_count += len(properties) + len(relations)
        if member_count > MAX_JSON_MEMBERS:
            raise ConversionLimitError('JSON has too many properties or relations')

        for prop in properties:
            _validate_member(prop, 'property')
        for relation in relations:
            _validate_member(relation, 'relation')

    return classes


def _add_names(graph, subject, names, sh):
    for language, name in names.items():
        graph.add((subject, sh.name, Literal(name, lang=language)))
        graph.add((subject, RDFS.label, Literal(name, lang=language)))


def _add_descriptions(graph, subject, descriptions, sh):
    for language, description in descriptions.items():
        literal = Literal(description, lang=language)
        graph.add((subject, DCTERMS.description, literal))
        graph.add((subject, sh.description, literal))
        graph.add((subject, RDFS.comment, literal))


def _add_constraints(graph, subject, constraints, sh):
    for constraint, value in constraints.items():
        if constraint == 'pattern':
            graph.add((subject, sh[constraint], Literal(value)))
        elif constraint in {'minCount', 'maxCount', 'minLength', 'maxLength'}:
            graph.add((subject, sh[constraint], Literal(value, datatype=XSD.integer)))
        elif constraint == 'uniqueLang':
            graph.add((subject, sh[constraint], Literal(value)))


def json_to_shacl(json_input, output_file, dataset_identifier='dataset_identifier'):
    sh = Namespace('http://www.w3.org/ns/shacl#')
    base_uri = 'https://register.ld.admin.ch/i14y/dataset/' + dataset_identifier + '/structure/'

    try:
        data = json.loads(json_input)
    except json.JSONDecodeError as error:
        raise ConversionLimitError('Invalid JSON input') from error

    classes = _validate_json_data(data)
    graph = Graph()
    graph.bind('sh', sh)
    graph.bind('dcterms', DCTERMS)
    graph.bind('xsd', XSD)
    graph.bind('i14y', base_uri)

    node_shapes = {}
    for cls in classes:
        identifier = cls['identifier']
        class_uri = URIRef(f'{base_uri}{identifier}')
        node_shapes[identifier] = class_uri
        graph.add((class_uri, RDF.type, sh.NodeShape))
        graph.add((class_uri, RDF.type, RDFS.Class))
        _add_names(graph, class_uri, cls.get('names', {}), sh)
        _add_descriptions(graph, class_uri, cls.get('descriptions', {}), sh)

        if 'modified' in cls:
            graph.add((class_uri, DCTERMS.modified, Literal(cls['modified'], datatype=XSD.dateTime)))
        if 'created' in cls:
            graph.add((class_uri, DCTERMS.created, Literal(cls['created'], datatype=XSD.dateTime)))
        graph.add((class_uri, DCTERMS.identifier, Literal(identifier)))
        if cls.get('closed', False):
            graph.add((class_uri, sh.closed, Literal(True)))

    datatype_map = {
        'string': XSD.string,
        'boolean': XSD.boolean,
        'integer': XSD.integer,
        'decimal': XSD.decimal,
        'float': XSD.float,
        'double': XSD.double,
        'date': XSD.date,
        'time': XSD.time,
        'dateTime': XSD.dateTime,
        'anyURI': XSD.anyURI,
        'language': XSD.language,
    }

    for cls in classes:
        identifier = cls['identifier']
        class_uri = node_shapes[identifier]

        for prop in cls.get('properties', []):
            prop_uri = URIRef(f"{base_uri}{identifier}/{prop['identifier']}")
            graph.add((class_uri, sh.property, prop_uri))
            graph.add((prop_uri, RDF.type, sh.PropertyShape))
            graph.add((prop_uri, RDF.type, OWL.DatatypeProperty))
            graph.add((prop_uri, sh.path, prop_uri))
            _add_names(graph, prop_uri, prop.get('names', {}), sh)
            _add_descriptions(graph, prop_uri, prop.get('descriptions', {}), sh)

            datatype = datatype_map.get(prop.get('datatype'))
            if datatype is not None:
                graph.add((prop_uri, sh.datatype, datatype))
            _add_constraints(graph, prop_uri, prop.get('constraints', {}), sh)
            if 'order' in prop:
                graph.add((prop_uri, sh.order, Literal(prop['order'], datatype=XSD.integer)))
            if 'conformsTo' in prop:
                graph.add((prop_uri, DCTERMS.conformsTo, URIRef(prop['conformsTo'])))

        for relation in cls.get('relations', []):
            relation_uri = URIRef(f"{base_uri}{identifier}/{relation['identifier']}")
            graph.add((class_uri, sh.property, relation_uri))
            graph.add((relation_uri, RDF.type, sh.PropertyShape))
            graph.add((relation_uri, RDF.type, OWL.ObjectProperty))
            graph.add((relation_uri, sh.path, relation_uri))
            _add_names(graph, relation_uri, relation.get('names', {}), sh)
            _add_descriptions(graph, relation_uri, relation.get('descriptions', {}), sh)

            if 'class' in relation:
                graph.add((relation_uri, sh.node, URIRef(f"{base_uri}{relation['class']}")))
            _add_constraints(graph, relation_uri, relation.get('constraints', {}), sh)
            if 'order' in relation:
                graph.add((relation_uri, sh.order, Literal(relation['order'], datatype=XSD.integer)))
            if 'conformsTo' in relation:
                graph.add((relation_uri, DCTERMS.conformsTo, URIRef(relation['conformsTo'])))

    graph.serialize(destination=output_file, format='turtle', encoding='utf-8')
    return output_file


if __name__ == '__main__':
    with open('structure_with_two_classes.json', 'r', encoding='utf-8') as input_file:
        json_to_shacl(input_file.read(), 'output.ttl')