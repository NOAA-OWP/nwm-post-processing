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
import faulthandler
import atexit

import collections.abc as generic

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

if typing.TYPE_CHECKING:
    from concurrent.futures import Executor

faulthandler.enable(all_threads=True)

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
        self.validate: bool = False
        """Just validate to make sure that all profiles are valid"""
        self.analyze: bool = False
        """Whether to analyze performance"""
        self.env_file: pathlib.Path | None = None

        self.__parse(args=args)
        self.__validate()

    def __validate(self):
        """
        Raise exceptions if arguments are invalid
        """
        messages: list[str] = []

        if isinstance(self.env_file, pathlib.Path) and self.env_file.is_dir():
            raise FileNotFoundError(f"Cannot use '{self.env_file}' as a .env file - it is a directory, not a file")
        elif isinstance(self.env_file, pathlib.Path) and self.env_file.is_file():
            settings.apply_env(self.env_file)

        if self.settings or self.version or self.validate:
            return

        if not self.source_file.exists():
            missing_input_message: str | None = None
            from post_processing.nwm_file import NWMFile
            try:
                parsed_name: NWMFile = NWMFile.parse(self.source_file)
                possible_corrected_path: pathlib.Path = self.source_file.parent / str(parsed_name)
                if possible_corrected_path.is_file():
                    missing_input_message = (
                        f"'{self.source_file}' does not exist and cannot be accepted as valid input. "
                        f"Did you mean to use '{possible_corrected_path}'?"
                    )
                    messages.append(missing_input_message)
            except:
                pass

            if missing_input_message is None:
                messages.append(f"Cannot accept '{self.source_file}' as input for post processing - it does not exist")

        if self.source_file.is_dir():
            messages.append(
                f"Cannot use '{self.source_file}' as input for post processing - "
                f"it is a directory but a file is required"
            )

        if self.source_file.is_file():
            try:
                with open(self.source_file, 'rb') as source:
                    head_bytes: bytes = source.read(4)

                if head_bytes not in (b'CDF\x01', b'CDF\x02', b'\x89HDF'):
                    messages.append(
                        f"Cannot use '{self.source_file}' as input - it does not appear to be a valid Netcdf file. Head bytes were '{repr(head_bytes)}'"
                    )
            except:
                pass
        
        if messages:
            raise ArgumentValidationException(__file__, messages=messages)

    def __parse(self, args: generic.Sequence[str]):
        """
        Parse passed in command line input
        """
        # TODO: Once the initial version is out, create a subparser that handles the 'settings', 'version',
        #  and 'run' actions. This hack is here until development has settled down.

        if len(sys.argv) > 1 and sys.argv[1].lower() == 'settings':
            self.settings = True
            return
        elif len(sys.argv) > 1 and sys.argv[1].lower() == 'version':
            self.version = True
            return
        if len(sys.argv) > 1 and sys.argv[1].lower() == 'validate':
            self.validate = True
            return

        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description="Process National Water Model output for easier use",
            epilog=(
                f"Subcommands:{os.linesep}"
                f"  settings :  Print out all configured settings{os.linesep}"
                f"  version  :  Print out version information about the application and what commit is in use{os.linesep}"
                f"  validate :  Make sure all profiles are valid"
            ),
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
            "--env-file",
            "-e",
            dest="env_file",
            type=pathlib.Path,
            default=None,
            help="A path to an optional env file to use for additional configuration"
        )

        parser.add_argument(
            "--peek",
            "-p",
            action="store_true",
            help="Print the headers of each produced file"
        )

        parser.add_argument(
            "--analyze",
            action="store_true",
            help="Measure runtime performance"
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
    """
    Print information about what software version and git commit is currently in use
    """
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
        pyproject_data: dict[str, typing.Any] = tomllib.loads((settings.application_path / "pyproject.toml").read_text())

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

    versions: list[str] = [
        f"{'Git Commit'.ljust(20)}: {commit}",
        f"{'Application Version'.ljust(20)}: {version}",
    ]

    print(os.linesep.join(versions))


def show_settings():
    """
    Print out all configured settings that will be used at runtime
    """
    from pprint import pprint
    print(f"{LOGGER.name} Settings:")
    print(f"===============================================================")
    pprint(settings.to_dict())


def find_invalid_profiles() -> generic.Sequence[str]:
    """
    Find profiles that can't be deserialized

    :returns: Descriptions of each profile that could not be deserialized
    """
    from post_processing.schema.profile import find_invalid_profiles
    return find_invalid_profiles()

@atexit.register
def shutdown():
    """
    Close down any leakable objects

    Registered for 'atexit' AND during 'main' to ensure all bases are covered in order to exit cleanly
    """
    from post_processing.utilities import netcdf
    if settings.this_is_verbose:
        LOGGER.debug(f"Shutting down the gateway")
    netcdf.close_gateway()
    if settings.this_is_verbose:
        LOGGER.debug(f"The gateway was closed")


def clean(executor: typing.Optional["Executor"]):
    if executor is None:
        LOGGER.info(f"There was no executor to shut down")
        return

    LOGGER.info(f"Shutting down '{executor}'")
    from post_processing.work.orchestration import starmap_executor
    from post_processing.transform.subsetting.cache import clean as remove_masks
    from post_processing.transform.reproject import clean as remove_projections
    from post_processing.utilities.netcdf import close_gateway

    mask_removals = starmap_executor(remove_masks, args=[[]] * os.cpu_count() * 3, executor=executor)
    projection_removals = starmap_executor(remove_projections, args=[[]] * os.cpu_count() * 3, executor=executor)
    close_results = starmap_executor(close_gateway, args=[[]] * os.cpu_count() * 3, executor=executor)

    LOGGER.info(f"Removed masks '{len(mask_removals)}' times")
    LOGGER.info(f"Removed projections {len(projection_removals)} times")
    LOGGER.info(f"Closed gateways '{len(close_results)}' times")


def main() -> int:
    """
    The entry point of the script

    :returns: The status code of the application run
    """
    if settings.mpi_is_available:
        from mpi4py import MPI
        communicator: MPI.Intracomm = MPI.COMM_WORLD
        LOGGER.debug(f"MPI is available on Rank {communicator.Get_rank()}, out of {communicator.Get_size()}")

    start_time = datetime.now()
    try:
        arguments: Arguments = Arguments()
    except ArgumentValidationException as exception:
        LOGGER.critical(str(exception))
        return 2

    profiler = None
    if arguments.analyze:
        LOGGER.info("Collecting runtime performance data")
        import cProfile
        profiler: typing.Optional[cProfile.Profile] = cProfile.Profile()
        profiler.enable()

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

    if arguments.validate:
        try:
            invalid_profiles: generic.Sequence[str] = find_invalid_profiles()
            if invalid_profiles:
                LOGGER.critical(
                    f"Invalid profiles were discovered:{os.linesep}"
                    f"    - {(os.linesep + '    - ').join(invalid_profiles)}"
                )
                return 1
            else:
                return 0
        except Exception as e:
            LOGGER.critical(str(e))
            return 1

    if settings.debug:
        LOGGER.info(' '.join(map(str, sys.argv)))
        show_version()
        print()
        show_settings()
        print()

    # Get all files that lie within the same cycle. If `arguments.source_file` is
    # `nwm.t00z.short_range.channel_rt.f018.conus.nc`, this will find all files that belong to t00z, short range,
    # channel_rt, conus
    try:
        cycle_files: generic.Sequence[pathlib.Path] = get_cycle_files(arguments.source_file)
    except Exception as exception:
        LOGGER.critical(f"Could not find files to process within this cycle: {exception}")
        return 1

    if len(cycle_files) == 0:
        LOGGER.critical("Cycle files could not be found")
        return 1

    # Use the NWM_FILENAME_PATTERN to extract the metadata from the filename
    file_attributes = NWM_FILENAME_PATTERN.match(arguments.source_file.name).groupdict()

    # Use the constants that were used to create the pattern to identify the groups of interest
    from post_processing.utilities.common import REGION_PATTERN_VARIABLE
    from post_processing.utilities.common import CONFIGURATION_PATTERN_VARIABLE
    from post_processing.utilities.common import OUTPUT_TYPE_PATTERN_VARIABLE
    from post_processing.utilities.common import CYCLE_PATTERN_VARIABLE
    from post_processing.utilities.common import MEMBER_PATTERN_VARIABLE

    manifest: InputManifest = InputManifest(
        region=Region.from_string(file_attributes[REGION_PATTERN_VARIABLE]),
        configuration=Configuration.from_string(file_attributes[CONFIGURATION_PATTERN_VARIABLE]),
        output_type=ModelOutputType.from_string(file_attributes[OUTPUT_TYPE_PATTERN_VARIABLE]),
        cycle=file_attributes[CYCLE_PATTERN_VARIABLE],
        files=cycle_files,
        member=file_attributes[MEMBER_PATTERN_VARIABLE]
    )

    profiles: generic.Sequence[Profile] = get_profile(manifest=manifest)
    
    try:
        if profiles:
            for profile in profiles:
                try:
                    if arguments.summarize:
                        print(str(profile))
                        continue

                    if settings.debug:
                        LOGGER.info(f"Running the profile from {profile.source_file}")

                    with profile:
                        outputs: generic.Sequence[pathlib.Path] = profile.run(
                            cycle=manifest.cycle,
                            files=manifest.files,
                            output_path=arguments.destination
                        )
                        clean(profile.executor)
                    LOGGER.info(
                        f"The results for the profile for {profile.output_type.describe()} data run within the "
                        f"{profile.configuration.describe()} configuration across {profile.region.describe()} were written to:{os.linesep}"
                        f"    - {(os.linesep + '    - ').join(map(str, outputs))}"
                    )

                    if arguments.peek:
                        for output in outputs:
                            from post_processing.utilities.netcdf import peek
                            representation: str = peek(output)
                            LOGGER.info(f"Output: {output}:{os.linesep}{representation}")
                    elif settings.debug:
                        for output in outputs[:5]:
                            from post_processing.utilities.netcdf import peek
                            representation: str = peek(output)
                            LOGGER.info(f"Output: {output}:{os.linesep}{representation}")

                except:
                    if profile.raw_configuration:
                        LOGGER.debug(
                            f"Could not execute the following Profile:{os.linesep}"
                            f"{profile.raw_configuration}"
                        )
                    raise
        else:
            LOGGER.warning(f"No profiles were found for '{manifest}'. Nothing will be processed")
    except BaseException as exception:
        LOGGER.error(exception, exc_info=True)
        LOGGER.critical(f"National Water Model Post Processing could not provide outputs", exc_info=False)
        shutdown()
        return 1
    LOGGER.info(f"Operation complete in {datetime.now() - start_time}")
    try:
        # TODO: Make `shutdown` a `finally` call
        shutdown()
    except BaseException as exception:
        LOGGER.error(exception, exc_info=True)

    if profiler is not None:
        profiler.disable()
        filename: str = f"{datetime.now().astimezone().strftime('%Y%m%d.%H%M')}_{arguments.source_file.name}.profile"
        profiler.dump_stats(filename)
        LOGGER.info(f"Profile results saved to: {filename}")

    return 0

if __name__ == "__main__":
    if settings.debug:
        LOGGER.warning("Debug mode is enabled. Stop and disable if this is a testing or production environment.")
    try:
        exit_code: int = main()
    except BaseException as exc:
        LOGGER.error(f"Error encountered: {exc}", exc_info=True)
        exit_code = 1
    sys.exit(exit_code)
