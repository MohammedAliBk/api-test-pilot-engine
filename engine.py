#!/usr/bin/env python3
"""Declarative HTTP API test engine for API Test Pilot."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx


ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
ALLOWED_BODY_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


class InputError(ValueError):
    """Raised when the test suite configuration is invalid."""


def load_suite(path: Path) -> dict:
    try:
        suite = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"Input file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(suite, dict):
        raise InputError("The JSON root must be an object.")

    api = suite.get("api")
    cases = suite.get("test_cases")
    if not isinstance(api, dict) or not isinstance(api.get("base_url"), str):
        raise InputError("Required field missing: api.base_url")
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
    # Keep paths under the configured base URL; reject absolute URLs and
    # protocol-relative paths so a test cannot change the target host.
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or path.startswith("//"):
        raise InputError("request.path must be a relative path, not a full URL.")
    return urljoin(base_url, path.lstrip("/"))


def json_type_name(value) -> str:
    """Return a JSON-oriented type name, distinguishing booleans from integers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate_body_expectation(spec, path: str = "expected.body") -> None:
    """Validate the supported declarative response-body assertion syntax."""
    if isinstance(spec, dict):
        if set(spec.keys()) == {"type"}:
            if spec["type"] not in ALLOWED_BODY_TYPES:
                raise InputError(
                    f"{path}.type must be one of: {', '.join(sorted(ALLOWED_BODY_TYPES))}."
                )
            return
        for key, value in spec.items():
            if not isinstance(key, str):
                raise InputError(f"{path} object keys must be strings.")
            validate_body_expectation(value, f"{path}.{key}")
    elif isinstance(spec, list):
        for index, value in enumerate(spec):
            validate_body_expectation(value, f"{path}[{index}]")


def compare_body(actual, expected, path: str = "$") -> list[dict]:
    """Compare expected JSON recursively. Objects are partial matches; arrays are exact."""
    mismatches = []

    if isinstance(expected, dict) and set(expected.keys()) == {"type"}:
        wanted_type = expected["type"]
        actual_type = json_type_name(actual)
        type_matches = actual_type == wanted_type
        # In JSON Schema, an integer is also a number, but booleans are not.
        if wanted_type == "number" and isinstance(actual, (int, float)) and not isinstance(actual, bool):
            type_matches = True
        if not type_matches:
            mismatches.append({
                "path": path,
                "expected": {"type": wanted_type},
                "actual": actual_type,
                "message": f"Expected type {wanted_type}, got {actual_type}.",
            })
        return mismatches

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{
                "path": path,
                "expected": "object",
                "actual": json_type_name(actual),
                "message": "Expected an object.",
            }]
        for key, expected_value in expected.items():
            child_path = f"{path}.{key}"
            if key not in actual:
                mismatches.append({
                    "path": child_path,
                    "expected": expected_value,
                    "actual": "<missing>",
                    "message": "Required response field is missing.",
                })
            else:
                mismatches.extend(compare_body(actual[key], expected_value, child_path))
        return mismatches

    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [{
                "path": path,
                "expected": "array",
                "actual": json_type_name(actual),
                "message": "Expected an array.",
            }]
        if len(actual) != len(expected):
            mismatches.append({
                "path": path,
                "expected": f"array of length {len(expected)}",
                "actual": f"array of length {len(actual)}",
                "message": "Array length does not match.",
            })
        for index, expected_value in enumerate(expected[:len(actual)]):
            mismatches.extend(compare_body(actual[index], expected_value, f"{path}[{index}]"))
        return mismatches

    # Python considers True == 1; JSON booleans and numbers must not be conflated.
    if type(actual) is not type(expected) or actual != expected:
        mismatches.append({
            "path": path,
            "expected": expected,
            "actual": actual,
            "message": "Value does not match.",
        })
    return mismatches


def validate_headers(value, field_name: str) -> str | None:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        return f"{field_name} must be an object of string values."
    return None


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
        "assertions": [],
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
    if (
        not isinstance(expected_status, int)
        or isinstance(expected_status, bool)
        or not 100 <= expected_status <= 599
    ):
        result["error"] = "expected.status_code must be an HTTP status integer (100-599)."
        return result

    try:
        url = build_url(base_url, request.get("path"))
    except InputError as exc:
        result["error"] = str(exc)
        return result

    result["expected_status"] = expected_status

    kwargs = {}
    if "headers" in request:
        error = validate_headers(request["headers"], "request.headers")
        if error:
            result["error"] = error
            return result
        kwargs["headers"] = request["headers"]

    # Accept both "params" and "query" as names for query parameters.
    if "params" in request and "query" in request:
        result["error"] = "Use only one of request.params or request.query, not both."
        return result
    query_params = request.get("params", request.get("query"))
    if query_params is not None:
        if not isinstance(query_params, dict):
            result["error"] = "request.params/request.query must be an object."
            return result
        kwargs["params"] = query_params

    if "json" in request:
        kwargs["json"] = request["json"]

    started = time.perf_counter()
    try:
        response = client.request(method, url, **kwargs)
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        result["actual_status"] = response.status_code

        status_passed = response.status_code == expected_status
        result["assertions"].append({
            "name": "status_code",
            "passed": status_passed,
            "expected": expected_status,
            "actual": response.status_code,
        })

        if "headers" in expected:
            error = validate_headers(expected["headers"], "expected.headers")
            if error:
                result["status"] = "ERROR"
                result["error"] = error
                return result

            actual_headers = {
                key.lower(): value
                for key, value in response.headers.items()
            }

            for header_name, expected_value in expected["headers"].items():
                actual_value = actual_headers.get(header_name.lower())

                if actual_value is None:
                    passed = False
                elif header_name.lower() == "content-type":
                    # Compare the media type, ignoring optional parameters such as charset.
                    actual_media_type = actual_value.split(";", 1)[0].strip().lower()
                    expected_media_type = expected_value.split(";", 1)[0].strip().lower()
                    passed = actual_media_type == expected_media_type
                else:
                    passed = actual_value.lower() == expected_value.lower()

                result["assertions"].append({
                    "name": f"headers.{header_name}",
                    "passed": passed,
                    "expected": expected_value,
                    "actual": actual_value,
                })

        if "body" in expected:
            try:
                expected_body = expected["body"]
                validate_body_expectation(expected_body)
            except InputError as exc:
                result["status"] = "ERROR"
                result["error"] = str(exc)
                return result

            try:
                actual_body = response.json()
            except (ValueError, json.JSONDecodeError):
                result["assertions"].append({
                    "name": "body",
                    "passed": False,
                    "expected": expected_body,
                    "actual": "<non-JSON response>",
                    "message": "Response body is not valid JSON.",
                })
            else:
                mismatches = compare_body(actual_body, expected_body)
                result["assertions"].append({
                    "name": "body",
                    "passed": not mismatches,
                    "expected": expected_body,
                    "actual": actual_body,
                    "mismatches": mismatches,
                })

        if "max_duration_ms" in expected:
            limit = expected["max_duration_ms"]
            if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit < 0:
                result["status"] = "ERROR"
                result["error"] = "expected.max_duration_ms must be a non-negative number."
                return result
            duration_passed = result["duration_ms"] <= limit
            result["assertions"].append({
                "name": "max_duration_ms",
                "passed": duration_passed,
                "expected": limit,
                "actual": result["duration_ms"],
            })

        result["status"] = (
            "PASS" if all(assertion["passed"] for assertion in result["assertions"]) else "FAIL"
        )
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
    # to test. Before exposing this as a service, enforce a host allowlist and
    # network-level blocking of private, loopback, link-local, and metadata IPs.
    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        for case in cases:
            if not isinstance(case, dict):
                results.append({
                    "test_id": "UNKNOWN",
                    "description": "",
                    "status": "ERROR",
                    "expected_status": None,
                    "actual_status": None,
                    "duration_ms": 0,
                    "assertions": [],
                    "error": "Each test_cases item must be an object.",
                })
            else:
                results.append(run_case(client, base_url, case))

    report = {
        "summary": {
            "total": len(results),
            "passed": sum(item["status"] == "PASS" for item in results),
            "failed": sum(item["status"] == "FAIL" for item in results),
            "errors": sum(item["status"] == "ERROR" for item in results),
        },
        "results": results,
    }
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")

    return 0 if report["summary"]["failed"] == 0 and report["summary"]["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
