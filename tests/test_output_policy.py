"""Integração: atualização da política sem sobrescrever traduções manuais."""
import argparse
import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbtr
import mbloc
from translator import Backend


class PolicyBackend(Backend):
    name = "argos"

    def __init__(self, fail_lord=False):
        self.calls = []
        self.fail_lord = fail_lord

    def translate(self, text):
        self.calls.append(text)
        if self.fail_lord and text == "lord":
            raise RuntimeError("falha simulada na escolha")
        for original, translated in (("Hello", "Olá"), ("Welcome", "Bem-vindo"),
                                     ("team", "equipa"), ("lord", "senhor"), ("lady", "senhora")):
            text = text.replace(original, translated)
        return text


class OutputPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.mod = self.root / "Modules" / "Test"
        self.source = self.mod / "languages" / "en" / "dialogs.csv"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("a|Hello Swadia {s0}\nb|Hello Swadia {s1}\n", encoding="utf-8")
        self.cfg = {"_dir": self.root, "mod": {"path": str(self.mod)},
                    "engine": {"backend": "argos"}, "cache": {"file": "cache.sqlite"},
                    "glossary": {"terms": {"Swadia": "SuadiaA"}},
                    "revision": {"ptbr_fix": False}}
        self.args = argparse.Namespace(file=None, limit=None)
        self.previous_style = mbloc.TOKEN_FMT
        self.addCleanup(mbloc.set_token_style, "xnx" if self.previous_style == "X{}X" else "math")
        mbloc.set_token_style("math")
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    @property
    def target(self):
        language = self.cfg["mod"].get("target_lang", "pt")
        return self.root / "saida" / "Test" / "languages" / language / "dialogs.csv"

    def rows(self):
        return {entry.key: entry.text for entry in mbloc.read_csv(self.target).entries if entry.key is not None}

    def translate(self, backend=None, expected_code=0):
        backend = backend or PolicyBackend()
        with patch.object(mbtr, "make_backend", return_value=backend):
            self.assertEqual(mbtr.cmd_translate(self.cfg, self.args), expected_code)
        return backend

    def write_manual_output(self, first="Saudação manual {s0}", second="Outra saudação {s1}"):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(f"a|{first}\nb|{second}\n", encoding="utf-8")

    def test_changing_glossary_updates_generated_output(self):
        self.translate()
        self.assertEqual(self.rows()["a"], "Olá SuadiaA {s0}")
        self.cfg["glossary"]["terms"]["Swadia"] = "SuadiaB"
        self.translate()
        self.assertEqual(self.rows(), {"a": "Olá SuadiaB {s0}", "b": "Olá SuadiaB {s1}"})

    def test_replacement_file_content_change_updates_output_at_same_path(self):
        replacements = self.root / "substituicoes.txt"
        self.cfg["revision"]["replacements_file"] = "substituicoes.txt"
        replacements.write_text("Olá = SaudacaoA\n", encoding="utf-8")
        self.translate()
        self.assertEqual(self.rows()["a"], "SaudacaoA SuadiaA {s0}")
        replacements.write_text("Olá = SaudacaoB\n", encoding="utf-8")
        self.translate()
        self.assertEqual(self.rows()["a"], "SaudacaoB SuadiaA {s0}")

    def test_reordering_case_insensitive_replacement_duplicates_updates_output(self):
        replacements = self.root / "substituicoes.txt"
        self.cfg["revision"]["replacements_file"] = "substituicoes.txt"
        replacements.write_text("Olá = SaudacaoA\nolá = SaudacaoB\n", encoding="utf-8")
        self.translate()
        self.assertEqual(self.rows()["a"], "SaudacaoB SuadiaA {s0}")
        replacements.write_text("olá = SaudacaoB\nOlá = SaudacaoA\n", encoding="utf-8")
        self.translate()
        self.assertEqual(self.rows()["a"], "SaudacaoA SuadiaA {s0}")

    def test_every_checkpoint_records_later_generated_rows_for_future_policy_changes(self):
        self.cfg["mod"]["checkpoint_every"] = 1
        self.translate()
        for term in ("SuadiaB", "SuadiaC"):
            with self.subTest(term=term):
                self.cfg["glossary"]["terms"]["Swadia"] = term
                self.translate()
                self.assertEqual(self.rows(), {"a": f"Olá {term} {{s0}}", "b": f"Olá {term} {{s1}}"})

    def test_manual_edit_over_generated_output_survives_two_policy_changes(self):
        self.translate()
        self.write_manual_output(second="Olá SuadiaA {s1}")
        for term in ("SuadiaB", "SuadiaC"):
            with self.subTest(term=term):
                self.cfg["glossary"]["terms"]["Swadia"] = term
                self.translate()
                self.assertEqual(self.rows()["a"], "Saudação manual {s0}")
                self.assertEqual(self.rows()["b"], f"Olá {term} {{s1}}")

    def test_imported_manual_output_survives_two_policy_changes(self):
        self.write_manual_output()
        expected = self.rows()
        self.assertEqual(self.translate().calls, [])
        for term in ("SuadiaB", "SuadiaC"):
            with self.subTest(term=term):
                self.cfg["glossary"]["terms"]["Swadia"] = term
                self.assertEqual(self.translate().calls, [])
                self.assertEqual(self.rows(), expected)

    def test_limit_keeps_later_translations_until_the_next_policy_update_run(self):
        self.translate()
        self.cfg["glossary"]["terms"]["Swadia"] = "SuadiaB"
        self.args.limit = 1
        self.translate()
        self.assertEqual(self.rows(), {"a": "Olá SuadiaB {s0}", "b": "Olá SuadiaA {s1}"})
        self.args.limit = None
        self.translate()
        self.assertEqual(self.rows(), {"a": "Olá SuadiaB {s0}", "b": "Olá SuadiaB {s1}"})

    def test_check_reports_changed_source_with_identical_ids_and_codes(self):
        self.translate()
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 0)
        previous_output = self.target.read_bytes()
        self.source.write_text("a|Welcome Swadia {s0}\nb|Hello Swadia {s1}\n", encoding="utf-8")
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)
        self.assertEqual(self.target.read_bytes(), previous_output)

    def test_check_reports_history_of_incomplete_conditional_translation(self):
        self.cfg["mod"]["checkpoint_every"] = 1
        self.source.write_text("a|Hello {s0}\nb|Hello {lord/lady} {s1}\n", encoding="utf-8")
        self.translate(PolicyBackend(fail_lord=True), expected_code=1)
        self.assertEqual(self.rows()["b"], "Olá {lord/lady} {s1}")
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)

    def test_check_does_not_create_a_missing_cache_or_its_parent_directory(self):
        self.write_manual_output()
        self.cfg["cache"]["file"] = "missing/nested/cache.sqlite"
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 0)
        self.assertFalse((self.root / "missing").exists())

    def test_check_keeps_legacy_cache_bytes_and_schema_unchanged(self):
        self.write_manual_output()
        for has_sources in (False, True):
            with self.subTest(has_sources=has_sources):
                cache = self.root / f"legacy-{has_sources}.sqlite"
                with contextlib.closing(sqlite3.connect(cache)) as db:
                    db.execute("CREATE TABLE t (k TEXT PRIMARY KEY, base TEXT, engine TEXT, out TEXT)")
                    db.execute("INSERT INTO t VALUES ('key', 'base', 'legacy', 'preserve-me')")
                    if has_sources:
                        db.execute("CREATE TABLE output_sources "
                                   "(path TEXT, id TEXT, source TEXT NOT NULL, PRIMARY KEY (path, id))")
                    db.commit()
                before = cache.read_bytes()
                self.cfg["cache"]["file"] = cache.name
                self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 0)
                self.assertEqual(cache.read_bytes(), before)
                with contextlib.closing(sqlite3.connect(cache)) as db:
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertEqual(tables, {"t", "output_sources"} if has_sources else {"t"})
                    if has_sources:
                        self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(output_sources)")],
                                         ["path", "id", "source"])
                    self.assertEqual(db.execute("SELECT out FROM t").fetchone(), ("preserve-me",))

    def test_spanish_target_skips_ptbr_rules_but_keeps_custom_replacements(self):
        self.cfg["mod"]["target_lang"] = "es"
        self.cfg["engine"]["target_code"] = "es"
        self.cfg["revision"].update(ptbr_fix=True, replacements_file="substituicoes.txt")
        (self.root / "substituicoes.txt").write_text("Olá = Saludos\n", encoding="utf-8")
        self.source.write_text("a|Hello team {s0}\n", encoding="utf-8")
        self.translate()
        self.assertEqual(self.rows(), {"a": "Saludos equipa {s0}"})

    def test_spanish_actor_and_director_are_not_changed_to_portuguese(self):
        self.cfg["mod"]["target_lang"] = "es"
        self.cfg["engine"]["target_code"] = "es"
        self.cfg["revision"]["ptbr_fix"] = True
        self.source.write_text("a|The actor knows the director.\n", encoding="utf-8")
        backend = PolicyBackend()
        backend.translate = lambda _: "El actor conoce al director."
        self.translate(backend)
        self.assertEqual(self.rows(), {"a": "El actor conoce al director."})

    def test_explicit_ollama_target_name_controls_revision_language(self):
        self.cfg["engine"].update(backend="ollama", target_code="pt", ollama={"target_name": "Spanish"})
        self.cfg["revision"]["ptbr_fix"] = True
        self.source.write_text("a|The actor knows the director.\n", encoding="utf-8")
        backend = PolicyBackend()
        backend.translate = lambda _: "El actor conoce al director."
        self.translate(backend)
        self.assertEqual(self.rows(), {"a": "El actor conoce al director."})

    def test_pt_br_target_applies_ptbr_rules(self):
        self.cfg["mod"]["target_lang"] = "pt_BR"
        self.cfg["engine"]["target_code"] = "pt_BR"
        self.cfg["revision"]["ptbr_fix"] = True
        self.source.write_text("a|Hello team {s0}\n", encoding="utf-8")
        self.translate()
        self.assertEqual(self.rows(), {"a": "Olá equipe {s0}"})


if __name__ == "__main__":
    unittest.main()
