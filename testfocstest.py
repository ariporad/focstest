"""Tests for focstest.py, from the creators of focstest.py"""
import unittest
import doctest

import focstest
from focstest import (
    get_tests,
    equivalent,
    strip_whitespace,
    normalize_whitespace,
    run_ocaml_code,
    OcamlError,
    UnimplementedException,
)


def load_tests(loader, tests, ignore):
    # run doctests from within unittest
    # see: <https://docs.python.org/3/library/doctest.html#unittest-api>
    tests.addTests(doctest.DocTestSuite(focstest))
    return tests


class TestTestParsing(unittest.TestCase):

    def test_funky_tests(self):
        text = '\n'.join((
            '# run tm_q2_not "0#1";; ',
            'start  [>] 0  #  1',
            '- : bool = true',
            '# run tm_q2_not "000#111";;',
            'start  [>] 0  0  0  #  1  1  1',
            '- : bool = true',
        ))
        res = get_tests(text)
        self.assertEqual(2, len(res))
        self.assertEqual(
            ('run tm_q2_not "0#1";;', 'start  [>] 0  #  1\n- : bool = true'),
            res[0]
        )
        self.assertEqual(
            ('run tm_q2_not "000#111";;', 'start  [>] 0  0  0  #  1  1  1\n- : bool = true'),
            res[1]
        )


class TestTextNormalization(unittest.TestCase):
    """Test text normalization techniques with real-world examples."""

    def test_normalize_whitespace(self):
        # add examples here in the format (expected output, generated output)
        cases = [
            ('- : int list =\n[19; 58; 29; 88; 44; 22; 11; 34; 17; 52; 26; 13; 40; 20; 10; 5; 16; 8; 4; 2; 1]',
            '- : int list =\n[19; 58; 29; 88; 44; 22; 11; 34; 17; 52; 26; 13; 40; 20; 10; 5; 16; 8; 4; 2;\n 1]\n')
        ]
        for expected, generated in cases:
            self.assertEqual(
                normalize_whitespace(expected),
                normalize_whitespace(generated))


class TestOcamlReplParsing(unittest.TestCase):
    error = \
        "Characters 0-9:\n" \
        "failworth \"Not implemented\"\n" \
        "^^^^^^^^^\n" \
        "Error: Unbound value failworth\n" \
        "Hint: Did you mean failwith?"
    exception = "Exception: Failure \"Not Implemented\"."
    printed = "foo\nbar\n- : unit = ()"
    unknown = "foo\nbar"

    def test_is_error(self):
        are_errors = (self.error, self.exception)
        not_errors = (self.printed, self.unknown)
        for case in are_errors:
            self.assertIsNotNone(focstest.parse_error(case))
        for case in not_errors:
            self.assertIsNone(focstest.parse_error(case))


class TestRunOcaml(unittest.TestCase):
    """Test return values from running Ocaml.

    Note: these require `ocaml` to be installed on the system.
    """

    def test_invalid_ocaml_code(self):
        for code, error in [
            ('[1;2]', ValueError),  # valid statement without `;;`
            ('[1;2;;', OcamlError),  # incomplete expression
            ('a_func 1;;', OcamlError),  # undefined function
            # and now a variety of user-defined unimplemented exceptions
            *(('failwith "{}";;'.format(s), UnimplementedException) for s in
                ('Unimplemented', 'unimplemented', 'Not implemented')),
        ]:
            with self.assertRaises(error):
                run_ocaml_code(code)

    def test_valid_ocaml(self):
        for code, output in (
            ('1;;', '- : int = 1'),
            ('"foo";;', '- : string = "foo"'),
            ('[1;2];;', '- : int list = [1; 2]'),
        ):
            self.assertEqual(output, run_ocaml_code(code))


if __name__ == '__main__':
    unittest.main()
