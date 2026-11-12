"""As cores dependem da ordem dos tokens, ao contrário das variáveis comuns."""
import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbloc
import mbtr
from translator import Backend, Translator


class ColorBackend(Backend):
    name = "color-test"

    def __init__(self, swap=False):
        self.swap = swap

    def translate(self, text):
        out = text.replace("Hello", "Olá")
        if self.swap:
            out = out.replace("⟦0⟧", "SWAP").replace("⟦1⟧", "⟦0⟧").replace("SWAP", "⟦1⟧")
        return out


class ColorIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.previous_style = mbloc.TOKEN_FMT
        mbloc.set_token_style("math")

    def tearDown(self):
        mbloc.set_token_style("xnx" if self.previous_style == "X{}X" else "math")

    def test_validator_rejects_inverted_opening_and_closing_tags(self):
        self.assertTrue(mbloc.check_pair("<c=red>Hello</c>", "</c>Olá<c=red>"))

    def test_validator_rejects_reordered_color_blocks(self):
        src = "<c=red>Hello</c> <c=blue>Bye</c>"
        dst = "<c=blue>Olá</c> <c=red>Adeus</c>"
        self.assertTrue(mbloc.check_pair(src, dst))

    def test_validator_checks_color_order_inside_each_conditional_branch(self):
        src = "{reg1?<c=red>Hello</c>:<c=blue>Bye</c>}"
        dst = "{reg1?</c>Olá<c=red>:<c=blue>Adeus</c>}"
        self.assertTrue(mbloc.check_pair(src, dst))
        self.assertFalse(mbloc.check_pair(src, dst.replace("</c>Olá<c=red>", "<c=red>Olá</c>")))

    def test_ordinary_variables_can_still_change_order(self):
        self.assertFalse(mbloc.check_pair("Hello {s0}, {s1}.", "{s1}, olá {s0}."))

    def test_inverted_backend_colors_are_recovered_by_plan_c(self):
        tr = Translator(ColorBackend(swap=True), log=lambda *_: None)
        self.assertEqual(tr.translate("<c=red>Hello</c>"), "<c=red>Olá</c>")
        self.assertEqual(tr.stats["planC"], 1)
        self.assertEqual(tr.stats["fail"], 0)

    def test_inverted_backend_colors_can_be_recovered_by_plan_b(self):
        class RotatingBackend(ColorBackend):
            def next_provider(self):
                self.swap = False
                return True

        tr = Translator(RotatingBackend(swap=True), log=lambda *_: None)
        self.assertEqual(tr.translate("<c=red>Hello</c>"), "<c=red>Olá</c>")
        self.assertEqual(tr.stats["planB"], 1)

    def test_revision_that_introduces_colors_keeps_the_original(self):
        tr = Translator(ColorBackend(), post_edit=lambda s: s + " <c=red>", log=lambda *_: None)
        source = "<c=red>Hello</c>"
        self.assertEqual(tr.translate(source), source)
        self.assertEqual(tr.stats["fail"], 1)
        self.assertEqual(tr.stats["planC"], 0)

    def test_existing_inverted_colors_are_reported_and_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mod = root / "Mod"
            source = mod / "languages" / "en" / "dialogs.csv"
            source.parent.mkdir(parents=True)
            source.write_text("a|<c=red>Hello</c>\n", encoding="utf-8")
            target = root / "saida" / "Mod" / "languages" / "pt" / "dialogs.csv"
            target.parent.mkdir(parents=True)
            target.write_text("a|</c>Olá<c=red>\n", encoding="utf-8")
            cfg = {"_dir": root, "mod": {"path": str(mod)}, "revision": {"ptbr_fix": False}}
            args = argparse.Namespace(file=None, limit=None)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mbtr.cmd_check(cfg, args), 1)
                with patch.object(mbtr, "make_backend", return_value=ColorBackend()):
                    self.assertEqual(mbtr.cmd_translate(cfg, args), 0)
                self.assertEqual(mbtr.cmd_check(cfg, args), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "a|<c=red>Olá</c>\n")


if __name__ == "__main__":
    unittest.main()
