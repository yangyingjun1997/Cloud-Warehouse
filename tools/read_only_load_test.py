#!/usr/bin/env python3
"""Dependency-free HTTP baseline tool for warehouse read endpoints.

Only GET requests are supported. Authentication can be supplied through an
environment variable containing a Cookie header, or established through the
normal login form using credentials held in environment variables. Secrets
are kept in memory and never written to the generated report.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from dataclasses import dataclass
import json
import os
import ssl
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = ''

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != 'input':
            return
        values = dict(attrs)
        if values.get('name') == 'csrfmiddlewaretoken':
            self.token = values.get('value') or ''


@dataclass(frozen=True)
class Sample:
    path: str
    status: int
    elapsed_ms: float
    error: str = ''


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


def ssl_context(insecure: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def make_opener(insecure: bool):
    return build_opener(NoRedirectHandler(), HTTPSHandler(context=ssl_context(insecure)))


def login_cookie(
    base_url: str,
    login_path: str,
    username: str,
    password: str,
    timeout: float,
    insecure: bool,
) -> str:
    jar = CookieJar()
    opener = build_opener(
        HTTPCookieProcessor(jar),
        HTTPSHandler(context=ssl_context(insecure)),
    )
    login_url = urljoin(base_url.rstrip('/') + '/', login_path.lstrip('/'))
    login_response = opener.open(
        Request(login_url, headers={'User-Agent': 'warehouse-read-only-baseline/2.0'}),
        timeout=timeout,
    )
    parser = CsrfTokenParser()
    parser.feed(login_response.read().decode('utf-8', errors='replace'))
    if not parser.token:
        raise RuntimeError('Login page did not contain a CSRF token.')

    payload = urlencode({
        'username': username,
        'password': password,
        'csrfmiddlewaretoken': parser.token,
        'next': '/',
    }).encode('utf-8')
    response = opener.open(
        Request(
            login_url,
            data=payload,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer': login_url,
                'User-Agent': 'warehouse-read-only-baseline/2.0',
            },
            method='POST',
        ),
        timeout=timeout,
    )
    response.read()
    if response.geturl().rstrip('/').endswith(login_path.rstrip('/')):
        raise RuntimeError('Login failed; verify the account, password, and rate-limit state.')
    cookie = '; '.join(f'{item.name}={item.value}' for item in jar)
    if not any(item.name == 'sessionid' for item in jar):
        raise RuntimeError('Login completed without a Django session cookie.')
    return cookie


def fetch(base_url: str, path: str, timeout: float, cookie: str, insecure: bool) -> Sample:
    url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
    headers = {
        'Accept': 'text/html,application/json',
        'User-Agent': 'warehouse-read-only-baseline/2.0',
    }
    if cookie:
        headers['Cookie'] = cookie
    started = time.perf_counter()
    try:
        response = make_opener(insecure).open(Request(url, headers=headers, method='GET'), timeout=timeout)
        status = response.status
        response.read()
        error = '' if 200 <= status < 300 else f'HTTP {status}'
    except HTTPError as exc:
        status = exc.code
        error = f'HTTP {exc.code}'
    except (URLError, TimeoutError, OSError) as exc:
        status = 0
        error = type(exc).__name__
    return Sample(path=path, status=status, elapsed_ms=(time.perf_counter() - started) * 1000, error=error)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Read-only HTTP concurrency baseline.')
    parser.add_argument('--base-url', required=True, help='Example: http://192.168.23.41/')
    parser.add_argument('--path', action='append', dest='paths', help='GET path; repeat as needed.')
    parser.add_argument('--requests', type=int, default=100, help='Total request count (default: 100).')
    parser.add_argument('--concurrency', type=int, default=4, help='Parallel workers (default: 4).')
    parser.add_argument(
        '--concurrency-ramp',
        help='Comma-separated worker levels, for example 1,4,8,12. Overrides --concurrency.',
    )
    parser.add_argument('--timeout', type=float, default=10.0, help='Per-request timeout seconds.')
    parser.add_argument('--warmup', type=int, default=5, help='Serial warm-up requests.')
    parser.add_argument(
        '--cookie-env',
        default='WAREHOUSE_SESSION_COOKIE',
        help='Environment variable containing the Cookie header.',
    )
    parser.add_argument('--login-path', default='/login/', help='Authentication form path.')
    parser.add_argument(
        '--username-env',
        default='WAREHOUSE_LOAD_USERNAME',
        help='Environment variable containing the login username.',
    )
    parser.add_argument(
        '--password-env',
        default='WAREHOUSE_LOAD_PASSWORD',
        help='Environment variable containing the login password.',
    )
    parser.add_argument('--insecure', action='store_true', help='Allow an untrusted test TLS certificate.')
    parser.add_argument('--output', help='Optional JSON report path.')
    return parser.parse_args()


def latency_summary(samples: list[Sample]) -> dict:
    elapsed = [sample.elapsed_ms for sample in samples]
    return {
        'min': round(min(elapsed), 2),
        'mean': round(statistics.fmean(elapsed), 2),
        'p50': round(percentile(elapsed, 0.50), 2),
        'p95': round(percentile(elapsed, 0.95), 2),
        'p99': round(percentile(elapsed, 0.99), 2),
        'max': round(max(elapsed), 2),
    }


def summarize_step(samples: list[Sample], concurrency: int, duration: float) -> dict:
    failures = [sample for sample in samples if sample.error]
    per_path = {}
    for path in sorted({sample.path for sample in samples}):
        path_samples = [sample for sample in samples if sample.path == path]
        path_failures = [sample for sample in path_samples if sample.error]
        per_path[path] = {
            'requests': len(path_samples),
            'latency_ms': latency_summary(path_samples),
            'status_counts': dict(sorted(Counter(str(sample.status) for sample in path_samples).items())),
            'error_counts': dict(sorted(Counter(sample.error for sample in path_failures).items())),
            'error_rate': round(len(path_failures) / len(path_samples), 4),
        }
    return {
        'requests': len(samples),
        'concurrency': concurrency,
        'duration_seconds': round(duration, 3),
        'throughput_requests_per_second': round(len(samples) / duration, 2),
        'latency_ms': latency_summary(samples),
        'status_counts': dict(sorted(Counter(str(sample.status) for sample in samples).items())),
        'error_counts': dict(sorted(Counter(sample.error for sample in failures).items())),
        'error_rate': round(len(failures) / len(samples), 4),
        'per_path': per_path,
    }


def main() -> int:
    args = parse_args()
    paths = args.paths or ['/health/']
    if args.requests < 1 or args.concurrency < 1 or args.timeout <= 0 or args.warmup < 0:
        raise SystemExit('requests/concurrency/timeout must be positive; warmup cannot be negative.')
    cookie = os.getenv(args.cookie_env, '').strip()
    username = os.getenv(args.username_env, '').strip()
    password = os.getenv(args.password_env, '')
    if bool(username) != bool(password):
        raise SystemExit('Both login username and password environment variables are required.')
    if not cookie and username:
        try:
            cookie = login_cookie(
                args.base_url,
                args.login_path,
                username,
                password,
                args.timeout,
                args.insecure,
            )
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
            raise SystemExit(f'Authentication failed: {type(exc).__name__}: {exc}') from None

    if args.concurrency_ramp:
        try:
            concurrency_levels = [
                int(value.strip()) for value in args.concurrency_ramp.split(',') if value.strip()
            ]
        except ValueError:
            raise SystemExit('concurrency-ramp must contain positive integers.') from None
        if not concurrency_levels or any(value < 1 for value in concurrency_levels):
            raise SystemExit('concurrency-ramp must contain positive integers.')
    else:
        concurrency_levels = [args.concurrency]

    for index in range(args.warmup):
        fetch(args.base_url, paths[index % len(paths)], args.timeout, cookie, args.insecure)

    steps = []
    for concurrency in concurrency_levels:
        started = time.perf_counter()
        work = [paths[index % len(paths)] for index in range(args.requests)]
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            samples = list(executor.map(
                lambda path: fetch(args.base_url, path, args.timeout, cookie, args.insecure),
                work,
            ))
        steps.append(summarize_step(samples, concurrency, time.perf_counter() - started))

    report = {
        'base_url': args.base_url,
        'paths': paths,
        'method': 'GET',
        'authenticated_cookie_supplied': bool(cookie),
        'authentication_source': 'login' if username else ('cookie' if cookie else 'anonymous'),
        'steps': steps,
    }
    if len(steps) == 1:
        report.update(steps[0])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    return 1 if any(step['error_rate'] for step in steps) else 0


if __name__ == '__main__':
    raise SystemExit(main())
