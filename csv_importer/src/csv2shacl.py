import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD, SH, OWL, RDFS

from security_limits import ConversionLimitError, MAX_CSV_CELL_CHARS, MAX_CSV_COLUMNS

csv.field_size_limit(MAX_CSV_CELL_CHARS)


@dataclass
class _ColumnStats:
    first_value: Optional[str] = None
    has_value: bool = False
    all_integer: bool = True
    all_decimal: bool = True
    all_boolean: bool = True
    all_date: bool = True
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    def observe(self, value: Optional[str], is_valid_date) -> None:
        if value is None:
            return
        if len(value) > MAX_CSV_CELL_CHARS:
            raise ConversionLimitError('CSV cell exceeds the allowed size')

        cleaned = value.strip()
        if not cleaned:
            return

        if self.first_value is None:
            self.first_value = cleaned
        self.has_value = True
        self.all_integer = self.all_integer and cleaned.isdigit()
        self.all_boolean = self.all_boolean and cleaned.lower() in {
            'true', 'false', 't', 'f', 'yes', 'no', '1', '0'
        }
        self.all_date = self.all_date and is_valid_date(cleaned)

        try:
            numeric_value = float(cleaned)
        except ValueError:
            self.all_decimal = False
        else:
            self.minimum = numeric_value if self.minimum is None else min(self.minimum, numeric_value)
            self.maximum = numeric_value if self.maximum is None else max(self.maximum, numeric_value)


class CSVToSHACL:
    """CSV to SHACL transformer with bounded, streaming type inference."""

    YEAR_KEYWORDS = {
        'en': ['year', 'yr'],
        'de': ['jahr', 'jahrgang'],
        'fr': ['année', 'an'],
        'it': ['anno', 'annata'],
    }

    def __init__(self, base_uri, default_lang: str = None):
        self.g = Graph()
        self.base_uri = base_uri.rstrip('/') + '/'
        self.default_lang = default_lang

        self.SH = Namespace('http://www.w3.org/ns/shacl#')
        self.QB = Namespace('http://purl.org/linked-data/cube#')
        self.DCTERMS = Namespace('http://purl.org/dc/terms/')
        self.schema = Namespace('https://schema.org/')
        self.pav = Namespace('http://purl.org/pav/')
        self.rdfs = Namespace('http://www.w3.org/2000/01/rdf-schema#')
        self.OWL = Namespace('http://www.w3.org/2002/07/owl#')

        self.g.bind('sh', self.SH)
        self.g.bind('QB', self.QB)
        self.g.bind('dcterms', self.DCTERMS)
        self.g.bind('schema', self.schema)
        self.g.bind('pav', self.pav)
        self.g.bind('rdfs', self.rdfs)
        self.g.bind('owl', self.OWL)

    def _is_year_column(self, column_name: str) -> bool:
        lower_name = column_name.lower()
        return any(
            keyword in lower_name
            for keywords in self.YEAR_KEYWORDS.values()
            for keyword in keywords
        )

    def _guess_property_type(self, stats: _ColumnStats, column_name: str) -> URIRef:
        if not stats.has_value:
            return XSD.string

        sample = stats.first_value or ''
        if self._is_year_column(column_name):
            if (len(sample) == 4 and sample.isdigit()) or self._is_valid_date(sample):
                return XSD.date
        if stats.all_integer:
            return XSD.integer
        if stats.all_decimal:
            return XSD.decimal
        if stats.all_boolean:
            return XSD.boolean
        if stats.all_date:
            return XSD.date
        return XSD.string

    @staticmethod
    def _is_valid_date(value: str) -> bool:
        parts = value.split('-')
        return (
            len(parts) == 3
            and len(parts[0]) == 4
            and parts[0].isdigit()
            and parts[1].isdigit()
            and parts[2].isdigit()
        )

    def _add_numeric_constraints(self, prop_uri: URIRef, stats: _ColumnStats, datatype: URIRef) -> None:
        if stats.minimum is None or stats.maximum is None:
            return

        minimum = int(stats.minimum) if datatype == XSD.integer else stats.minimum
        maximum = int(stats.maximum) if datatype == XSD.integer else stats.maximum
        self.g.add((prop_uri, SH.minInclusive, Literal(minimum, datatype=datatype)))
        self.g.add((prop_uri, SH.maxInclusive, Literal(maximum, datatype=datatype)))

    def _add_property_shape(
        self,
        node_shape: URIRef,
        property_name: str,
        property_type: URIRef,
        stats: _ColumnStats,
        order: int,
    ) -> None:
        safe_name = property_name.replace(' ', '_').replace('.', '_')
        prop_uri = URIRef(f'{node_shape}/{safe_name}')

        self.g.add((prop_uri, RDF.type, SH.PropertyShape))
        self.g.add((prop_uri, RDF.type, OWL.DatatypeProperty))
        self.g.add((prop_uri, SH.path, prop_uri))
        self.g.add((prop_uri, SH.datatype, property_type))
        self.g.add((prop_uri, SH.order, Literal(order)))

        if self.default_lang:
            self.g.add((prop_uri, SH.name, Literal(property_name, lang=self.default_lang)))
            self.g.add((prop_uri, RDFS.label, Literal(property_name, lang=self.default_lang)))
        else:
            self.g.add((prop_uri, SH.name, Literal(property_name)))
            self.g.add((prop_uri, RDFS.label, Literal(property_name)))

        if property_type in (XSD.integer, XSD.decimal):
            self._add_numeric_constraints(prop_uri, stats, property_type)

        self.g.add((node_shape, SH.property, prop_uri))

    def transform_csv_to_shacl(
        self,
        csv_file: str,
        node_shape_name: Optional[str] = None,
        shape_identifier: Optional[str] = None,
        delimiter: Optional[str] = None,
    ) -> bool:
        try:
            with open(csv_file, 'r', encoding='utf-8-sig', newline='') as input_file:
                first_line = input_file.readline(MAX_CSV_CELL_CHARS + 1)
                if len(first_line) > MAX_CSV_CELL_CHARS:
                    raise ConversionLimitError('CSV header exceeds the allowed size')
                input_file.seek(0)

                used_delimiter = delimiter if delimiter else (';' if ';' in first_line else ',')
                if used_delimiter == r'\t':
                    used_delimiter = '\t'
                if len(used_delimiter) != 1:
                    raise ConversionLimitError('Invalid CSV delimiter')

                reader = csv.DictReader(input_file, delimiter=used_delimiter)
                if not reader.fieldnames:
                    return False
                if len(reader.fieldnames) > MAX_CSV_COLUMNS:
                    raise ConversionLimitError('CSV has too many columns')

                columns = [(column, column.strip('\ufeff')) for column in reader.fieldnames]
                stats = {raw_column: _ColumnStats() for raw_column, _ in columns}
                row_seen = False

                for row in reader:
                    row_seen = True
                    if None in row:
                        raise ConversionLimitError('CSV row has more fields than the header')
                    for raw_column, _ in columns:
                        stats[raw_column].observe(row.get(raw_column), self._is_valid_date)

                if not row_seen:
                    return False

                shape_name = node_shape_name or Path(csv_file).stem
                shape_uri = URIRef(f'{self.base_uri}{shape_identifier or shape_name}')
                self.g.add((shape_uri, RDF.type, SH.NodeShape))
                self.g.add((shape_uri, RDF.type, self.rdfs.Class))
                self.g.add((shape_uri, SH.closed, Literal(True)))

                if self.default_lang:
                    self.g.add((shape_uri, SH.name, Literal(shape_name, lang=self.default_lang)))
                    self.g.add((shape_uri, self.rdfs.label, Literal(shape_name, lang=self.default_lang)))
                else:
                    self.g.add((shape_uri, SH.name, Literal(shape_name)))
                    self.g.add((shape_uri, self.rdfs.label, Literal(shape_name)))

                for order, (raw_column, clean_column) in enumerate(columns):
                    property_type = self._guess_property_type(stats[raw_column], clean_column)
                    self._add_property_shape(
                        shape_uri,
                        clean_column,
                        property_type,
                        stats[raw_column],
                        order,
                    )

                return True
        except ConversionLimitError:
            raise
        except csv.Error as error:
            raise ConversionLimitError('Invalid or oversized CSV input') from error
        except Exception as error:
            print(f'Error processing CSV: {error}')
            return False

    def save_shacl(self, output_file: str) -> None:
        self.g.serialize(destination=output_file, format='turtle')
        print(f'SHACL shape saved to {output_file}')


if __name__ == '__main__':
    dataset_identifier = 'dataset_identifier'
    base_uri = 'https://register.ld.admin.ch/i14y/dataset/' + dataset_identifier + '/structure/'
    transformer = CSVToSHACL(base_uri)
    input_csv = 'csv_importer/example/iris.csv'
    output_ttl = 'csv_importer/example/iris.ttl'
    if transformer.transform_csv_to_shacl(input_csv):
        transformer.save_shacl(output_ttl)