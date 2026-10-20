# API Test Pilot - Minimal Test Engine

API Test Pilot is a lightweight, declarative HTTP API test engine written in Python. It reads a JSON test suite, sends real HTTP requests, evaluates explicit assertions, and prints a structured JSON report.

> **Current scope:** this repository contains the deterministic test engine. It does not yet automatically import a Swagger/OpenAPI URL or generate tests with AI. Those are planned platform capabilities, not features of this engine version.

## Features

- Supports `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, and `OPTIONS`.
- Configurable request headers, query parameters, and JSON request bodies.
- Checks expected HTTP status codes.
- Checks response headers, with case-insensitive header names and values. `Content-Type` comparisons ignore optional parameters such as `charset=utf-8`.
- Checks JSON response bodies using recursive, partial object matching.
- Checks JSON value types: `object`, `array`, `string`, `integer`, `number`, `boolean`, and `null`.
- Checks response duration against a maximum in milliseconds.
- Reports each assertion, expected and actual values, mismatch paths, duration, and request/configuration errors.
- Writes reports to a JSON file when requested.
- Uses a bounded request timeout and disables redirects.

## Requirements

- Python 3.10 or newer
- Network access to the API being tested
- Permission to test the target API
- `httpx`, installed from `requirements.txt`

## Install and run

Run these commands from the repository root.

### Arch Linux / Omarchy

Install Python and pip if needed:

```bash
sudo pacman -S python python-pip
```

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If your shell says `python: No module named pip`, install `python-pip` with `pacman` and repeat the installation commands.

### Debian / Ubuntu

Install Python, venv support, and pip:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

Then create the environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Run the example suite

Make sure `example.json` exists in the repository root and points to an API you are authorized to test:

```bash
python engine.py example.json
```

Write the report to a file:

```bash
python engine.py example.json --output report.json
```

Set the per-request timeout to five seconds:

```bash
python engine.py example.json --timeout 5
```

You can combine options:

```bash
python engine.py example.json --timeout 5 --output report.json
```

The default suite path is `example.json`; therefore, `python engine.py` runs that file if it exists. Activate `.venv` again whenever you open a new terminal:

```bash
source .venv/bin/activate
```

## Configure a test suite

A suite is a JSON object with an `api.base_url` and a non-empty `test_cases` array. Each test case has a `request` and an `expected` object.

Example `example.json` structure:

```json
{
  "api": {
    "base_url": "https://api.example.com"
  },
  "test_cases": [
    {
      "id": "GET001",
      "description": "Verify that retrieving a user returns HTTP 200 and JSON",
      "request": {
        "method": "GET",
        "path": "/api/users/1",
        "headers": {
          "Accept": "application/json"
        },
        "params": {
          "include": "profile"
        }
      },
      "expected": {
        "status_code": 200,
        "headers": {
          "Content-Type": "application/json"
        },
        "body": {
          "id": 1,
          "name": {
            "type": "string"
          }
        },
        "max_duration_ms": 3000
      }
    }
  ]
}
```

Replace `https://api.example.com` and the example path with a real API base URL and endpoint. These values are placeholders, not a live test target.

### Request fields

| Field | Required? | Description |
| --- | --- | --- |
| `request.method` | Yes | One of `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`. |
| `request.path` | Yes | Relative endpoint path under `api.base_url`, such as `/api/users/1`. Do not use a full URL here. |
| `request.headers` | No | Object containing string header names and values. |
| `request.params` | No | Query-parameter object. |
| `request.query` | No | Alternative name for query parameters. Use `params` **or** `query`, not both. |
| `request.json` | No | JSON request body, useful for methods such as `POST`, `PUT`, and `PATCH`. |

### Expected assertion fields

| Field | Required? | Description |
| --- | --- | --- |
| `expected.status_code` | Yes | Expected HTTP status code from 100 to 599. |
| `expected.headers` | No | Response headers that must match. Header names and values are compared case-insensitively. For `Content-Type`, media types are compared while ignoring parameters such as `charset=utf-8`. |
| `expected.body` | No | Expected JSON structure or values. |
| `expected.max_duration_ms` | No | Maximum acceptable request duration in milliseconds. The measured duration must be less than or equal to this value. |

## Response body assertions

### Check selected fields

Expected JSON objects are **partial matches**. The engine checks each expected key and value, but additional keys in the actual response are allowed.

For example, this expectation:

```json
{
  "body": {
    "project": "api-test-pilot"
  }
}
```

passes if the response contains `"project": "api-test-pilot"`, even when it has other fields too.

### Check types

Use an object containing only the `type` key to check a JSON value's type:

```json
{
  "body": {
    "id": {
      "type": "integer"
    },
    "active": {
      "type": "boolean"
    },
    "tags": {
      "type": "array"
    },
    "metadata": {
      "type": "object"
    }
  }
}
```

Supported types are `object`, `array`, `string`, `integer`, `number`, `boolean`, and `null`. An integer also satisfies the `number` type. Booleans are not treated as numbers.

### Check arrays

Arrays are compared by length and by the expected values at each index. Unlike object matching, the array length must match exactly.

For example, if the response contains:

```json
{
  "project": ["api-test-pilot"]
}
```

the matching expectation is:

```json
{
  "body": {
    "project": ["api-test-pilot"]
  }
}
```

Expectations must describe the behavior you intend to verify. Do not derive expected values from the response after the request, because that would make the assertion meaningless.

## Understand the report

The report contains a summary and a result for each test case:

- `PASS`: all configured assertions passed.
- `FAIL`: the request completed, but one or more assertions did not pass.
- `ERROR`: the test could not be evaluated, for example because the suite is invalid, the request timed out, or a network error occurred.

A report may look like this:

```json
{
  "summary": {
    "total": 1,
    "passed": 1,
    "failed": 0,
    "errors": 0
  },
  "results": [
    {
      "test_id": "GET001",
      "description": "Verify that retrieving a user returns HTTP 200 and JSON",
      "status": "PASS",
      "expected_status": 200,
      "actual_status": 200,
      "duration_ms": 84,
      "assertions": [
        {
          "name": "status_code",
          "passed": true,
          "expected": 200,
          "actual": 200
        }
      ],
      "error": null
    }
  ]
}
```

The values above are illustrative. Actual status codes, durations, and assertion details depend on the API response.

The process exits with code `0` only when every test passes. It exits with code `1` if any test fails or errors, and code `2` for command-line or suite input errors. This is useful for CI pipelines.

## Run the unit tests

If the repository contains a `tests/` directory with Python `unittest` tests, run:

```bash
python -m unittest discover -s tests -v
```

If `tests/` does not exist yet, this command will not run the project's unit tests. Add the test files before relying on it.

## Troubleshooting

- **`No module named pip`:** install pip using your OS package manager, then recreate or reactivate the virtual environment.
- **`No module named httpx`:** activate `.venv` and run `python -m pip install -r requirements.txt`.
- **`Input file not found`:** run the command from the repository root or pass the correct suite path.
- **`Invalid JSON`:** check commas, quotes, braces, and the line/column reported by the engine.
- **`ERROR` / request timeout:** check DNS, network access, the base URL, and the `--timeout` value.
- **`FAIL` for a body field:** compare the expected JSON type and value with the actual response. For example, `"project": "api-test-pilot"` is a string, while `"project": ["api-test-pilot"]` is an array.

## Safety

- Only test APIs you own or have explicit permission to test.
- This engine executes declarative JSON test cases; it does not execute generated Python or shell commands.
- The base URL must be an absolute `http://` or `https://` URL. Credentials must not be embedded in the base URL.
- Individual test paths must be relative paths. Absolute URLs and protocol-relative paths are rejected.
- HTTP redirects are disabled.
- **Before exposing the engine as a network-accessible service**, enforce a strict target-host allowlist and block loopback, private, link-local, and cloud metadata addresses at the network layer to mitigate server-side request forgery (SSRF). Add rate and concurrency limits as well.
- Use a staging environment or disposable test data for tests that modify resources.

## Current limitations

- The engine consumes a manually written JSON test suite; it does not currently parse Swagger/OpenAPI specifications automatically.
- AI-based test generation and AI failure explanations are not implemented in this minimal engine.
- Authentication flows, environment-variable substitution, test setup/teardown, concurrency, queues, worker recovery, and a web UI are not included in this version.
