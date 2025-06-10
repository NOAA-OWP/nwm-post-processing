#!/usr/bin/env python3
"""
Crawls through nomads and constructs sample profile configurations for each type currently available
"""
import typing
import os
import re
import logging
import pathlib
import time
import sys

from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests

# Compile the NWM filename regex pattern
NWM_FILENAME_PATTERN: re.Pattern = re.compile(
    r"nwm\."
    r"t(?P<date>\d+)z\."
    r"(?P<configuration>[^.]+)\."
    r"(?P<output_type>channel_rt|land|forcing)"
    r"(?:_(?P<member>\d+))?"
    r"(?:\.(?P<timing>tm\d+|day\d+|\d+hour|f\d+))?"
    r"\.(?P<region>[a-z]+)(?:\.\w\wrfc)?\.nc"
)
"""
A regex that parsed post-processing file names and extracts the following strings:
    - date
    - configuration
    - output_type
    - member
    - timing
    - region
"""

ADDRESS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwm/post-processed/"
"""The address to production post processing data"""
BEAUTIFUL_SOUP_FEATURES: typing.Union[str, typing.Sequence[str]] = "html.parser"
"""Features used to parse HTML documents"""

LOGGER = logging.getLogger(pathlib.Path(__file__).stem)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

PROFILES_DIRECTORY: pathlib.Path = pathlib.Path(__file__).parent.parent / "resources" / "profiles"
"""The path to where profiles should be stored"""

def get_markup(url: str, session: requests.Session) -> typing.Optional[BeautifulSoup]:
    """
    Get the markup from the specified URL

    :param url: The URL to get the markup from
    :param session: A reusable connection provider to the core site so that new connections don't need to be created
    :returns: Markup if a page was found, None otherwise
    """
    try:
        potential_content_type: str = get_content_type(url=url, session=session)
        if 'html' not in potential_content_type and "text/plain" not in potential_content_type:
            LOGGER.warning(f"'{url}' won't be crawled for html - its content type is '{potential_content_type}'")
            return None
        response = session.get(url, timeout=10)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        LOGGER.warning(f"Failed to fetch {url}: {e}")
    return None


def get_content_type(url: str, session: requests.Session) -> typing.Optional[str]:
    """
    Get the content type of the specified URL

    :param url: The URL to get the content type from
    :param session: A reusable connection provider to the core site so that new connections don't need to be created
    :returns: The type of data that will be retrieved from the url if `get` were called on it
    """
    head: requests.models.Response = session.head(url, timeout=10)
    if head.status_code >= 400:
        raise requests.HTTPError(
            f"Cannot fetch content type from {url}: [{head.status_code}] {head.reason}",
            request=head.request,
            response=head,
        )
    accessible_header_values: typing.Dict[str, str] =  {key.lower(): value for key, value in head.headers.items()}
    return accessible_header_values.get("content-type")

def extract_netcdf_links(base_urls: typing.Sequence[str], session: requests.Session) -> typing.List[str]:
    """
    Crawl the given url and find all netcdf files

    :param base_urls: The urls to crawl
    :param session: The http session to use in order to reuse connections
    :returns: A list of netcdf file urls
    """
    netcdf_urls: typing.List[str] = []
    visited_urls: typing.Set[str] = set()

    # Create a queue of links to visit
    queue = [*base_urls]

    total_sites_checked = 0

    while queue:
        current_url = queue.pop(0)

        # Don't revisit previously visited urls - this will result in an infinite loop
        if current_url in visited_urls:
            continue

        visited_urls.add(current_url)
        markup: BeautifulSoup = get_markup(current_url, session=session)

        # Give progress reports every 25 pages checked so the caller knows that work is being performed
        total_sites_checked += 1
        if total_sites_checked % 25 == 0:
            LOGGER.info(f"Checked {total_sites_checked} sites")

        if not markup:
            continue

        # We only want to look for the content of links, so only look for 'a' tags with references to URLs
        for link in markup.find_all("a"):
            # Get the link - if there isn't a link this tag is useless
            href = link.get("href")
            if not href:
                continue
            full_url = urljoin(current_url, href)

            # We can assume we've found a netcdf file if the link ends with the '.nc' extension.
            # Otherwise, a link ending in '/' indicates a subdirectory. A link ending in anything else (.., .png, .tgz)
            # indicates information we aren't interested in
            if href.endswith(".nc"):
                netcdf_urls.append(full_url)
            elif href.endswith("/") and any(full_url.startswith(base_url) for base_url in base_urls):
                queue.append(full_url)

    return netcdf_urls

def parse_metadata_from_url(url: str) -> typing.Optional[typing.Dict[str, typing.Dict[str, typing.Any]]]:
    """
    Parse the url to a netcdf file and use it to create a single element dictionary keyed by intended filename
    with a value consisting of the data expected within a bare profile

    :param url: The url to break apart for parameters
    :returns: If the url follows that of a link to a NWM file, a dictionary keyed by filename with a profile as its
        value, None otherwise
    """
    # For a link like:
    # "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwm/post-processed/WMS/long_range/channel_rt/nwm.t2025060806z.long_range.channel_rt.conus.nc"
    # Split by '/' and choose the last value to get the intended file name
    filename = url.split("/")[-1]
    match: typing.Optional[re.Match] = NWM_FILENAME_PATTERN.match(filename)

    if not match:
        LOGGER.warning(f"Filename did not match pattern:{os.linesep}File: {filename}{os.linesep}URL: {url}")
        return None

    filename_parameters: typing.Dict[str, str] = match.groupdict()
    region = "conus"

    if "alaska" in filename_parameters.get("region", ""):
        region = "alaska"
    if "hawaii" in filename_parameters.get("region", ""):
        region = "hawaii"
    if "puerto" in filename_parameters.get("region", "") or "pr" in filename_parameters.get("region", ""):
        region = "puertorico"

    # The output type used here should be an amalgamation of actual output type and its potential ensemble member.
    # This way ensembles are kept separate to maintain product individuality
    output_type_name: str = (
        f"{filename_parameters['output_type']}"
        f"{'_' + filename_parameters['member'] if filename_parameters.get('member') else ''}"
    )

    # Craft a message that will be logged by default if the generated profile is run
    message: str = (
        f"An output for 'nwm.t<date><cycle>z."
        f"{filename_parameters['configuration']}."
        f"{output_type_name}"
        f"{'.' + filename_parameters['timing'] if filename_parameters.get('timing') else ''}."
        f"{filename_parameters['region']}."
        "nc' should be generated, as defined in {source_file}."
    )

    # Create an operation that will notify that caller that the profile has not been fully implemented
    notice_operation: typing.Dict[str, str] = {
        "operation": "echo",
        "message": message,
        "level": "error"
    }

    # The filename should mimic the structure of the output without features that indicate each individual product,
    # i.e. no cycle or date indicators
    expected_filename: str = f"{filename_parameters['configuration']}.{output_type_name}.{region}.json"

    # Craft a filename and profile dictionary that will indicate that the profile has not been fully implemented
    new_profile: typing.Dict[str, typing.Dict[str, typing.Any]] = {
        expected_filename: {
            "operations": [notice_operation],
            "configuration": filename_parameters['configuration'],
            "output_type": filename_parameters['output_type'],
            "region": region,
            "member": filename_parameters.get('member', None)
        }
    }
    return new_profile

def collect_metadata(max_attempts: int = 5) -> typing.Dict[str, typing.Dict[str, typing.Any]]:
    """
    Crawl through production post-processing data and generate profiles for every type of output found

    :param max_attempts: The maximum number of attempts to crawl before giving up
    :returns: A dictionary keyed by intended file names and whose values are profiles waiting to be written
    """
    # Track the number of attempts to receive data - we don't want to try and try and try to retrieve inaccessible data
    attempts = 0
    all_metadata = {}

    # All initial URLs to crawl. We keep it down to "RFC/" and "WMS/" to avoid crawling through branches
    # that will have no data
    urls_to_crawl: typing.Sequence[str] = [
        urljoin(ADDRESS, "RFC/"),
        urljoin(ADDRESS, "WMS/"),
    ]

    last_exception: typing.Optional[Exception] = None

    # Use a Session in order to continue to use established connections
    with requests.Session() as session:
        while attempts < max_attempts:
            if attempts > 0:
                LOGGER.info(f"Attempt {attempts + 1} to scrape {urls_to_crawl}")
                time.sleep(1)

            attempts += 1
            try:
                netcdf_links: typing.Sequence[str] = extract_netcdf_links(base_urls=urls_to_crawl, session=session)

                if not netcdf_links:
                    LOGGER.warning(
                        f"Could not get links to production post processing output. "
                        f"Waiting and trying again if allowable"
                    )

                # Attempt to create profiles for all encountered links
                for link in netcdf_links:
                    metadata: typing.Dict[str, typing.Any] = parse_metadata_from_url(link) or {}

                    # Add all generated values to the master collection
                    for future_filename, contents in metadata.items():
                        # Merge profiles if a duplicate is found.
                        # This will be encountered for profiles that generate multiple outputs
                        if future_filename in all_metadata:
                            # Extract the message operation from the more recent profile and stick it into the
                            # existing profile if it wasn't already there
                            message: str = contents['operations'][0]['message']
                            current_operations = list(all_metadata[future_filename]['operations'])
                            if not any(operation['message'] == message for operation in current_operations):
                                all_metadata[future_filename]['operations'].extend(contents['operations'])
                            else:
                                LOGGER.error(
                                    f"The message {message} has already been added to {future_filename} - "
                                    f"something is generating multiple messages and expected outputs aren't "
                                    f"being documented"
                                )
                        else:
                            all_metadata[future_filename] = contents
                return all_metadata
            except TimeoutError:
                LOGGER.error(f"Timed out while trying to get metadata from {urls_to_crawl}")
            except Exception as e:
                last_exception = e
                LOGGER.error(f"{e}{os.linesep}Trying to get post processing metadata again...", exc_info=True)

    # If no data was received because of an error, raise the error
    if last_exception:
        raise last_exception

    LOGGER.error("Failed to retrieve any metadata after max attempts.")
    return all_metadata

def test_metadata_scraping(metadata: typing.Dict[str, typing.Dict]):
    """
    Test to ensure that scraped output matches the bare expectations

    :param metadata: The generated metadata
    """
    assert isinstance(metadata, typing.Mapping), "Result should be a dictionary"
    assert any(
        key.endswith(".json") for key in metadata
    ), "There should be at least one valid JSON key"
    assert all(
        key.endswith(".json") for key in metadata
    ), "All keys within the generated metadata should be intended filenames"

def main() -> int:
    """
    Collect production post processing profiles, make sure they are valid, and save them to disk

    :returns: The exit code for the application
    """
    try:
        metadata = collect_metadata()
        test_metadata_scraping(metadata=metadata)

        PROFILES_DIRECTORY.mkdir(parents=True, exist_ok=True)

        import json
        for filename, content in metadata.items():
            output_path: pathlib.Path = PROFILES_DIRECTORY / filename
            output_path.write_text(json.dumps(content, indent=4))
            LOGGER.info(f"Wrote profile to: {output_path}")
    except Exception as e:
        LOGGER.error(f"Could not generate profiles based on production output: {e}", exc_info=True)
        return 1

    LOGGER.info(f"Created {len(metadata)} profiles")

    return 0

if __name__ == "__main__":
    sys.exit(main())
