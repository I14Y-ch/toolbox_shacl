import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD, DCTERMS, QB, OWL
from werkzeug.utils import secure_filename

from security_limits import (
    ConversionLimitError,
    DSD_DETAIL_WORKERS,
    DSD_TOTAL_TIMEOUT_SECONDS,
    MAX_DSD_ELEMENTS,
    MAX_DSD_IDENTIFIER_CHARS,
    MAX_DSD_RESPONSE_BYTES,
    MAX_DSD_TOTAL_RESPONSE_BYTES,
    MAX_LANGUAGES_PER_FIELD,
    MAX_TEXT_CHARS,
)

API_HOST = 'input.i14y.admin.ch'
API_BASE_URL = f'https://{API_HOST}/api'
REQUEST_TIMEOUT = (3, 10)
_IDENTIFIER_PATTERN = re.compile(rf'^[A-Za-z0-9_-]{{1,{MAX_DSD_IDENTIFIER_CHARS}}}$')


def _remove_unsafe_filename_sequences(filename: str) -> str:
    while '..' in filename:
        filename = filename.replace('..', '.')
    return filename.strip('._')


def _safe_identifier_filename(identifier: str) -> str:
    return _remove_unsafe_filename_sequences(secure_filename(identifier)) or 'dsd'


def _validate_api_identifier(identifier: Any) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ConversionLimitError('Invalid DSD identifier')
    return identifier


class _RequestBudget:
    def __init__(self):
        self.deadline = time.monotonic() + DSD_TOTAL_TIMEOUT_SECONDS
        self.total_bytes = 0
        self.lock = threading.Lock()

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ConversionLimitError('DSD processing exceeded its time budget')
        return remaining

    def request_timeout(self):
        remaining = self.remaining()
        return min(REQUEST_TIMEOUT[0], remaining), min(REQUEST_TIMEOUT[1], remaining)

    def consume(self, byte_count: int) -> None:
        with self.lock:
            self.total_bytes += byte_count
            if self.total_bytes > MAX_DSD_TOTAL_RESPONSE_BYTES:
                raise ConversionLimitError('DSD responses exceed the cumulative size limit')


class DSD2SHACLTransformer:
    """Transform bounded I14Y Data Structure Definitions to SHACL shapes."""

    def __init__(self, dataset_identifier: str):
        self.dataset_identifier = dataset_identifier
        self.g = Graph()
        self._initialize_namespaces()

    def _initialize_namespaces(self) -> None:
        i14y_base_path = (
            f'https://register.ld.admin.ch/i14y/dataset/'
            f'{self.dataset_identifier}/structure/'
        )
        self.sh = Namespace('http://www.w3.org/ns/shacl#')
        self.i14y = Namespace(i14y_base_path)
        self.QB = Namespace('http://purl.org/linked-data/cube#')
        self.DCTERMS = Namespace('http://purl.org/dc/terms/')
        self.schema = Namespace('https://schema.org/')
        self.pav = Namespace('http://purl.org/pav/')
        self.rdfs = Namespace('http://www.w3.org/2000/01/rdf-schema#')
        self.OWL = Namespace('http://www.w3.org/2002/07/owl#')

        self.g.bind('sh', self.sh)
        self.g.bind('i14y', self.i14y)
        self.g.bind('QB', self.QB)
        self.g.bind('dcterms', self.DCTERMS)
        self.g.bind('schema', self.schema)
        self.g.bind('pav', self.pav)
        self.g.bind('rdfs', self.rdfs)
        self.g.bind('owl', self.OWL)

    @staticmethod
    def _clean_text(text: Optional[str]) -> str:
        if not text:
            return ''
        if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
            raise ConversionLimitError('DSD text exceeds the allowed size')
        soup = BeautifulSoup(text, 'html.parser')
        return ' '.join(soup.get_text(separator=' ').split())

    @staticmethod
    def _api_get_request(
        url: str,
        token,
        budget: Optional[_RequestBudget] = None,
    ) -> Optional[Any]:
        parsed_url = urlsplit(url)
        if parsed_url.scheme != 'https' or parsed_url.hostname != API_HOST:
            raise ConversionLimitError('DSD API target is not allowed')

        request_budget = budget or _RequestBudget()
        headers = {
            'Accept': 'application/json',
            'Authorization': token,
        }
        try:
            # request_timeout() always returns a bounded connect/read tuple.
            response = requests.get(  # nosec B113
                url,
                verify=True,
                headers=headers,
                timeout=request_budget.request_timeout(),
                stream=True,
                allow_redirects=False,
            )
        except requests.Timeout as error:
            raise ConversionLimitError('DSD API request timed out') from error

        try:
            if response.status_code != 200:
                return None

            content_length = response.headers.get('Content-Length')
            try:
                declared_length = int(content_length) if content_length else None
            except ValueError:
                declared_length = None
            if declared_length is not None and declared_length > MAX_DSD_RESPONSE_BYTES:
                raise ConversionLimitError('DSD API response is too large')

            payload = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                request_budget.remaining()
                if not chunk:
                    continue
                payload.extend(chunk)
                if len(payload) > MAX_DSD_RESPONSE_BYTES:
                    raise ConversionLimitError('DSD API response is too large')
                request_budget.consume(len(chunk))

            try:
                return json.loads(payload)
            except json.JSONDecodeError as error:
                raise ConversionLimitError('DSD API returned invalid JSON') from error
        finally:
            response.close()

    def get_dsd(self, dsd_id: str, token, budget=None) -> Optional[Dict[str, Any]]:
        dsd_id = _validate_api_identifier(dsd_id)
        return self._api_get_request(
            f'{API_BASE_URL}/DataStructureDefinitionInput/{dsd_id}',
            token,
            budget,
        )

    def get_data_elements(self, dsd_id: str, token, budget=None) -> Optional[Any]:
        dsd_id = _validate_api_identifier(dsd_id)
        return self._api_get_request(
            f'{API_BASE_URL}/DataStructureDefinitionInput/{dsd_id}'
            '/dataElements?page=1&pageSize=100',
            token,
            budget,
        )

    def get_data_element_details(self, element_id: str, token, budget=None) -> Optional[Dict[str, Any]]:
        element_id = _validate_api_identifier(element_id)
        return self._api_get_request(
            f'{API_BASE_URL}/DataElementInput/{element_id}',
            token,
            budget,
        )

    @staticmethod
    def _validate_languages(value, field_name):
        if not isinstance(value, dict) or len(value) > MAX_LANGUAGES_PER_FIELD:
            raise ConversionLimitError(f'Invalid DSD {field_name}')

    def _add_dsd_metadata(self, dsd_node: URIRef, dsd_data: Dict[str, Any]) -> None:
        valid_from = dsd_data.get('validFrom', 'N/A')
        valid_until = dsd_data.get('validTo', 'N/A')
        version = dsd_data.get('version', 'N/A')
        names = dsd_data.get('name', {})
        self._validate_languages(names, 'names')

        self.g.add((dsd_node, RDF.type, QB.DataStructureDefinition))
        self.g.add((dsd_node, RDF.type, self.sh.NodeShape))
        self.g.add((dsd_node, RDF.type, self.rdfs.Class))
        self.g.add((dsd_node, self.schema.validFrom, Literal(valid_from, datatype=XSD.date)))
        if valid_until is not None:
            self.g.add((dsd_node, self.schema.validUntil, Literal(valid_until, datatype=XSD.date)))
        self.g.add((dsd_node, self.schema.version, Literal(version)))
        self.g.add((dsd_node, self.pav.version, Literal(version)))

        for language, value in names.items():
            cleaned_value = self._clean_text(value)
            if cleaned_value:
                self.g.add((dsd_node, self.sh.name, Literal(cleaned_value, lang=language)))
                self.g.add((dsd_node, self.rdfs.label, Literal(cleaned_value, lang=language)))

    def _add_data_element_properties(
        self,
        identifier: str,
        entry: Dict[str, Any],
        details: Dict[str, Any],
        dsd_node: URIRef,
    ) -> None:
        if not isinstance(identifier, str) or len(identifier) > MAX_TEXT_CHARS:
            raise ConversionLimitError('Invalid DSD element identifier')

        property_node = URIRef(f'{dsd_node}/{identifier}')
        concept_id = details.get('conceptId', 'N/A')
        self.g.add((property_node, RDF.type, self.sh.PropertyShape))
        self.g.add((property_node, RDF.type, self.OWL.DatatypeProperty))
        self.g.add((property_node, self.sh.order, Literal(entry.get('position', 'N/A'))))
        self.g.add((property_node, self.sh.path, property_node))
        self._add_multilingual_properties(property_node, entry)
        self._add_role_properties(property_node, entry.get('role', 'N/A'))
        self._add_type_properties(property_node, entry.get('type', 'N/A'))
        self.g.add((
            property_node,
            self.DCTERMS.conformsTo,
            URIRef(f'https://www.i14y.admin.ch/catalog/concepts/{concept_id}/description'),
        ))

    def _add_multilingual_properties(self, node: URIRef, entry: Dict[str, Any]) -> None:
        names = entry.get('name', {})
        descriptions = entry.get('description', {})
        self._validate_languages(names, 'names')
        self._validate_languages(descriptions, 'descriptions')

        for language, value in names.items():
            cleaned_value = self._clean_text(value)
            if cleaned_value:
                self.g.add((node, self.sh.name, Literal(cleaned_value, lang=language)))

        for language, value in descriptions.items():
            cleaned_value = self._clean_text(value)
            if cleaned_value:
                literal = Literal(cleaned_value, lang=language)
                self.g.add((node, self.DCTERMS.description, literal))
                self.g.add((node, self.sh.description, literal))
                self.g.add((node, self.rdfs.comment, literal))

    def _add_role_properties(self, node: URIRef, role: str) -> None:
        role_types = {
            'Dimension': self.QB.DimensionProperty,
            'Measure': self.QB.MeasureProperty,
            'Attribute': self.QB.AttributeProperty,
        }
        if role in role_types:
            self.g.add((node, RDF.type, role_types[role]))

    def _add_type_properties(self, node: URIRef, type_name: str) -> None:
        datatypes = {
            'String': XSD.string,
            'Numeric': XSD.decimal,
            'Date': XSD.date,
        }
        if type_name in datatypes:
            self.g.add((node, self.sh.datatype, datatypes[type_name]))
        elif type_name == 'CodeList':
            self.g.add((node, RDF.type, self.QB.CodedProperty))

    def _fetch_element_details(self, entries, token, budget):
        executor = ThreadPoolExecutor(max_workers=DSD_DETAIL_WORKERS)
        futures = {}
        try:
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ConversionLimitError('Invalid DSD data element')
                element_id = _validate_api_identifier(entry.get('id'))
                future = executor.submit(self.get_data_element_details, element_id, token, budget)
                futures[future] = index

            results = {}
            try:
                for future in as_completed(futures, timeout=budget.remaining()):
                    results[futures[future]] = future.result()
            except FuturesTimeoutError as error:
                raise ConversionLimitError('DSD processing exceeded its time budget') from error
            return [(entry, results.get(index)) for index, entry in enumerate(entries)]
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    def transform_to_shacl(self, dsd_id: str, output_path: str, token) -> Optional[str]:
        budget = _RequestBudget()
        dsd_id = _validate_api_identifier(dsd_id)
        dsd_data = self.get_dsd(dsd_id, token, budget)
        if not isinstance(dsd_data, dict):
            return None

        identifier_dsd = dsd_data.get('identifier', 'N/A')
        if not isinstance(identifier_dsd, str) or len(identifier_dsd) > MAX_TEXT_CHARS:
            raise ConversionLimitError('Invalid DSD output identifier')
        resolved_dsd_id = _validate_api_identifier(dsd_data.get('id'))
        dsd_node = URIRef(f'{self.i14y}{identifier_dsd}')
        self._add_dsd_metadata(dsd_node, dsd_data)

        data_elements = self.get_data_elements(resolved_dsd_id, token, budget)
        if not isinstance(data_elements, list):
            return None
        if len(data_elements) > MAX_DSD_ELEMENTS:
            raise ConversionLimitError('DSD has too many data elements')

        for entry, details in self._fetch_element_details(data_elements, token, budget):
            if not details:
                continue
            identifier = entry.get('identifier', 'N/A')
            self._add_data_element_properties(identifier, entry, details, dsd_node)
            self.g.add((dsd_node, self.sh.property, URIRef(f'{dsd_node}/{identifier}')))

        budget.remaining()
        output_dir = Path(output_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_identifier = _safe_identifier_filename(identifier_dsd)
        output_file = output_dir / f'{safe_identifier}.ttl'
        self.g.serialize(destination=output_file, format='turtle')
        return str(output_file)