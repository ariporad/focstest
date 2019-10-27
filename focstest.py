#!/usr/bin/env python3
import argparse
import logging
import os
from pkg_resources import get_distribution, DistributionNotFound
import re
import subprocess
import sys
import tempfile
import urllib.parse

from bs4 import BeautifulSoup
import requests
from termcolor import colored


logger = logging.getLogger(name=__name__)  # create logger in order to change level later
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())


# get version from setuptools installation
try:
    __version__ = get_distribution('focstest').version
except DistributionNotFound:
    # not installed
    # TODO: try git directly
    __version__ = 'unknown, try `git describe`'


# default url matching
BASE_URL = "http://rpucella.net/courses/focs-fa19/homeworks/"  # website and path to look under
OCAML_FILE_PATTERN = "homework(\d{1,2}).ml"  # pattern to pass the user-given ocaml file
HTML_FILE_TEMPLATE = "homework{}.html"  # template to build the html filename given a homework number

# selectors for parsing html
CODE_BLOCK_SELECTOR = 'pre code'  # css selector to get code blocks

# regex patterns for parsing text
TEST_PATTERN = "^# (.+;;) *\n(.*)$"  # pattern to get input and output
OCAML_PATTERN = "^(.*)"  # pattern to grab output of lines

# compile regexes ahead of time
OCAML_FILE_COMP = re.compile(OCAML_FILE_PATTERN)
TEST_COMP = re.compile(TEST_PATTERN, re.MULTILINE + re.DOTALL)
OCAML_COMP = re.compile(OCAML_PATTERN, re.MULTILINE + re.DOTALL)


def get_blocks(html):
    """Parse code blocks from html.

    :param html: html text
    :returns: list of strings of code blocks
    """
    page = BeautifulSoup(html, 'html.parser')
    code_blocks = page.select(CODE_BLOCK_SELECTOR)
    if len(code_blocks) == 0:
        logger.error('Code block selector {!r} returned no matches'.format(
            CODE_BLOCK_SELECTOR))
    return [block.get_text() for block in code_blocks]


def get_tests(text):
    """Parse Ocaml tests from text.

    :returns: list of test tuples with format (input, expected, output)
    """
    tests = []
    # iteratively match through the text
    while text != '':
        eot = text.find('\n# ')
        if eot == -1:
            eot = None
        match = TEST_COMP.match(text[0:eot])
        if match is None:
            logger.error("Couldn't parse test {!r}".format(text))
            break
        tests.append(tuple(txt.strip() for txt in match.groups()))
        text = text[match.end()+1:]
    return tests


def _run_ocaml_code(code, timeout=5):
    """Run ocaml code with the REPL and capture the output.

    `code` should generally cause the repl to exit, or a TimeoutExpired
    exception will be raised.

    :param code: string of code to run
    :returns: tuple of raw stdout and stderr strings (output, errors)
    """
    # -noinit disables loading the init file
    # unsetting TERM prevents ocaml from returning escape characters
    env = os.environ.copy()
    env.pop('TERM', None)
    with subprocess.Popen(['ocaml', '-noinit'],
                          env=env,
                          stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        try:
            outs, errs = p.communicate(code, timeout)
        except subprocess.TimeoutExpired as e:
            p.kill()
            outs, errs = p.communicate()
            logger.warning('Ocaml process timed out: {} {}'.format(outs, errs))
            raise e
    return (outs, errs)


class OcamlError(Exception):
    pass


class UnimplementedException(OcamlError):
    pass


def parse_error(output):
    """Check a section of Ocaml output for errors/exceptions.

    Returns the text of an error (potentially) if `output` is an Ocaml Error or
        Exception, None otherwise.
    """
    keywords = ('Error:', 'Exception:')  # what to look for at the beginning of lines
    for k in keywords:
        loc = output.find(k)
        if loc != -1:
            # try to grab the entire error message w/ context
            start = output.rfind('Characters', 0, loc)
            if start != -1:
                return output[start:]
            start = output.rfind('File', 0, loc)
            if start != -1:
                return output[start:]
            return output[loc:]


def run_ocaml_code(code, files=()):
    """Returns parsed output after loading `files` and running `code`.

    Raises a ValueError if `code` is a syntactically-correct but incomplete
        Ocaml expression.

    Raises an `OcamlError` if during the execution of `code`:
        - an Ocaml error occurs
        - an Ocaml exception is raised

    Raises an `UnimplementedException` if it recognizes a 'not implemented'
        exception, commonly used by FoCS HW that haven't been finished yet.

    Returns the output of `code`, which should include any printed output and the
        type expression.
    """
    cmds = [code]
    for file in files:
        cmds.insert(0, '#use "{}";;'.format(file))
    cmds.append('#quit;;\n')  # add a quit command at the end to exit from the repl

    outs, errs = _run_ocaml_code('\n'.join(cmds))
    # Before each input, ocaml spits out a `# ` (the interactive prompt).
    # Here, it is used to separate prints/return values from statements.
    matches = [m.strip() for m in outs.split('# ')]
    # matches should line up to be:
    # startup text | [ file output | ... ] code output | quit whitespace
    # first match is everything printed on startup, generally just version info
    # last match is the remaining whitespace after the `#quit;;` command, unless
    # something went wrong]
    expected = 1 + len(files) + 2
    logger.debug('Found %s matches, expected %s', len(matches), expected)

    if len(matches) != expected:
        # look for reasons why it failed
        # the issue should be with the code statement
        # #use statements may return errors, but they should still evaluate
        raise ValueError("Couldn't evaluate code {!r}: {!r}".format(code, matches[-1]))

    # parse for errors, exceptions in all outputs
    for output in matches:
        err = parse_error(output)
        if err is None:
            continue
        # try to find incomplete expressions for `code`
        if err.find('It has no method quit') != -1:
            raise ValueError('Incomplete Ocaml Expression: {!r}'.format(code))
        # catch a variety of `unimplemented`-like `failwith`s
        if err.lower().find('implemented') != -1:
            raise UnimplementedException('{}: {!r}'.format(err, code))
        else:
            raise OcamlError(err)

    return matches[-2]


# text normalization techniques

def equivalent(text):
    return text

def strip_whitespace(text):
    return text.strip()

def normalize_whitespace(text):
    """Replace instances of whitespace with ' '.

    >>> normalize_whitespace(' a\\n b c \td\\n')
    'a b c d'
    """
    return ' '.join(text.split())


def run_test(code: str, expected_out: str, file: str = None):
    """Check the output of a line of code against an expected value.

    :param code: code to run
    :param expected_out: the expected output of the code
    :param file: the path to a file to load in the interpreter before running
        the code
    :returns: tuple of a boolean indicating the results of the test and the
        output of the command
    """

    steps = [
        equivalent,
        strip_whitespace,
        normalize_whitespace
    ]

    output = run_ocaml_code(code, files=(file,))
    for step in steps:
        function = code.split()[0]  # grab the first word of the command (probably the function name)
        method = step.__name__
        result = step(output) == step(expected_out)
        if result is True:
            logger.debug('Test {!r} passed with method {!r}'.format(function, method))
            break
    return (result, output, method)


def get_test_str(test_input: str, test_output: str, expected: str,
                 use_color=True, indent='  '):
    """Create an explanatory str about a test for printing."""
    def format_info(kind, value):
        return indent+kind.upper()+':\t'+repr(value)
    lines = [
        format_info('input', test_input),
        format_info('expected', expected),
        format_info('output', test_output),
    ]
    return '\n'.join(lines)


def infer_url(filepath):
    """Infer a url based on a filename.

    Basically connects 'homeworkX.ml' -> 'http://.../hX.html'

    Returns: False if the filename could not be named

    >>> infer_url('foo/bar.ml')
    False

    >>> infer_url('foo/bar/homework1.ml')
    'http://rpucella.net/courses/focs-fa19/homeworks/homework1.html'
    """
    filename = os.path.basename(filepath)
    match = OCAML_FILE_COMP.match(filename)
    if not match:
        return False
    hw_num = match.group(1)
    url = urllib.parse.urljoin(BASE_URL, HTML_FILE_TEMPLATE.format(hw_num))
    return url


def main():
    parser = argparse.ArgumentParser(
        description='Run ocaml "doctests".',
        epilog='Submit bugs to <https://github.com/olin/focstest/issues/>.')
    parser.add_argument('--version', action='version', version=__version__)
    input_types = parser.add_mutually_exclusive_group(required=False)
    input_types.add_argument('--url', type=str,
                             help='a url to scrape tests from (usually automagically guessed from ocaml-file)')
    parser.add_argument('ocaml-file', type=str,
                        help='the ocaml file to test against')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='increase test output verbosity')
    parser.add_argument('-uc', '--update-cache', action='store_true',
                        help='update cached files')
    test_selection = parser.add_mutually_exclusive_group(required=False)
    test_selection.add_argument('-u', '--use-suites', metavar='N', type=int, nargs='*',
                                help='test suites to use exclusively, indexed from 1')
    test_selection.add_argument('-s', '--skip-suites', metavar='N', type=int, nargs='*',
                                help='test suites to skip, indexed from 1')
    args = parser.parse_args()

    # check environment var for logging level
    log_level = os.getenv('LOG_LEVEL')
    if log_level is not None:
        log_level = log_level.upper()
        try:
            numeric_level = getattr(logging, os.getenv('LOG_LEVEL'))
        except AttributeError as e:
            logging.warning("Found 'LOG_LEVEL' env var, but was unable to parse: {}".format(e))
        else:
            logger.setLevel(numeric_level)
            logger.debug('Set logging level to {!r} ({}) from env var'.format(log_level, numeric_level))

    URL = args.url
    FILE = getattr(args, 'ocaml-file')

    if not args.url:
        url_guess = infer_url(FILE)
        if not url_guess:  # break if filename can't be matched
            logger.critical('Could not infer url from filename {!r}. Try passing a url manually with the `--url` flag.'.format(FILE))
            sys.exit(1)
        else:
            URL = url_guess

    # get and cache webpage
    temp_dir = tempfile.gettempdir()  # most likely /tmp/ on Linux
    CACHE_DIR = os.path.join(temp_dir, 'focstest-cache')
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        logger.info('Created cache directory at {!r}'.format(CACHE_DIR))
    page_name = os.path.basename(urllib.parse.urlparse(URL).path)  # get page name from url
    html_filepath = os.path.join(CACHE_DIR, page_name)  # local filepath

    # get webpage if cached version doesn't already exist
    if not os.path.isfile(html_filepath) or args.update_cache:
        response = requests.get(URL)
        if response.status_code != 200:  # break if webpage can't be fetched
            logger.critical("Unable to fetch url {}: Status {}: {}".format(
                URL,
                response.status_code,
                response.reason))
            sys.exit(1)
        # write to file and continue
        html = response.text
        with open(html_filepath, 'w') as htmlcache:
            htmlcache.write(html)
            logger.debug("Saved {!r} to cache at {!r}".format(URL, html_filepath))
    else:
        logger.debug("Using cached version of page at {!r}".format(html_filepath))
        with open(html_filepath, 'r') as htmlcache:
            html = htmlcache.read()

    # parse for code blocks
    # TODO: get titles/descriptions from code blocks
    blocks = get_blocks(html)
    # parse code blocks for tests
    test_suites = list(enumerate(filter(None, (get_tests(b) for b in blocks)), 1))  # list of suites and indices (starting at 1) (skipping empty suites)
    num_tests = sum([len(suite) for j, suite in test_suites])
    logger.info("Found {} test suites and {} tests total".format(
        len(test_suites), num_tests))
    # TODO: save tests to file

    # run tests
    if not os.path.exists(FILE):
        logger.critical("File {} does not exist".format(FILE))
        sys.exit(1)
    num_failed = 0
    num_skipped = 0

    # select test suites based on args
    # i is indexed from 0, j is indexed from 1
    if args.use_suites:
        skipped_suites = [suite for j, suite in test_suites if j not in args.use_suites]
        for suite in skipped_suites:
            num_skipped += len(suite)
        test_suites = [test_suites[j-1] for j in args.use_suites]
    elif args.skip_suites:
        skipped_suites = [test_suites[j-1][1] for j in args.skip_suites]
        for suite in skipped_suites:
            num_skipped += len(suite)
        test_suites = [(j, suite) for j, suite in test_suites if j not in args.skip_suites]

    print('Starting tests')
    for j, suite in test_suites:
        if args.verbose:
            print('Testing suite {}'.format(j))
        for k, (test, expected_output) in enumerate(suite):
            header_temp = ' test {} of {} in suite {}'.format(k+1, len(suite), j)
            try:
                res = run_test(test, expected_output, file=FILE)
            except UnimplementedException as e:
                # skip unimplemented suites
                if args.verbose:
                    print(colored('Unimplemented'+header_temp, 'yellow'))
                    print(test_str)
                num_skipped += len(suite) - (k + 1)
                print(colored('Skipped unimplemented suite {} {!r}'.format(j, function), 'yellow'))
                break
            except ValueError as e:
                # skip tests that can't be run
                print(colored('Unable to run test {!r}: {}'.format(test, OcamlError), 'yellow'))
                continue
            except OcamlError as e:
                # break everything on other Ocaml errors
                print(colored("Ocaml returned the following error:", 'red', attrs=['bold']))
                print(colored(e, 'red'))
                sys.exit(1)
            if res is None:  # skip unparsable texts
                print(colored('Skipped'+header_temp+': Unable to parse output', 'yellow'))
                continue
            else:
                result, output, method = res
            test_str = get_test_str(test, output, expected_output)
            function = test.split()[0]
            if result is False:
                num_failed += 1
                print(colored('Failed'+header_temp, 'red'))
                print(test_str)
            elif args.verbose:
                header = 'Passed'+header_temp
                if method not in ['equivalent', 'strip_whitespace']:
                    header += ' w/ method '+method
                print(colored(header, 'green'))
                print(test_str)
        if args.verbose:
            print('-'*80)
    print('Finished testing')
    fail_summary = '{} of {} tests failed'.format(num_failed, num_tests - num_skipped)
    if num_failed > 0:
        print(colored(fail_summary, 'red'))
    else:
        print(colored(fail_summary, 'green'))
    skip_summary = '{} tests skipped'.format(num_skipped)
    if num_skipped > 0:
        print(colored(skip_summary, 'yellow'))
    else:
        print(skip_summary)


if __name__ == "__main__":
    main()
