class ConversionLimitError(ValueError):
    """Raised when an import exceeds a configured resource or structure limit."""

    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


MIB = 1024 * 1024

MAX_UPLOAD_BYTES = 16 * MIB
MAX_JSON_INPUT_BYTES = 4 * MIB
MAX_XSD_INPUT_BYTES = 4 * MIB
MAX_OUTPUT_BYTES = 16 * MIB

MAX_CSV_COLUMNS = 512
MAX_CSV_CELL_CHARS = 1 * MIB

MAX_JSON_CLASSES = 500
MAX_JSON_MEMBERS = 5_000
MAX_LANGUAGES_PER_FIELD = 20
MAX_TEXT_CHARS = 20_000
MAX_IDENTIFIER_CHARS = 256

MAX_XSD_NODES = 20_000

MAX_DSD_IDENTIFIER_CHARS = 128
MAX_DSD_ELEMENTS = 100
MAX_DSD_RESPONSE_BYTES = 2 * MIB
MAX_DSD_TOTAL_RESPONSE_BYTES = 16 * MIB
DSD_TOTAL_TIMEOUT_SECONDS = 30
DSD_DETAIL_WORKERS = 4
