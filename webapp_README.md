# SHACL Importer Web Application

This Flask web application provides a user-friendly interface for generating SHACL (Shapes Constraint Language) structures for the Swiss Interoperability Platform I14Y.

## Features

- **CSV Import**: Generate SHACL shapes from CSV files by analyzing column types and data patterns
- **DSD Import**: Import existing Data Structure Definitions from I14Y platform using API
- **JSON Template Import**: Create SHACL shapes from structured JSON template files
- **PostgreSQL Import**: Generate SHACL shapes from PostgreSQL database schema
- **XSD Import**: Transform XML Schema Definition (XSD) files to SHACL shapes

## Installation

1. **Create a virtual environment (recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Linux/Mac
   # or on Windows: venv\Scripts\activate
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the Flask application:**
   ```bash
   python app.py
   ```

4. **Access the web interface:**
   Open your browser and navigate to `http://localhost:5000`

## Usage

1. **Select Import Type**: Choose the type of data source you want to convert
2. **Fill Form**: Provide the required information and upload files as needed
3. **Generate Structure**: Click "Create Structure" to process your data
4. **Download Result**: The generated SHACL file will be automatically downloaded

## Configuration

- **Upload Folder**: Files are temporarily stored in the `uploads/` directory
- **Max File Size**: 16MB limit for uploaded files
- **Supported Formats**: CSV, JSON, XSD files

## Requirements

- Python 3.8+
- Flask
- rdflib
- lxml (for XSD processing)
- psycopg2 (for PostgreSQL connections)
- beautifulsoup4 (for HTML processing)
- requests (for API calls)

## Security Notes

- Database credentials are used only for the conversion process and are not stored
- Uploaded files are temporarily stored and cleaned up after processing
- API tokens are handled securely during DSD imports

## Support

For issues or questions about the conversion scripts, refer to the main project documentation or contact the development team.
