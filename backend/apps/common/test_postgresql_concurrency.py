"""PostgreSQL concurrency checks for shared security counters."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from .auth_throttle import account_key, ip_key, record_failure
from .models import LoginThrottleState


@skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL 行锁语义。')
class PostgreSQLSecurityCounterConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @override_settings(
        LOGIN_RATE_LIMIT_FAILURES=100,
        LOGIN_RATE_LIMIT_IP_FAILURES=100,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=300,
        LOGIN_RATE_LIMIT_LOCKOUT_SECONDS=900,
    )
    def test_parallel_failures_are_not_lost_between_gunicorn_workers(self):
        workers = 8
        barrier = Barrier(workers)

        def record_once():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                record_failure(username='parallel-user', client_ip='203.0.113.8')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(record_once) for _ in range(workers)]
            for future in futures:
                future.result(timeout=20)

        self.assertEqual(
            LoginThrottleState.objects.get(key=account_key('parallel-user')).failure_count,
            workers,
        )
        self.assertEqual(
            LoginThrottleState.objects.get(key=ip_key('203.0.113.8')).failure_count,
            workers,
        )
