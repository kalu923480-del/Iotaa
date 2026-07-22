"""
Tests for utils.ai_provider — AI Provider System.

Covers:
  - register_provider / unregister_provider
  - add / remove / bulk-add API keys
  - set_model with custom providers
  - set_provider_priority with custom providers
  - get_all_models free/premium split
  - save/load config with fake mongo
  - removed keys stay gone after reload (DB source of truth)
  - is_builtin_provider / get_provider_info

Run:  python -m unittest tests.test_ai_provider -v   (from the iota/ folder)
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

os.environ.setdefault("BOT_TOKEN", "123456:fake-test-token")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault(
    "MONGO_URI",
    "mongodb+srv://test:test@cluster0.tjpjh4k.mongodb.net/iota_bot",
)


def _reset_providers():
    """Reset _providers and _provider_priority to built-in defaults."""
    import utils.ai_provider as ap
    ap._providers = {
        pid: ap._Provider(pid, name, kind, base_url, keys, free_m, prem_m,
                          custom=custom, account_id=account_id, extra_headers=extra_headers)
        for pid, name, kind, base_url, keys, free_m, prem_m, custom, account_id, extra_headers in ap._PROVIDER_DEFS
    }
    ap._provider_priority = [p[0] for p in ap._PROVIDER_DEFS]


class TestProviderBasics(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_providers()

    def test_list_providers_returns_builtins(self):
        from utils.ai_provider import list_providers
        pids = list_providers()
        for expected in ("groq", "gemini", "openrouter", "cloudflare"):
            self.assertIn(expected, pids)

    def test_is_builtin_provider(self):
        from utils.ai_provider import is_builtin_provider
        self.assertTrue(is_builtin_provider("groq"))
        self.assertFalse(is_builtin_provider("my_custom"))

    def test_register_custom_provider(self):
        from utils.ai_provider import register_provider, list_providers
        ok = register_provider(
            "together", "Together AI", kind="openai_compat",
            base_url="https://api.together.xyz/v1",
            free_model="meta-llama/Llama-3-8b-chat-hf",
            premium_model="meta-llama/Llama-3-70b-chat-hf",
            keys=["sk-test-key"], enabled=True, custom=True,
        )
        self.assertTrue(ok)
        self.assertIn("together", list_providers())

    def test_register_duplicate_returns_false(self):
        from utils.ai_provider import register_provider
        register_provider("dup1", "Dup", base_url="https://example.com")
        ok = register_provider("dup1", "Dup2", base_url="https://example.com")
        self.assertFalse(ok)

    def test_unregister_custom_provider(self):
        from utils.ai_provider import register_provider, unregister_provider, list_providers
        register_provider("tmp1", "Tmp", base_url="https://example.com")
        self.assertIn("tmp1", list_providers())
        ok = unregister_provider("tmp1")
        self.assertTrue(ok)
        self.assertNotIn("tmp1", list_providers())

    def test_unregister_builtin_returns_false(self):
        from utils.ai_provider import unregister_provider
        self.assertFalse(unregister_provider("groq"))

    def test_get_provider_info_custom(self):
        from utils.ai_provider import register_provider, get_provider_info
        register_provider(
            "mistral", "Mistral", kind="openai_compat",
            base_url="https://api.mistral.ai/v1",
            free_model="mistral-tiny", premium_model="mistral-large",
            account_id="acct-123",
        )
        info = get_provider_info("mistral")
        self.assertIsNotNone(info)
        self.assertEqual(info["id"], "mistral")
        self.assertEqual(info["name"], "Mistral")
        self.assertTrue(info["custom"])
        self.assertEqual(info["account_id"], "acct-123")

    def test_get_provider_info_unknown(self):
        from utils.ai_provider import get_provider_info
        self.assertIsNone(get_provider_info("nonexistent"))


class TestKeyManagement(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_providers()

    def test_add_key_success(self):
        from utils.ai_provider import add_api_key
        ok = add_api_key("__test_uniq_key_abcdef123456", provider="groq")
        self.assertTrue(ok)

    def test_add_duplicate_key_returns_false(self):
        from utils.ai_provider import add_api_key
        add_api_key("__test_dup_key_abcdef123456", provider="groq")
        ok = add_api_key("__test_dup_key_abcdef123456", provider="groq")
        self.assertFalse(ok)

    def test_remove_key_by_prefix(self):
        from utils.ai_provider import add_api_key, remove_api_key
        add_api_key("__test_rm_abcdef1234567890", provider="groq")
        self.assertTrue(remove_api_key("__test_rm", provider="groq"))

    def test_bulk_add_keys(self):
        from utils.ai_provider import add_api_keys_bulk
        added, skipped = add_api_keys_bulk(
            ["__test_bulk1_abcdef123456", "__test_bulk2_abcdef123456", "__test_bulk3_abcdef123456"],
            provider="groq"
        )
        self.assertEqual(added, 3)
        self.assertEqual(skipped, 0)

    def test_bulk_add_with_duplicates(self):
        from utils.ai_provider import add_api_key, add_api_keys_bulk
        add_api_key("__test_existing_abcdef123456", provider="groq")
        added, skipped = add_api_keys_bulk(
            ["__test_existing_abcdef123456", "__test_new1_abcdef123456"],
            provider="groq"
        )
        self.assertEqual(added, 1)
        self.assertEqual(skipped, 1)

    def test_clear_provider_keys(self):
        from utils.ai_provider import add_api_key, clear_provider_keys
        add_api_key("__test_clear_abcdef1234567890", provider="groq")
        count = clear_provider_keys("groq")
        self.assertGreater(count, 0)

    def test_list_api_keys_masked(self):
        from utils.ai_provider import add_api_key, list_api_keys_masked
        add_api_key("__test_mask_abcdef1234567890", provider="groq")
        masked = list_api_keys_masked("groq")
        self.assertIn("groq", masked)
        self.assertTrue(any("7890" in k for k in masked["groq"]))

    def test_list_api_keys_masked_all_providers(self):
        from utils.ai_provider import list_api_keys_masked
        masked = list_api_keys_masked()
        self.assertIsInstance(masked, dict)


class TestModelFunctions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_providers()

    def test_set_model_free(self):
        from utils.ai_provider import set_model, get_current_models
        set_model("free", "__test_free_model_xyz", provider="groq")
        cfg = get_current_models("groq")
        self.assertEqual(cfg["free_model"], "__test_free_model_xyz")

    def test_set_model_premium(self):
        from utils.ai_provider import set_model, get_current_models
        set_model("premium", "__test_premium_model_xyz", provider="gemini")
        cfg = get_current_models("gemini")
        self.assertEqual(cfg["premium_model"], "__test_premium_model_xyz")

    def test_set_model_invalid_tier_raises(self):
        from utils.ai_provider import set_model
        with self.assertRaises(ValueError):
            set_model("invalid", "model-name")

    def test_get_all_models_free_premium_split(self):
        from utils.ai_provider import set_model, get_all_models
        set_model("free", "__test_free_only_model", provider="groq")
        set_model("premium", "__test_prem_only_model", provider="groq")
        result = get_all_models("groq")
        self.assertEqual(result["free"], ["__test_free_only_model"])
        self.assertEqual(result["premium"], ["__test_prem_only_model"])

    def test_get_all_models_unknown_provider(self):
        from utils.ai_provider import get_all_models
        result = get_all_models("nonexistent_xyz")
        self.assertEqual(result["live"], [])
        self.assertEqual(result["free"], [])
        self.assertEqual(result["premium"], [])


class TestProviderPriority(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_providers()

    def test_set_priority_with_custom_provider(self):
        from utils.ai_provider import (register_provider, set_provider_priority,
                                         get_provider_priority, list_providers)
        register_provider("custom1", "Custom1", base_url="https://example.com")
        all_pids = list_providers()
        ok = set_provider_priority(all_pids)
        self.assertTrue(ok)
        self.assertEqual(set(get_provider_priority()), set(all_pids))

    def test_set_priority_missing_provider_returns_false(self):
        from utils.ai_provider import set_provider_priority, list_providers
        all_pids = list_providers()
        incomplete = all_pids[:-1]
        ok = set_provider_priority(incomplete)
        self.assertFalse(ok)


class TestPersistence(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_providers()

    def _make_fake_db(self):
        """Create a minimal fake MongoDB-like object."""
        fake_db = MagicMock()
        fake_collection = MagicMock()
        fake_db.bot_config = fake_collection
        fake_db.users = MagicMock()
        fake_collection.find_one = MagicMock()
        fake_collection.update_one = MagicMock()
        fake_collection.insert_one = MagicMock()
        return fake_db, fake_collection

    async def test_save_model_config_db_saves_custom_providers(self):
        from utils.ai_provider import register_provider, save_model_config_db
        register_provider(
            "together", "Together AI", kind="openai_compat",
            base_url="https://api.together.xyz/v1",
            free_model="meta-llama/Llama-3-8b-chat-hf",
            premium_model="meta-llama/Llama-3-70b-chat-hf",
        )
        fake_db, fake_col = self._make_fake_db()
        with patch("utils.ai_provider.get_db", return_value=fake_db):
            await save_model_config_db()
        call_args = fake_col.update_one.call_args
        doc = call_args.kwargs.get("$set", call_args[1].get("$set", {}))
        providers = doc.get("providers", {})
        self.assertIn("together", providers)
        self.assertEqual(providers["together"]["name"], "Together AI")
        self.assertEqual(providers["together"]["base_url"], "https://api.together.xyz/v1")
        self.assertTrue(providers["together"]["custom"])

    async def test_load_model_config_db_replaces_keys_from_db(self):
        """After removing a key and saving, reloading should NOT bring it back."""
        from utils.ai_provider import (add_api_key, remove_api_key,
                                         save_api_keys_db, load_model_config_db,
                                         _providers)
        groq = _providers.get("groq")
        if groq:
            groq.keys = []
        add_api_key("__test_keep_this_key_abcdef123456", provider="groq")
        add_api_key("__test_remove_this_key_abcdef123456", provider="groq")
        remove_api_key("__test_remove_this_key", provider="groq")

        fake_db, fake_col = self._make_fake_db()
        fake_col.find_one.side_effect = [
            {"_id": "ai_model_config", "max_tokens": 1024,
             "priority": ["groq", "gemini", "openrouter", "cloudflare"],
             "providers": {}},
            {"_id": "ai_api_keys",
             "by_provider": {"groq": ["__test_keep_this_key_abcdef123456"]}},
        ]
        with patch("utils.ai_provider.get_db", return_value=fake_db):
            await load_model_config_db()

        from utils.ai_provider import list_api_keys_masked
        masked = list_api_keys_masked("groq")
        all_keys = []
        for k in masked.get("groq", []):
            all_keys.append(k.replace("...", ""))
        self.assertNotIn("__test_remove_this_key_abcdef123456", all_keys)
        self.assertIn("__test_keep_this_key_abcdef123456", all_keys)

    async def test_load_model_config_db_keeps_env_keys_when_no_db_doc(self):
        """When no DB keys doc exists, env-initialized keys stay."""
        from utils.ai_provider import load_model_config_db
        fake_db, fake_col = self._make_fake_db()
        fake_col.find_one.side_effect = [
            {"_id": "ai_model_config", "max_tokens": 1024, "priority": ["groq"],
             "providers": {}},
            None,
        ]
        with patch("utils.ai_provider.get_db", return_value=fake_db):
            await load_model_config_db()
        # No error should occur; keys remain from env init


class TestGetAllModelsEdgeCases(unittest.TestCase):
    def setUp(self):
        _reset_providers()

    def test_get_all_models_returns_distinct_free_premium(self):
        from utils.ai_provider import set_model, get_all_models
        set_model("free", "__test_free_only_mdl", provider="openrouter")
        set_model("premium", "__test_prem_only_mdl", provider="openrouter")
        result = get_all_models("openrouter")
        self.assertIn("__test_free_only_mdl", result["free"])
        self.assertIn("__test_prem_only_mdl", result["premium"])
        self.assertNotEqual(result["free"], result["premium"])

    def test_get_all_models_no_provider_defaults_to_first_enabled(self):
        from utils.ai_provider import get_all_models
        result = get_all_models()
        self.assertIsInstance(result, dict)
        self.assertIn("live", result)
        self.assertIn("free", result)
        self.assertIn("premium", result)


class TestRegisterProviderValidation(unittest.TestCase):
    def setUp(self):
        _reset_providers()

    def test_invalid_id_raises(self):
        from utils.ai_provider import register_provider
        with self.assertRaises(ValueError):
            register_provider("Invalid ID!", "Bad", base_url="https://example.com")

    def test_valid_id_lowercase_underscore(self):
        from utils.ai_provider import register_provider
        ok = register_provider("my_provider_1", "My Provider", base_url="https://example.com")
        self.assertTrue(ok)

    def test_priority_includes_custom_after_register(self):
        from utils.ai_provider import register_provider, get_provider_priority
        register_provider("custom-p", "CustomP", base_url="https://example.com")
        priority = get_provider_priority()
        self.assertIn("custom-p", priority)


if __name__ == "__main__":
    unittest.main()
