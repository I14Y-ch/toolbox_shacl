import io
import json
import os
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('SECRET_KEY', 'x' * 32)

import app as app_module
from csv_importer.src import csv2shacl
from csv_importer.src.csv2shacl import CSVToSHACL
from dsd_importer.src import dsd2shacl
from dsd_importer.src.dsd2shacl import DSD2SHACLTransformer
from import_template.json_template.src.json_template_importer import json_to_shacl
from postgres_importer.src import postgres2schacl
from security_limits import ConversionLimitError, MAX_JSON_CLASSES
from xsd_importer.src import xsd2shacl


class SecretKeyTests(unittest.TestCase):
    def test_secret_key_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                app_module._load_secret_key()

    def test_secret_key_must_be_long_enough(self):
        with patch.dict(os.environ, {'SECRET_KEY': 'too-short'}, clear=True):
            with self.assertRaises(RuntimeError):
                app_module._load_secret_key()

    def test_secret_key_is_loaded_from_environment(self):
        expected = 'a' * 32
        with patch.dict(os.environ, {'SECRET_KEY': expected}, clear=True):
            self.assertEqual(app_module._load_secret_key(), expected)


class ServerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.upload_root = tempfile.mkdtemp()
        self.previous_upload_folder = app_module.app.config['UPLOAD_FOLDER']
        self.previous_testing = app_module.app.config.get('TESTING')
        self.previous_limiter_enabled = app_module.limiter.enabled
        app_module.app.config['UPLOAD_FOLDER'] = self.upload_root
        app_module.app.config['TESTING'] = True
        app_module.limiter.enabled = False
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.app.config['UPLOAD_FOLDER'] = self.previous_upload_folder
        app_module.app.config['TESTING'] = self.previous_testing
        app_module.limiter.enabled = self.previous_limiter_enabled
        app_module.limiter.reset()
        shutil.rmtree(self.upload_root, ignore_errors=True)

    @staticmethod
    def _postgres_form(host='db.internal'):
        return {
            'host': host,
            'port': '5432',
            'database': '../prod/db',
            'user': 'user',
            'password': 'password',
            'schema': '..\\private',
        }

    def test_postgres_import_is_disabled_by_default(self):
        with patch.dict(os.environ, {'ENABLE_POSTGRES_IMPORT': 'false'}, clear=False), \
                patch('app.postgres_to_shacl') as postgres_mock:
            response = self.client.post('/process_postgres', data=self._postgres_form())

        self.assertEqual(response.status_code, 302)
        postgres_mock.assert_not_called()

    def test_enabled_postgres_import_requires_allowlist(self):
        with patch.dict(
            os.environ,
            {'ENABLE_POSTGRES_IMPORT': 'true', 'POSTGRES_ALLOWED_NETWORKS': ''},
            clear=False,
        ), patch('app.postgres_to_shacl') as postgres_mock:
            response = self.client.post('/process_postgres', data=self._postgres_form())

        self.assertEqual(response.status_code, 422)
        postgres_mock.assert_not_called()
        self.assertEqual(list(Path(self.upload_root).iterdir()), [])

    def test_enabled_postgres_import_uses_validated_hostaddr_and_safe_path(self):
        captured = {}

        def fake_postgres_to_shacl(
            host,
            port,
            database,
            user,
            password,
            schema,
            output_path,
            hostaddr=None,
        ):
            captured['args'] = (host, port, database, user, password, schema, output_path)
            captured['hostaddr'] = hostaddr
            Path(output_path).write_text('@prefix sh: <http://www.w3.org/ns/shacl#> .', encoding='utf-8')

        with patch.dict(
            os.environ,
            {
                'ENABLE_POSTGRES_IMPORT': 'true',
                'POSTGRES_ALLOWED_NETWORKS': 'db.internal',
            },
            clear=False,
        ), patch('app.resolve_allowed_postgres_target', return_value='10.0.0.8'), \
                patch('app.postgres_to_shacl', side_effect=fake_postgres_to_shacl):
            response = self.client.post(
                '/process_postgres',
                data=self._postgres_form(),
                buffered=True,
            )

        self.assertEqual(response.status_code, 200)
        output_path = Path(captured['args'][6])
        self.assertEqual(output_path.parent.parent, Path(self.upload_root))
        self.assertNotIn('..', output_path.name)
        self.assertNotIn('/', output_path.name)
        self.assertNotIn('\\', output_path.name)
        self.assertEqual(captured['hostaddr'], '10.0.0.8')
        response.close()
        self.assertFalse(output_path.parent.exists())

    def test_send_file_cleanup_removes_tempdir_on_close(self):
        cleanup_dir = Path(self.upload_root) / 'download-cleanup'
        cleanup_dir.mkdir()
        output_path = cleanup_dir / 'result.ttl'
        output_path.write_text('@prefix sh: <http://www.w3.org/ns/shacl#> .', encoding='utf-8')

        with app_module.app.test_request_context('/download'):
            response = app_module._send_file_with_cleanup(
                str(output_path),
                'result.ttl',
                str(cleanup_dir),
            )
            self.assertTrue(cleanup_dir.exists())
            response.close()

        self.assertFalse(cleanup_dir.exists())

    def test_oversized_output_is_rejected_and_cleaned(self):
        def fake_json_to_shacl(_content, output_path, _identifier):
            Path(output_path).write_bytes(b'xx')

        with patch('app.MAX_OUTPUT_BYTES', 1), \
                patch('app.json_to_shacl', side_effect=fake_json_to_shacl):
            response = self.client.post(
                '/process_json',
                data={'json_file': (io.BytesIO(b'{}'), 'input.json')},
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(list(Path(self.upload_root).iterdir()), [])

    def test_json_endpoint_rejects_endpoint_size_limit(self):
        with patch('app.MAX_JSON_INPUT_BYTES', 4):
            response = self.client.post(
                '/process_json',
                data={'json_file': (io.BytesIO(b'12345'), 'input.json')},
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(list(Path(self.upload_root).iterdir()), [])

    def test_failed_xsd_processing_removes_request_tempdir(self):
        with patch('app.xsd_to_shacl', side_effect=RuntimeError('conversion failed')):
            response = self.client.post(
                '/process_xsd',
                data={'xsd_file': (io.BytesIO(b'<schema/>'), 'schema.xsd')},
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Path(self.upload_root).iterdir()), [])

    def test_rate_limit_returns_generic_429(self):
        app_module.limiter.enabled = True
        app_module.limiter.reset()
        responses = [
            self.client.post(
                '/process_dsd',
                data={},
                environ_base={'REMOTE_ADDR': '192.0.2.55'},
            )
            for _ in range(4)
        ]
        self.assertEqual([response.status_code for response in responses[:3]], [302, 302, 302])
        self.assertEqual(responses[3].status_code, 429)
        self.assertEqual(responses[3].get_json(), {'error': 'Too many import requests'})


class ConverterLimitTests(unittest.TestCase):
    def test_csv_streaming_preserves_type_and_numeric_bounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'sample.csv'
            input_path.write_text('name,value\na,2\nb,7\nc,4\n', encoding='utf-8')
            transformer = CSVToSHACL('https://example.test/structure/')

            self.assertTrue(transformer.transform_csv_to_shacl(str(input_path)))
            serialized = transformer.g.serialize(format='turtle')

        self.assertIn('integer', serialized)
        self.assertIn('minInclusive 2', serialized)
        self.assertIn('maxInclusive 7', serialized)

    def test_csv_column_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'sample.csv'
            input_path.write_text('a,b\n1,2\n', encoding='utf-8')
            transformer = CSVToSHACL('https://example.test/structure/')
            with patch.object(csv2shacl, 'MAX_CSV_COLUMNS', 1):
                with self.assertRaises(ConversionLimitError):
                    transformer.transform_csv_to_shacl(str(input_path))

    def test_json_class_limit_is_enforced(self):
        payload = {
            'classes': [
                {'identifier': f'class-{index}'}
                for index in range(MAX_JSON_CLASSES + 1)
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ConversionLimitError):
                json_to_shacl(json.dumps(payload), str(Path(tmpdir) / 'output.ttl'))

    def test_xsd_node_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'schema.xsd'
            input_path.write_text(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                '<xs:element name="one"/><xs:element name="two"/>'
                '</xs:schema>',
                encoding='utf-8',
            )
            with patch.object(xsd2shacl, 'MAX_XSD_NODES', 2):
                with self.assertRaises(ConversionLimitError):
                    xsd2shacl.parse_xsd(str(input_path))


class PostgresTargetTests(unittest.TestCase):
    @staticmethod
    def _address(ip_address):
        family = socket.AF_INET6 if ':' in ip_address else socket.AF_INET
        return family, socket.SOCK_STREAM, 6, '', (ip_address, 0)

    def test_cidr_allowlist_accepts_resolved_address(self):
        with patch(
            'postgres_importer.src.postgres2schacl.socket.getaddrinfo',
            return_value=[self._address('10.0.0.8')],
        ):
            result = postgres2schacl.resolve_allowed_postgres_target(
                'db.internal',
                '10.0.0.0/24',
            )
        self.assertEqual(result, '10.0.0.8')

    def test_mixed_dns_results_are_rejected(self):
        with patch(
            'postgres_importer.src.postgres2schacl.socket.getaddrinfo',
            return_value=[
                self._address('10.0.0.8'),
                self._address('169.254.169.254'),
            ],
        ):
            with self.assertRaises(postgres2schacl.PostgresTargetError):
                postgres2schacl.resolve_allowed_postgres_target(
                    'db.internal',
                    '10.0.0.0/24',
                )

    def test_postgres_connection_uses_hostaddr_and_timeouts(self):
        with patch('postgres_importer.src.postgres2schacl.psycopg2.connect') as connect_mock:
            postgres2schacl.connect_to_postgres(
                'db.internal',
                5432,
                'db',
                'user',
                'password',
                hostaddr='10.0.0.8',
            )

        kwargs = connect_mock.call_args.kwargs
        self.assertEqual(kwargs['connect_timeout'], postgres2schacl.DEFAULT_CONNECT_TIMEOUT)
        self.assertEqual(kwargs['hostaddr'], '10.0.0.8')
        self.assertIn('statement_timeout=', kwargs['options'])


class DSDLimitTests(unittest.TestCase):
    @staticmethod
    def _response(payload, content_length=None):
        response = Mock()
        response.status_code = 200
        response.headers = {}
        if content_length is not None:
            response.headers['Content-Length'] = str(content_length)
        response.iter_content.return_value = [payload]
        return response

    def test_dsd_requests_verify_tls_disable_redirects_and_set_timeout(self):
        response = self._response(b'{"ok": true}')
        with patch('dsd_importer.src.dsd2shacl.requests.get', return_value=response) as get_mock:
            result = DSD2SHACLTransformer._api_get_request(
                'https://input.i14y.admin.ch/api/example',
                'token',
            )

        self.assertEqual(result, {'ok': True})
        kwargs = get_mock.call_args.kwargs
        self.assertTrue(kwargs['verify'])
        self.assertTrue(kwargs['stream'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], dsd2shacl.REQUEST_TIMEOUT)
        response.close.assert_called_once()

    def test_dsd_rejects_invalid_identifier_before_network(self):
        transformer = DSD2SHACLTransformer('dataset')
        with patch('dsd_importer.src.dsd2shacl.requests.get') as get_mock:
            with self.assertRaises(ConversionLimitError):
                transformer.get_dsd('../invalid', 'token')
        get_mock.assert_not_called()

    def test_dsd_cumulative_response_limit_is_enforced(self):
        response = self._response(b'{}')
        budget = dsd2shacl._RequestBudget()
        with patch.object(dsd2shacl, 'MAX_DSD_TOTAL_RESPONSE_BYTES', 1), \
                patch('dsd_importer.src.dsd2shacl.requests.get', return_value=response):
            with self.assertRaises(ConversionLimitError):
                DSD2SHACLTransformer._api_get_request(
                    'https://input.i14y.admin.ch/api/example',
                    'token',
                    budget,
                )

    def test_dsd_response_size_limit_is_enforced_before_download(self):
        response = self._response(b'', dsd2shacl.MAX_DSD_RESPONSE_BYTES + 1)
        with patch('dsd_importer.src.dsd2shacl.requests.get', return_value=response):
            with self.assertRaises(ConversionLimitError):
                DSD2SHACLTransformer._api_get_request(
                    'https://input.i14y.admin.ch/api/example',
                    'token',
                )
        response.iter_content.assert_not_called()

    def test_dsd_time_budget_is_enforced(self):
        budget = dsd2shacl._RequestBudget()
        budget.deadline = time.monotonic() - 1
        with self.assertRaises(ConversionLimitError):
            budget.remaining()

    def test_dsd_element_limit_is_enforced(self):
        transformer = DSD2SHACLTransformer('dataset')
        dsd_payload = {
            'identifier': 'dataset',
            'id': 'dsd-id',
            'validFrom': '2024-01-01',
            'validTo': None,
            'version': '1.0',
            'name': {},
        }
        entries = [
            {'identifier': f'field-{index}', 'id': f'field-{index}'}
            for index in range(dsd2shacl.MAX_DSD_ELEMENTS + 1)
        ]
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(transformer, 'get_dsd', return_value=dsd_payload), \
                patch.object(transformer, 'get_data_elements', return_value=entries), \
                patch.object(transformer, 'get_data_element_details') as details_mock:
            with self.assertRaises(ConversionLimitError):
                transformer.transform_to_shacl('dsd-id', tmpdir, 'token')
        details_mock.assert_not_called()
    def test_dsd_transform_returns_sanitized_generated_file(self):
        transformer = DSD2SHACLTransformer('dataset')
        dsd_payload = {
            'identifier': '../unsafe/name',
            'id': 'dsd-id',
            'validFrom': '2024-01-01',
            'validTo': None,
            'version': '1.0',
            'name': {'en': 'Dataset'},
        }
        element_payload = {
            'identifier': 'field',
            'id': 'field-id',
            'position': 1,
            'name': {'en': 'Field'},
            'description': {'en': 'Plain description'},
            'role': 'Measure',
            'type': 'String',
        }

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(transformer, 'get_dsd', return_value=dsd_payload), \
                patch.object(transformer, 'get_data_elements', return_value=[element_payload]), \
                patch.object(
                    transformer,
                    'get_data_element_details',
                    return_value={'conceptId': 'concept'},
                ):
            output_file = Path(transformer.transform_to_shacl('dsd-id', tmpdir, 'token'))
            self.assertEqual(output_file.parent, Path(tmpdir))
            self.assertEqual(output_file.name, 'unsafe_name.ttl')
            self.assertTrue(output_file.exists())


if __name__ == '__main__':
    unittest.main()