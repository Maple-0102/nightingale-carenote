"""Domain errors mapped to safe API responses."""


class CareNoteError(Exception):
    status_code = 400
    code = "care_note_error"

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(CareNoteError):
    status_code = 404
    code = "not_found"


class ForbiddenError(CareNoteError):
    status_code = 403
    code = "forbidden"


class ConflictError(CareNoteError):
    status_code = 409
    code = "version_conflict"


class ValidationError(CareNoteError):
    status_code = 422
    code = "validation_error"
