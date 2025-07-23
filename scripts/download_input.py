#!/usr/bin/env python3
"""
Downloads NWM output to be used as input data for post processing
"""
import typing
import argparse
import logging
import re
import pathlib
import sys
import os

from datetime import datetime
from datetime import timedelta

from html.parser import HTMLParser

import requests

from post_processing.configuration import settings
from post_processing.enums import Configuration
from post_processing.enums import ModelOutputType
from post_processing.utilities.common import starmap

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format=settings.log_format,
        datefmt=settings.date_format
    )

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
"""The primary logger for this file"""


_DEFAULT_SOURCE_URL: str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwm/v3.0"
"""The default location for where to find NWM output"""

_VALID_CONFIGURATIONS: typing.List[str] = [
    "analysis_assim",
    "analysis_assim_alaska",
    "analysis_assim_alaska_no_da",
    "analysis_assim_hawaii",
    "analysis_assim_long",
    "analysis_assim_long_no_da",
    "analysis_assim_no_da",
    "analysis_assim_puertorico",
    "analysis_assim_puertorico_no_da",
    "forcing_analysis_assim",
    "forcing_analysis_assim_alaska",
    "forcing_analysis_assim_hawaii",
    "forcing_analysis_assim_puertorico",
    "forcing_medium_range",
    "forcing_medium_range_alaska",
    "forcing_medium_range_blend",
    "forcing_medium_range_blend_alaska",
    "forcing_short_range",
    "forcing_short_range_alaska",
    "forcing_short_range_hawaii",
    "forcing_short_range_puertorico",
    "long_range_mem1",
    "long_range_mem2",
    "long_range_mem3",
    "long_range_mem4",
    "medium_range_alaska_mem1",
    "medium_range_alaska_mem2",
    "medium_range_alaska_mem3",
    "medium_range_alaska_mem4",
    "medium_range_alaska_mem5",
    "medium_range_alaska_mem6",
    "medium_range_alaska_no_da",
    "medium_range_blend",
    "medium_range_blend_alaska",
    "medium_range_mem1",
    "medium_range_mem2",
    "medium_range_mem3",
    "medium_range_mem4",
    "medium_range_mem5",
    "medium_range_mem6",
    "medium_range_no_da",
    "short_range",
    "short_range_alaska",
    "short_range_hawaii",
    "short_range_hawaii_no_da",
    "short_range_puertorico",
    "short_range_puertorico_no_da"
]
"""Configurations that may be downloaded - excludes types not used in post processing"""

_VALID_OUTPUT_TYPE: typing.List[str] = [
    "channel_rt",
    "land",
    "forcing"
]
"""Model Output Types that may be downloaded - excludes types not used in post processing"""

NUMERIC_PATTERN_PATTERN: re.Pattern = re.compile(r"^(?:(?:\\d|\[(?:\d-\d|\d+)]|\d)(?:\?|[*+]|\{\d+(?:,\d*)?})?)+$")
"""
A pattern that indicates that a given string indicates a regular expression for identifying a series of digits.

Broken out, it translates to:
 - from beginning to end,
 - at least one:
    - '\d' pattern or [#-#], like [0-9], or [###], like [1356] to isolate what specific digits to accept, or just a literal number
    - followed by at most one of:
        - *
        - +
        - ?
        - {#}, like {3}, meaning 'match on exactly 3 of the previous'
        - {#,}, like {5,}, meaning 'match on at least 5 of the previous'
        - {#,#}, like {2,6}, meaning 'match on 2 to six of the previous'
    
Grouping is not supported - patterns like: '\d{4}0(4|[0-2][679])+' won't work

Covers inputs like:
    - '\d+'
    - '\d'
    - '\d\d+'
    - '5'
    - '6+'
    - '[0-9]'
    - '005?'
    - '[13579]{2,}'
"""


class Arguments:
    """
    Command line parameter parser and handler
    """
    def __init__(self, *args: str):
        self.source_url: str = _DEFAULT_SOURCE_URL
        """Where to retrieve NWM output"""
        self.configuration: Configuration = None
        """What NWM configuration to download"""
        self.cycle: str = None
        """What cycle of the configuration to download """
        self.date: str = (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y%m%d")
        """The day of the model run (ex. 20250311)"""
        self.output_type: ModelOutputType = None
        """What type of output to pull down"""
        self.region: typing.Literal['conus', 'alaska', 'hawaii', 'puertorico'] = None
        """The location for the data to retrieve"""
        self.member: typing.Optional[int] = None
        """The ensemble member to download"""
        self.destination: pathlib.Path = settings.resource_path / "sample"
        """Where to store the downloaded data"""
        self.overwrite: bool = False
        """Whether to overwrite preexisting data"""
        self.log_level: typing.Literal['INFO', 'DEBUG', 'ERROR'] = 'INFO'
        """The level of messages that may be logged"""
        self.frame: str = r"\d+"
        """The pattern to use to indicate what frame or frames to retrieve ('\d+' for all or '00' for just 00)"""

        self.__parse(args=args)
        self.__validate()

    def __validate(self):
        """
        Raise exceptions if contained arguments are not valid
        """
        if self.destination.is_file():
            raise FileExistsError(f"Cannot use {self.destination} as an output location - it is a file and must be a directory")
        
        if not re.match(r"^\d{8}$", self.date):
            raise ValueError(f"'{self.date}' is not a valid date format - it should be 8 digits and nothing else")
        
        if not self.cycle:
            raise ValueError("No cycle was provided")
        
        if not re.match(r"^[0-2][0-9]$", self.cycle):
            raise ValueError(f"'{self.cycle}' is not a valid cycle - it must be a 2 digit string between 00 and 23")
        
        if int(self.cycle) > 23:
            raise ValueError(f"'{self.cycle}' is too high - the maximum value is 23")
        
        if self.configuration not in (Configuration.LongRange, Configuration.MediumRange) and self.member:
            raise ValueError(f"Ensemble members are only valid for long or short range configurations. Received '{self.configuration}'")
        
        if self.member and self.member < 1:
            raise ValueError(f"The minimum ensemble member is 1 - received {self.member}")
        
        if self.configuration == Configuration.LongRange and self.member > 4:
            raise ValueError(f"The maximum ensemble member for long range data is 4 - received {self.member}")
        
        if self.configuration == Configuration.MediumRange and self.member > 6:
            raise ValueError(f"The maximum ensemble member for medium range data is 6 - received {self.member}")
        
        if self.configuration in (Configuration.ExtendedAnalysisAssimilation, Configuration.ExtendedAnalysisAssimilationNoDA):
            raise ValueError(f"The downloading of {str(self.configuration).replace('_', ' ').title()} data is not yet supported")

        if not NUMERIC_PATTERN_PATTERN.match(self.frame):
            raise argparse.ArgumentTypeError(
                f"The frame string '{self.frame}' is not valid - "
                f"it MUST be a regex pattern that ONLY matches on numbers, like '\d' or '\d\d' or '0+' or '00'"
            )

    def __parse(self, args: typing.Sequence[str]):
        """
        Parse input parameters and set values

        :param args: Input from the command line
        """
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description="Download NWM output to be used as input for post processing"
        )

        parser.add_argument(
            "-u",
            "--url",
            dest="source_url",
            type=str,
            default=self.source_url,
            help="Where to get the data"
        )

        parser.add_argument(
            "configuration",
            type=Configuration,
            choices=[configuration for configuration in Configuration if 'extend' not in configuration.value],
            help="What NWM configuration to download",
        )

        parser.add_argument(
            "cycle",
            type=str,
            help="2-digit number between '00' and '23' used to indicate what cycle to download - corresponds to t##z where ## is the cycle"
        )

        parser.add_argument(
            "-d",
            "--date",
            default=self.date,
            type=str,
            help=f"The 8 digit date, such as {self.date}"
        )

        parser.add_argument(
            "output_type",
            type=ModelOutputType,
            choices=[output_type for output_type in ModelOutputType],
            help="The model output type to download"
        )

        parser.add_argument(
            "region",
            type=str,
            choices=('conus', 'alaska', 'hawaii', 'puertorico'),
            help="Where the output was configured to model"
        )

        parser.add_argument(
            "-o",
            "--directory",
            dest="destination",
            type=pathlib.Path,
            default=self.destination,
            help="Where to store the results"
        )

        parser.add_argument(
            '-f',
            '--overwrite',
            dest="overwrite",
            action="store_true",
            help="Whether to overwrite preexisting data"
        )

        parser.add_argument(
            "-l",
            "--log-level",
            type=str,
            default=self.log_level,
            choices=["INFO", "ERROR", "DEBUG"],
            help="What level messages can be logged"
        )

        parser.add_argument(
            "-F",
            "--frame",
            dest="frame",
            type=str,
            default=self.frame,
            help="A pattern used to constrain what frames to include"
        )

        parameters: argparse.Namespace = parser.parse_args(args=args) if args else parser.parse_args()

        for key, value in vars(parameters).items():
            if hasattr(self, key):
                setattr(self, key, value)


class ApacheDirectoryListingParser(HTMLParser):
    """
    Parses Apache Directory Listing HTML to find links to items available for download
    """
    def __init__(self, *, convert_charrefs = True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.in_anchor: bool = False
        self.href: typing.Optional[str] = None
        self.current_text: typing.List[str] = []
        self.links: typing.Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.in_anchor = True
            self.href = dict(attrs).get("href")
            self.current_text = []
        return super().handle_starttag(tag, attrs)
    
    def handle_data(self, data):
        if self.in_anchor:
            self.current_text.append(data.strip())
        return super().handle_data(data)
    
    def handle_endtag(self, tag):
        if tag == 'a' and self.in_anchor:
            full_text = ''.join(self.current_text)
            if self.href and not self.href.endswith("/"):
                self.links[full_text] = self.href
            self.in_anchor = False
            self.href = None
            self.current_text = []
        return super().handle_endtag(tag)


def get_directory_links(url: str) -> typing.Dict[str, str]:
    """
    Get the links to files within an apache directory listing

    :param url: The URL of the apache directory listing
    :returns: A mapping of file names to their address
    """
    raw_markup: bytes = requests.get(url=url).content
    markup: str = raw_markup.decode()

    parser: ApacheDirectoryListingParser = ApacheDirectoryListingParser()
    parser.feed(data=markup)
    return parser.links


def form_configuration_link(
    configuration: Configuration,
    output_type: ModelOutputType,
    region: str,
    member: typing.Optional[int]
) -> str:
    """
    Form the part of the apache directory listing URL that contains the desired NWM output

    :param configuration: The configuration of the data to pull
    :param output_type: The type of data that was output
    :param region: Where the output was configured to model
    :param member: The ensemble member of interest
    :returns: The part of the directory listing url that contains the desired output
    """
    link: str = configuration.value

    if output_type == ModelOutputType.Forcing:
        link = f"forcing_{link}"

    if region != "conus" and "no_da" in configuration.value:
        no_da_index: int = link.index("no_da")
        link = f"{link[:no_da_index]}{region}_{link[no_da_index:]}"
    elif region != "conus":
        link = f"{link}_{region}"

    if member:
        link = f"{link}_mem{member}"

    return link


def download_file(
    url: str,
    filename: str,
    directory: pathlib.Path,
    overwrite: bool = False
) -> typing.Optional[pathlib.Path]:
    """
    Download the file at the URL and store it within the directory

    :param url: Where the file is
    :param filename: What to name the file
    :param directory: What directory to store the file in
    :param overwrite: Whether to overwrite a preexisting file
    :returns: The path to the downloaded file
    """
    directory.mkdir(parents=True, exist_ok=True)
    path: pathlib.Path = directory / filename

    if path.exists() and not overwrite:
        LOGGER.info(f"Not downloading '{filename}' - it is already present")
        return None

    if path.is_dir():
        raise ValueError(f"Cannot save '{filename}' to '{path}' - it is already a directory")
    
    LOGGER.info(f"Downloading '{filename}'")
    raw_data: bytes = requests.get(url=url).content
    path.write_bytes(data=raw_data)
    return path



def download_input(
    source_url: str,
    configuration: Configuration,
    cycle: str,
    date: str,
    output_type: ModelOutputType,
    region: str,
    member: typing.Optional[int],
    destination: pathlib.Path,
    frame_pattern: str = r"\d+",
    overwrite: bool = False
) -> typing.Sequence[pathlib.Path]:
    """
    The main application logic

    :param source_url: Where to download from
    :param configuration: The NWM configuration to download
    :param cycle: The NWM cycle to download
    :param date: The date of the data to download
    :param output_type: The type of output to download
    :param region: Where the output was configured to model
    :param member: The ensemble member to download
    :param destination: Where to put the data
    :param frame_pattern: A regex pattern to inject into the regex that matches on file name.
        Must be some variation of '\d' or '000*'. This will match on terms like 'tm00' or 'f018'
    :param overwrite: Whether to overwrite preexisting data

    :returns: A list of the paths to the files that were downloaded
    """
    configuration_part: str = form_configuration_link(
        configuration=configuration,
        output_type=output_type,
        region=region,
        member=member
    )
    listing_address: str = f"{source_url}/nwm.{date}/{configuration_part}/"
    """The address of the Apache directory listing where all the files may be found"""

    links_in_listing: typing.Dict[str, str] = get_directory_links(url=listing_address)
    """Links to each file within the listing for the date and configuration"""

    # Now that we have the links for every item for the given configuration for that day, 
    # use the cycle and output type to find the right links to request
    model_output_type: str = output_type.value
    if member:
        model_output_type = f"{model_output_type}_{member}"

    desired_link_pattern: re.Pattern = re.compile(
        rf"nwm\.t{cycle}z\.{configuration.value}\.{model_output_type}\.(?:tm|f){frame_pattern}\.{region}\.nc"
    )

    pertinent_links: typing.Dict[str, str] = {
        filename: f"{listing_address}{address}"
        for filename, address in links_in_listing.items()
        if desired_link_pattern.match(filename)
    }

    download_directory: pathlib.Path = destination / f"nwm.{date}"
    download_directory.mkdir(parents=True, exist_ok=True)

    downloaded_files: typing.Sequence[pathlib.Path] = starmap(
        download_file,
        [
            {
                "url": url,
                "filename": filename,
                "directory": download_directory,
                "overwrite": overwrite
            }
            for filename, url in pertinent_links.items()
        ]
    )

    downloaded_files = list(filter(lambda path: path is not None, downloaded_files))
    return downloaded_files


def main() -> int:
    """
    The entrypoint for this script
    """
    arguments: Arguments = Arguments()

    LOGGER.setLevel(logging.getLevelName(arguments.log_level))

    try:
        downloaded_files: typing.Sequence[pathlib.Path] = download_input(
            source_url=arguments.source_url,
            configuration=arguments.configuration,
            cycle=arguments.cycle,
            date=arguments.date,
            output_type=arguments.output_type,
            region=arguments.region,
            member=arguments.member,
            destination=arguments.destination,
            overwrite=arguments.overwrite,
            frame_pattern=arguments.frame,
        )

        if downloaded_files:
            print(f"{len(downloaded_files)} files were downloaded:")
            for downloaded_file in downloaded_files:
                print(f"    - {downloaded_file}")
        else:
            print("No files were downloaded")
    except BaseException as exception:
        LOGGER.error(f"'{__file__} failed: {exception}", exc_info=True)
        return 1
    return 0

if __name__ == "__main__":
    if not settings.debug:
        LOGGER.warning(
            "This environment is not in debug mode. Do not run this script within a testing or production environment as this is "
            "intended for development purposes only."
        )
    sys.exit(main())
