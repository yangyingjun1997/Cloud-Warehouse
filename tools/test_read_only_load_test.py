import argparse
from contextlib import redirect_stdout
from http.cookiejar import Cookie
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import read_only_load_test as load_test


class FakeResponse:
    def __init__(self, body=b'', url='http://testserver/'):
        self._body = body
        self._url = url

    def read(self):
        return self._body

    def geturl(self):
        return self._url


class LoginCookieTests(unittest.TestCase):
    def _cookie(self):
        return Cookie(
            version=0,
            name='sessionid',
            value='session-value',
            port=None,
            port_specified=False,
            domain='testserver',
            domain_specified=False,
            domain_initial_dot=False,
            path='/',
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )

    def test_login_cookie_submits_csrf_form_and_returns_session(self):
        responses = [
            FakeResponse(
                b'<input name="csrfmiddlewaretoken" value="csrf-value">',
                'http://testserver/login/',
            ),
            FakeResponse(b'ok', 'http://testserver/'),
        ]

        class FakeOpener:
            def __init__(self, jar):
                self.jar = jar
                self.requests = []

            def open(self, request, timeout):
                self.requests.append(request)
                response = responses.pop(0)
                if request.get_method() == 'POST':
                    self.jar.set_cookie(LoginCookieTests()._cookie())
                    self.post_body = request.data.decode('utf-8')
                return response

        fake_opener = None

        def opener_factory(*handlers):
            nonlocal fake_opener
            cookie_handler = next(
                handler for handler in handlers
                if hasattr(handler, 'cookiejar')
            )
            fake_opener = FakeOpener(cookie_handler.cookiejar)
            return fake_opener

        with patch.object(load_test, 'build_opener', side_effect=opener_factory):
            cookie = load_test.login_cookie(
                'http://testserver/', '/login/', 'warehouse-user', 'secret-value', 2, False
            )

        self.assertEqual(cookie, 'sessionid=session-value')
        self.assertIn('csrfmiddlewaretoken=csrf-value', fake_opener.post_body)
        self.assertIn('username=warehouse-user', fake_opener.post_body)
        self.assertIn('password=secret-value', fake_opener.post_body)

    def test_login_cookie_rejects_redirect_back_to_login(self):
        responses = [
            FakeResponse(
                b'<input name="csrfmiddlewaretoken" value="csrf-value">',
                'http://testserver/login/',
            ),
            FakeResponse(b'login failed', 'http://testserver/login/'),
        ]

        class FakeOpener:
            def open(self, request, timeout):
                return responses.pop(0)

        with patch.object(load_test, 'build_opener', return_value=FakeOpener()):
            with self.assertRaisesRegex(RuntimeError, 'Login failed'):
                load_test.login_cookie(
                    'http://testserver/', '/login/', 'warehouse-user', 'wrong-value', 2, False
                )


class ReportTests(unittest.TestCase):
    def test_summary_contains_per_path_metrics(self):
        samples = [
            load_test.Sample('/warehouse/', 200, 10.0),
            load_test.Sample('/warehouse/', 200, 30.0),
            load_test.Sample('/reports/', 500, 50.0, 'HTTP 500'),
        ]

        report = load_test.summarize_step(samples, concurrency=4, duration=1.5)

        self.assertEqual(report['requests'], 3)
        self.assertEqual(report['status_counts'], {'200': 2, '500': 1})
        self.assertEqual(report['per_path']['/warehouse/']['error_rate'], 0.0)
        self.assertEqual(report['per_path']['/reports/']['error_rate'], 1.0)
        self.assertEqual(report['per_path']['/warehouse/']['latency_ms']['p95'], 30.0)

    def test_main_supports_ramp_without_exposing_credentials_or_cookie(self):
        args = argparse.Namespace(
            base_url='http://testserver/',
            paths=['/warehouse/', '/reports/'],
            requests=4,
            concurrency=1,
            concurrency_ramp='1,4,8',
            timeout=2.0,
            warmup=0,
            cookie_env='TEST_COOKIE',
            login_path='/login/',
            username_env='TEST_USERNAME',
            password_env='TEST_PASSWORD',
            insecure=False,
            output=None,
        )

        def fake_fetch(base_url, path, timeout, cookie, insecure):
            self.assertEqual(cookie, 'sessionid=private-session')
            return load_test.Sample(path, 200, 5.0)

        output = io.StringIO()
        environment = {
            'TEST_USERNAME': 'private-user',
            'TEST_PASSWORD': 'private-password',
            'TEST_COOKIE': '',
        }
        with (
            patch.object(load_test, 'parse_args', return_value=args),
            patch.object(load_test, 'login_cookie', return_value='sessionid=private-session'),
            patch.object(load_test, 'fetch', side_effect=fake_fetch),
            patch.dict(os.environ, environment, clear=False),
            redirect_stdout(output),
        ):
            exit_code = load_test.main()

        raw_report = output.getvalue()
        report = json.loads(raw_report)
        self.assertEqual(exit_code, 0)
        self.assertEqual([step['concurrency'] for step in report['steps']], [1, 4, 8])
        self.assertEqual(report['authentication_source'], 'login')
        self.assertNotIn('private-user', raw_report)
        self.assertNotIn('private-password', raw_report)
        self.assertNotIn('private-session', raw_report)

    def test_main_rejects_invalid_ramp(self):
        args = argparse.Namespace(
            base_url='http://testserver/',
            paths=['/health/'],
            requests=1,
            concurrency=1,
            concurrency_ramp='1,zero,4',
            timeout=2.0,
            warmup=0,
            cookie_env='TEST_COOKIE',
            login_path='/login/',
            username_env='TEST_USERNAME',
            password_env='TEST_PASSWORD',
            insecure=False,
            output=None,
        )
        with (
            patch.object(load_test, 'parse_args', return_value=args),
            patch.dict(
                os.environ,
                {'TEST_COOKIE': '', 'TEST_USERNAME': '', 'TEST_PASSWORD': ''},
                clear=False,
            ),
        ):
            with self.assertRaisesRegex(SystemExit, 'positive integers'):
                load_test.main()


if __name__ == '__main__':
    unittest.main()
