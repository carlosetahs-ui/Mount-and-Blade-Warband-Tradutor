"""Validação do Argos antes de iniciar um lote, sem modelos ou downloads reais."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translator import ArgosBackend


class Language:
    def __init__(self, code):
        self.code = code
        self.routes = {}

    def get_translation(self, target):
        return self.routes.get(target.code)


class ArgosSetupTests(unittest.TestCase):
    def setUp(self):
        self.package = types.ModuleType("argostranslate.package")
        self.package.update_package_index = Mock()
        self.package.get_available_packages = Mock(return_value=[])
        self.package.install_from_path = Mock()
        self.translate = types.ModuleType("argostranslate.translate")
        self.translate.get_installed_languages = Mock(return_value=[])
        argos = types.ModuleType("argostranslate")
        argos.package, argos.translate = self.package, self.translate
        modules = {"argostranslate": argos,
                   "argostranslate.package": self.package,
                   "argostranslate.translate": self.translate}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.en, self.pt = Language("en"), Language("pt")
        self.route = Mock()
        self.route.translate.return_value = "Olá"

    def assert_no_downloads(self):
        self.package.update_package_index.assert_not_called()
        self.package.get_available_packages.assert_not_called()
        self.package.install_from_path.assert_not_called()

    def test_missing_languages_fail_without_download_when_disabled(self):
        with self.assertRaisesRegex(ValueError, "en -> pt"):
            ArgosBackend(auto_install=False)
        self.assert_no_downloads()

    def test_installed_languages_do_not_imply_requested_direction(self):
        self.pt.routes["en"] = self.route
        self.translate.get_installed_languages.return_value = [self.en, self.pt]
        with self.assertRaisesRegex(ValueError, "en -> pt"):
            ArgosBackend(auto_install=False)
        self.assert_no_downloads()

    def test_installed_direct_route_is_reused_without_download(self):
        self.en.routes["pt"] = self.route
        self.translate.get_installed_languages.return_value = [self.en, self.pt]
        backend = ArgosBackend()
        self.assertEqual(backend.translate("Hello"), "Olá")
        self.route.translate.assert_called_once_with("Hello")
        self.assert_no_downloads()

    def test_argos_pivot_route_is_reused_without_download(self):
        # get_installed_languages() já expõe rotas compostas pelo próprio Argos.
        pivot_route = Mock()
        pivot_route.translate.return_value = "Olá pelo idioma intermediário"
        self.en.routes["pt"] = pivot_route
        self.translate.get_installed_languages.return_value = [self.en, Language("es"), self.pt]
        backend = ArgosBackend(auto_install=False)
        self.assertEqual(backend.translate("Hello"), "Olá pelo idioma intermediário")
        self.assert_no_downloads()

    def test_missing_direction_is_installed_even_when_both_languages_exist(self):
        self.translate.get_installed_languages.return_value = [self.en, self.pt]
        model = types.SimpleNamespace(from_code="en", to_code="pt", download=Mock(return_value="en-pt.argosmodel"))
        reverse = types.SimpleNamespace(from_code="pt", to_code="en", download=Mock())
        self.package.get_available_packages.return_value = [reverse, model, model]
        self.package.install_from_path.side_effect = lambda _: self.en.routes.update(pt=self.route)
        backend = ArgosBackend()
        self.assertEqual(backend.translate("Hello"), "Olá")
        self.package.update_package_index.assert_called_once_with()
        self.package.install_from_path.assert_called_once_with("en-pt.argosmodel")
        model.download.assert_called_once_with()
        reverse.download.assert_not_called()

    def test_missing_catalog_pair_fails_before_translation(self):
        with self.assertRaisesRegex(ValueError, "en -> pt.*catálogo"):
            ArgosBackend()
        self.package.install_from_path.assert_not_called()

    def test_installation_is_checked_for_a_usable_translation(self):
        model = types.SimpleNamespace(from_code="en", to_code="pt", download=Mock(return_value="en-pt.argosmodel"))
        self.package.get_available_packages.return_value = [model]
        with self.assertRaisesRegex(ValueError, "não há tradução instalada.*en -> pt"):
            ArgosBackend()
        self.package.install_from_path.assert_called_once_with("en-pt.argosmodel")


if __name__ == "__main__":
    unittest.main()
