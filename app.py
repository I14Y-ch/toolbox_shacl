from flask import Flask, render_template, request, send_file, flash, redirect, url_for
import os
import tempfile
import json
from werkzeug.utils import secure_filename
from pathlib import Path

# Import the converters
from csv_importer.src.csv2shacl import CSVToSHACL
from dsd_importer.src.dsd2shacl import DSD2SHACLTransformer
from import_template.json_template.src.json_template_importer import json_to_shacl
from postgres_importer.src.postgres2schacl import postgres_to_shacl
from xsd_importer.src.xsd2shacl import xsd_to_shacl

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/')
def index():
    """Main page to select the import type."""
    return render_template('index.html')

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
def process_csv():
    """Process CSV import."""
    try:
        # Get form data
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')
        base_uri = f"https://www.i14y.admin.ch/resources/datasets/{dataset_identifier}/structure/"
        default_lang = request.form.get('default_lang', 'de')
        node_shape_name = request.form.get('node_shape_name', '')
        shape_identifier = request.form.get('shape_identifier', '')
        delimiter = request.form.get('delimiter', '')
        
        # Handle file upload
        if 'csv_file' not in request.files:
            flash('No file selected')
            return redirect(url_for('csv_import'))
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('csv_import'))
        
        if file and file.filename.endswith('.csv'):
            filename = secure_filename(file.filename)
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(input_path)
            
            # Generate output filename
            output_filename = f"{Path(filename).stem}.ttl"
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
            
            # Process CSV
            transformer = CSVToSHACL(base_uri, default_lang if default_lang else None)
            
            if transformer.transform_csv_to_shacl(
                input_path, 
                node_shape_name if node_shape_name else None,
                shape_identifier if shape_identifier else None,
                delimiter if delimiter else None
            ):
                transformer.save_shacl(output_path)
                
                # Clean up input file
                os.remove(input_path)
                
                return send_file(output_path, as_attachment=True, download_name=output_filename)
            else:
                flash('Failed to process CSV file')
                return redirect(url_for('csv_import'))
        else:
            flash('Please upload a valid CSV file')
            return redirect(url_for('csv_import'))
            
    except Exception as e:
        flash(f'Error processing CSV: {str(e)}')
        return redirect(url_for('csv_import'))

@app.route('/process_dsd', methods=['POST'])
def process_dsd():
    """Process DSD import."""
    try:
        # Get form data
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')
        dsd_id = request.form.get('dsd_id')
        token = request.form.get('token')
        
        if not dsd_id or not token:
            flash('DSD ID and Token are required')
            return redirect(url_for('dsd_import'))
        
        # Create transformer
        transformer = DSD2SHACLTransformer(dataset_identifier)
        
        # Generate output path
        output_path = app.config['UPLOAD_FOLDER']
        
        if transformer.transform_to_shacl(dsd_id, output_path + "/", token):
            # Find the generated file
            for file in os.listdir(output_path):
                if file.endswith('.ttl'):
                    return send_file(os.path.join(output_path, file), as_attachment=True, download_name=file)
            
            flash('No output file generated')
            return redirect(url_for('dsd_import'))
        else:
            flash('Failed to process DSD')
            return redirect(url_for('dsd_import'))
            
    except Exception as e:
        flash(f'Error processing DSD: {str(e)}')
        return redirect(url_for('dsd_import'))

@app.route('/process_json', methods=['POST'])
def process_json():
    """Process JSON template import."""
    try:
        # Get form data
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')
        
        # Handle file upload
        if 'json_file' not in request.files:
            flash('No file selected')
            return redirect(url_for('json_import'))
        
        file = request.files['json_file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('json_import'))
        
        if file and file.filename.endswith('.json'):
            filename = secure_filename(file.filename)
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(input_path)
            
            # Generate output filename
            output_filename = f"{Path(filename).stem}.ttl"
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
            
            # Read and process JSON
            with open(input_path, 'r', encoding='utf-8') as f:
                json_content = f.read()
            
            json_to_shacl(json_content, output_path, dataset_identifier)
            
            # Clean up input file
            os.remove(input_path)
            
            return send_file(output_path, as_attachment=True, download_name=output_filename)
        else:
            flash('Please upload a valid JSON file')
            return redirect(url_for('json_import'))
            
    except Exception as e:
        flash(f'Error processing JSON: {str(e)}')
        return redirect(url_for('json_import'))

@app.route('/process_postgres', methods=['POST'])
def process_postgres():
    """Process PostgreSQL import."""
    try:
        # Get form data
        host = request.form.get('host')
        port = int(request.form.get('port', 5432))
        database = request.form.get('database')
        user = request.form.get('user')
        password = request.form.get('password')
        schema = request.form.get('schema', 'public')
        
        if not all([host, database, user, password]):
            flash('All database connection fields are required')
            return redirect(url_for('postgres_import'))
        
        # Generate output filename
        output_filename = f"{database}_{schema}.ttl"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        
        # Process PostgreSQL schema
        postgres_to_shacl(host, port, database, user, password, schema, output_path)
        
        return send_file(output_path, as_attachment=True, download_name=output_filename)
            
    except Exception as e:
        flash(f'Error processing PostgreSQL schema: {str(e)}')
        return redirect(url_for('postgres_import'))

@app.route('/process_xsd', methods=['POST'])
def process_xsd():
    """Process XSD import."""
    try:
        dataset_identifier = request.form.get('dataset_identifier', 'dataset_identifier')
        
        # Handle file upload
        if 'xsd_file' not in request.files:
            flash('No file selected')
            return redirect(url_for('xsd_import'))
        
        file = request.files['xsd_file']
        if file.filename == '':
            flash('No file selected')
            return redirect(url_for('xsd_import'))
        
        if file and file.filename.endswith('.xsd'):
            filename = secure_filename(file.filename)
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(input_path)
            
            # Generate output filename
            output_filename = f"{Path(filename).stem}.ttl"
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
            
            # Process XSD
            xsd_to_shacl(input_path, output_path, app.config['UPLOAD_FOLDER'], dataset_identifier)
            
            # Clean up input file
            os.remove(input_path)
            
            return send_file(output_path, as_attachment=True, download_name=output_filename)
        else:
            flash('Please upload a valid XSD file')
            return redirect(url_for('xsd_import'))
            
    except Exception as e:
        flash(f'Error processing XSD: {str(e)}')
        return redirect(url_for('xsd_import'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
