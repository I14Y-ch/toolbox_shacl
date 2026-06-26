# Improved XSD to SHACL converter that captures all information
# This file includes adapted code from the XSD2SHACL project,
# originally developed by Xuemin Duan, David Chaves-Fraga, and Anastasia Dimou.
# Source: https://github.com/dtai-kg/XSD2SHACL
# License: Apache License 2.0

from lxml import etree
from rdflib import Graph, Namespace, Literal, URIRef, BNode
from rdflib.namespace import RDF, RDFS, XSD, OWL
import os

dataset_identifier = "dataset_identifier"
i14y_base_path = "https://register.ld.admin.ch/i14y/dataset/" + dataset_identifier + "/structure/"

# Define namespaces
SH = Namespace("http://www.w3.org/ns/shacl#")
I14Y = Namespace(i14y_base_path)
DCT = Namespace("http://purl.org/dc/terms/")

def parse_xsd(xsd_file):
    """Parse the XSD file and return the root element."""
    with open(xsd_file, 'rb') as f:
        tree = etree.parse(f)
        root = tree.getroot()
        return root

def resolve_imports(xsd_root, base_path):
    """Resolve and parse imported XSD files."""
    imports = xsd_root.findall('.//{http://www.w3.org/2001/XMLSchema}import')
    for imp in imports:
        schema_location = imp.get('schemaLocation')
        if schema_location:
            import_path = os.path.join(base_path, schema_location)
            if os.path.exists(import_path):
                imported_root = parse_xsd(import_path)

def handle_enumeration(enumerations, subject, graph):
    """Handle XSD enumeration with sh:in."""
    if not enumerations:
        return
    
    # Create a list node for the enumeration values
    enum_list = BNode()
    current = enum_list
    
    for i, enum_value in enumerate(enumerations):
        graph.add((current, RDF.first, Literal(enum_value)))
        if i < len(enumerations) - 1:
            next_node = BNode()
            graph.add((current, RDF.rest, next_node))
            current = next_node
        else:
            graph.add((current, RDF.rest, RDF.nil))
    
    graph.add((subject, SH["in"], enum_list))

def process_complex_type_content(complex_type, xsd_root, graph, node_shape, type_name):
    """Process the content of a complex type."""
    # Handle simple content
    simple_content = complex_type.find('{http://www.w3.org/2001/XMLSchema}simpleContent')
    if simple_content is not None:
        extension = simple_content.find('{http://www.w3.org/2001/XMLSchema}extension')
        restriction = simple_content.find('{http://www.w3.org/2001/XMLSchema}restriction')
        
        if extension is not None:
            base = extension.get('base')
            if base and ('xs:' in base or 'xsd:' in base):
                base_type = base.split(':')[-1]
                graph.add((node_shape, SH.datatype, XSD[base_type]))
            
            # Process attributes
            for attr in extension.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
                handle_attribute(attr, xsd_root, graph, node_shape, type_name)
        
        elif restriction is not None:
            base = restriction.get('base')
            if base and ('xs:' in base or 'xsd:' in base):
                base_type = base.split(':')[-1]
                graph.add((node_shape, SH.datatype, XSD[base_type]))
                
                # Process restriction facets
                for facet in restriction:
                    facet_name = facet.tag.split('}')[-1]
                    facet_value = facet.get('value')
                    if facet_value:
                        translate_restriction(facet_name, facet_value, base_type, node_shape, graph)
    
    # Handle complex content or direct content model
    complex_content = complex_type.find('{http://www.w3.org/2001/XMLSchema}complexContent')
    content_root = complex_content if complex_content is not None else complex_type
    
    # Handle inheritance (extension/restriction)
    if complex_content is not None:
        extension = complex_content.find('{http://www.w3.org/2001/XMLSchema}extension')
        restriction = complex_content.find('{http://www.w3.org/2001/XMLSchema}restriction')
        
        if extension is not None:
            base = extension.get('base')
            if base:
                base_type_name = base.split(':')[-1]
                graph.add((node_shape, SH['node'], I14Y[base_type_name]))
            content_root = extension
        elif restriction is not None:
            base = restriction.get('base')
            if base:
                base_type_name = base.split(':')[-1]
                graph.add((node_shape, SH['node'], I14Y[base_type_name]))
            content_root = restriction
    
    # Process content model (sequence, choice, all)
    sequence = content_root.find('{http://www.w3.org/2001/XMLSchema}sequence')
    choice = content_root.find('{http://www.w3.org/2001/XMLSchema}choice')
    all_elem = content_root.find('{http://www.w3.org/2001/XMLSchema}all')
    
    if sequence is not None:
        handle_sequence(sequence, xsd_root, graph, node_shape, type_name)
    elif choice is not None:
        handle_choice(choice, xsd_root, graph, node_shape, type_name)
    elif all_elem is not None:
        handle_all(all_elem, xsd_root, graph, node_shape, type_name)
    
    # Process attributes
    for attr in content_root.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
        handle_attribute(attr, xsd_root, graph, node_shape, type_name)
    
    # Process attribute groups
    for attr_group in content_root.findall('{http://www.w3.org/2001/XMLSchema}attributeGroup'):
        attr_group_ref = attr_group.get('ref')
        if attr_group_ref:
            group_name = attr_group_ref.split(':')[-1]
            attr_group_def = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}attributeGroup[@name="{group_name}"]')
            if attr_group_def:
                for attr in attr_group_def.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
                    handle_attribute(attr, xsd_root, graph, node_shape, type_name)
    
    graph.add((node_shape, SH.closed, Literal(True)))

def process_global_element(element, xsd_root, graph):
    """Process a global element definition."""
    element_name = element.get('name')
    if not element_name:
        return
    
    # Create a NodeShape for the global element
    node_shape = I14Y[element_name]
    graph.add((node_shape, RDF.type, SH.NodeShape))
    graph.add((node_shape, RDF.type, RDFS.Class))
    graph.add((node_shape, SH.name, Literal(element_name, lang='en')))
    graph.add((node_shape, RDFS.label, Literal(element_name, lang='en')))
    
    # Add annotation
    translate_annotation(element, node_shape, graph)
    
    # Process type
    element_type = element.get('type')
    if element_type:
        if 'xs:' in element_type or 'xsd:' in element_type:
            # Built-in type
            base_type = element_type.split(':')[-1]
            graph.add((node_shape, SH.datatype, XSD[base_type]))
        else:
            # Custom type reference
            type_name = element_type.split(':')[-1]
            graph.add((node_shape, SH['node'], I14Y[type_name]))
    else:
        # Check for inline type definition
        simple_type = element.find('{http://www.w3.org/2001/XMLSchema}simpleType')
        complex_type = element.find('{http://www.w3.org/2001/XMLSchema}complexType')
        
        if simple_type is not None:
            type_info = process_simple_type(simple_type, xsd_root, graph)
            if type_info[0] == 'simple':
                graph.add((node_shape, SH.datatype, XSD[type_info[1]]))
                # Add facets
                for facet_name, facet_value in type_info[2].get('facets', {}).items():
                    translate_restriction(facet_name, facet_value, type_info[1], node_shape, graph)
                # Add enumerations
                if type_info[2].get('enumerations'):
                    handle_enumeration(type_info[2]['enumerations'], node_shape, graph)
        
        elif complex_type is not None:
            process_complex_type_content(complex_type, xsd_root, graph, node_shape, element_name)
    
    # Handle default and fixed values
    if element.get('default'):
        graph.add((node_shape, SH.defaultValue, Literal(element.get('default'))))
    if element.get('fixed'):
        graph.add((node_shape, SH.hasValue, Literal(element.get('fixed'))))

def handle_extension(extension, xsd_root, subject, graph):
    """
    Handle XSD extension with sh:and.
    
    Args:
        extension: The XSD extension element
        xsd_root: The root of the XSD document
        subject: The RDF subject to add constraints to
        graph: The RDF graph
    """
    if extension is None:
        return
    
    base = extension.get('base')
    if not base:
        return
    
    # Create an and list
    and_list = BNode()
    current = and_list
    
    # Add base type constraint
    if 'xs:' in base or 'xsd:' in base:
        base_type = base.split(':')[-1]
        base_node = BNode()
        graph.add((base_node, SH.datatype, XSD[base_type]))
        graph.add((current, RDF.first, base_node))
        
        # Add extension constraints
        extension_node = BNode()
        for elem in extension:
            if elem.tag.endswith('attribute'):
                handle_attribute(elem, xsd_root, graph, extension_node)
            elif elem.tag.endswith('sequence') or elem.tag.endswith('choice') or elem.tag.endswith('all'):
                # Handle content model extensions
                pass
        
        if len(list(extension_node.predicates())) > 0:
            next_node = BNode()
            graph.add((current, RDF.rest, next_node))
            current = next_node
            graph.add((current, RDF.first, extension_node))
        
        graph.add((current, RDF.rest, RDF.nil))
        graph.add((subject, SH.and_, and_list))
    else:
        # Handle custom base types
        type_name = base.split(':')[-1]
        base_node = BNode()
        graph.add((base_node, SH['node'], I14Y[type_name]))
        graph.add((current, RDF.first, base_node))
        
        # Add extension constraints
        extension_node = BNode()
        for elem in extension:
            if elem.tag.endswith('attribute'):
                handle_attribute(elem, xsd_root, graph, extension_node)
            elif elem.tag.endswith('sequence') or elem.tag.endswith('choice') or elem.tag.endswith('all'):
                # Handle content model extensions
                pass
        
        if len(list(extension_node.predicates())) > 0:
            next_node = BNode()
            graph.add((current, RDF.rest, next_node))
            current = next_node
            graph.add((current, RDF.first, extension_node))
        
        graph.add((current, RDF.rest, RDF.nil))
        graph.add((subject, SH.and_, and_list))

def process_simple_type(simple_type, xsd_root, graph, type_name=None):
    """Process a simpleType definition and create NodeShape if it's a global type."""
    restriction = simple_type.find('{http://www.w3.org/2001/XMLSchema}restriction')
    union = simple_type.find('{http://www.w3.org/2001/XMLSchema}union')
    list_elem = simple_type.find('{http://www.w3.org/2001/XMLSchema}list')
    
    # If this is a global simpleType, create a NodeShape for it
    if type_name:
        node_shape = I14Y[type_name]
        graph.add((node_shape, RDF.type, SH.NodeShape))
        graph.add((node_shape, RDF.type, RDFS.Class))
        graph.add((node_shape, SH.name, Literal(type_name, lang='en')))
        graph.add((node_shape, RDFS.label, Literal(type_name, lang='en')))
        
        # Add annotation
        translate_annotation(simple_type, node_shape, graph)
    
    if restriction is not None:
        base = restriction.get('base')
        facets = {}
        enumerations = []
        
        for facet in restriction:
            facet_name = facet.tag.split('}')[-1]
            facet_value = facet.get('value')
            if facet_value:
                if facet_name == 'enumeration':
                    enumerations.append(facet_value)
                    # Add annotation for enumeration value
                    annotation = facet.find('.//{http://www.w3.org/2001/XMLSchema}annotation')
                    if annotation is not None:
                        for doc in annotation.findall('.//{http://www.w3.org/2001/XMLSchema}documentation'):
                            lang = doc.get('{http://www.w3.org/XML/1998/namespace}lang', 'en')
                            if doc.text and doc.text.strip():
                                # Could store enumeration descriptions as comments
                                pass
                else:
                    facets[facet_name] = facet_value
        
        if type_name and base and ('xs:' in base or 'xsd:' in base):
            base_type = base.split(':')[-1]
            graph.add((node_shape, SH.datatype, XSD[base_type]))
            
            # Add facets
            for facet_name, facet_value in facets.items():
                translate_restriction(facet_name, facet_value, base_type, node_shape, graph)
            
            # Add enumeration
            if enumerations:
                handle_enumeration(enumerations, node_shape, graph)
        
        return ('simple', base.split(':')[-1] if base and ('xs:' in base or 'xsd:' in base) else 'string', {
            'facets': facets,
            'enumerations': enumerations
        })
    
    elif union is not None:
        if type_name:
            # Handle union type
            member_types = []
            for member in union.findall('{http://www.w3.org/2001/XMLSchema}simpleType'):
                # Process inline member types
                pass
            
            # Could add sh:or constraint here
        return ('union', [], {})
    
    elif list_elem is not None:
        if type_name:
            item_type = list_elem.get('itemType')
            if item_type and ('xs:' in item_type or 'xsd:' in item_type):
                base_type = item_type.split(':')[-1]
                # Create a property constraint for list items
                graph.add((node_shape, SH.datatype, XSD[base_type]))
        return ('list', 'string', {})
    
    return ('simple', 'string', {})

def translate_restriction(facet_name, facet_value, base_type=None, subject=None, graph=None):
    """Translates XSD restrictions to SHACL constraints."""
    # Numeric constraints
    numeric_facets = {
        'minInclusive': SH.minInclusive,
        'maxInclusive': SH.maxInclusive,
        'minExclusive': SH.minExclusive,
        'maxExclusive': SH.maxExclusive,
        'totalDigits': SH.totalDigits,
        'fractionDigits': SH.fractionDigits
    }
    
    # String constraints
    string_facets = {
        'minLength': SH.minLength,
        'maxLength': SH.maxLength,
        'length': (SH.minLength, SH.maxLength),
        'pattern': SH.pattern
    }
    
    # Convert value to appropriate type
    if facet_name in ['minLength', 'maxLength', 'length', 'totalDigits', 'fractionDigits']:
        value = Literal(int(facet_value), datatype=XSD.integer)
    elif facet_name in numeric_facets and base_type:
        value = Literal(facet_value, datatype=XSD[base_type])
    elif facet_name == 'pattern':
        value = Literal(facet_value.replace('\\\\', '\\\\\\\\'))
    else:
        value = Literal(facet_value)
    
    # Determine the predicate(s)
    if facet_name in numeric_facets:
        predicate = numeric_facets[facet_name]
    elif facet_name in string_facets:
        if facet_name == 'length':
            if graph and subject:
                graph.add((subject, SH.minLength, value))
                graph.add((subject, SH.maxLength, value))
                return None
            return (SH.minLength, value), (SH.maxLength, value)
        predicate = string_facets[facet_name]
    else:
        return None
    
    if graph and subject:
        graph.add((subject, predicate, value))
        return None
    return (predicate, value)


def handle_sequence(sequence, xsd_root, graph, parent_shape, parent_type_name):
    """
    Handle XSD sequence with order constraints.
    Adds properties with sh:order to maintain sequence.
    """
    if sequence is None:
        return
    
    order = 0
    for element in sequence.findall('{http://www.w3.org/2001/XMLSchema}element'):
        element_name = element.get('name')
        if element_name:
            # Create unique property shape URI based on parent type and element name
            prop_shape = I14Y[f"{parent_type_name}/{element_name}"]   # THIS IS THE SOLUTION FOR THE BUG, BUT IT COULD BE _ OR /
            graph.add((prop_shape, RDF.type, SH.PropertyShape))
            graph.add((prop_shape, SH.path, I14Y[f"{parent_type_name}/{element_name}"] ))
            graph.add((prop_shape, SH.name, Literal(element_name, lang='en')))
            graph.add((prop_shape, SH.order, Literal(order, datatype=XSD.integer)))
            
            # Process element details
            process_element_details(element, xsd_root, graph, prop_shape)
            
            graph.add((parent_shape, SH.property, prop_shape))
            order += 1

def handle_choice(choice, xsd_root, graph, parent_shape, parent_type_name):
    """
    Handle XSD choice with sh:xone.
    
    Args:
        choice: The XSD choice element
        xsd_root: The root of the XSD document
        graph: The RDF graph
        parent_shape: The parent NodeShape
        parent_type_name: The name of the parent type
    """
    if choice is None:
        return
    
    options = []
    
    # Process each option in the choice
    for option in choice:
        if option.tag.endswith('element'):
            # Simple element choice
            element_name = option.get('name')
            if element_name:
                # Create property shape for this element
                prop_shape = I14Y[f"{parent_type_name}/{element_name}"]
                graph.add((prop_shape, RDF.type, SH.PropertyShape))
                graph.add((prop_shape, SH.path, I14Y[f"{parent_type_name}/{element_name}"]))
                graph.add((prop_shape, SH.name, Literal(element_name, lang='en')))
                
                # Process element details
                process_element_details(option, xsd_root, graph, prop_shape)
                
                # Add to options list as a single property constraint
                option_node = BNode()
                graph.add((option_node, SH.property, prop_shape))
                options.append(option_node)
                
        elif option.tag.endswith('sequence'):
            # Sequence choice - create a node for the sequence
            seq_node = BNode()
            order = 0
            
            for seq_element in option.findall('{http://www.w3.org/2001/XMLSchema}element'):
                element_name = seq_element.get('name')
                if element_name:
                    # Create property shape for this element
                    prop_shape = I14Y[f"{parent_type_name}/{element_name}"]
                    graph.add((prop_shape, RDF.type, SH.PropertyShape))
                    graph.add((prop_shape, SH.path, I14Y[f"{parent_type_name}/{element_name}"]))
                    graph.add((prop_shape, SH.name, Literal(element_name, lang='en')))
                    graph.add((prop_shape, SH.order, Literal(order, datatype=XSD.integer)))
                    
                    # Process element details
                    process_element_details(seq_element, xsd_root, graph, prop_shape)
                    
                    # Add to sequence node
                    graph.add((seq_node, SH.property, prop_shape))
                    order += 1
            
            options.append(seq_node)
            
        elif option.tag.endswith('all'):
            # All choice - create a node for the all compositor
            all_node = BNode()
            
            for all_element in option.findall('{http://www.w3.org/2001/XMLSchema}element'):
                element_name = all_element.get('name')
                if element_name:
                    # Create property shape for this element
                    prop_shape = I14Y[f"{parent_type_name}/{element_name}"]
                    graph.add((prop_shape, RDF.type, SH.PropertyShape))
                    graph.add((prop_shape, SH.path, I14Y[f"{parent_type_name}/{element_name}"]))
                    graph.add((prop_shape, SH.name, Literal(element_name, lang='en')))
                    
                    # Process element details
                    process_element_details(all_element, xsd_root, graph, prop_shape)
                    
                    # Add to all node
                    graph.add((all_node, SH.property, prop_shape))
            
            options.append(all_node)
    
    # Create the xone list if we have options
    if options:
        if len(options) == 1:
            # For single option, just add the properties directly
            for stmt in graph.triples((options[0], None, None)):
                graph.add((parent_shape, stmt[1], stmt[2]))
        else:
            # Create xone list
            xone_list = BNode()
            current = xone_list
            
            for i, option in enumerate(options):
                graph.add((current, RDF.first, option))
                if i < len(options) - 1:
                    next_node = BNode()
                    graph.add((current, RDF.rest, next_node))
                    current = next_node
                else:
                    graph.add((current, RDF.rest, RDF.nil))
            
            graph.add((parent_shape, SH.xone, xone_list))

def handle_all(all_elem, xsd_root, graph, parent_shape, parent_type_name):
    """
    Handle XSD all compositor (all properties required, no order).
    """
    if all_elem is None:
        return
    
    for element in all_elem.findall('{http://www.w3.org/2001/XMLSchema}element'):
        element_name = element.get('name')
        if element_name:
            prop_shape = I14Y[f"{parent_type_name}/{element_name}"] 
            # All elements are required (minCount=1)
            graph.add((prop_shape, SH.minCount, Literal(1, datatype=XSD.integer)))
            graph.add((parent_shape, SH.property, prop_shape))


def handle_extension(extension, xsd_root):
    """
    Handles XSD extension elements, returning base type and any additional facets.
    
    Args:
        extension: The XSD extension element
        xsd_root: The root of the XSD document
        
    Returns:
        A tuple (base_type, facets) where:
        - base_type: The base type being extended
        - facets: Dictionary of additional constraints
    """
    base = extension.get('base')
    facets = {}
    
    if not base:
        return (None, facets)
    
    # Check if base is a built-in type
    if 'xs:' in base or 'xsd:' in base:
        return (base.split(':')[-1], facets)
    
    # Handle custom base types
    base_type_name = base.split(':')[-1]
    
    # Check if base is a simpleType
    simple_type = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}simpleType[@name="{base_type_name}"]')
    if simple_type:
        restriction = simple_type.find('{http://www.w3.org/2001/XMLSchema}restriction')
        if restriction:
            base_type = restriction.get('base')
            if base_type and ('xs:' in base_type or 'xsd:' in base_type):
                return (base_type.split(':')[-1], facets)
    
    return (None, facets)


def translate_annotation(xsd_element, subject, graph):
    """Converts XSD annotations (documentation/appinfo) to SHACL descriptions."""
    annotations = xsd_element.find('.//{http://www.w3.org/2001/XMLSchema}annotation')
    if annotations is not None:
        # Handle documentation elements (descriptions)
        for doc in annotations.findall('.//{http://www.w3.org/2001/XMLSchema}documentation'):
            lang = doc.get('{http://www.w3.org/XML/1998/namespace}lang', 'en')
            if doc.text and doc.text.strip():
                graph.add((subject, DCT.description, Literal(doc.text.strip(), lang=lang)))
                graph.add((subject, RDFS.comment, Literal(doc.text.strip(), lang=lang)))
                graph.add((subject, SH.description, Literal(doc.text.strip(), lang=lang)))


def handle_attribute(attribute, xsd_root, graph, parent_shape=None, parent_type_name=None):
    """
    Handles XSD attribute definitions by creating PropertyShapes.
    
    Args:
        attribute: The XSD attribute element
        xsd_root: The root of the XSD document
        graph: The RDF graph
        parent_shape: Optional parent NodeShape for the attribute
    """
    attr_name = attribute.get('name') or attribute.get('ref')
    if not attr_name:
        return None
    
    # Handle attribute references (ref="...")
    if attribute.get('ref'):
        attr_name = attribute.get('ref').split(':')[-1]
        # Look up the attribute definition
        attribute = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}attribute[@name="{attr_name}"]')
        if not attribute:
            return None
    
    # Create a PropertyShape for the attribute
    attr_shape = I14Y[f"{parent_type_name}/{attr_name}"] 
    graph.add((attr_shape, RDF.type, SH.PropertyShape))
    graph.add((attr_shape, RDF.type, OWL.DatatypeProperty)) 
    graph.add((attr_shape, SH.path, I14Y[f"{parent_type_name}/{attr_name}"] ))
    graph.add((attr_shape, SH.name, Literal(attr_name, lang='en')))
    
    # Handle attribute type
    attr_type = attribute.get('type')
    if attr_type:
        if 'xs:' in attr_type or 'xsd:' in attr_type:
            # Built-in type
            datatype = attr_type.split(':')[-1]
            graph.add((attr_shape, SH.datatype, XSD[datatype]))
            graph.add((attr_shape, RDFS.range, XSD[datatype]))
        else:
            # Custom type
            type_name = attr_type.split(':')[-1]
            simple_type = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}simpleType[@name="{type_name}"]')
            if simple_type:
                type_info = process_simple_type(simple_type, xsd_root, graph)
                if type_info[0] == 'simple':
                    graph.add((attr_shape, SH.datatype, XSD[type_info[1]]))
                    for facet_name, facet_value in type_info[2].get('facets', {}).items():
                        translate_restriction(facet_name, facet_value, type_info[1], attr_shape, graph)
                    if type_info[2].get('enumerations'):
                        handle_enumeration(type_info[2]['enumerations'], attr_shape, graph)
    
    # Handle inline simpleType
    simple_type = attribute.find('{http://www.w3.org/2001/XMLSchema}simpleType')
    if simple_type is not None:
        type_info = process_simple_type(simple_type, xsd_root, graph)
        if type_info[0] == 'simple':
            graph.add((attr_shape, SH.datatype, XSD[type_info[1]]))
            graph.add((attr_shape, RDFS.range, XSD[type_info[1]]))
            # Add facets
            for facet_name, facet_value in type_info[2].get('facets', {}).items():
                translate_restriction(facet_name, facet_value, type_info[1], attr_shape, graph)
            # Add enumerations
            if type_info[2].get('enumerations'):
                handle_enumeration(type_info[2]['enumerations'], attr_shape, graph)    # Handle use (required/optional)
    use = attribute.get('use', 'optional')
    if use == 'required':
        graph.add((attr_shape, SH.minCount, Literal(1, datatype=XSD.integer)))
    else:
        graph.add((attr_shape, SH.minCount, Literal(0, datatype=XSD.integer)))
    
    # Handle default and fixed values
    if attribute.get('default'):
        graph.add((attr_shape, SH.defaultValue, Literal(attribute.get('default'))))
    if attribute.get('fixed'):
        graph.add((attr_shape, SH.hasValue, Literal(attribute.get('fixed'))))
    
    # Handle annotations
    translate_annotation(attribute, attr_shape, graph)
    
    # If we have a parent shape, link the attribute to it
    if parent_shape:
        graph.add((parent_shape, SH.property, attr_shape))
    
    return attr_shape



def _process_complex_type(complex_type, xsd_root):
    """Process a complexType definition and return type details."""
    if complex_type.get('mixed') == 'true':
        return ('complex_mixed', None, {})
    
    simple_content = complex_type.find('{http://www.w3.org/2001/XMLSchema}simpleContent')
    complex_content = complex_type.find('{http://www.w3.org/2001/XMLSchema}complexContent')
    
    if simple_content is not None:
        extension = simple_content.find('{http://www.w3.org/2001/XMLSchema}extension')
        restriction = simple_content.find('{http://www.w3.org/2001/XMLSchema}restriction')
        
        if extension is not None:
            base_type, facets = handle_extension(extension, xsd_root)
            attrs = {}
            for attr in extension.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
                attrs[attr.get('name')] = attr
            return ('complex_simple_content', base_type, {'attributes': attrs})
        elif restriction is not None:
            base = restriction.get('base')
            if base and ('xs:' in base or 'xsd:' in base):
                # Process restriction facets
                facets = {}
                for facet in restriction.findall('{http://www.w3.org/2001/XMLSchema}*'):
                    facet_name = facet.tag.split('}')[-1]
                    facet_value = facet.get('value')
                    if facet_value:
                        facets[facet_name] = facet_value
                return ('complex_simple_content', base.split(':')[-1], {'facets': facets})
    
    elif complex_content is not None:
        extension = complex_content.find('{http://www.w3.org/2001/XMLSchema}extension')
        restriction = complex_content.find('{http://www.w3.org/2001/XMLSchema}restriction')
        
        if extension is not None:
            base = extension.get('base')
            if base:
                attrs = {}
                for attr in extension.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
                    attrs[attr.get('name')] = attr
                return ('complex', base.split(':')[-1], {'attributes': attrs})
        elif restriction is not None:
            base = restriction.get('base')
            if base:
                return ('complex', base.split(':')[-1], {})
    
    # Check for compositors (sequence, choice, all)
    compositors = {
        'sequence': None,
        'choice': None,
        'all': None
    }
    
    for compositor in compositors:
        elem = complex_type.find(f'{{http://www.w3.org/2001/XMLSchema}}{compositor}')
        if elem is not None:
            compositors[compositor] = elem
            break
    
    # Collect attributes
    attrs = {}
    for attr in complex_type.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
        attrs[attr.get('name')] = attr
    for attr_group in complex_type.findall('{http://www.w3.org/2001/XMLSchema}attributeGroup'):
        attr_group_ref = attr_group.get('ref')
        if attr_group_ref:
            group_name = attr_group_ref.split(':')[-1]
            attr_group_def = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}attributeGroup[@name="{group_name}"]')
            if attr_group_def:
                for attr in attr_group_def.findall('{http://www.w3.org/2001/XMLSchema}attribute'):
                    attrs[attr.get('name')] = attr
    
    return ('complex', None, {
        'compositors': compositors,
        'attributes': attrs
    })

def process_element_details(element, xsd_root, graph, prop_shape):
    """
    Process element details and add to property shape.
    """
    translate_annotation(element, prop_shape, graph)

    type_kind, base_type, facets = get_element_type_details(xsd_root, element)
    
    if type_kind in ['builtin', 'simple']:
        if base_type:
            graph.add((prop_shape, SH.datatype, XSD[base_type]))
            # Add as DatatypeProperty
            graph.add((prop_shape, RDF.type, OWL.DatatypeProperty))
            graph.add((prop_shape, RDFS.range, XSD[base_type]))
        
        if 'facets' in facets:
            for facet_name, facet_value in facets['facets'].items():
                translate_restriction(facet_name, facet_value, base_type, prop_shape, graph)
        
        if 'enumerations' in facets:
            handle_enumeration(facets['enumerations'], prop_shape, graph)
    
    elif type_kind == 'union':
        # Handle union types - could add sh:or constraint here
        pass
    
    elif type_kind in ['complex', 'complex_mixed', 'complex_simple_content']:
        type_name = element.get('type').split(':')[-1] if element.get('type') else None
        if type_name:
            graph.add((prop_shape, SH['node'], I14Y[type_name]))
            graph.add((prop_shape, RDF.type, OWL.ObjectProperty))
    
    if type_kind in ['simple', 'complex', 'complex_mixed', 'complex_simple_content', 'union']:
        type_name = element.get('type').split(':')[-1] if element.get('type') else None


    # Handle minOccurs and maxOccurs
    min_occurs = element.get('minOccurs', '1')
    max_occurs = element.get('maxOccurs', '1')

    graph.add((prop_shape, SH.minCount, Literal(int(min_occurs), datatype=XSD.integer)))
    if max_occurs != "unbounded":
        graph.add((prop_shape, SH.maxCount, Literal(int(max_occurs), datatype=XSD.integer)))
    else:
        graph.add((prop_shape, SH.maxCount, SH.unbounded))

def get_element_type_details(xsd_root, element):
    """Determine the type of an XSD element and return details."""
    element_type = element.get('type')
    if not element_type:
        # Check for inline type definition
        simple_type = element.find('{http://www.w3.org/2001/XMLSchema}simpleType')
        complex_type = element.find('{http://www.w3.org/2001/XMLSchema}complexType')
        if simple_type is not None:
            return process_simple_type(simple_type, xsd_root, None)
        elif complex_type is not None:
            return _process_complex_type(complex_type, xsd_root)
        else:
            return ('builtin', 'string', {})  # Default to string if no type info
    
    if 'xs:' in element_type or 'xsd:' in element_type:
        # Built-in simple type
        return ('builtin', element_type.split(':')[-1], {})
    
    # Custom type - could be simple or complex
    type_name = element_type.split(':')[-1]
    
    # Check for simpleType first
    simple_type = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}simpleType[@name="{type_name}"]')
    if simple_type is not None:
        return process_simple_type(simple_type, xsd_root, None)
    
    # Check for complexType
    complex_type = xsd_root.find(f'.//{{http://www.w3.org/2001/XMLSchema}}complexType[@name="{type_name}"]')
    if complex_type is not None:
        return _process_complex_type(complex_type, xsd_root)
    
    # If we can't determine, assume builtin string
    return ('builtin', 'string', {})

def create_orphaned_element_properties(graph, processed_global_elements):
    """Create property shapes for orphaned global elements that have no property relationships."""
    
    # Check which elements exist as classes but don't have corresponding property shapes
    orphaned_elements = []
    
    for element_name in processed_global_elements:
        element_class = I14Y[element_name]
        
        # Check if this element is already referenced as a property somewhere
        has_property_reference = False
        
        # Look for any property shape that references this element as sh:node
        for s, p, o in graph.triples((None, SH.node, element_class)):
            has_property_reference = True
            break
            
        # Look for any property shape with a path that matches this element
        element_property_path = I14Y[element_name]
        for s, p, o in graph.triples((None, SH.path, element_property_path)):
            has_property_reference = True
            break
            
        # If no property reference found, it's orphaned
        if not has_property_reference:
            orphaned_elements.append(element_name)
    
    if orphaned_elements:
        # Create a root schema class to hold orphaned elements
        schema_root = I14Y["Schema"]
        graph.add((schema_root, RDF.type, RDFS.Class))
        graph.add((schema_root, RDF.type, SH.NodeShape))
        graph.add((schema_root, RDFS.label, Literal("Schema", lang='en')))
        graph.add((schema_root, DCT.description, Literal("Root schema containing global elements", lang='en')))
        graph.add((schema_root, RDFS.comment, Literal("Root schema containing global elements", lang='en')))
        graph.add((schema_root, SH.description, Literal("Root schema containing global elements", lang='en')))
        graph.add((schema_root, SH.name, Literal("Schema", lang='en')))
        graph.add((schema_root, SH.closed, Literal(True)))
        
        # Create property shapes for each orphaned element
        property_shapes = []
        for i, element_name in enumerate(orphaned_elements):
            element_class = I14Y[element_name]
            property_path = I14Y[f"Schema/{element_name}"]
            
            # Create the property shape
            property_shape = property_path
            graph.add((property_shape, RDF.type, SH.PropertyShape))
            
            # Determine if it should be an object property or datatype property
            # Check if the element class has a datatype
            has_datatype = False
            for s, p, o in graph.triples((element_class, SH.datatype, None)):
                has_datatype = True
                graph.add((property_shape, RDF.type, OWL.DatatypeProperty))
                break
                
            if not has_datatype:
                graph.add((property_shape, RDF.type, OWL.ObjectProperty))
                graph.add((property_shape, SH.node, element_class))
            
            # Copy basic properties from the class
            for s, p, o in graph.triples((element_class, DCT.description, None)):
                graph.add((property_shape, DCT.description, o))
                graph.add((property_shape, RDFS.comment, o))
                graph.add((property_shape, SH.description, o))
                
            # Copy datatype and constraints
            for s, p, o in graph.triples((element_class, SH.datatype, None)):
                graph.add((property_shape, SH.datatype, o))
                graph.add((property_shape, RDFS.range, o))
                
            for s, p, o in graph.triples((element_class, SH['in'], None)):
                graph.add((property_shape, SH['in'], o))
                
            for constraint_prop in [SH.minLength, SH.maxLength, SH.pattern, SH.minInclusive, SH.maxInclusive, SH.totalDigits]:
                for s, p, o in graph.triples((element_class, constraint_prop, None)):
                    graph.add((property_shape, constraint_prop, o))
            
            # Set property shape metadata
            graph.add((property_shape, SH.path, property_path))
            graph.add((property_shape, SH.name, Literal(element_name, lang='en')))
            graph.add((property_shape, SH.order, Literal(i, datatype=XSD.integer)))
            graph.add((property_shape, SH.minCount, Literal(0, datatype=XSD.integer)))
            graph.add((property_shape, SH.maxCount, Literal(1, datatype=XSD.integer)))
            
            property_shapes.append(property_shape)
        
        # Link all property shapes to the schema root
        for prop_shape in property_shapes:
            graph.add((schema_root, SH.property, prop_shape))


def generate_shacl(xsd_root):
    """Generate comprehensive SHACL shapes from the XSD schema."""
    g = Graph()
    g.bind("sh", SH)
    g.bind("i14y", I14Y)
    g.bind("dct", DCT)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    
    # Process global simple types
    for simple_type in xsd_root.findall('.//{http://www.w3.org/2001/XMLSchema}simpleType'):
        type_name = simple_type.get('name')
        if type_name:  # Only process global simple types
            process_simple_type(simple_type, xsd_root, g, type_name)
    
    # Process global complex types
    for complex_type in xsd_root.findall('.//{http://www.w3.org/2001/XMLSchema}complexType'):
        type_name = complex_type.get('name')
        if type_name:  # Only process global complex types
            node_shape = I14Y[type_name]
            g.add((node_shape, RDF.type, SH.NodeShape))
            g.add((node_shape, RDF.type, RDFS.Class))
            g.add((node_shape, SH.name, Literal(type_name, lang='en')))
            g.add((node_shape, RDFS.label, Literal(type_name, lang='en')))
            
            # Add annotation
            translate_annotation(complex_type, node_shape, g)
            
            # Process complex type content
            process_complex_type_content(complex_type, xsd_root, g, node_shape, type_name)
    
    # Process global elements
    processed_global_elements = set()
    for element in xsd_root.findall('.//{http://www.w3.org/2001/XMLSchema}element'):
        # Only process top-level elements (direct children of schema)
        if element.getparent().tag == '{http://www.w3.org/2001/XMLSchema}schema':
            element_name = element.get('name')
            if element_name:
                processed_global_elements.add(element_name)
            process_global_element(element, xsd_root, g)
    
    # Create property shapes for orphaned global elements
    # These are elements that exist as classes but aren't referenced as properties
    create_orphaned_element_properties(g, processed_global_elements)
    
    # Process global attributes
    for attribute in xsd_root.findall('.//{http://www.w3.org/2001/XMLSchema}attribute'):
        # Only process top-level attributes (direct children of schema)
        if attribute.getparent().tag == '{http://www.w3.org/2001/XMLSchema}schema':
            attr_name = attribute.get('name')
            if attr_name:
                # Create a property shape for global attributes
                attr_shape = I14Y[attr_name]
                g.add((attr_shape, RDF.type, SH.PropertyShape))
                g.add((attr_shape, RDF.type, OWL.DatatypeProperty))
                g.add((attr_shape, SH.path, I14Y[attr_name]))
                g.add((attr_shape, SH.name, Literal(attr_name, lang='en')))
                
                # Process attribute details
                handle_attribute(attribute, xsd_root, g, None, "")
    
    return g

def save_shacl(g, output_file):
    """Save the SHACL graph to an RDF file."""
    g.serialize(destination=output_file, format='turtle')

def xsd_to_shacl(xsd_file, output_file, base_path, dataset_identifier="dataset_identifier"):
    """Convert an XSD schema to a SHACL RDF file."""
    global I14Y
    
    # Update the namespace with the provided dataset_identifier
    i14y_base_path = f"https://register.ld.admin.ch/i14y/dataset/{dataset_identifier}/structure/"
    I14Y = Namespace(i14y_base_path)
    
    xsd_root = parse_xsd(xsd_file)
    resolve_imports(xsd_root, base_path)
    shacl_graph = generate_shacl(xsd_root)
    save_shacl(shacl_graph, output_file)



# Example usage
# xsd_to_shacl("xsd_importer/example/eCH-0108-7-0.xsd", 'xsd_importer/example/master_unit.ttl', 'xsd_importer/example')
# xsd_to_shacl("C:/Users/U80877014/Documents/Structure/shacl_importer_scripts/xsd_importer/example/do-d-14.04-SPIGES-2024-02.xsd", 'xsd_importer/example/do-d-14.04-SPIGES-2024-02.ttl', 'xsd_importer/example')
