from __future__ import annotations

import os
import unittest

from job_agent.web import create_app


class AccessControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_required = os.environ.get("JOBTRACKER_AUTH_REQUIRED")
        self.previous_password = os.environ.get("JOBTRACKER_ACCESS_PASSWORD")
        self.previous_import_token = os.environ.get("JOBTRACKER_EXTENSION_IMPORT_TOKEN")
        os.environ["JOBTRACKER_AUTH_REQUIRED"] = "true"
        os.environ["JOBTRACKER_ACCESS_PASSWORD"] = "test-password"
        os.environ["JOBTRACKER_EXTENSION_IMPORT_TOKEN"] = "test-import-token"
        self.client = create_app().test_client()

    def tearDown(self) -> None:
        if self.previous_required is None:
            os.environ.pop("JOBTRACKER_AUTH_REQUIRED", None)
        else:
            os.environ["JOBTRACKER_AUTH_REQUIRED"] = self.previous_required
        if self.previous_password is None:
            os.environ.pop("JOBTRACKER_ACCESS_PASSWORD", None)
        else:
            os.environ["JOBTRACKER_ACCESS_PASSWORD"] = self.previous_password
        if self.previous_import_token is None:
            os.environ.pop("JOBTRACKER_EXTENSION_IMPORT_TOKEN", None)
        else:
            os.environ["JOBTRACKER_EXTENSION_IMPORT_TOKEN"] = self.previous_import_token

    def test_dashboard_requires_login(self) -> None:
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_health_stays_public(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_correct_password_opens_dashboard(self) -> None:
        response = self.client.post("/login", data={"password": "test-password", "next": "/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_external_next_url_is_rejected(self) -> None:
        response = self.client.post(
            "/login",
            data={"password": "test-password", "next": "https://example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_extension_import_requires_token(self) -> None:
        response = self.client.post("/api/linkedin/import", json=[])

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "A valid JobTracker import token is required.")

    def test_extension_import_accepts_bearer_token(self) -> None:
        response = self.client.post(
            "/api/linkedin/import?defer_enrichment=1",
            json=[],
            headers={"Authorization": "Bearer test-import-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["captured"], 0)

    def test_extension_import_accepts_header_token(self) -> None:
        response = self.client.post(
            "/api/linkedin/finalize-collection",
            json={"links": []},
            headers={"X-JobTracker-Import-Token": "test-import-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["scheduled"])
