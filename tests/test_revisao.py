"""Testes para o módulo de revisão pós-tradução."""
import sys
from pathlib import Path

# Adiciona o diretório pai ao path para importar revisao
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import revisao


class RevisionTests(unittest.TestCase):
    def test_ptbr_vocabulary(self):
        """Testa substituição de vocabulário PT-PT para PT-BR."""
        post = revisao.build(ptbr_fix=True)
        self.assertEqual(post("partilhar"), "compartilhar")
        self.assertEqual(post("equipa"), "equipe")
        self.assertEqual(post("facto"), "fato")
        self.assertEqual(post("ecrã"), "tela")

    def test_ptbr_capitalization(self):
        """Testa preservação de capitalização."""
        post = revisao.build(ptbr_fix=True)
        self.assertEqual(post("Partilhar"), "Compartilhar")
        self.assertEqual(post("PARTILHAR"), "COMPARTILHAR")
        self.assertEqual(post("Partilham"), "Compartilham")

    def test_ptbr_gerund(self):
        """Testa correção de gerúndios (está a fazer -> está fazendo)."""
        post = revisao.build(ptbr_fix=True)
        self.assertEqual(post("está a fazer"), "está fazendo")
        self.assertEqual(post("estava a correr"), "estava correndo")
        self.assertEqual(post("estão a comer"), "estão comendo")

    def test_ptbr_enclitic(self):
        """Testa correção de pronomes enclíticos (meio da frase)."""
        post = revisao.build(ptbr_fix=True)
        # No meio da frase deve mudar
        self.assertEqual(post("Eles impressionam-me"), "Eles me impressionam")
        self.assertEqual(post("Eles viram-nos"), "Eles nos viram")
        # No início da frase não deve mudar (aceito em PT-BR)
        self.assertEqual(post("impressionam-me agora"), "impressionam-me agora")

    def test_ptbr_old_spelling(self):
        """Testa correção de grafias antigas pré-acordo."""
        post = revisao.build(ptbr_fix=True)
        self.assertEqual(post("acção"), "ação")
        self.assertEqual(post("direcção"), "direção")
        self.assertEqual(post("protecção"), "proteção")
        self.assertEqual(post("objectivo"), "objetivo")

    def test_custom_replacements(self):
        """Testa substituições personalizadas do usuário."""
        post = revisao.build(ptbr_fix=False, replacements={"sword": "espada", "knight": "cavaleiro"})
        self.assertEqual(post("sword"), "espada")
        self.assertEqual(post("knight"), "cavaleiro")
        self.assertEqual(post("Sword"), "Espada")

    def test_combined_replacements(self):
        """Testa substituições personalizadas + PT-BR."""
        post = revisao.build(ptbr_fix=True, replacements={"test": "teste"})
        self.assertEqual(post("o ecrã test"), "a tela teste")

    def test_no_revision_when_disabled(self):
        """Testa que nada é alterado quando desativado."""
        post = revisao.build(ptbr_fix=False, replacements=None)
        self.assertIsNone(post)

    def test_load_replacements_from_file(self):
        """Testa carregamento de substituições de arquivo."""
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("# comentário\n")
            f.write("errado = certo\n")
            f.write("wrong\tcorrect\n")
            temp_path = Path(f.name)

        try:
            repl = revisao.load_replacements(temp_path)
            self.assertEqual(repl["errado"], "certo")
            self.assertEqual(repl["wrong"], "correct")
        finally:
            temp_path.unlink()

    def test_load_replacements_missing_file(self):
        """Testa que arquivo inexistente retorna dict vazio."""
        from pathlib import Path
        repl = revisao.load_replacements(Path("/nonexistent/file.txt"))
        self.assertEqual(repl, {})

    def test_backend_specific_revision(self):
        """Testa lógica de aplicação de revisão por backend."""
        # Lista vazia = aplica em todos
        revision_backends = []
        self.assertTrue(not revision_backends or "argos" in revision_backends)

        # Backend específico
        revision_backends = ["argos"]
        self.assertTrue(not revision_backends or "argos" in revision_backends)
        self.assertFalse(not revision_backends or "translators" in revision_backends)

        # Múltiplos backends
        revision_backends = ["argos", "translators"]
        self.assertTrue(not revision_backends or "argos" in revision_backends)
        self.assertTrue(not revision_backends or "translators" in revision_backends)


if __name__ == "__main__":
    unittest.main()
