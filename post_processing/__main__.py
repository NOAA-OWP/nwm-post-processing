#!/usr/bin/env python3
"""
The entrypoint for the core post processing application
"""
import typing
import argparse
import logging
import pathlib
import sys

from post_processing.utilities.logging import setup_logging

if __name__ == "__main__":
    setup_logging()

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def main() -> int:
    """
    The entry point of the script

    :returns: The status code of the application run
    """
    return 0


if __name__ == "__main__":
    sys.exit(main())