"""The long account name convention (docs/60, rules approved by the owner 2026-10-21) as a pure function.

Use the Salesforce name when it has 40 characters or fewer; otherwise keep the first two words, turn the last word plus the legal form
into capital initials, drop filler words, then initialise middle words from the right until it fits. Never touch the first two words.
Hermetic, no I/O.
"""

from __future__ import annotations

import unittest

from integration.onboarding import account_name_shortener as short


class ExamplesTests(unittest.TestCase):
    def test_the_owner_example(self):
        result = short.propose("Barmherzige Brüder gemeinnützige Träger GmbH")
        self.assertEqual(result["proposed"], "Barmherzige Brüder gemeinnützige TG")
        self.assertIs(result["changed"], True)

    def test_the_docs_60_second_example(self):
        self.assertEqual(short.propose("Example Städtische Kliniken Betriebs gGmbH")["proposed"], "Example Städtische Kliniken BG")

    def test_a_name_of_40_characters_or_fewer_is_unchanged(self):
        name = "Advanced Technology International"
        result = short.propose(name)
        self.assertEqual((result["proposed"], result["changed"]), (name, False))
        forty = "A" * 12 + " " + "B" * 12 + " " + "C" * 12  # exactly 38
        self.assertFalse(short.propose(forty)["changed"])
        exactly = ("X" * 9 + " ") * 3 + "Y" * 9 + "Z"  # 40 characters
        self.assertEqual(len(exactly), 40)
        self.assertFalse(short.propose(exactly)["changed"])

    def test_forty_one_characters_is_shortened(self):
        name = "Alphabet Betaville Gammaworks Deltaforce GmbH"
        self.assertGreater(len(name), 40)
        self.assertEqual(short.propose(name)["proposed"], "Alphabet Betaville Gammaworks DG")


class RuleTests(unittest.TestCase):
    def test_the_first_two_words_are_never_changed(self):
        result = short.propose("Münchner Rück Versicherungs und Beteiligungs Verwaltungs gemeinnützige Träger GmbH")
        self.assertTrue(result["proposed"].startswith("Münchner Rück "))
        self.assertLessEqual(len(result["proposed"]), short.LIMIT)

    def test_filler_words_go_first_and_then_middle_words_are_initialised_from_the_right(self):
        name = "Nordwest Medizin Zentrum für Hochschulmedizin und angewandte Forschung Betriebs GmbH"
        result = short.propose(name)
        self.assertEqual(result["proposed"], "Nordwest Medizin Zentrum H A F BG")
        self.assertTrue(result["proposed"].startswith("Nordwest Medizin "))
        self.assertNotIn(" für ", result["proposed"])
        self.assertNotIn(" und ", result["proposed"])

    def test_legal_forms_use_their_first_letter(self):
        for legal, initial in (("GmbH", "G"), ("gGmbH", "G"), ("AG", "A"), ("SE", "S"), ("KG", "K"), ("UG", "U"), ("e.V.", "E"),
                               ("Ltd", "L"), ("Inc", "I"), ("B.V.", "B"), ("S.A.", "S"), ("LLC", "L")):
            result = short.propose("Alphabet Betaville Gammaworks Hochschulverwaltung " + legal)
            self.assertTrue(result["proposed"].endswith(" H" + initial), (legal, result["proposed"]))

    def test_without_a_legal_form_only_the_last_word_becomes_an_initial(self):
        result = short.propose("Alphabet Betaville Gammaworks Deltaforce Hochschulverwaltung")
        self.assertEqual(result["proposed"], "Alphabet Betaville Gammaworks D H")

    def test_a_name_that_cannot_be_shortened_fails_closed(self):
        for name in ("A" * 45, "Alphabetisierungsgesellschaftsvereinigung Betaville GmbH", "B" * 30 + " " + "C" * 30):
            result = short.propose(name)
            self.assertIsNone(result["proposed"], name)
            self.assertEqual(result["reason"], "cannot_shorten")

    def test_the_result_never_exceeds_the_limit_and_keeps_the_first_two_words(self):
        for name in ("Barmherzige Brüder gemeinnützige Träger GmbH", "Example Städtische Kliniken Betriebs gGmbH",
                     "Alphabet Betaville Gammaworks Deltaforce Hochschulverwaltung und Betrieb GmbH"):
            result = short.propose(name)
            self.assertLessEqual(len(result["proposed"]), short.LIMIT)
            self.assertEqual(result["proposed"].split()[:2], name.split()[:2])

    def test_whitespace_is_collapsed_and_blank_or_odd_input_is_refused(self):
        self.assertEqual(short.propose("  Barmherzige   Brüder gemeinnützige  Träger GmbH ")["proposed"],
                         "Barmherzige Brüder gemeinnützige TG")
        for bad in ("", "   ", None, 5, "bad\nname"):
            with self.assertRaises(ValueError):
                short.propose(bad)

    def test_the_steps_explain_what_was_done(self):
        steps = short.propose("Barmherzige Brüder gemeinnützige Träger GmbH")["steps"]
        self.assertTrue(steps)
        self.assertTrue(all(isinstance(step, str) for step in steps))
        self.assertEqual(short.propose("Short Name")["steps"], [])


if __name__ == "__main__":
    unittest.main()
