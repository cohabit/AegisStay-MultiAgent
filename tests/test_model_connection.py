"""Regression tests for explicit OpenAI-compatible model connectivity."""
from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import hotel_core


class _FakeModels:
    def __init__(self, model_ids: list[str]):
        self.model_ids = model_ids

    def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in self.model_ids])


class _FakeOpenAI:
    def __init__(self, *, api_key: str, base_url: str, timeout: float, max_retries: int):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.models = _FakeModels(["Qwen/Qwen2.5-7B-Instruct"])


class ModelConnectionTests(unittest.TestCase):
    def test_explicit_cloud_connection_clears_offline_switch_and_probes_model(self):
        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = _FakeOpenAI
        with patch.dict(
            os.environ,
            {
                "AEGIS_FORCE_RULES": "1",
                "OPENAI_API_KEY": "old-key",
                "OPENAI_BASE_URL": "https://old.invalid/v1",
                "OPENAI_MODEL": "old-model",
            },
            clear=False,
        ), patch.dict(sys.modules, {"openai": fake_openai}):
            orchestrator, probe = app._connect_cloud_model(
                "new-key", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"
            )

        self.assertNotIn("AEGIS_FORCE_RULES", os.environ)
        self.assertFalse(orchestrator.model.disabled)
        self.assertEqual(orchestrator.model.model, "Qwen/Qwen2.5-7B-Instruct")
        self.assertTrue(probe["ok"])
        self.assertEqual(probe["code"], "ok")

    def test_missing_openai_sdk_is_reported_instead_of_silent_fallback(self):
        with patch.object(hotel_core, "_load_env_or_config", lambda: None), patch.dict(
            os.environ,
            {
                "AEGIS_FORCE_RULES": "0",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://api.siliconflow.cn/v1",
                "OPENAI_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            },
            clear=True,
        ), patch.dict(sys.modules, {"openai": None, "httpx": None}):
            client = hotel_core.OpenAICompatibleClient()

        self.assertEqual(client.initialization_error, "openai_sdk_missing")
        self.assertFalse(client.probe()["ok"])
        self.assertEqual(client.probe()["code"], "openai_sdk_missing")

    def test_missing_openai_sdk_uses_http_compatibility_transport(self):
        fake_httpx = types.ModuleType("httpx")

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": [{"id": "Qwen/Qwen2.5-7B-Instruct"}]}

        fake_httpx.get = lambda *args, **kwargs: Response()
        with patch.object(hotel_core, "_load_env_or_config", lambda: None), patch.dict(
            os.environ,
            {
                "AEGIS_FORCE_RULES": "0",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://api.siliconflow.cn/v1",
                "OPENAI_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            },
            clear=True,
        ), patch.dict(sys.modules, {"openai": None, "httpx": fake_httpx}):
            client = hotel_core.OpenAICompatibleClient()

        probe = client.probe()
        self.assertTrue(probe["ok"])
        self.assertEqual(probe["code"], "ok")
        self.assertEqual(client.transport, "httpx")

    def test_probe_rejects_a_model_that_is_not_listed(self):
        fake_openai = types.ModuleType("openai")

        class DifferentModelOpenAI(_FakeOpenAI):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.models = _FakeModels(["other/model"])

        fake_openai.OpenAI = DifferentModelOpenAI
        with patch.object(hotel_core, "_load_env_or_config", lambda: None), patch.dict(
            os.environ,
            {
                "AEGIS_FORCE_RULES": "0",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://api.siliconflow.cn/v1",
                "OPENAI_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            },
            clear=True,
        ), patch.dict(sys.modules, {"openai": fake_openai}):
            client = hotel_core.OpenAICompatibleClient()

        probe = client.probe()
        self.assertFalse(probe["ok"])
        self.assertEqual(probe["code"], "model_not_listed")


if __name__ == "__main__":
    unittest.main()
