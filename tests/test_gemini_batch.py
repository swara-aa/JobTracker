from __future__ import annotations

import unittest
from unittest.mock import patch

from job_agent.gemini_batch import _wait_for_uploaded_file


class _File:
    def __init__(self, state: str) -> None:
        self.state = state


class _Client:
    def __init__(self, states: list[str]) -> None:
        self.states = iter(states)

    def files(self):  # pragma: no cover - mirrors the SDK attribute shape
        return self

    def get(self, *, name: str) -> _File:
        return _File(next(self.states))


class GeminiBatchTests(unittest.TestCase):
    def test_waits_until_the_uploaded_file_is_active(self) -> None:
        client = _Client(["PROCESSING", "ACTIVE"])
        client.files = client
        with patch("job_agent.gemini_batch.time.sleep") as sleep:
            _wait_for_uploaded_file(client, "files/example")
        sleep.assert_called_once()

    def test_rejects_a_failed_uploaded_file(self) -> None:
        client = _Client(["FAILED"])
        client.files = client
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            _wait_for_uploaded_file(client, "files/example")


if __name__ == "__main__":
    unittest.main()
