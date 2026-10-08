import time

from django.db import OperationalError, connection


def retry_database_operation(operation, attempts: int = 3):
    """Retry brief SQLite write contention without hiding other DB errors."""

    for attempt in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            message = str(exc).lower()
            is_lock_error = connection.vendor == 'sqlite' and ('locked' in message or 'busy' in message)
            if not is_lock_error or attempt == attempts - 1:
                raise
            connection.close()
            time.sleep(0.2 * (attempt + 1))
