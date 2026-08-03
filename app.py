from flask import Flask, render_template, request, send_file, flash, redirect, url_for
import os
import shutil
import tempfile
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wsgi import ClosingIterator
from werkzeug.utils import secure_filename
from pathlib import Path

# Import the converters
from csv_importer.src.csv2shacl import CSVToSHACL
from dsd_importer.src.dsd2shacl import DSD2SHACLTransformer
from import_template.json_template.src.json_template_importer import json_to_shacl
from postgres_importer.src.postgres2schacl import (
    PostgresTargetError,
    postgres_to_shacl,
    resolve_allowed_postgres_target,
)
from security_limits import (
    ConversionLimitError,
    MAX_JSON_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_XSD_INPUT_BYTES,
)
from xsd_importer.src.xsd2shacl import xsd_to_shacl


def _load_secret_key():
    secret_key = os.environ.get('SECRET_KEY', '')
    if len(secret_key) < 32:
        raise RuntimeError('SECRET_KEY must be configured with at least 32 characters')
    return secret_key


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
app.config['RATELIMIT_HEADERS_ENABLED'] = True

# Session cookie hardening (secure-by-default). Opt-out for local HTTP dev
# by setting HTTPS_ENABLED=false explicitly.
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HTTPS_ENABLED', 'true').lower() != 'false'


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response


limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
)

# Create upload directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _postgres_import_enabled():
    return _env_flag('ENABLE_POSTGRES_IMPORT', default=False)


def _has_extension(filename, extension):
    return filename.lower().endswith(extension)


def _remove_unsafe_filename_sequences(filename):
    while '..' in filename:
        filename = filename.replace('..', '.')
    return filename.strip('._')


def _safe_filename(filename, fallback):
    filename = _remove_unsafe_filename_sequences(secure_filename(filename))
    return filename or fallback


def _safe_ttl_filename(raw_name, fallback):
    filename = _safe_filename(raw_name, fallback)
    if not filename.lower().endswith('.ttl'):
        filename = f"{Path(filename).stem or Path(fallback).stem}.ttl"
    return filename


def _create_request_dir(prefix):
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=app.config['UPLOAD_FOLDER'])


def _cleanup_dir(path):
    if path:
        shutil.rmtree(path, ignore_errors=True)


def _enforce_file_size(path, maximum_bytes):
    if os.path.getsize(path) > maximum_bytes:
        raise ConversionLimitError('Uploaded file is too large', status_code=413)


def _send_file_with_cleanup(output_path, download_name, cleanup_dir):
    if os.path.getsize(output_path) > MAX_OUTPUT_BYTES:
        raise ConversionLimitError('Generated output is too large')

    response = send_file(output_path, as_attachment=True, download_name=download_name)
    cleanup = lambda: _cleanup_dir(cleanup_dir)
    response.response = ClosingIterator(response.response, [cleanup])
    response.call_on_close(cleanup)
    return response


def _limit_error_response(error):
    app.logger.warning('Import rejected by a processing limit: %s', error)
    return {'error': 'Input exceeds allowed processing limits'}, error.status_code


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    return {'error': 'Uploaded file is too large'}, 413


@app.errorhandler(429)
def handle_rate_limit(_error):
    return {'error': 'Too many import requests'}, 429


@app.route('/')
def index():
    """Main page to select the import type."""
    return render_template('index.html')

@app.route('/health')
def health():
    """Health check endpoint for DigitalOcean."""
    return {"status": "ok"}, 200

@app.route('/csv_import')
def csv_import():
    """CSV import form."""
    return render_template('csv_import.html')

@app.route('/dsd_import')
def dsd_import():
    """DSD import form."""
    return render_template('dsd_import.html')

@app.route('/json_import')
def json_import():
    """JSON template import form."""
    return render_template('json_import.html')

@app.route('/postgres_import')
def postgres_import():
    """PostgreSQL import form."""
    return render_template('postgres_import.html')

@app.route('/xsd_import')
def xsd_import():
    """XSD import form."""
    return render_template('xsd_import.html')

@app.route('/process_csv', methods=['POST'])
@limiter.limit('10 per minute')
def process_csv():
    """Process CSV import."""
    request_dir = None
    try:
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')
        base_uri = f"https://register.ld.admin.ch/i14y/dataset/{dataset_identifier}/structure/"
        default_lang = request.form.get('default_lang', 'de')
        node_shape_name = request.form.get('node_shape_name', '')
        shape_identifier = request.form.get('shape_identifier', '')
        delimiter = request.form.get('delimiter', '')

        if 'csv_file' not in request.files:
            flash('No file selected')
            return redirect(url_for('csv_import'))

        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('csv_import'))

        if file and _has_extension(file.filename, '.csv'):
            request_dir = _create_request_dir('csv-')
            filename = _safe_filename(file.filename, 'upload.csv')
            input_path = os.path.join(request_dir, filename)
            file.save(input_path)
            _enforce_file_size(input_path, MAX_UPLOAD_BYTES)

            output_filename = _safe_ttl_filename(f"{Path(filename).stem}.ttl", 'csv_output.ttl')
            output_path = os.path.join(request_dir, output_filename)

            transformer = CSVToSHACL(base_uri, default_lang if default_lang else None)

            if transformer.transform_csv_to_shacl(
                input_path,
                node_shape_name if node_shape_name else None,
                shape_identifier if shape_identifier else None,
                delimiter if delimiter else None
            ):
                transformer.save_shacl(output_path)
                response = _send_file_with_cleanup(output_path, output_filename, request_dir)
                request_dir = None
                return response

            flash('Failed to process CSV file')
            return redirect(url_for('csv_import'))

        flash('Please upload a valid CSV file')
        return redirect(url_for('csv_import'))

    except ConversionLimitError as error:
        return _limit_error_response(error)
    except Exception:
        app.logger.exception('Error processing CSV')
        flash('Error processing CSV file')
        return redirect(url_for('csv_import'))
    finally:
        _cleanup_dir(request_dir)

@app.route('/process_dsd', methods=['POST'])
@limiter.limit('3 per minute')
def process_dsd():
    """Process DSD import."""
    request_dir = None
    try:
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')
        dsd_id = request.form.get('dsd_id')
        token = request.form.get('token')

        if not dsd_id or not token:
            flash('DSD ID and Token are required')
            return redirect(url_for('dsd_import'))

        request_dir = _create_request_dir('dsd-')
        transformer = DSD2SHACLTransformer(dataset_identifier)
        output_file = transformer.transform_to_shacl(dsd_id, request_dir, token)

        if output_file:
            output_filename = Path(output_file).name
            response = _send_file_with_cleanup(output_file, output_filename, request_dir)
            request_dir = None
            return response

        flash('Failed to process DSD')
        return redirect(url_for('dsd_import'))

    except ConversionLimitError as error:
        return _limit_error_response(error)
    except Exception:
        app.logger.exception('Error processing DSD')
        flash('Error processing DSD')
        return redirect(url_for('dsd_import'))
    finally:
        _cleanup_dir(request_dir)

@app.route('/process_json', methods=['POST'])
@limiter.limit('10 per minute')
def process_json():
    """Process JSON template import."""
    request_dir = None
    try:
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')

        if 'json_file' not in request.files:
            flash('No file selected')
            return redirect(url_for('json_import'))

        file = request.files['json_file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('json_import'))

        if file and _has_extension(file.filename, '.json'):
            request_dir = _create_request_dir('json-')
            filename = _safe_filename(file.filename, 'upload.json')
            input_path = os.path.join(request_dir, filename)
            file.save(input_path)
            _enforce_file_size(input_path, MAX_JSON_INPUT_BYTES)

            output_filename = _safe_ttl_filename(f"{Path(filename).stem}.ttl", 'json_output.ttl')
            output_path = os.path.join(request_dir, output_filename)

            with open(input_path, 'r', encoding='utf-8') as f:
                json_content = f.read()

            json_to_shacl(json_content, output_path, dataset_identifier)

            response = _send_file_with_cleanup(output_path, output_filename, request_dir)
            request_dir = None
            return response

        flash('Please upload a valid JSON file')
        return redirect(url_for('json_import'))

    except ConversionLimitError as error:
        return _limit_error_response(error)
    except Exception:
        app.logger.exception('Error processing JSON')
        flash('Error processing JSON file')
        return redirect(url_for('json_import'))
    finally:
        _cleanup_dir(request_dir)

@app.route('/process_postgres', methods=['POST'])
@limiter.limit('3 per minute')
def process_postgres():
    """Process PostgreSQL import."""
    request_dir = None
    try:
        if not _postgres_import_enabled():
            flash('PostgreSQL import is not available')
            return redirect(url_for('postgres_import'))

        host = request.form.get('host')
        port_raw = request.form.get('port', '5432')
        database = request.form.get('database')
        user = request.form.get('user')
        password = request.form.get('password')
        schema = request.form.get('schema', 'public')

        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            flash('Invalid PostgreSQL connection settings')
            return redirect(url_for('postgres_import'))

        if port < 1 or port > 65535:
            flash('Invalid PostgreSQL connection settings')
            return redirect(url_for('postgres_import'))

        if not all([host, database, user, password]):
            flash('All database connection fields are required')
            return redirect(url_for('postgres_import'))

        hostaddr = resolve_allowed_postgres_target(
            host,
            os.environ.get('POSTGRES_ALLOWED_NETWORKS', ''),
        )

        request_dir = _create_request_dir('postgres-')
        output_filename = _safe_ttl_filename(f"{database}_{schema}.ttl", 'postgres_schema.ttl')
        output_path = os.path.join(request_dir, output_filename)

        postgres_to_shacl(
            host,
            port,
            database,
            user,
            password,
            schema,
            output_path,
            hostaddr=hostaddr,
        )

        response = _send_file_with_cleanup(output_path, output_filename, request_dir)
        request_dir = None
        return response

    except PostgresTargetError:
        app.logger.warning('Rejected PostgreSQL target outside the configured allowlist')
        return {'error': 'Invalid PostgreSQL connection settings'}, 422
    except ConversionLimitError as error:
        return _limit_error_response(error)
    except Exception:
        app.logger.exception('Error processing PostgreSQL schema')
        flash('Error processing PostgreSQL schema')
        return redirect(url_for('postgres_import'))
    finally:
        _cleanup_dir(request_dir)

@app.route('/process_xsd', methods=['POST'])
@limiter.limit('10 per minute')
def process_xsd():
    """Process XSD import."""
    request_dir = None
    try:
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')

        if 'xsd_file' not in request.files:
            flash('No file selected')
            return redirect(url_for('xsd_import'))

        file = request.files['xsd_file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('xsd_import'))

        if file and _has_extension(file.filename, '.xsd'):
            request_dir = _create_request_dir('xsd-')
            filename = _safe_filename(file.filename, 'upload.xsd')
            input_path = os.path.join(request_dir, filename)
            file.save(input_path)
            _enforce_file_size(input_path, MAX_XSD_INPUT_BYTES)

            output_filename = _safe_ttl_filename(f"{Path(filename).stem}.ttl", 'xsd_output.ttl')
            output_path = os.path.join(request_dir, output_filename)

            xsd_to_shacl(input_path, output_path, request_dir, dataset_identifier)

            response = _send_file_with_cleanup(output_path, output_filename, request_dir)
            request_dir = None
            return response

        flash('Please upload a valid XSD file')
        return redirect(url_for('xsd_import'))

    except ConversionLimitError as error:
        return _limit_error_response(error)
    except Exception:
        app.logger.exception('Error processing XSD')
        flash('Error processing XSD')
        return redirect(url_for('xsd_import'))
    finally:
        _cleanup_dir(request_dir)

if __name__ == '__main__':
    # Get port from environment variable (for DigitalOcean App Platform)
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host='0.0.0.0', port=port)  # nosec B104  # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
else:
    # This is important for gunicorn to find the app
    # Disable debug for production
    app.debug = False
