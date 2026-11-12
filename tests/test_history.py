"""Migração e recuperação do histórico após falhas entre CSV e SQLite."""
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
from translator import Backend, Cache, Translator


class HistoryBackend(Backend):
    name = "stub"

    def translate(self, text):
        return text.replace("Hello", "Olá").replace("Welcome", "Bem-vindo")


class CacheMigrationTests(unittest.TestCase):
    def test_migration_preserves_legacy_cache_and_does_not_claim_manual_output(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "old.sqlite"
            output = Path(directory) / "dialogs.csv"
            with contextlib.closing(sqlite3.connect(cache_path)) as db:
                db.execute("CREATE TABLE t (k TEXT PRIMARY KEY, base TEXT, engine TEXT, out TEXT)")
                db.execute("INSERT INTO t VALUES ('key', 'base', 'legacy', 'translation')")
                db.execute("CREATE TABLE output_sources "
                           "(path TEXT, id TEXT, source TEXT NOT NULL, PRIMARY KEY (path, id))")
                db.execute("INSERT INTO output_sources VALUES (?, 'a', 'source-hash')", (str(output.resolve()),))
                db.commit()
            with Cache(cache_path) as cache:
                self.assertEqual(cache.output_sources(output), {"a": "source-hash"})
                record = cache.output_records(output)["a"]
                self.assertIsNone(record.policy)
                self.assertIsNone(record.generated_hash)
                self.assertEqual(cache.db.execute("SELECT out FROM t WHERE k='key'").fetchone(), ("translation",))
                # A API anterior continua utilizável após adicionar as colunas.
                cache.remember_sources(output, {"b": "new-source"})
            self.assertEqual(Cache.read_output_records(cache_path, output)["b"].source, "new-source")


class HistoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "Mod" / "languages" / "en" / "dialogs.csv"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("a|Hello Swadia {s0}\nb|Welcome Swadia {s1}\n", encoding="utf-8")
        self.target = self.root / "saida" / "Mod" / "languages" / "pt" / "dialogs.csv"
        self.cache_path = self.root / "cache.sqlite"
        self.cfg = {"_dir": self.root, "mod": {"path": str(self.root / "Mod"), "checkpoint_every": 1},
                    "glossary": {"terms": {"Swadia": "SuadiaA"}}, "revision": {"ptbr_fix": False}}
        self.args = argparse.Namespace(file=None, limit=None)
        previous = "xnx" if mbloc.TOKEN_FMT == "X{}X" else "math"
        self.addCleanup(mbloc.set_token_style, previous)
        mbloc.set_token_style("math")
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def translate(self):
        with patch.object(mbtr, "make_backend", return_value=HistoryBackend()):
            return mbtr.cmd_translate(self.cfg, self.args)

    def rows(self):
        return {entry.key: entry.text for entry in mbloc.read_csv(self.target).entries if entry.key}

    def test_interrupted_policy_update_keeps_previous_translations_and_resumes(self):
        self.assertEqual(self.translate(), 0)
        self.cfg["glossary"]["terms"]["Swadia"] = "SuadiaB"
        original = Translator.translate
        count = 0
        def interrupt(translator, text):
            nonlocal count
            count += 1
            if count == 2:
                raise KeyboardInterrupt
            return original(translator, text)
        with patch.object(Translator, "translate", new=interrupt):
            self.assertEqual(self.translate(), 130)
        self.assertEqual(self.rows(), {"a": "Olá SuadiaB {s0}", "b": "Bem-vindo SuadiaA {s1}"})
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)
        self.assertEqual(self.translate(), 0)
        self.assertEqual(self.rows()["b"], "Bem-vindo SuadiaB {s1}")
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 0)

    def test_sqlite_failure_after_csv_write_does_not_claim_new_history(self):
        self.assertEqual(self.translate(), 0)
        before = Cache.read_output_records(self.cache_path, self.target)
        self.cfg["glossary"]["terms"]["Swadia"] = "SuadiaB"
        with patch.object(Cache, "remember_output", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.translate()
        self.assertEqual(self.rows(), {"a": "Olá SuadiaB {s0}", "b": "Bem-vindo SuadiaA {s1}"})
        self.assertEqual(Cache.read_output_records(self.cache_path, self.target), before)
        # A saída que diverge do último registro é conservada como uma edição
        # desconhecida; o restante do arquivo continua podendo ser atualizado.
        self.assertEqual(self.translate(), 0)
        self.assertEqual(self.rows(), {"a": "Olá SuadiaB {s0}", "b": "Bem-vindo SuadiaB {s1}"})

    def test_manually_repaired_branch_clears_known_incomplete_status(self):
        self.source.write_text("a|Hello {lord/lady}\n", encoding="utf-8")
        failing = HistoryBackend()
        def fail_branch(text):
            if text == "lord":
                raise RuntimeError("offline")
            return text.replace("Hello", "Olá")
        failing.translate = fail_branch
        with patch.object(mbtr, "make_backend", return_value=failing):
            self.assertEqual(mbtr.cmd_translate(self.cfg, self.args), 1)
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)
        self.target.write_text("a|Saudação manual {senhor/senhora}\n", encoding="utf-8")
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 0)
        self.assertEqual(self.translate(), 0)
        self.assertEqual(self.rows(), {"a": "Saudação manual {senhor/senhora}"})
        self.assertFalse(Cache.read_output_records(self.cache_path, self.target)["a"].source.startswith("incomplete:"))


if __name__ == "__main__":
    unittest.main()
