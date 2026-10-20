import unittest
import time
import httpx

from engine import run_case


class EngineAssertionsTests(unittest.TestCase):
    def setUp(self):
        def handler(request):
            # Ensure a zero-millisecond performance threshold reliably fails.
            time.sleep(0.003)
            return httpx.Response(
                200,
                headers={"content-type": "application/json", "x-test": "yes"},
                json={
                    "id": 1,
                    "name": "Alice",
                    "active": True,
                    "items": [1, 2]
                },
                request=request,
            )

        self.transport = httpx.MockTransport(handler)
        self.client = httpx.Client(transport=self.transport)
        self.base_url = "https://unit.test/"

    def tearDown(self):
        self.client.close()

    def test_body_type_header_and_status_pass(self):
        case = {
            "id": "PASS",
            "description": "all assertions match",
            "request": {"method": "GET", "path": "/users/1"},
            "expected": {
                "status_code": 200,
                "headers": {"Content-Type": "application/json", "x-test": "yes"},
                "body": {
                    "id": 1,
                    "name": {"type": "string"},
                    "active": {"type": "boolean"}
                },
                "max_duration_ms": 5000
            }
        }
        result = run_case(self.client, self.base_url, case)
        self.assertEqual(result["status"], "PASS")

    def test_wrong_body_value_fails(self):
        case = {
            "id": "FAIL",
            "request": {"method": "GET", "path": "/users/1"},
            "expected": {"status_code": 200, "body": {"id": 2}}
        }
        result = run_case(self.client, self.base_url, case)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["assertions"][1]["passed"])

    def test_missing_body_field_fails(self):
        case = {
            "id": "MISSING",
            "request": {"method": "GET", "path": "/users/1"},
            "expected": {"status_code": 200, "body": {"email": {"type": "string"}}}
        }
        result = run_case(self.client, self.base_url, case)
        self.assertEqual(result["status"], "FAIL")

    def test_wrong_header_fails(self):
        case = {
            "id": "HEADER",
            "request": {"method": "GET", "path": "/users/1"},
            "expected": {"status_code": 200, "headers": {"x-test": "no"}}
        }
        result = run_case(self.client, self.base_url, case)
        self.assertEqual(result["status"], "FAIL")

    def test_slow_threshold_fails_when_zero(self):
        case = {
            "id": "SLOW",
            "request": {"method": "GET", "path": "/users/1"},
            "expected": {"status_code": 200, "max_duration_ms": 0}
        }
        result = run_case(self.client, self.base_url, case)
        self.assertEqual(result["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
