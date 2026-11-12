"""Scanner iterativo e prioridade dos códigos sobre o glossário."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbloc
from translator import Backend, Translator


class ScannerBackend(Backend):
    name = "scanner"

    def __init__(self):
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return text.replace("Hello", "Olá").replace("Bye", "Adeus")


class MarkerScannerTests(unittest.TestCase):
    def setUp(self):
        style = "xnx" if mbloc.TOKEN_FMT == "X{}X" else "math"
        self.addCleanup(mbloc.set_token_style, style)

    @staticmethod
    def nested(depth):
        marker = "{s0}"
        for index in range(depth):
            marker = f"{{reg{index}?one:{marker}}}"
        return marker

    def test_nested_expression_is_a_single_marker_at_any_depth(self):
        for depth in (4, 20, 1500):
            with self.subTest(depth=depth):
                marker = self.nested(depth)
                text = f"Hello {marker} Bye"
                self.assertEqual(mbloc.markers(text), [marker])
                self.assertEqual(mbloc.strip_markers(text), "Hello  Bye")
                masked, subs = mbloc.mask(text)
                self.assertEqual(mbloc.unmask(masked, subs), text)
                self.assertFalse(mbloc.check_pair(text, text))

    def test_deep_expression_never_reaches_backend_in_either_token_style(self):
        marker = self.nested(12)
        for style in ("math", "xnx"):
            with self.subTest(style=style):
                mbloc.set_token_style(style)
                backend = ScannerBackend()
                tr = Translator(backend)
                self.assertEqual(tr.translate(f"Hello {marker} Bye"), f"Olá {marker} Adeus")
                self.assertTrue(backend.calls)
                self.assertTrue(all("{" not in call and "}" not in call for call in backend.calls))
                self.assertEqual(tr.stats["fail"], 0)

    def test_pure_deep_code_is_not_sent_for_translation(self):
        backend = ScannerBackend()
        marker = self.nested(1500)
        self.assertEqual(Translator(backend).translate(marker), marker)
        self.assertEqual(backend.calls, [])

    def test_incomplete_opening_protects_the_entire_remainder(self):
        text = "Hello {reg1?one:{s0} Bye"
        masked, subs = mbloc.mask(text)
        self.assertEqual(subs, ["{reg1?one:{s0} Bye"])
        self.assertEqual(mbloc.unmask(masked, subs), text)
        self.assertNotIn("{", masked)

    def test_unmatched_closing_brace_is_not_translated(self):
        backend = ScannerBackend()
        self.assertEqual(Translator(backend).translate("Hello } Bye"), "Olá } Adeus")
        self.assertTrue(all("}" not in call for call in backend.calls))

    def test_extended_printf_formats_are_masked_and_roundtrip(self):
        formats = ["%d", "%s", "%.2f", "%08d", "%#x", "%lld", "%I64d", "%zu", "%p", "%c",
                   "%*.*f", "%2$s", "%1$*2$.*3$f", "%%"]
        text = "Hello " + " ".join(formats)
        self.assertEqual(mbloc.markers(text), formats)
        masked, subs = mbloc.mask(text)
        self.assertEqual(subs, formats)
        self.assertEqual(mbloc.unmask(masked, subs), text)

    def test_printf_percent_suffix_is_protected_separately(self):
        self.assertEqual(mbloc.markers("Hello %d%%"), ["%d", "%%"])

    def test_percentages_before_words_are_not_misread_as_printf(self):
        self.assertEqual(mbloc.markers("50% chance and 20% damage. %.2f coins"), ["%.2f"])
        self.assertEqual(mbloc.markers("Value: % d coins"), ["% d"])

    def test_glossary_cannot_consume_code_even_when_term_starts_earlier(self):
        glossary = {"Hello {s0}": "CORRUPTED", "Hello": "Oi", "Bye": "Tchau"}
        text = "Hello {s0} Bye"
        masked, subs = mbloc.mask(text, glossary)
        self.assertEqual(mbloc.unmask(masked, subs), "Oi {s0} Tchau")
        self.assertNotIn("CORRUPTED", subs)

    def test_glossary_cannot_consume_printf_or_nested_expressions(self):
        marker = self.nested(8)
        text = f"Hello %.2f {marker}"
        glossary = {text: "CORRUPTED", "Hello %.2f": "WRONG", "one": "um", "Hello": "Oi"}
        masked, subs = mbloc.mask(text, glossary)
        self.assertEqual(mbloc.unmask(masked, subs), f"Oi %.2f {marker}")

    def test_glossary_cannot_consume_literal_tokens(self):
        for literal in ("X0X", "⟦0⟧"):
            with self.subTest(literal=literal):
                text = f"Hello {literal} Bye"
                masked, subs = mbloc.mask(text, {text: "CORRUPTED", "Hello": "Oi"})
                self.assertEqual(mbloc.unmask(masked, subs), f"Oi {literal} Bye")

    def test_splitting_protected_spans_does_not_invent_word_boundaries(self):
        for text in ("HelloX0X", "X0XHello"):
            with self.subTest(text=text):
                masked, subs = mbloc.mask(text, {"Hello": "WRONG"})
                self.assertEqual(mbloc.unmask(masked, subs), text)


if __name__ == "__main__":
    unittest.main()
