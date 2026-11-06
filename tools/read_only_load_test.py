#!/usr/bin/env python3
"""Small dependency-free HTTP baseline tool for warehouse read endpoints.

Only GET requests are supported. Authentication can be supplied through an
environment variable containing a Cookie header, so credentials never appear
in the process command line or generated report.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
import ssl
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


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


def make_opener(insecure: bool):
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return build_opener(NoRedirectHandler(), HTTPSHandler(context=context))


def fetch(base_url: str, path: str, timeout: float, cookie: str, insecure: bool) -> Sample:
    url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
    headers = {
        'Accept': 'text/html,application/json',
        'User-Agent': 'warehouse-read-only-baseline/1.0',
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
    parser.add_argument('--timeout', type=float, default=10.0, help='Per-request timeout seconds.')
    parser.add_argument('--warmup', type=int, default=5, help='Serial warm-up requests.')
    parser.add_argument(
        '--cookie-env',
        default='WAREHOUSE_SESSION_COOKIE',
        help='Environment variable containing the Cookie header.',
    )
    parser.add_argument('--insecure', action='store_true', help='Allow an untrusted test TLS certificate.')
    parser.add_argument('--output', help='Optional JSON report path.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = args.paths or ['/health/']
    if args.requests < 1 or args.concurrency < 1 or args.timeout <= 0 or args.warmup < 0:
        raise SystemExit('requests/concurrency/timeout must be positive; warmup cannot be negative.')
    cookie = os.getenv(args.cookie_env, '').strip()

    for index in range(args.warmup):
        fetch(args.base_url, paths[index % len(paths)], args.timeout, cookie, args.insecure)

    started = time.perf_counter()
    work = [paths[index % len(paths)] for index in range(args.requests)]
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        samples = list(executor.map(
            lambda path: fetch(args.base_url, path, args.timeout, cookie, args.insecure),
            work,
        ))
    duration = time.perf_counter() - started

    elapsed = [sample.elapsed_ms for sample in samples]
    failures = [sample for sample in samples if sample.error]
    report = {
        'base_url': args.base_url,
        'paths': paths,
        'method': 'GET',
        'requests': len(samples),
        'concurrency': args.concurrency,
        'duration_seconds': round(duration, 3),
        'throughput_requests_per_second': round(len(samples) / duration, 2),
        'latency_ms': {
            'min': round(min(elapsed), 2),
            'mean': round(statistics.fmean(elapsed), 2),
            'p50': round(percentile(elapsed, 0.50), 2),
            'p95': round(percentile(elapsed, 0.95), 2),
            'p99': round(percentile(elapsed, 0.99), 2),
            'max': round(max(elapsed), 2),
        },
        'status_counts': dict(sorted(Counter(str(sample.status) for sample in samples).items())),
        'error_counts': dict(sorted(Counter(sample.error for sample in failures).items())),
        'error_rate': round(len(failures) / len(samples), 4),
        'authenticated_cookie_supplied': bool(cookie),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
