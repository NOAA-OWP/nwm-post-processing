#!/usr/bin/env python3
"""
The entrypoint for the core post-processing application
"""
import os
import typing
import argparse
import logging
import pathlib
import sys

from post_processing.configuration import settings
from post_processing.utilities.logging import setup_logging
from post_processing.exceptions import ArgumentValidationException
from post_processing.utilities.common import get_cycle_files
from post_processing.utilities.common import NWM_FILENAME_PATTERN

from post_processing.enums import Region
from post_processing.enums import Configuration
from post_processing.enums import ModelOutputType

from post_processing.schema import InputManifest
from post_processing.schema.profile import Profile
from post_processing.schema.profile import get_profile

if __name__.endswith("__main__"):
    setup_logging()

LOGGER: logging.Logger = logging.getLogger("post-process")

class Arguments:
    """
    Command line input
    """
    def __init__(self, *args):
        self.source_file: pathlib.Path = None
        """Where to get the data to process"""
        self.destination: pathlib.Path = None
        """Where to put the post processed data"""
        self.summarize: bool = False
        """Whether to summarize the profile rather than running it"""

        self.__parse(args=args)
        self.__validate()

    def __validate(self):
        """
        Raise exceptions if arguments are invalid
        """
        messages: typing.List[str] = []

        if not self.source_file.exists():
            messages.append(f"Cannot process data within the '{self.source_file}' file - it does not exist")
        if self.source_file.is_dir():
            messages.append(
                f"Cannot process data from within '{self.source_file}' - it is a directory but a file is required"
            )
        
        if messages:
            raise ArgumentValidationException(__file__, messages=messages)

    def __parse(self, args: typing.Sequence[str]):
        """
        Parse passed in command line input
        """
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description="Process National Water Model output for easier use"
        )

        parser.add_argument(
            "source_file",
            type=pathlib.Path,
            help="Where to get input data"
        )

        parser.add_argument(
            "destination",
            type=pathlib.Path,
            help="Where to put the output"
        )

        parser.add_argument(
            "--summarize",
            action="store_true",
            help="Describe what will occur rather than running the post processing operations"
        )

        parameters: argparse.Namespace = parser.parse_args(args=args) if args else parser.parse_args()

        for key, value in vars(parameters).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                LOGGER.warning(
                    f"{self.__class__.__module__}.{self.__class__.__qualname__} does not have an attribute named '{key}'"
                )



def main() -> int:
    """
    The entry point of the script

    :returns: The status code of the application run
    """
    try:
        arguments: Arguments = Arguments()
    except ArgumentValidationException as exception:
        LOGGER.critical(str(exception))
        return 2

    try:
        cycle_files: typing.Sequence[pathlib.Path] = get_cycle_files(arguments.source_file)
    except Exception as exception:
        LOGGER.critical(f"Could not find files to process within this cycle: {exception}")
        return 1

    if len(cycle_files) == 0:
        LOGGER.critical("Cycle files could not be found")
        return 1

    file_attributes = NWM_FILENAME_PATTERN.match(arguments.source_file.name).groupdict()

    manifest: InputManifest = InputManifest(
        region=Region.from_string(file_attributes["region"]),
        configuration=Configuration.from_string(file_attributes["configuration"]),
        output_type=ModelOutputType.from_string(file_attributes["output_type"]),
        cycle=file_attributes["cycle"],
        files=cycle_files,
        member=file_attributes["member"]
    )

    profiles: typing.Sequence[Profile] = get_profile(manifest=manifest)
    
    try:
        if profiles:
            for profile in profiles:
                if arguments.summarize:
                    print(str(profile))
                    continue
                    
                outputs: typing.Sequence[pathlib.Path] = profile.run(
                    cycle=manifest.cycle,
                    files=manifest.files,
                    output_path=arguments.destination
                )
                LOGGER.info(
                    f"The results for the profile for {profile.output_type.describe()} data run within the "
                    f"{profile.configuration.describe()} configuration across {profile.region.describe()} were written to:{os.linesep}"
                    f"    - {(os.linesep + '    - ').join(map(str, outputs))}"
                )
        else:
            LOGGER.warning(f"No profiles were found for '{manifest}'. Nothing will be processed")
    except BaseException as exception:
        LOGGER.critical(exception, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    if settings.debug:
        LOGGER.warning("Debug mode is enabled. Stop and disable if this is a testing or production environment.")
    sys.exit(main())
