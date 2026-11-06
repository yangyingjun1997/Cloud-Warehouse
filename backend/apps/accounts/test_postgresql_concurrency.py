"""PostgreSQL race regression for privileged account session limits."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection
from django.test import RequestFactory, TransactionTestCase, override_settings

from .models import AccountSession
from .session_services import register_account_session

User = get_user_model()


@skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL 行锁语义。')
@override_settings(
    PRIVILEGED_MAX_ACTIVE_SESSIONS=2,
    ACCOUNT_SESSION_SYNC_SECONDS=0,
)
class PostgreSQLAccountSessionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_parallel_logins_cannot_exceed_privileged_session_limit(self):
        user = User.objects.create_user(username='parallel-session-user', password='password123')
        user.groups.add(Group.objects.get_or_create(name='warehouse_outbound')[0])
        barrier = Barrier(3)

        def register(index):
            close_old_connections()
            try:
                session = SessionStore()
                session['_auth_user_id'] = str(user.pk)
                session.save()
                request = RequestFactory().get(
                    '/',
                    REMOTE_ADDR=f'192.0.2.{index}',
                    HTTP_USER_AGENT=f'Mozilla/5.0 (Windows NT 10.0) Chrome/{index}',
                )
                request.user = User.objects.get(pk=user.pk)
                request.session = session
                barrier.wait(timeout=10)
                register_account_session(request, request.user)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(register, index) for index in range(1, 4)]
            for future in futures:
                future.result(timeout=20)

        self.assertEqual(
            AccountSession.objects.filter(user=user, revoked_at__isnull=True).count(),
            2,
        )
        self.assertEqual(Session.objects.count(), 2)
        self.assertEqual(
            AccountSession.objects.filter(user=user, revoked_at__isnull=False).count(),
            1,
        )
