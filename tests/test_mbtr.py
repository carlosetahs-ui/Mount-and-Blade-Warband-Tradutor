import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbloc
import mbtr
from translator import BlockedError, Cache, RateLimiter, Translator, TranslatorsBackend, Backend


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def translate(self, text):
        self.calls += 1
        return self.fn(text)


WORDS = {"Hello": "Olá", "welcome to": "bem-vindo a", "my": "meu", "lady": "senhora",
         "lord": "senhor", "men": "homens", "man": "homem", "You have": "Você tem"}


def fake_mt(text):
    for a, b in WORDS.items():
        text = text.replace(a, b)
    return text


class MaskTests(unittest.TestCase):
    def test_roundtrip(self):
        s = "Hello {playername}, welcome to {s1}.^Go {reg3?now:later}."
        m, subs = mbloc.mask(s)
        self.assertNotIn("{", m)
        self.assertEqual(mbloc.unmask(m, subs), s)

    def test_tokens_ok_detects_loss_and_dup(self):
        m, _ = mbloc.mask("a {s0} b {s1}")
        self.assertFalse(mbloc.tokens_ok(m, "a ⟦0⟧ b"))
        self.assertFalse(mbloc.tokens_ok(m, "a ⟦0⟧ ⟦0⟧ ⟦1⟧"))
        self.assertTrue(mbloc.tokens_ok(m, "⟦1⟧ x ⟦0⟧"))

    def test_glossary_forced(self):
        m, subs = mbloc.mask("The Kingdom of Swadia", {"Swadia": "Suádia"})
        self.assertEqual(mbloc.unmask(m, subs), "The Kingdom of Suádia")

    def test_branches(self):
        self.assertEqual(mbloc.translatable_branches("{my lord/my lady}")[1], ["my lord", "my lady"])
        self.assertEqual(mbloc.translatable_branches("{reg1?men:man}")[1], ["men", "man"])
        self.assertIsNone(mbloc.translatable_branches("{s0}"))
        self.assertIsNone(mbloc.translatable_branches("{playername}"))

    def test_clean_output(self):
        self.assertEqual(mbloc.clean_output("a|b\nc"), "a/b c")


class TranslatorTests(unittest.TestCase):
    def test_plan_a_keeps_codes(self):
        t = Translator(FakeBackend(fake_mt))
        self.assertEqual(t.translate("Hello {playername}, welcome to {s1}."),
                         "Olá {playername}, bem-vindo a {s1}.")

    def test_gender_choice_translated(self):
        t = Translator(FakeBackend(fake_mt))
        out = t.translate("Hello, {my lord/my lady}. You have {reg1} {reg1?men:man}.")
        self.assertIn("{meu senhor/meu senhora}", out)
        self.assertIn("{reg1?homens:homem}", out)
        self.assertIn("{reg1}", out)

    def test_plan_c_when_backend_eats_tokens(self):
        def bad(text):
            return mbloc.TOKEN_RE.sub("", fake_mt(text))
        t = Translator(FakeBackend(bad))
        out = t.translate("Hello {s0}^welcome to {s1}")
        self.assertEqual(mbloc.markers(out), ["{s0}", "^", "{s1}"])
        self.assertEqual(t.stats["planC"], 1)

    def test_failure_keeps_original(self):
        def boom(_):
            raise RuntimeError("x")
        t = Translator(FakeBackend(boom), log=lambda *_: None)
        self.assertEqual(t.translate("Hello {s0}"), "Hello {s0}")
        self.assertEqual(t.stats["fail"], 1)

    def test_only_codes_not_sent(self):
        b = FakeBackend(fake_mt)
        self.assertEqual(Translator(b).translate("{s0} {s1}^"), "{s0} {s1}^")
        self.assertEqual(b.calls, 0)

    def test_cache_resume_and_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            c = Cache(Path(d) / "c.sqlite")
            b = FakeBackend(fake_mt)
            Translator(b, c).translate("Hello {s0}")
            c.close()
            c2 = Cache(Path(d) / "c.sqlite")
            b2 = FakeBackend(lambda t: "NOVO")
            b2.name = "outro"
            out = Translator(b2, c2).translate("Hello {s0}")
            self.assertEqual(out, "Olá {s0}")
            self.assertEqual(b2.calls, 0)
            c2.close()


class AntiBanTests(unittest.TestCase):
    def test_rate_limit_per_minute(self):
        now = [0.0]; slept = []
        def sleep(s): slept.append(s); now[0] += s
        r = RateLimiter(per_minute=3, per_hour=0, pause=(0, 0), long_every=0, clock=lambda: now[0], sleep=sleep)
        for _ in range(4):
            r.wait()
        self.assertTrue(any(s >= 59 for s in slept))

    def _backend(self, api):
        now = [0.0]
        def sleep(s): now[0] += s
        lim = RateLimiter(per_minute=0, per_hour=0, pause=(0, 0), long_every=0, clock=lambda: now[0], sleep=sleep)
        return TranslatorsBackend(providers=["google", "bing", "yandex"], limiter=lim, api=api,
                                  clock=lambda: now[0], sleep=sleep)

    def test_google_removed(self):
        self.assertEqual(self._backend(lambda t, p: t).providers, ["bing", "yandex"])

    def test_rotates_when_blocked(self):
        used = []
        def api(text, p):
            used.append(p)
            if p == "bing":
                raise RuntimeError("HTTP 429 Too Many Requests")
            return "ok-" + p
        b = self._backend(api)
        self.assertEqual(b.translate("x"), "ok-yandex")
        self.assertEqual(b.translate("y"), "ok-yandex")
        self.assertEqual(used.count("bing"), 1)  # em cooldown, não foi usado de novo

    def test_all_fail_raises(self):
        def api(text, p):
            raise RuntimeError("erro qualquer")
        with self.assertRaises(RuntimeError):
            self._backend(api).translate("x")


class FileTests(unittest.TestCase):
    def test_full_run_and_check(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            en = d / "Modules" / "Teste" / "languages" / "en"
            en.mkdir(parents=True)
            (en / "dialogs.csv").write_text(
                "dlga_a|Hello {playername}, welcome to {s1}.\n"
                "dlga_b|{s0}\n"
                "trp_x|Hello\n", encoding="utf-8")
            cfg = {"_dir": d, "mod": {"path": str(d / "Modules" / "Teste"), "skip_ids": ["^trp_"]},
                   "cache": {"file": "c.sqlite"}}
            orig = mbtr.make_backend
            mbtr.make_backend = lambda cfg, g: FakeBackend(fake_mt)
            try:
                mbtr.cmd_translate(cfg, type("A", (), {"limit": None, "file": None})())
            finally:
                mbtr.make_backend = orig
            out = d / "saida" / "Teste" / "languages" / "pt" / "dialogs.csv"
            lines = out.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "dlga_a|Olá {playername}, bem-vindo a {s1}.")
            self.assertEqual(lines[1], "dlga_b|{s0}")
            self.assertEqual(lines[2], "trp_x|Hello")
            self.assertEqual(mbtr.cmd_check(cfg, None), 0)

    def test_check_finds_lost_code(self):
        self.assertTrue(mbtr.check_pair("a {s0}", "a"))
        self.assertFalse(mbtr.check_pair("{sir/madam} {s0}", "{senhor/senhora} {s0}"))


if __name__ == "__main__":
    unittest.main()
