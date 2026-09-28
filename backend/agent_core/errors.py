"""Errors shared by the generic host and replaceable business packs."""


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, *, details: dict | None = None):
        self.code, self.message, self.status = code, message, status
        self.details = details
        super().__init__(message)

    def as_dict(self):
        result = {'code': self.code, 'message': self.message}
        if self.details is not None:
            result['details'] = self.details
        return result
