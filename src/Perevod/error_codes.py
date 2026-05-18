# error_codes.py


class ErrorCodes:
    # General Errors (1000-1999)
    UNKNOWN_ERROR = {"code": 1000, "message": "An unknown error occurred."}
    INITIALIZATION_FAILED = {"code": 1001, "message": "Initialization failed."}

    # File System Errors (2000-2999)
    FILE_NOT_FOUND = {"code": 2000, "message": "File not found."}
    DIRECTORY_NOT_FOUND = {"code": 2001, "message": "Directory not found."}
    PROJECT_CREATION_FAILED = {"code": 2002, "message": "Failed to create project."}
    PROJECT_DELETION_FAILED = {"code": 2003, "message": "Failed to delete project."}

    # API Errors (3000-3999)
    API_KEY_MISSING = {"code": 3000, "message": "API key is missing."}
    API_REQUEST_FAILED = {"code": 3001, "message": "API request failed."}
    RATE_LIMIT_EXCEEDED = {"code": 3002, "message": "API rate limit exceeded."}

    # Database Errors (4000-4999)
    DB_CONNECTION_FAILED = {"code": 4000, "message": "Database connection failed."}
    DB_QUERY_FAILED = {"code": 4001, "message": "Database query failed."}

    # Knowledge Base Errors (5000-5999)
    KB_INDEX_BUILD_FAILED = {
        "code": 5000,
        "message": "Knowledge base index build failed.",
    }
    KB_QUERY_FAILED = {"code": 5001, "message": "Knowledge base query failed."}

    # Translation Errors (6000-6999)
    TRANSLATION_FAILED = {"code": 6000, "message": "Translation failed."}

    @staticmethod
    def get_error(error_code, additional_info=""):
        error = error_code.copy()
        if additional_info:
            error["message"] = f"{error['message']} - {additional_info}"
        return error
