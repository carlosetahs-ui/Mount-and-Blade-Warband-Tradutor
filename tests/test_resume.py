"""Regressões de pré-validação e retomada depois de uma atualização do mod."""
import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbtr
import mbloc
from translator import Backend, Cache, OllamaBackend, Translator


class RecordingBackend(Backend):
    name = "stub"

    def __init__(self):
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return text.replace("Hello", "Olá").replace("Welcome", "Bem-vindo").replace("Wait", "Espere")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.mod = self.root / "Modules" / "Test"
        self.src = self.mod / "languages" / "en"
        self.src.mkdir(parents=True)
        self.source = self.src / "dialogs.csv"
        self.source.write_text("a|Hello {s0}\nb|Wait {s1}\n", encoding="utf-8")
        self.cfg = {"_dir": self.root, "mod": {"path": str(self.mod)},
                    "cache": {"file": "cache.sqlite"}, "revision": {"ptbr_fix": False}}
        self.target = self.root / "saida" / "Test" / "languages" / "pt" / "dialogs.csv"
        self.args = argparse.Namespace(file=None, limit=None)
        self.addCleanup(mbloc.set_token_style, "math")
        mbloc.set_token_style("math")
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def run_translation(self, backend=None):
        backend = backend or RecordingBackend()
        with patch.object(mbtr, "make_backend", return_value=backend):
            self.assertEqual(mbtr.cmd_translate(self.cfg, self.args), 0)
        return backend

    def test_resume_retains_translations_for_unchanged_source(self):
        self.run_translation()
        self.assertEqual(self.run_translation().calls, [])

    def test_changed_text_with_same_id_and_codes_is_retranslated(self):
        self.run_translation()
        self.source.write_text("a|Welcome {s0}\nb|Wait {s1}\n", encoding="utf-8")
        backend = self.run_translation()
        self.assertEqual(backend.calls, ["Welcome ⟦0⟧"])
        self.assertEqual(mbloc.read_csv(self.target).entries[0].text, "Bem-vindo {s0}")

    def test_limited_update_does_not_retain_stale_translations_in_later_rows(self):
        self.run_translation()
        self.source.write_text("a|Welcome {s0}\nb|Welcome {s1}\n", encoding="utf-8")
        self.args.limit = 1
        self.run_translation()
        self.assertEqual(mbloc.read_csv(self.target).entries[1].text, "Welcome {s1}")
        self.args.limit = None
        self.run_translation()
        self.assertEqual(mbloc.read_csv(self.target).entries[1].text, "Bem-vindo {s1}")

    def test_existing_manual_translation_without_history_is_preserved(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("a|Saudações {s0}\nb|Aguarde {s1}\n", encoding="utf-8")
        self.assertEqual(self.run_translation().calls, [])
        self.assertEqual(mbloc.read_csv(self.target).entries[0].text, "Saudações {s0}")

    def test_changing_target_code_invalidates_existing_output(self):
        self.run_translation()
        self.cfg["engine"] = {"target_code": "fr"}
        backend = RecordingBackend()
        backend.translate = lambda text: (backend.calls.append(text) or text.replace("Hello", "Bonjour"))
        self.run_translation(backend)
        self.assertTrue(backend.calls)
        self.assertEqual(mbloc.read_csv(self.target).entries[0].text, "Bonjour {s0}")

    def test_all_source_files_are_validated_before_backend_or_output_changes(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("tradução anterior", encoding="utf-8")
        (self.src / "z_invalid.csv").write_text("x|Hello\nx|Wait\n", encoding="utf-8")
        with patch.object(mbtr, "make_backend") as backend:
            with self.assertRaisesRegex(ValueError, "IDs vazios ou repetidos"):
                mbtr.cmd_translate(self.cfg, self.args)
        backend.assert_not_called()
        self.assertEqual(self.target.read_text(encoding="utf-8"), "tradução anterior")
        self.assertFalse((self.root / "cache.sqlite").exists())

    def test_failed_output_write_does_not_advance_source_history(self):
        self.run_translation()
        with Cache(self.root / "cache.sqlite") as cache:
            old_sources = cache.output_sources(self.target)
        original = self.target.read_bytes()
        self.source.write_text("a|Welcome {s0}\nb|Wait {s1}\n", encoding="utf-8")
        with patch.object(mbtr, "make_backend", return_value=RecordingBackend()), \
                patch.object(mbloc, "write_csv", side_effect=PermissionError("arquivo em uso")):
            with self.assertRaises(PermissionError):
                mbtr.cmd_translate(self.cfg, self.args)
        self.assertEqual(self.target.read_bytes(), original)
        with Cache(self.root / "cache.sqlite") as cache:
            self.assertEqual(cache.output_sources(self.target), old_sources)

    def test_failed_branch_is_retried_even_after_an_earlier_checkpoint(self):
        self.cfg["mod"]["checkpoint_every"] = 1
        self.source.write_text("a|Wait {s0}\nb|Hello {lord/lady}\n", encoding="utf-8")
        failing = RecordingBackend()
        def fail_branch(text):
            if text == "lord":
                raise RuntimeError("serviço indisponível")
            return text.replace("Hello", "Olá").replace("Wait", "Espere")
        failing.translate = fail_branch
        with patch.object(mbtr, "make_backend", return_value=failing):
            self.assertEqual(mbtr.cmd_translate(self.cfg, self.args), 1)
        self.assertEqual(mbloc.read_csv(self.target).entries[1].text, "Olá {lord/lady}")
        repaired = RecordingBackend()
        repaired.translate = lambda text: text.replace("lord", "senhor").replace("lady", "senhora")
        self.run_translation(repaired)
        self.assertEqual(mbloc.read_csv(self.target).entries[1].text, "Olá {senhor/senhora}")


class OllamaLanguageTests(unittest.TestCase):
    def test_requested_target_language_is_used_in_prompt(self):
        for target, expected in (("pt", "Brazilian Portuguese"), ("fr", "fr"), ("de", "de")):
            with self.subTest(target=target):
                cfg = {"mod": {"path": "Mod", "target_lang": target}, "engine": {"backend": "ollama"}}
                self.assertEqual(mbtr.make_backend(cfg, {}).dst, expected)

    def test_explicit_target_name_is_preserved(self):
        cfg = {"mod": {"path": "Mod"}, "engine": {"backend": "ollama", "target_code": "fr",
                                                   "ollama": {"target_name": "French"}}}
        self.assertEqual(mbtr.make_backend(cfg, {}).dst, "French")

    def test_strict_cache_distinguishes_ollama_servers(self):
        with tempfile.TemporaryDirectory() as directory, Cache(Path(directory) / "cache.sqlite") as cache:
            first = OllamaBackend(url="http://localhost:11434")
            second = OllamaBackend(url="http://localhost:11435")
            with patch.object(first, "translate", return_value="Olá"):
                self.assertEqual(Translator(first, cache, reuse_cache=False).translate("Hello"), "Olá")
            with patch.object(second, "translate", return_value="Oi") as translate:
                self.assertEqual(Translator(second, cache, reuse_cache=False).translate("Hello"), "Oi")
            translate.assert_called_once()

    def test_cache_distinguishes_explicit_prompt_languages(self):
        with tempfile.TemporaryDirectory() as directory, Cache(Path(directory) / "cache.sqlite") as cache:
            portuguese = OllamaBackend()
            french = OllamaBackend(dst="French")
            with patch.object(portuguese, "translate", return_value="Olá"):
                Translator(portuguese, cache).translate("Hello")
            with patch.object(french, "translate", return_value="Bonjour") as translate:
                self.assertEqual(Translator(french, cache).translate("Hello"), "Bonjour")
            translate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
