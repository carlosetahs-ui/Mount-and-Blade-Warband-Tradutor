"""Candidatas estruturais inválidas não viram traduções nem entradas de cache."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbloc
from translator import Backend, Cache, Translator


class RecordingBackend(Backend):
    name = "integrity"

    def __init__(self, fn=None, rotate=False):
        self.fn = fn or self.words
        self.calls = []
        self.provider = 0
        self.rotate = rotate

    @staticmethod
    def words(text):
        if text == "and":
            return "e"
        return (text.replace("Hello", "Ola").replace("Name", "Nome")
                .replace("count", "quantidade").replace("place", "lugar")
                .replace(" and ", " e "))

    @property
    def engine_id(self):
        return f"{self.name}:{self.provider}"

    def translate(self, text):
        self.calls.append(text)
        return self.fn(text)

    def next_provider(self):
        if self.rotate and self.provider == 0:
            self.provider = 1
            return True
        return False


def swap_tokens(text):
    tokens = list(mbloc.TOKEN_RE.finditer(text))
    if len(tokens) == 2:
        return "Quantidade " + tokens[1].group(0) + " nome " + tokens[0].group(0)
    return RecordingBackend.words(text)


class TranslationIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Cache(Path(temporary.name) / "cache.sqlite")
        self.addCleanup(self.cache.close)
        self.addCleanup(mbloc.set_token_style, "math")
        mbloc.set_token_style("math")

    def translator(self, backend, **kwargs):
        return Translator(backend, self.cache, log=lambda *_: None, **kwargs)

    def cached(self, translator, source, engine=None):
        masked, _ = mbloc.mask(source)
        return self.cache.get(engine or translator.b.engine_id, masked, translator.cache_context,
                              translator.gl_hash, reuse_any=False)

    def test_reordered_printf_recovers_in_plan_c_without_caching_the_bad_phrase(self):
        source = "Name %s count %d"
        bad = self.translator(RecordingBackend(swap_tokens))
        self.assertEqual(bad.translate(source), "Nome %s quantidade %d")
        self.assertEqual(bad.stats["planC"], 1)
        self.assertIsNone(self.cached(bad, source))

        healthy_backend = RecordingBackend()
        healthy = self.translator(healthy_backend)
        self.assertEqual(healthy.translate(source), "Nome %s quantidade %d")
        self.assertEqual(healthy_backend.calls, [mbloc.mask(source)[0]])
        self.assertEqual(self.cached(healthy, source), mbloc.mask("Nome %s quantidade %d")[0])

    def test_reordered_legacy_cache_is_ignored_and_replaced(self):
        source = "Name %s count %d"
        backend = RecordingBackend()
        tr = self.translator(backend)
        masked, _ = mbloc.mask(source)
        self.cache.put(backend.engine_id, masked, tr.cache_context, tr.gl_hash, swap_tokens(masked))
        self.assertEqual(tr.translate(source), "Nome %s quantidade %d")
        self.assertEqual(backend.calls, [masked])
        self.assertEqual(tr.stats["cache"], 0)
        self.assertEqual(self.cached(tr, source), mbloc.mask("Nome %s quantidade %d")[0])

    def test_identical_printf_occurrences_cannot_swap_before_unmask(self):
        source = "Name %s place %s"
        tr = self.translator(RecordingBackend(swap_tokens))
        self.assertEqual(tr.translate(source), "Nome %s lugar %s")
        self.assertEqual(tr.stats["planC"], 1)
        self.assertIsNone(self.cached(tr, source))

    def test_fully_indexed_printf_arguments_can_change_order(self):
        source = "Name %1$s count %2$d"
        tr = self.translator(RecordingBackend(swap_tokens))
        self.assertEqual(tr.translate(source), "Quantidade %2$d nome %1$s")
        self.assertEqual(tr.stats["ok"], 1)
        self.assertEqual(tr.stats["planC"], 0)

    def test_plan_b_stores_only_the_valid_provider_response(self):
        source = "Name %s count %d"
        backend = RecordingBackend(rotate=True)
        backend.fn = lambda text: swap_tokens(text) if backend.provider == 0 else backend.words(text)
        tr = self.translator(backend)
        self.assertEqual(tr.translate(source), "Nome %s quantidade %d")
        self.assertEqual(tr.stats["planB"], 1)
        self.assertIsNone(self.cached(tr, source, "integrity:0"))
        self.assertEqual(self.cached(tr, source, "integrity:1"), mbloc.mask("Nome %s quantidade %d")[0])

    def test_branch_printf_reordering_uses_fragment_recovery(self):
        branch = "Name %s count %d"
        source = "Hello {reg1?" + branch + ":other}"
        tr = self.translator(RecordingBackend(swap_tokens))
        self.assertEqual(tr.translate(source), "Ola {reg1?Nome %s quantidade %d:other}")
        self.assertEqual(tr.stats["fail"], 0)
        self.assertIsNone(self.cached(tr, branch))

    def test_same_selector_conditions_keep_their_occurrence_order(self):
        source = "{reg1?first:last} and {reg1?upper:lower}"
        tr = self.translator(RecordingBackend(swap_tokens))
        self.assertEqual(tr.translate(source), "{reg1?first:last} e {reg1?upper:lower}")
        self.assertEqual(tr.stats["planC"], 1)
        self.assertIsNone(self.cached(tr, source))

    def test_conditions_with_different_selectors_can_change_order(self):
        source = "{reg1?first:last} and {reg2?upper:lower}"
        tr = self.translator(RecordingBackend(swap_tokens))
        self.assertEqual(tr.translate(source), "Quantidade {reg2?upper:lower} nome {reg1?first:last}")
        self.assertEqual(tr.stats["ok"], 1)
        self.assertEqual(tr.stats["planC"], 0)

    def test_conditional_question_mark_is_normalized_on_fresh_and_cached_branches(self):
        source = "Hello {reg1?why:other}"
        backend = RecordingBackend(lambda text: text.replace("why", "por que?")
                                   .replace("other", "outro").replace("Hello", "Ola"))
        tr = self.translator(backend)
        expected = "Ola {reg1?por que :outro}"
        self.assertEqual(tr.translate(source), expected)
        self.assertEqual(tr.stats["fail"], 0)
        self.assertEqual(self.cached(tr, "why"), "por que?")

        cached_backend = RecordingBackend(lambda _: self.fail("a retomada deveria usar o cache"))
        resumed = self.translator(cached_backend)
        self.assertEqual(resumed.translate(source), expected)
        self.assertEqual(resumed.stats["fail"], 0)
        self.assertEqual(cached_backend.calls, [])

    def test_gender_question_marks_and_colons_are_normalized(self):
        source = "Hello {lord/lady}"
        backend = RecordingBackend(lambda text: text.replace("lord", "senhor?")
                                   .replace("lady", "senhora:").replace("Hello", "Ola"))
        tr = self.translator(backend)
        self.assertEqual(tr.translate(source), "Ola {senhor /senhora }")
        self.assertEqual(tr.stats["fail"], 0)

    def test_branch_text_normalizes_delimiters_without_changing_color_tags(self):
        text = "<c=red>senhor?/rei:</c>"
        self.assertEqual(Translator._branch_text(text, "/?:"), "<c=red>senhor  rei </c>")

    def test_supported_conditional_keeps_color_codes_during_normalization(self):
        source = "Hello {reg1?<c=red>why</c>:other}"
        backend = RecordingBackend(lambda text: text.replace("why", "por que?")
                                   .replace("other", "outro").replace("Hello", "Ola"))
        tr = self.translator(backend)
        expected = "Ola {reg1?<c=red>por que </c>:outro}"
        self.assertEqual(tr.translate(source), expected)
        self.assertEqual(mbloc.check_pair(source, expected), [])
        self.assertEqual(tr.stats["fail"], 0)

    def test_complex_gender_choice_with_color_tags_stays_opaque(self):
        marker = "{<c=red>lord</c>/<c=blue>lady</c>}"
        source = "Hello " + marker
        backend = RecordingBackend()
        tr = self.translator(backend)
        self.assertEqual(tr.translate(source), "Ola " + marker)
        self.assertEqual(backend.calls, [mbloc.mask(source)[0]])
        self.assertEqual(tr.stats["fail"], 0)

    def test_invalid_revision_keeps_original_without_storing_the_candidate(self):
        source = "Hello {s0}"
        revisions = []
        def revise(text):
            revisions.append(text)
            return text + " {reg99}"
        backend = RecordingBackend()
        tr = self.translator(backend, post_edit=revise)
        self.assertEqual(tr.translate(source), source)
        self.assertEqual(tr.stats["fail"], 1)
        self.assertEqual(len(revisions), 1)
        self.assertIsNone(self.cached(tr, source))
        healthy = self.translator(backend)
        self.assertEqual(healthy.translate(source), "Ola {s0}")
        self.assertEqual(len(backend.calls), 2)

    def test_invalid_plan_b_revision_does_not_retry_the_revision_in_plan_c(self):
        source = "Hello {s0}"
        revisions = []
        backend = RecordingBackend(rotate=True)
        backend.fn = lambda text: "invalid" if backend.provider == 0 else backend.words(text)
        def revise(text):
            revisions.append(text)
            return text + " {reg99}"
        tr = self.translator(backend, post_edit=revise)
        self.assertEqual(tr.translate(source), source)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(tr.stats["planC"], 0)
        self.assertEqual(tr.stats["fail"], 1)
        self.assertIsNone(self.cached(tr, source, "integrity:0"))
        self.assertIsNone(self.cached(tr, source, "integrity:1"))

    def test_plan_c_fragment_cache_waits_for_the_final_revision(self):
        source = "Hello {s0}"
        backend = RecordingBackend(lambda text: "invalid" if mbloc.TOKEN_RE.search(text) else "Ola")
        tr = self.translator(backend, post_edit=lambda text: text + " {reg99}")
        self.assertEqual(tr.translate(source), source)
        self.assertEqual(self.cache.db.execute("SELECT COUNT(*) FROM t").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
