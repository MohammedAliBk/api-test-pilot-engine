#!/usr/bin/env python3
"""Minimal declarative HTTP API test engine for API Test Pilot."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx


ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class InputError(ValueError):
    pass


def load_suite(path: Path) -> dict:
    try:
        suite = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"Input file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc

    if not isinstance(suite, dict):
        raise InputError("The JSON root must be an object.")

    api = suite.get("api")
    cases = suite.get("test_cases")
    if not isinstance(api, dict) or not isinstance(api.get("base_url"), str):
        raise InputError('Required field missing: api.base_url')
    if not isinstance(cases, list) or not cases:
        raise InputError("test_cases must be a non-empty array.")

    base_url = api["base_url"].strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InputError("api.base_url must be an absolute http:// or https:// URL.")
    if parsed.username or parsed.password:
        raise InputError("Credentials must not be embedded in api.base_url.")

    api["base_url"] = base_url.rstrip("/") + "/"
    return suite


def build_url(base_url: str, path: str) -> str:
    if not isinstance(path, str) or not path:
        raise InputError("Each test case needs a non-empty request.path.")
    # Keep paths relative to the configured base URL; reject absolute URLs and
    # protocol-relative paths so a generated test cannot change the target host.
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or path.startswith("//"):
        raise InputError("request.path must be a relative path, not a full URL.")
    return urljoin(base_url, path.lstrip("/"))


def run_case(client: httpx.Client, base_url: str, case: dict) -> dict:
    test_id = case.get("id", "UNKNOWN")
    description = case.get("description", "")
    request = case.get("request")
    expected = case.get("expected")

    result = {
        "test_id": test_id,
        "description": description,
        "status": "ERROR",
        "expected_status": None,
        "actual_status": None,
        "duration_ms": 0,
        "error": None,
    }

    if not isinstance(request, dict) or not isinstance(expected, dict):
        result["error"] = "Each test case requires request and expected objects."
        return result

    method = str(request.get("method", "")).upper()
    if method not in ALLOWED_METHODS:
        result["error"] = f"Unsupported HTTP method: {method or '(missing)'}"
        return result

    expected_status = expected.get("status_code")
    if not isinstance(expected_status, int) or not 100 <= expected_status <= 599:
        result["error"] = "expected.status_code must be an HTTP status integer (100-599)."
        return result

    try:
        url = build_url(base_url, request.get("path"))
    except InputError as exc:
        result["error"] = str(exc)
        return result

    result["expected_status"] = expected_status

    # Only allow a small, explicit request shape. AI-generated arbitrary code is
    # never executed by this engine.
    kwargs = {}
    if "headers" in request:
        if not isinstance(request["headers"], dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in request["headers"].items()
        ):
            result["error"] = "request.headers must be an object of string values."
            return result
        kwargs["headers"] = request["headers"]

    if "params" in request:
        if not isinstance(request["params"], dict):
            result["error"] = "request.params must be an object."
            return result
        kwargs["params"] = request["params"]

    if "json" in request:
        kwargs["json"] = request["json"]

    started = time.perf_counter()
    try:
        response = client.request(method, url, **kwargs)
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        result["actual_status"] = response.status_code
        result["status"] = "PASS" if response.status_code == expected_status else "FAIL"
    except httpx.TimeoutException:
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        result["error"] = "Request timed out."
        result["status"] = "ERROR"
    except httpx.RequestError as exc:
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        result["error"] = f"HTTP request failed: {type(exc).__name__}"
        result["status"] = "ERROR"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run API Test Pilot JSON test cases.")
    parser.add_argument("suite", nargs="?", default="example.json", help="Path to test suite JSON")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    parser.add_argument("--output", help="Optional path to write the report JSON")
    args = parser.parse_args()

    if args.timeout <= 0:
        print("ERROR: --timeout must be greater than zero.", file=sys.stderr)
        return 2

    try:
        suite = load_suite(Path(args.suite))
    except InputError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    base_url = suite["api"]["base_url"]
    cases = suite["test_cases"]
    results = []

    # Redirects are disabled so a target cannot silently redirect the engine
    # to a different host. Only test APIs you own or are explicitly authorized
    # to test. For production, enforce a host allowlist and block private IPs
    # at the network/container level as well.
    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        for case in cases:
            if not isinstance(case, dict):
                results.append({
                    "test_id": "UNKNOWN",
                    "status": "ERROR",
                    "expected_status": None,
                    "actual_status": None,
                    "duration_ms": 0,
                    "error": "Each test_cases item must be an object.",
                })
            else:
                results.append(run_case(client, base_url, case))

    summary = {
        "summary": {
            "total": len(results),
            "passed": sum(r["status"] == "PASS" for r in results),
            "failed": sum(r["status"] == "FAIL" for r in results),
            "errors": sum(r["status"] == "ERROR" for r in results),
        },
        "results": results,
    }
    output = json.dumps(summary, indent=2, ensure_ascii=False)
    print(output)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")

    return 0 if summary["summary"]["failed"] == 0 and summary["summary"]["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
