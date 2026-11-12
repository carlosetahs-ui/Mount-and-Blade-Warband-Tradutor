"""Limpeza da resposta Ollama sem apagar pontuação ou rótulos do diálogo."""
import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translator import Cache, OllamaBackend, Translator


class OllamaOutputTests(unittest.TestCase):
    def translate_response(self, source, output):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"response": output}).encode("utf-8")
        with patch("translator.urllib.request.urlopen", return_value=response) as request:
            result = Translator(OllamaBackend(), log=lambda *_: None).translate(source)
        request.assert_called_once()
        return result

    def test_quote_at_end_of_dialogue_is_preserved(self):
        self.assertEqual(self.translate_response('He shouted "Charge!"', 'Ele gritou "Ataque!"'),
                         'Ele gritou "Ataque!"')

    def test_quote_at_start_of_dialogue_is_preserved(self):
        self.assertEqual(self.translate_response('"Charge!", he shouted.', '"Ataque!", ele gritou.'),
                         '"Ataque!", ele gritou.')

    def test_fully_quoted_dialogue_keeps_straight_and_curly_quotes(self):
        for source, output in (('"Charge!"', '"Ataque!"'), ('“Charge!”', '“Ataque!”')):
            with self.subTest(source=source):
                self.assertEqual(self.translate_response(source, output), output)

    def test_ambiguous_external_quotes_are_preserved(self):
        self.assertEqual(self.translate_response("Charge!", '"Ataque!"'), '"Ataque!"')

    def test_added_translation_prefix_is_removed_without_removing_quotes(self):
        for prefix in ("Translation: ", "Tradução : ", "TRANSLATION:\t"):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.translate_response("Charge!", prefix + '"Ataque!"'), '"Ataque!"')

    def test_original_translation_label_is_preserved(self):
        for source in ("Translation: Hello", "Tradução: Hello", "  TRANSLATION : Hello"):
            with self.subTest(source=source):
                self.assertEqual(self.translate_response(source, "Tradução: Olá"), "Tradução: Olá")

    def test_quoted_original_translation_label_is_preserved(self):
        self.assertEqual(self.translate_response('"Translation: Hello"', '"Tradução: Olá"'),
                         '"Tradução: Olá"')

    def test_words_starting_with_translation_are_not_removed(self):
        self.assertEqual(self.translate_response("Translation manual", "Translation manual: Olá"),
                         "Translation manual: Olá")

    def test_damaged_quotes_in_legacy_cache_are_not_reused(self):
        source = 'He shouted "Charge!"'
        output = 'Ele gritou "Ataque!"'
        with tempfile.TemporaryDirectory() as directory, Cache(Path(directory) / "cache.sqlite") as cache:
            backend = OllamaBackend()
            legacy_glossary_hash = hashlib.sha1(json.dumps({}, sort_keys=True).encode()).hexdigest()[:12]
            cache.put(backend.engine_id, source, "", legacy_glossary_hash, output[:-1])
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({"response": output}).encode("utf-8")
            with patch("translator.urllib.request.urlopen", return_value=response) as request:
                result = Translator(backend, cache, log=lambda *_: None).translate(source)
            self.assertEqual(result, output)
            request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
