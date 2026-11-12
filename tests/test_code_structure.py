"""Preservação da posição de argumentos e da identidade de cada condição."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mbloc


class CodeStructureTests(unittest.TestCase):
    def test_unnumbered_printf_arguments_cannot_change_order(self):
        self.assertTrue(mbloc.check_pair("Hello %d %s", "Olá %s %d"))

    def test_printf_width_precision_and_length_cannot_change(self):
        source = "Values %08d %.2f %lld %zu %p"
        self.assertFalse(mbloc.check_pair(source, source.replace("Values", "Valores")))
        for original, changed in (("%08d", "%8d"), ("%.2f", "%.3f"),
                                  ("%lld", "%ld"), ("%zu", "%u"), ("%p", "%s")):
            with self.subTest(original=original):
                self.assertTrue(mbloc.check_pair(source, source.replace(original, changed)))

    def test_numbered_printf_arguments_can_change_order(self):
        self.assertFalse(mbloc.check_pair("%2$s scored %1$04d.", "%1$04d pontos para %2$s."))

    def test_numbered_printf_arguments_keep_their_identity(self):
        self.assertTrue(mbloc.check_pair("%2$s scored %1$04d.", "%1$s marcou %2$04d."))

    def test_unnumbered_arguments_keep_order_among_numbered_arguments(self):
        self.assertTrue(mbloc.check_pair("%1$s %d %s", "%s %1$s %d"))

    def test_unnumbered_star_arguments_cannot_change_order(self):
        self.assertTrue(mbloc.check_pair("%1$*s %2$*d", "%2$*d %1$*s"))

    def test_numbered_value_width_and_precision_can_change_order(self):
        self.assertFalse(mbloc.check_pair("%1$*2$.*3$f %4$s", "%4$s %1$*2$.*3$f"))

    def test_unnumbered_precision_star_cannot_change_order(self):
        self.assertTrue(mbloc.check_pair("%1$.*f %2$.*s", "%2$.*s %1$.*f"))

    def test_literal_percent_keeps_its_position_among_printf_formats(self):
        self.assertTrue(mbloc.check_pair("%d%% for %s", "%% %d para %s"))

    def test_repeated_conditional_occurrences_cannot_exchange_codes(self):
        source = "{reg1?%s one:none} {reg1?%d two:none}"
        translated = "{reg1?%d um:nada} {reg1?%s dois:nada}"
        self.assertTrue(mbloc.check_pair(source, translated))

    def test_codes_cannot_be_redistributed_between_repeated_conditions(self):
        source = "{reg1?%s %d one:none} {reg1?two:none}"
        translated = "{reg1?%s um:nada} {reg1?%d dois:nada}"
        self.assertTrue(mbloc.check_pair(source, translated))

    def test_repeated_gender_choices_cannot_exchange_codes(self):
        source = "{one %s/none} {two %d/none}"
        translated = "{um %d/nada} {dois %s/nada}"
        self.assertTrue(mbloc.check_pair(source, translated))

    def test_conditions_with_distinct_selectors_and_numbered_formats_can_change_order(self):
        source = "{reg1?%1$s many:none} {reg2?%2$d gold:none}"
        translated = "{reg2?%2$d ouro:nada} {reg1?%1$s muitos:nada}"
        self.assertFalse(mbloc.check_pair(source, translated))

    def test_conditions_with_distinct_selectors_without_printf_can_change_order(self):
        self.assertFalse(mbloc.check_pair("{reg1?many:none} {reg2?gold:none}",
                                         "{reg2?ouro:nada} {reg1?muitos:nada}"))

    def test_conditions_with_unnumbered_formats_cannot_change_global_argument_order(self):
        source = "{reg1?%s many:none} {reg2?%d gold:none}"
        translated = "{reg2?%d ouro:nada} {reg1?%s muitos:nada}"
        self.assertTrue(mbloc.check_pair(source, translated))

    def test_conditions_with_identical_unnumbered_formats_keep_their_global_position(self):
        source = "{reg1?%s many:none} {reg2?%s gold:none}"
        translated = "{reg2?%s ouro:nada} {reg1?%s muitos:nada}"
        self.assertTrue(mbloc.check_pair(source, translated))

    def test_conditional_printf_cannot_move_across_a_direct_printf_argument(self):
        self.assertTrue(mbloc.check_pair("%s {reg1?%d gold:none}",
                                         "{reg1?%d ouro:nada} %s"))

    def test_printf_in_second_branch_also_keeps_its_global_position(self):
        self.assertTrue(mbloc.check_pair("%s {reg1?none:%d gold}",
                                         "{reg1?nada:%d ouro} %s"))

    def test_text_inside_repeated_conditions_can_still_be_translated(self):
        source = "{reg1?%s one:none} {reg1?%d two:none}"
        translated = "{reg1?%s um:nada} {reg1?%d dois:nada}"
        self.assertFalse(mbloc.check_pair(source, translated))

    def test_printf_order_is_checked_inside_each_branch(self):
        self.assertTrue(mbloc.check_pair("{reg1?%d %s:none}", "{reg1?%s %d:nada}"))

    def test_normal_variables_and_distinct_conditions_can_change_order(self):
        source = "{s0} {reg1?%1$d many:none} {reg2?%2$s gold:none} {reg3}"
        translated = "{reg3} {reg2?%2$s ouro:nada} {s0} {reg1?%1$d muitos:nada}"
        self.assertFalse(mbloc.check_pair(source, translated))

    def test_color_order_is_still_checked_inside_branches(self):
        self.assertTrue(mbloc.check_pair("{reg1?<c=red>Hello</c>:Bye}",
                                         "{reg1?</c>Olá<c=red>:Adeus}"))


if __name__ == "__main__":
    unittest.main()
