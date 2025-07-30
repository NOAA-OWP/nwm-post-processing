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

from datetime import datetime

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
        self.peek: bool = False
        """Print the headers of each produced file at the end"""
        self.version: bool = False
        """Print the version rather than run a profile"""
        self.settings: bool = False
        """Print available settings rather than run a profile"""

        self.__parse(args=args)
        self.__validate()

    def __validate(self):
        """
        Raise exceptions if arguments are invalid
        """
        messages: typing.List[str] = []

        if self.settings or self.version:
            return

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
        subcommand_parser: argparse.ArgumentParser = argparse.ArgumentParser()
        subparsers = subcommand_parser.add_subparsers(dest="subcommand", required=True)
        subparsers.add_parser("settings", description="Show available settings")
        subparsers.add_parser("version", description="Show application version")

        subcommand_usages: str = (
            f"Subcommands:{os.linesep}"
            f"    {(os.linesep + '    - ').join([str(action.prog.split()[-1:][0]).ljust(10) + ': ' + (action.description or action.format_help()) for action in subparsers.choices.values()])}"
        )

        if len(sys.argv) > 1 and sys.argv[1].lower() == 'settings':
            self.settings = True
            return
        elif len(sys.argv) > 1 and sys.argv[1].lower() == 'version':
            self.version = True
            return

        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description="Process National Water Model output for easier use",
            epilog=subcommand_usages,
            formatter_class=argparse.RawDescriptionHelpFormatter,
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

        parser.add_argument(
            "--peek",
            "-p",
            action="store_true",
            help="Print the headers of each produced file"
        )

        parameters: argparse.Namespace = parser.parse_args(args=args) if args else parser.parse_args()

        for key, value in vars(parameters).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                LOGGER.warning(
                    f"{self.__class__.__module__}.{self.__class__.__qualname__} does not have an attribute named '{key}'"
                )


def show_version():
    import subprocess

    try:
        commit: str = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except:
        commit: str = "Unknown"

    version: str = "Unknown"
    if os.path.exists('__version__.py'):
        version: str = pathlib.Path('__version__.py').read_text().strip()
    elif os.path.exists(pathlib.Path(__file__).parent / '__version__.py'):
        version: str = (pathlib.Path(__file__).parent / '__version__.py').read_text().strip()
    elif (settings.application_path / "pyproject.toml").is_file():
        import tomllib
        pyproject_data: typing.Dict[str, typing.Any] = tomllib.loads((settings.application_path / "pyproject.toml").read_text())

        if 'project' in pyproject_data and 'version' in pyproject_data['project']:
            version = pyproject_data['project']['version']

        if 'tool' in pyproject_data and 'poetry' in pyproject_data['tool']:
            version = pyproject_data['tool']['poetry'].get("version", "Unknown")
    elif (settings.application_path / "setup.cfg").is_file():
        import configparser
        parser: configparser.ConfigParser = configparser.ConfigParser()
        parser.read(settings.application_path / "setup.cfg")
        if parser.has_section("metadata") and parser.has_option("metadata", "version"):
            version = parser.get("metadata", "version")

    versions: typing.List[str] = [
        f"{'Git Commit'.ljust(20)}: {commit}",
        f"{'Application Version'.ljust(20)}: {version}",
    ]

    print(os.linesep.join(versions))


def show_settings():
    from pprint import pprint
    pprint(settings.to_dict())


def main() -> int:
    """
    The entry point of the script

    :returns: The status code of the application run
    """
    start_time = datetime.now()
    try:
        arguments: Arguments = Arguments()
    except ArgumentValidationException as exception:
        LOGGER.critical(str(exception))
        return 2

    if arguments.settings:
        try:
            show_settings()
            return 0
        except Exception as e:
            LOGGER.critical(str(e))
            return 1

    if arguments.version:
        try:
            show_version()
            return 0
        except Exception as e:
            LOGGER.critical(str(e))
            return 1

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

                if arguments.peek:
                    for output in outputs:
                        from post_processing.utilities.netcdf import load_netcdf
                        data = load_netcdf(output)
                        LOGGER.info(f"Output: {output}:{os.linesep}{data}")
        else:
            LOGGER.warning(f"No profiles were found for '{manifest}'. Nothing will be processed")
    except BaseException as exception:
        LOGGER.critical(exception, exc_info=True)
        return 1
    LOGGER.info(f"Operation complete in {datetime.now() - start_time}")
    return 0


if __name__ == "__main__":
    if settings.debug:
        LOGGER.warning("Debug mode is enabled. Stop and disable if this is a testing or production environment.")
    sys.exit(main())
