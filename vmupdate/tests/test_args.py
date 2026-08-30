import argparse
from argparse import ArgumentParser

import pytest
from vmupdate.agent.source.args import AgentArgs


@pytest.fixture
def get_parser():
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)

    yield parser


def test_argparse_options_invalid_choice(get_parser: ArgumentParser) -> None:
    with pytest.raises(SystemExit):
        get_parser.parse_args(["--log", "INVALID"])


def test_argparse_options_valid_choice(get_parser: ArgumentParser) -> None:
    parsed_args = get_parser.parse_args(["--log", "dEbuG"])
    assert parsed_args.log == "DEBUG"
