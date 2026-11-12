"""Regressões de entradas da revisão e infinitivos irregulares."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import revisao


class ReplacementInputTests(unittest.TestCase):
    def test_programmatic_empty_original_is_rejected(self):
        for original in ("", " ", "\t"):
            with self.subTest(original=original), self.assertRaises(ValueError):
                revisao.build(ptbr_fix=False, replacements={original: "texto inesperado"})

    def test_malformed_file_entries_are_reported_and_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "substituicoes.txt"
            path.write_text(
                "  # comentário = não é uma substituição\n"
                "sem separador\n"
                "= texto inesperado\n"
                "  \ttexto inesperado\n"
                "errado = certo\n"
                "remover =\n",
                encoding="utf-8-sig",
            )
            with contextlib.redirect_stderr(io.StringIO()) as warnings:
                replacements = revisao.load_replacements(path)

            self.assertEqual(replacements, {"errado": "certo", "remover": ""})
            self.assertEqual(len(warnings.getvalue().splitlines()), 3)
            for line in (2, 3, 4):
                self.assertIn(f"{path}:{line}:", warnings.getvalue())
            self.assertEqual(revisao.build(False, replacements)("Errado remover"), "Certo ")

    def test_empty_replacement_keeps_its_removal_semantics(self):
        post = revisao.build(False, {"muito": ""})
        self.assertEqual(post("muito boa espada"), " boa espada")
        self.assertEqual(post("muitoboa"), "muitoboa")


class IrregularGerundTests(unittest.TestCase):
    def test_short_irregular_infinitives_are_converted(self):
        for source, expected in (
            ("Ele está a ir para casa.", "Ele está indo para casa."),
            ("Ele está a pôr a espada.", "Ele está pondo a espada."),
            ("ESTÁ A IR", "ESTÁ INDO"),
            ("ESTÁ A PÔR", "ESTÁ PONDO"),
            ("Ele estava a pôr-se de pé.", "Ele estava pondo-se de pé."),
        ):
            with self.subTest(source=source):
                self.assertEqual(revisao.ptbr(source), expected)

    def test_nouns_and_longer_words_are_not_matched_as_infinitives(self):
        for source in (
            "está a mar", "está a irmã", "está a pôrque",
            "Onde está a mulher?", "Aqui está a mulher do rei.",
            "ONDE ESTÁ A MULHER?", "Onde está a colher?",
            "Onde está a circular do comandante?",
        ):
            with self.subTest(source=source):
                self.assertEqual(revisao.ptbr(source), source)

    def test_common_actions_still_convert_with_case_and_pronouns(self):
        for source, expected in (
            ("Ele está a fazer guarda.", "Ele está fazendo guarda."),
            ("Os homens estão a lutar.", "Os homens estão lutando."),
            ("ESTAVA A CORRER", "ESTAVA CORRENDO"),
            ("Ele está a defender-se.", "Ele está defendendo-se."),
            ("Estamos a seguir o rasto.", "Estamos seguindo o rasto."),
            ("Não está a acontecer nada.", "Não está acontecendo nada."),
            ("Aqui está a mulher. O guarda está a atacar.",
             "Aqui está a mulher. O guarda está atacando."),
        ):
            with self.subTest(source=source):
                self.assertEqual(revisao.ptbr(source), expected)

    def test_unknown_infinitives_are_preserved_for_manual_revision(self):
        self.assertEqual(revisao.ptbr("A fera está a bramir."), "A fera está a bramir.")


if __name__ == "__main__":
    unittest.main()
