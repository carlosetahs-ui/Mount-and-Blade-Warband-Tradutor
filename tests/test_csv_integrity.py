"""Integridade dos IDs e do formato CSV antes da substituição dos arquivos."""
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


class CsvIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "Mod" / "languages" / "en" / "dialogs.csv"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"a|Hello\r\nb|Welcome\r\n")
        self.cfg = {"_dir": self.root, "mod": {"path": "Mod"}}
        self.target = self.root / "saida" / "Mod" / "languages" / "pt" / "dialogs.csv"
        self.args = argparse.Namespace(file=None, limit=None)

    def test_crlf_is_removed_without_touching_id_whitespace_or_braces(self):
        self.source.write_bytes(b"  qstr:{reg3  |Hello\r\n")
        source = mbloc.read_csv(self.source)
        mbtr.validate_source(source)
        self.assertEqual(source.entries[0].key, "  qstr:{reg3  ")
        self.assertEqual(source.entries[0].text, "Hello")

    def test_isolated_and_repeated_cr_are_not_silently_stripped(self):
        for raw in (b"a|Hello\r\r\n", b"a|Hello\r"):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                source = mbloc.read_csv(self.source)
                self.assertTrue(source.entries[0].raw.endswith("\r"))
                with self.assertRaisesRegex(ValueError, "CR isolada"):
                    mbtr.validate_source(source)
                with patch.object(mbtr, "make_backend") as backend:
                    with self.assertRaisesRegex(ValueError, "CR isolada"):
                        mbtr.cmd_translate(self.cfg, self.args)
                backend.assert_not_called()
                self.assertFalse(self.target.exists())

    def test_nul_and_utf16_fail_before_parsing_or_initializing_backend(self):
        for raw in (b"a|Hello\x00\n", "a|Hello\n".encode("utf-16")):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "NUL.*UTF-16"):
                    mbloc.read_csv(self.source)
                with patch.object(mbtr, "make_backend") as backend:
                    with self.assertRaises(ValueError):
                        mbtr.cmd_translate(self.cfg, self.args)
                backend.assert_not_called()
                self.assertFalse(self.target.exists())

    def test_writer_rejects_injected_or_ambiguous_rows_without_a_source(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"original")
        for rows in (
            [("", "Hello")], [("  ", "Hello")],
            [("a", "Hello"), ("a", "Welcome")],
            [("a", "Hello|injected")], [(None, "injected|Hello")],
            [("#a", "Hello")], [("a", "Hello\x00")], [("a\x00", "Hello")],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                mbloc.write_csv(self.target, rows)
            self.assertEqual(self.target.read_bytes(), b"original")
            self.assertEqual(list(self.target.parent.glob("*.tmp")), [])

    def test_original_extra_separator_is_preserved_but_cannot_be_changed(self):
        self.source.write_bytes(b"a|X: {reg31} | Y: {reg32}\n")
        source = mbloc.read_csv(self.source)
        rows = [(entry.key, entry.text) for entry in source.entries]
        mbloc.write_csv(self.target, rows, source=source)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        with self.assertRaises(ValueError):
            mbloc.write_csv(self.target, [("a", "X: {reg31} | Z: {reg32}")], source=source)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())

    def test_writer_accepts_comments_with_pipes_and_preserves_opaque_ids(self):
        rows = [(None, " # comentário | texto"), ("  qstr:{reg3  ", "Olá")]
        mbloc.write_csv(self.target, rows)
        entries = mbloc.read_csv(self.target).entries
        self.assertIsNone(entries[0].key)
        self.assertEqual(entries[0].raw, rows[0][1])
        self.assertEqual(entries[1].key, rows[1][0])

    def test_check_rejects_reordered_ids_but_allows_different_comments(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# comentário diferente\nb|Bem-vindo\na|Olá\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as report:
            self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)
        self.assertIn("ordem dos IDs", report.getvalue())
        self.target.write_text("# outro comentário\na|Olá\nb|Bem-vindo\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 0)

    def test_check_reports_isolated_cr_in_source_and_translation(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"a|Ola\nb|Bem-vindo\n")
        self.source.write_bytes(b"a|Hello\r\r\nb|Welcome\n")
        with contextlib.redirect_stdout(io.StringIO()) as report:
            self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)
        self.assertIn("CR isolada na origem", report.getvalue())
        self.source.write_bytes(b"a|Hello\nb|Welcome\n")
        self.target.write_bytes(b"a|Ola\r\nb|Bem-vindo\r")
        with contextlib.redirect_stdout(io.StringIO()) as report:
            self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)
        self.assertIn("CR isolada na tradução", report.getvalue())


if __name__ == "__main__":
    unittest.main()
