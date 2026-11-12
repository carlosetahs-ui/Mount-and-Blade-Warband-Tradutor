"""Regressões de integridade, retomada e configuração, sem serviços externos."""
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
import revisao
from translator import Backend, Cache, RateLimiter, TextTooLongError, Translator, TranslatorsBackend


class StubBackend(Backend):
    name = "stub"

    def __init__(self, fn=lambda text: text.replace("Hello", "Olá")):
        self.fn = fn
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return self.fn(text)


class ProtectionTests(unittest.TestCase):
    def test_complete_row_never_sends_id_to_translation_or_revision(self):
        backend = StubBackend()
        tr = Translator(backend, glossary={"Hello": "Oi"},
                        post_edit=lambda s: s.replace("dlga", "CORROMPIDO"))
        self.assertEqual(tr.translate("  dlga_Hello:{s0}  |Hello {s1}"),
                         "  dlga_Hello:{s0}  |Oi {s1}")
        self.assertTrue(all("dlga" not in call for call in backend.calls))

    def test_ambiguous_rows_and_multiline_input_are_not_sent(self):
        backend = StubBackend()
        tr = Translator(backend, log=lambda *_: None)
        for text in ("# Hello|comment", "|Hello", "a|Hello|b", "a|Hello\nb|Hello"):
            with self.subTest(text=text):
                self.assertEqual(tr.translate(text), text)
        self.assertEqual(backend.calls, [])

    def tearDown(self):
        mbloc.set_token_style("math")

    def test_revision_preserves_glossary_and_game_codes(self):
        post = revisao.build(replacements={"s0": "s1"})
        tr = Translator(StubBackend(), glossary={"Swadia": "Equipa"}, post_edit=post)
        self.assertEqual(tr.translate("Hello {s0} Swadia"), "Olá {s0} Equipa")

    def test_glossary_applies_inside_gender_branches(self):
        tr = Translator(StubBackend(), glossary={"lord": "senhor", "lady": "senhora"})
        self.assertEqual(tr.translate("Hello {lord/lady}"), "Olá {senhor/senhora}")

    def test_branch_preserves_glossary_capitalization_and_punctuation(self):
        tr = Translator(StubBackend(), glossary={"lord": "Senhor.", "lady": "À dama"})
        self.assertEqual(tr.translate("Hello {lord/lady}"), "Olá {Senhor./À dama}")

    def test_failed_branch_is_reported(self):
        def fail(_):
            raise RuntimeError("serviço indisponível")
        tr = Translator(StubBackend(fail), log=lambda *_: None)
        self.assertEqual(tr.translate("{lord/lady}"), "{lord/lady}")
        self.assertEqual(tr.stats["fail"], 1)

    def test_standalone_gender_branches_are_translated(self):
        tr = Translator(StubBackend(lambda s: s.replace("lord", "senhor").replace("lady", "senhora")))
        self.assertEqual(tr.translate("{lord/lady}"), "{senhor/senhora}")

    def test_conditional_register_change_is_rejected(self):
        self.assertTrue(mbtr.check_pair("{reg1?men:man}", "{reg2?homens:homem}"))
        self.assertTrue(mbtr.check_pair("{reg1?men:man}", "{homens/homem}"))

    def test_codes_inside_conditional_branches_are_checked(self):
        self.assertTrue(mbtr.check_pair("{reg1?%s men:man}", "{reg1?%d homens:homem}"))
        self.assertTrue(mbtr.check_pair("{reg1?%s men:man}", "{reg1?homens:%s homem}"))
        self.assertFalse(mbtr.check_pair("{reg1?%s men:man}", "{reg1?%s homens:homem}"))

    def test_literal_tokens_survive_both_styles(self):
        for style, literal in (("math", "⟦0⟧"), ("xnx", "X0X")):
            with self.subTest(style=style):
                mbloc.set_token_style(style)
                text = f"Hello {literal} {{s0}}"
                masked, subs = mbloc.mask(text)
                self.assertEqual(mbloc.unmask(masked, subs), text)

    def test_glossary_does_not_replace_part_of_a_token(self):
        mbloc.set_token_style("xnx")
        text = "Hello {s0}"
        masked, subs = mbloc.mask(text, {"X0X": "corrompido"})
        self.assertEqual(mbloc.unmask(masked, subs), text)

    def test_invented_game_codes_are_not_returned(self):
        tr = Translator(StubBackend(lambda text: "Olá {reg9}"), log=lambda *_: None)
        self.assertEqual(tr.translate("Hello"), "Hello")
        self.assertEqual(tr.stats["fail"], 1)

    def test_long_plain_text_is_split(self):
        backend = StubBackend()
        backend.max_chars = 12
        def limited(text):
            if len(text) > backend.max_chars:
                raise TextTooLongError("longo")
            return text.replace("Hello", "Olá")
        backend.fn = limited
        text = "Hello Hello Hello Hello Hello"
        tr = Translator(backend, log=lambda *_: None)
        self.assertEqual(tr.translate(text), "Olá Olá Olá Olá Olá")

    def test_invalid_cached_output_is_retranslated(self):
        with tempfile.TemporaryDirectory() as directory:
            with Cache(Path(directory) / "cache.sqlite") as cache:
                cache.put("stub", "Hello ⟦0⟧", "", "", "Olá ⟦0⟧ {reg99}")
                backend = StubBackend()
                tr = Translator(backend, cache)
                self.assertEqual(tr.translate("Hello {s0}"), "Olá {s0}")
                self.assertEqual(len(backend.calls), 1)

    def test_revision_cannot_introduce_game_codes(self):
        tr = Translator(StubBackend(), post_edit=lambda text: text + " {reg99}", log=lambda *_: None)
        self.assertEqual(tr.translate("Hello {s0}"), "Hello {s0}")

    def test_gender_translation_can_be_disabled(self):
        backend = StubBackend()
        self.assertEqual(Translator(backend, translate_branches=False).translate("{lord/lady}"), "{lord/lady}")
        self.assertEqual(backend.calls, [])

    def test_output_cannot_add_an_unbalanced_token(self):
        self.assertFalse(mbloc.tokens_ok("Hello ⟦0⟧", "Olá ⟦0⟧ ⟦"))

    def test_literal_tokens_survive_translation(self):
        for style, literal in (("math", "⟦0⟧"), ("xnx", "X0X")):
            with self.subTest(style=style):
                mbloc.set_token_style(style)
                tr = Translator(StubBackend())
                self.assertEqual(tr.translate(f"Hello {literal} {{s0}}"), f"Olá {literal} {{s0}}")

    def test_glossary_only_text_does_not_call_backend(self):
        backend = StubBackend()
        self.assertEqual(Translator(backend, glossary={"Swadia": "Suádia"}).translate("Swadia"), "Suádia")
        self.assertEqual(backend.calls, [])

    def test_word_boundaries_and_overlapping_glossary_terms(self):
        masked, subs = mbloc.mask("Kingdom of Swadia and Swadian", {"Swadia": "Suádia", "Kingdom of": "Reino de"})
        self.assertEqual(mbloc.unmask(masked, subs), "Reino de Suádia and Swadian")

    def test_split_text_preserves_content_and_limits(self):
        for text in ("Hello " * 20, "A" * 51, "   " * 20, "Hello.\tBye! Goodbye!"):
            for size in (1, 5, 12):
                with self.subTest(text=text, size=size):
                    chunks = mbloc.split_text(text, size)
                    self.assertEqual("".join(chunks), text)
                    self.assertTrue(all(0 < len(chunk) <= size for chunk in chunks))


class BackendSafetyTests(unittest.TestCase):
    def test_minute_and_hour_limits_do_not_wait_twice(self):
        now, waits = [0.0], []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        limiter = RateLimiter(per_minute=1, per_hour=1, pause=(0, 0), long_every=0,
                              clock=lambda: now[0], sleep=sleep)
        limiter.wait()
        limiter.wait()
        self.assertGreaterEqual(now[0], 3600)
        self.assertLess(now[0], 3603)
        self.assertEqual(sum(seconds > 0 for seconds in waits), 1)

    def test_blocked_requests_respect_retry_budget(self):
        now, calls = [0.0], []
        def sleep(seconds):
            now[0] += seconds
        def api(text, provider):
            calls.append(provider)
            # Evita que a implementação antiga rode indefinidamente.
            if len(calls) <= 2:
                raise RuntimeError("HTTP 429 Too Many Requests")
            raise RuntimeError("fim do teste")
        limiter = RateLimiter(per_minute=0, per_hour=0, pause=(0, 0), long_every=0,
                              clock=lambda: now[0], sleep=sleep)
        backend = TranslatorsBackend(providers=["a", "b"], api=api, max_retries=1,
                                     limiter=limiter, clock=lambda: now[0], sleep=sleep)
        with self.assertRaises(RuntimeError):
            backend.translate("Hello")
        self.assertEqual(len(calls), 2)

    def test_cache_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory) / "nested" / "cache.sqlite")
            cache.close()


class CommandSafetyTests(unittest.TestCase):
    def test_ids_are_preserved_exactly_and_never_sent_to_backend(self):
        text = "  qstr_Hello:{reg3  |Hello {s0}\ndlga_Hello:close|Hello {s1}\n"
        self.source.write_text(text, encoding="utf-8")
        backend = StubBackend()
        with patch.object(mbtr, "make_backend", return_value=backend):
            self.assertEqual(mbtr.cmd_translate(self.cfg, self.args), 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), text.replace("|Hello", "|Olá"))
        self.assertTrue(all("qstr_" not in call and "dlga_" not in call for call in backend.calls))

    def test_writer_rejects_changed_ids_and_injected_rows_before_replacing(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("original", encoding="utf-8")
        source = mbloc.read_csv(self.source)
        for rows in ([("alterado", "Hello {s0}"), ("b", "Hello {s1}")],
                     [("a", "Hello {s0}\nnovo|texto"), ("b", "Hello {s1}")],
                     [("b", "Hello {s1}"), ("a", "Hello {s0}")],
                     [("a", "Hello {reg99}"), ("b", "Hello {s1}")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                mbloc.write_csv(self.target, rows, source=source)
            self.assertEqual(self.target.read_text(encoding="utf-8"), "original")

    def test_extra_source_separator_is_preserved_without_backend_call(self):
        text = "qstr_X|X: {reg31} | Y: {reg32}\n"
        self.source.write_text(text, encoding="utf-8")
        backend = StubBackend()
        with patch.object(mbtr, "make_backend", return_value=backend):
            self.assertEqual(mbtr.cmd_translate(self.cfg, self.args), 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), text)
        self.assertEqual(backend.calls, [])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.mod = self.root / "Modules" / "Teste"
        self.src = self.mod / "languages" / "en"
        self.src.mkdir(parents=True)
        self.source = self.src / "dialogs.csv"
        self.source.write_text("a|Hello {s0}\nb|Hello {s1}\n", encoding="utf-8")
        self.cfg = {"_dir": self.root, "mod": {"path": str(self.mod)},
                    "cache": {"file": "cache.sqlite"}, "revision": {"ptbr_fix": False}}
        self.target = self.root / "saida" / "Teste" / "languages" / "pt" / "dialogs.csv"
        self.args = argparse.Namespace(file=None, limit=None)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_mod_path_is_relative_to_config(self):
        self.cfg["mod"]["path"] = "Modules/Teste"
        self.assertEqual(mbtr.paths(self.cfg)[1], self.src)

    def test_check_missing_file_fails(self):
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)

    def test_check_duplicate_ids_fails(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("a|Olá {s0}\na|Olá {s0}\nb|Olá {s1}\n", encoding="utf-8")
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)

    def test_interrupt_saves_completed_rows(self):
        self.source.write_text("a|Hello {s0}\nb|Welcome {s1}\n", encoding="utf-8")
        backend = StubBackend()
        def interrupt(text):
            if len(backend.calls) > 1:
                raise KeyboardInterrupt
            return text.replace("Hello", "Olá")
        backend.fn = interrupt
        with patch.object(mbtr, "make_backend", return_value=backend):
            result = mbtr.cmd_translate(self.cfg, self.args)
        self.assertEqual(result, 130)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "a|Olá {s0}\nb|Welcome {s1}\n")

    def test_invalid_existing_translation_is_repaired(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("a|Olá {reg99}\nb|Olá {s1}\n", encoding="utf-8")
        with patch.object(mbtr, "make_backend", return_value=StubBackend()):
            mbtr.cmd_translate(self.cfg, self.args)
        self.assertEqual(mbloc.read_csv(self.target).entries[0].text, "Olá {s0}")

    def test_source_directory_cannot_be_overwritten(self):
        self.cfg["mod"].update(install=True, target_lang="en")
        with self.assertRaises(ValueError):
            mbtr.paths(self.cfg)

    def test_unknown_file_fails_before_backend_initialization(self):
        self.args.file = "missing.csv"
        with patch.object(mbtr, "make_backend") as backend:
            with self.assertRaises((ValueError, SystemExit)):
                mbtr.cmd_translate(self.cfg, self.args)
        backend.assert_not_called()

    def test_cache_does_not_mix_target_languages(self):
        with patch.object(mbtr, "make_backend", return_value=StubBackend()):
            mbtr.cmd_translate(self.cfg, self.args)
        self.cfg["mod"]["target_lang"] = "fr"
        self.cfg["engine"] = {"target_code": "fr"}
        backend = StubBackend(lambda text: text.replace("Hello", "Bonjour"))
        with patch.object(mbtr, "make_backend", return_value=backend):
            mbtr.cmd_translate(self.cfg, self.args)
        target = self.target.parent.parent / "fr" / "dialogs.csv"
        self.assertEqual(mbloc.read_csv(target).entries[0].text, "Bonjour {s0}")

    def test_limit_preserves_later_existing_rows_and_comments(self):
        self.source.write_text("# cabeçalho\na|Hello {s0}\nb|Welcome {s1}\nc|Wait\n", encoding="utf-8-sig")
        self.target.parent.mkdir(parents=True)
        self.target.write_text("a|Hello {s0}\nb|Bem-vindo {s1}\nc|Wait\n", encoding="utf-8")
        self.args.limit = 1
        with patch.object(mbtr, "make_backend", return_value=StubBackend()):
            mbtr.cmd_translate(self.cfg, self.args)
        self.assertTrue(self.target.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.target.read_text(encoding="utf-8-sig"),
                         "# cabeçalho\na|Olá {s0}\nb|Bem-vindo {s1}\nc|Wait\n")

    def test_check_detects_extra_ids(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("a|Olá {s0}\nb|Olá {s1}\nextra|Mais\n", encoding="utf-8")
        self.assertEqual(mbtr.cmd_check(self.cfg, self.args), 1)

    def test_failed_atomic_write_preserves_original(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("original", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=PermissionError("arquivo em uso")):
            with self.assertRaises(PermissionError):
                mbloc.write_csv(self.target, [("a", "Novo")])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.target.parent.glob("*.tmp")), [])

    def test_source_duplicates_are_rejected(self):
        self.source.write_text("a|Hello\na|World\n", encoding="utf-8")
        with patch.object(mbtr, "make_backend", return_value=StubBackend()):
            with self.assertRaises(ValueError):
                mbtr.cmd_translate(self.cfg, self.args)
        self.assertFalse(self.target.exists())

    def test_cli_roundtrip_with_a_config_file(self):
        config = self.root / "config.toml"
        config.write_text("[mod]\npath = 'Modules/Teste'\ncheckpoint_every = 1\n"
                          "[cache]\nfile = 'cache/translations.sqlite'\n", encoding="utf-8-sig")
        for command in ("scan", "translate", "check"):
            with self.subTest(command=command), patch.object(mbtr, "make_backend", return_value=StubBackend()):
                self.assertEqual(mbtr.main([command, "--config", str(config), "--file", "dialogs.csv"]), 0)
        self.assertEqual(mbloc.read_csv(self.target).entries[0].text, "Olá {s0}")


class ConfigurationTests(unittest.TestCase):
    def test_invalid_configuration_fails_with_clear_errors(self):
        cases = [
            ("mod", {"path": ""}),
            ("mod", {"target_lang": "../en"}),
            ("mod", {"keep_existing": "false"}),
            ("mod", {"checkpoint_every": 0}),
            ("mod", {"skip_ids": ["["]}),
            ("mod", {"skip_files": "dialogs.csv"}),
            ("engine", {"backend": "unknown"}),
            ("engine", {"token_style": "unknown"}),
            ("engine", {"translators": {"providers": []}}),
            ("engine", {"translators": {"max_chars": 0}}),
            ("engine", {"translators": {"max_retries": -1}}),
            ("engine", {"translators": {"timeout": 0}}),
            ("engine", {"translators": {"pause": [5, 1]}}),
            ("engine", {"translators": {"long_pause": [1, float("inf")]}}),
            ("engine", {"argos": {"auto_install": "true"}}),
            ("cache", {"commit_every": 0}),
            ("cache", {"reuse_cache_from": "typo"}),
            ("revision", {"backends": ["typo"]}),
            ("glossary", {"terms": {"": "palavra"}}),
        ]
        for section, values in cases:
            with self.subTest(section=section, values=values):
                cfg = {"_dir": Path.cwd(), "mod": {"path": "Mod"}}
                cfg.setdefault(section, {}).update(values)
                with self.assertRaises(ValueError):
                    mbtr.validate_config(cfg)

    def test_config_accepts_bom_and_relative_mod_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[mod]\npath = 'Mod'\n", encoding="utf-8-sig")
            cfg = mbtr.load_config(str(path))
            self.assertEqual(mbtr.paths(cfg)[0], Path(directory) / "Mod")

    def test_missing_config_returns_error_without_traceback(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = mbtr.main(["scan", "--config", "missing-config.toml"])
        self.assertEqual(result, 1)
        self.assertIn("Erro:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_limit_must_be_positive_and_used_for_translation(self):
        for arguments in (("translate", "--limit", "0"), ("translate", "--limit", "-1"), ("scan", "--limit", "1")):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as err:
                    mbtr.main(arguments)
                self.assertEqual(err.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
