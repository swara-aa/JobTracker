from __future__ import annotations

import unittest

from job_agent.web import create_app


class HealthCheckTests(unittest.TestCase):
    def test_health_endpoint_reports_service_status(self) -> None:
        response = create_app().test_client().get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["service"], "jobtracker")


if __name__ == "__main__":
    unittest.main()
