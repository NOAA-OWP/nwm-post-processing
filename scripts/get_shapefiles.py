#!/usr/bin/env python3
"""
Downloads All necessary shapefiles to the desired locations
"""
import typing
import logging
import pathlib
import argparse
import sys
import io
import tarfile
import os
import concurrent.futures
import time
import random

import concurrent.futures.thread as thread
import concurrent.futures.process as process

from functools import partial

from post_processing.configuration import settings
from post_processing.utilities import networking
from post_processing.enums import RFC

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format=settings.log_format,
        datefmt=settings.date_format
    )

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


DEFAULT_SHAPEFILE_URL: str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwm/para_post-processed/RFC"
"""The default root URL for where to look for shapefiles"""


class Arguments:
    """
    Command line arguments
    """
    def __init__(self, *args: str):
        self.rfc_url: str = DEFAULT_SHAPEFILE_URL
        self.rfc: typing.Optional[RFC] = None
        self.destination: pathlib.Path = settings.resource_path
        self.overwrite: bool = False,
        self.log_level: str = logging.getLevelName(LOGGER.level)
        self.parallelization: typing.Optional[typing.Literal['threaded', 'multiprocessed']] = 'multiprocessed'
        self.__parse(args)

    def __parse(self, args: typing.Iterable[str] = None):
        """
        Parse given command line parameters

        :param args: Command line parameters. sys.argv[1:] will be used if this isn't supplied
        """
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            "Downloads shapefiles defining the boundaries of RFCs",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )

        parser.add_argument(
            "-l",
            "--location",
            dest="rfc_url",
            type=str,
            default=self.rfc_url,
            help="The address for where to start looking for shapefiles"
        )

        parser.add_argument(
            "--rfc",
            dest="rfc",
            type=RFC,
            choices=[rfc.value for rfc in RFC],
            default=None,
            help="Only download the shapefiles for this given RFC - all will be downloaded if this is ommitted"
        )

        parser.add_argument(
            "-o",
            "--overwrite",
            dest="overwrite",
            action="store_true",
            help="Overwrite Preexisting Files"
        )

        parser.add_argument(
            "-d",
            "--destination",
            type=pathlib.Path,
            dest="destination",
            default=self.destination,
            help="Where to save the downloaded files"
        )

        parser.add_argument(
            "-l",
            "--log-level",
            dest="log_level",
            type=str,
            choices=["INFO", "ERROR", "DEBUG"],
            default=self.log_level,
            help="What level of logging to output"
        )

        parser.add_argument(
            "-p",
            "--parallelization",
            dest="parallelization",
            type=str,
            choices=["threaded", "multiprocessed"],
            default=self.parallelization,
            help="How to parallelize the download operation"
        )

        parameters: argparse.Namespace = parser.parse_args(args=args) if args else parser.parse_args()

        for key, value in vars(parameters).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                LOGGER.warning(
                    "Invalid command line parameter detected - there is not a recognized command line parameter named '{key}'".format(
                        key=key
                    )
                )



def get_aprfc_shapefiles(rfc_url: str) -> typing.Dict[str, bytes]:
    """
    Get the shapefiles for APRFC

    This is its own setting since it handles APRFC, Alaska, and Hawaii

    :returns: A mapping between each file name and their raw contents
    """
    rfc_url += "AP/shapefile"

    alaska_url = "{rfc_url}/APRFC_alaska_shapefile.tgz".format(rfc_url=rfc_url)
    hawaii_url = "{rfc_url}/APRFC_hawaii_shapefile.tgz".format(rfc_url=rfc_url)
    aprfc_url = "{rfc_url}/APRFC_shapefile.tgz".format(rfc_url=rfc_url)

    archive: typing.Dict[str, bytes] = {}

    archive.update(
        download_shapefiles(url=aprfc_url)
    )

    hawaii_shapefiles: typing.Dict[str, bytes] = download_shapefiles(url=hawaii_url)

    conflicting_files: typing.List[str] = [
        name
        for name in hawaii_shapefiles.keys()
        if name in archive
    ]

    if conflicting_files:
        LOGGER.warning(
            "There is a conflict for Hawaii shapefiles - the following files will be overwritten:{newline}    - {overwritten_files}".format(
                overwritten_files=(os.linesep + "    - ").join(conflicting_files)
            )
        )

    archive.update(
        hawaii_shapefiles
    )

    alaska_shapefiles: typing.Dict[str, bytes] = download_shapefiles(url=alaska_url)

    conflicting_files = [
        name
        for name in alaska_shapefiles
        if name in archive
    ]

    if conflicting_files:
        LOGGER.warning(
            "There is a conflict for Alaska shapefiles - the following files will be overwritten:{newline}    - {overwritten_files}".format(
                overwritten_files=(os.linesep + "    - ").join(conflicting_files)
            )
        )

    archive.update(
        alaska_shapefiles
    )

    return archive

def get_serfc_shapefiles(rfc_url: str) -> typing.Dict[str, bytes]:
    """
    Get the shapefiles for SERFC

    This is its own setting since it handles SERFC and Puerto Rico

    :returns: A mapping between each file name and their raw contents
    """
    rfc_url += "SE/shapefile"

    puertorico_url = "{rfc_url}/SERFC_puertorico_shapefile.tgz".format(rfc_url=rfc_url)
    serfc_url = "{rfc_url}/SERFC_shapefile.tgz".format(rfc_url=rfc_url)

    archive: typing.Dict[str, bytes] = {}

    archive.update(
        download_shapefiles(url=serfc_url)
    )

    puertorico_shapefiles: typing.Dict[str, bytes] = download_shapefiles(url=puertorico_url)

    conflicting_files: typing.List[str] = [
        name
        for name in puertorico_shapefiles.keys()
        if name in archive
    ]

    if conflicting_files:
        LOGGER.warning(
            "There is a conflict for Puerto Rico shapefiles - the following files will be overwritten:{newline}    - {overwritten_files}".format(
                overwritten_files=(os.linesep + "    - ").join(conflicting_files)
            )
        )

    archive.update(
        puertorico_shapefiles
    )

    return archive



def get_shapefiles(rfc_url: str, rfc: RFC) -> typing.Dict[str, bytes]:
    """
    Get the raw shapefile from NOMADS

    :param rfc: The RFC to get the shapefile for
    :returns: A mapping between each file name and their raw contents
    """
    if not rfc_url.endswith("/"):
        rfc_url += "/"

    if rfc == RFC.APRFC:
        return get_aprfc_shapefiles(rfc_url=rfc_url)
    if rfc == RFC.SERFC:
        return get_serfc_shapefiles(rfc_url=rfc_url)
    
    rfc_url = "{rfc_url}{rfc_abbreviation}/shapefile/{rfc}_shapefile.tgz".format(
        rfc_url=rfc_url,
        rfc_abbreviation=rfc.value,
        rfc=rfc.name
    )

    archive: typing.Dict[str, bytes] = download_shapefiles(url=rfc_url)

    return archive


def download_shapefiles(url: str) -> typing.Dict[str, bytes]:
    """
    Download and extract the shapefile at the given url

    :param url: The address of the shapefile
    :returns: The extracted contents of the packaged shapefile
    """
    raw_archive: bytes = networking.get(url=url)
    LOGGER.debug("Data from '{url}' has been downloaded".format(url))
    archive: typing.Dict[str, bytes] = extract_archive(archive_bytes=raw_archive)
    LOGGER.debug("Data from '{url}' has been unpacked".format(url))

    return archive


def extract_archive(archive_bytes: bytes) -> typing.Dict[str, bytes]:
    """
    Extracts the raw bytes of all files in an archive

    :param archive_bytes: The raw bytes for the file
    :returns: A mapping of each contained file within the archive to its raw bytes contents
    """

    archive_buffer: io.BytesIO = io.BytesIO(archive_bytes)
    archive: typing.Dict[str, bytes] = {}

    with tarfile.open(fileobj=archive_buffer, mode="r:*") as tar:
        for member in tar.getmembers():
            if member.isfile():
                extracted_file: io.BytesIO = tar.extractfile(member=member)
                if extracted_file:
                    archive[member.name] = extracted_file.read()

    return archive


def save_shapefiles(directory: pathlib.Path, rfc_url: str, rfc: RFC, overwrite: bool = False):
    """
    Save a shapefile to disk

    :param directory: The directory for where to save the data
    :param rfc_url: The URL for where to start looking for RFC shapefiles
    :param rfc: The RFC whose shapefiles to download
    :param overwrite: Whether to overwrite preexisting files
    """
    if directory.is_file():
        raise ValueError("Cannot save shapefiles to '{directory}' it is a path to a file")
    
    rfc_data: typing.Dict[str, bytes] = get_shapefiles(rfc_url=rfc_url, rfc=rfc)

    directory.mkdir(parents=True, exist_ok=True)

    for filename, filecontents in rfc_data.items():
        full_path: pathlib.Path = directory / filename
        if full_path.is_dir():
            LOGGER.error(
                "Cannot save the '{filename}' file to '{directory}' - it already exists and is a directory".format(
                    filename=filename,
                    directory=directory
                )
            )
            continue
        if full_path.is_file() and not overwrite:
            LOGGER.warning(
                "There is already a file named '{filename}' in '{directory}' - it won't be overwritten".format(
                    filename=filename,
                    directory=directory
                )
            )
            continue

        buffer: io.BytesIO = io.BytesIO(filecontents)
        full_path.write_bytes(data=buffer)
        LOGGER.info("'{name}' saved to '{path}'".format(name=filename, path=full_path))


def get_shapefiles(
    rfc_url: str,
    rfc: typing.Optional[RFC],
    destination: pathlib.Path,
    overwrite: bool = False,
    parallelization: typing.Optional[typing.Literal['multiprocessed', 'threaded']] = 'multiprocessed'
):
    """
    Download shapefiles to disk

    This is the primary application logic

    :param rfc_url: The location of the shapefiles. Shapefiles are expected to be at {rfc_url}/{RFC 2 Letter Abbreviation}/{RFC 2 Letter Abbreviation}_shapefile.tgz
    :param rfc: What specific rfc to download. If none are given, download all of them
    :param destination: Where to put the downloaded shapefiles
    :param overwrite: Whether to overwrite preexisting files
    """
    if rfc:
        save_shapefiles(directory=destination, rfc_url=rfc_url, rfc=rfc, overwrite=overwrite)
        return
    
    rfcs_to_download: typing.List[RFC] = list(RFC)

    if parallelization:
        get_shapefiles_in_parallel(
            rfc_url=rfc_url,
            rfcs_to_download=rfcs_to_download,
            destination=destination,
            overwrite=overwrite,
            parallelization=parallelization
        )
        return
    
    for rfc_to_download in rfcs_to_download:
        save_shapefiles(directory=destination, rfc_url=rfc_url, rfc=rfc_to_download, overwrite=overwrite)


def get_shapefiles_in_parallel(
    rfc_url: str,
    rfcs_to_download: typing.List[RFC],
    destination: pathlib.Path,
    overwrite: bool,
    parallelization: typing.Literal['multiprocessed', 'threaded'] = 'multiprocessed'
):
    executor_type: typing.Type[concurrent.futures.Executor] = process.ProcessPoolExecutor if parallelization == 'multiprocessed' else thread.ThreadPoolExecutor

    future_results: typing.Dict[RFC, concurrent.futures.Future] = {}
    with executor_type(max_workers=min(os.cpu_count(), len(rfcs_to_download))) as executor:
        for rfc in rfcs_to_download:
            future: concurrent.futures.Future = executor.submit(
                save_shapefiles,
                directory=destination,
                rfc_url=rfc_url,
                rfc=rfc,
                overwrite=overwrite
            )
            future_results[rfc] = future

        while future_results:
            for rfc, future in future_results.items():
                if not future.done():
                    LOGGER.debug("Waiting to finishing downloading '{rfc}' data...".format(rfc=rfc.name))
                    continue
                if future.exception:
                    LOGGER.error(
                        'Failed to download shapefiles for {rfc}: {exception}'.format(
                            rfc=rfc.name,
                            exception=future.exception
                        ),
                        exc_info=future.exception
                    )
                del future_results[rfc]

            if future_results:
                time_to_wait: float = 0.3
                LOGGER.debug("Waiting {time_to_wait} seconds before checking for completion again".format(time_to_wait))
                time.sleep(0.3)



def main() -> int:
    """
    The main entry point of the script
    """
    arguments: Arguments = Arguments()

    LOGGER.setLevel(arguments.log_level)

    try:
        get_shapefiles(
            rfc_url=arguments.rfc_url,
            rfc=arguments.rfc,
            destination=arguments.destination,
            overwrite=arguments.overwrite
        )
    except:
        LOGGER.error("{filename} failed", exc_info=True, stack_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())