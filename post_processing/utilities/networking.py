"""
Common functions used to make network calls without the aid of third party libraries
"""
import typing
import io
import pathlib
import urllib
import logging
import http.client
import urllib.parse
import traceback
import os

from concurrent.futures import Executor
from concurrent.futures import Future

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

LOGGER.warning(
    f"{pathlib.Path(__file__).absolute()} is deprecated - a home grown networking solution is no longer required. "
    f"Use `requests` instead"
)

_MAXIMUM_REDIRECTS: int = 5
"""The maximum amount of times that a web response can redirect"""

T = typing.TypeVar('T')
"""A generic type"""


def multiget(
    urls: typing.List[str],
    executor: Executor = None,
    destination: typing.Union[io.BytesIO, io.StringIO, pathlib.Path, typing.Callable[[bytes], typing.Any]] = None
) -> typing.Sequence[typing.Optional[bytes]]:
    """
    Make multiple requests to the same host

    :param urls: The urls to make requests of
    :param executor: A concurrent executor to use to split up requests
    :param destination: Where to put the results of the query. Returns the byte content if no destination was given
    :returns: A list of non or the resulting bytes of each query
    """
    if not urls:
        return []
    
    futures: typing.List[Future] = []
    results: typing.List[typing.Union[T, bytes, None]]
    
    host_paths: typing.Dict[typing.Tuple[str, bool], typing.List[typing.Tuple[str, typing.Optional[str]]]] = {}

    for url in urls:
        parsed_url: urllib.parse.ParseResult = urllib.parse.urlparse(url=url)
        key: typing.Tuple[str, bool] = (parsed_url.netloc, parsed_url.scheme == 'https')

        if key not in host_paths:
            host_paths[key] = []

        host_paths[key].append((parsed_url.path, parsed_url.query))

    for (host, secured), paths in host_paths.items():
        if not paths:
            continue

        connection_class = http.client.HTTPSConnection if secured else http.client.HTTPConnection
        """The type of connection to form"""

        connection: http.client.HTTPConnection = connection_class(
            host=host,
            port=443 if secured else 80
        )
        """The connection to the URL's host (NOT the requested resource)"""

        for path, query in paths:
            if query:
                path += f"?{query}"
            url: str = urllib.parse.urljoin(host, path)
            futures.append(
                executor.submit(get, url=url, destination=destination, connection=connection)
            )

    errors: typing.List[str] = []
    while futures:
        current_future: Future = futures.pop()
        try:
            if current_future.running():
                futures.append(current_future)
            else:
                result = current_future.result(timeout=1)
                results.append(result)
        except TimeoutError:
            futures.append(current_future)
        except:
            errors.append(traceback.format_exc())

    if errors:
        full_message: str = f"Multiget failed:{os.linesep * 2}{(os.linesep * 2).join(errors)}"
        raise Exception(full_message)
    
    return results



def get(
    url: str,
    destination: typing.Union[io.BytesIO, io.StringIO, pathlib.Path, typing.Callable[[bytes], typing.Any]] = None,
    redirect_count: int = 0,
    connection: http.client.HTTPConnection = None
) -> typing.Optional[bytes]:
    """
    Run a GET request to retrieve data from a network address

    :param url: The address of the resource to retrieve
    :param destination: Where to store the retrieved data
    :param redirect_count: The number of times this request has been redirected
    :param connection: A persistent http connection to use
    :returns: The raw data in bytes form if no destination is given
    """
    parsed_url: urllib.parse.ParseResult = urllib.parse.urlparse(url=url)
    """The broken down URL"""

    path: str = parsed_url.path or '/'
    """The path to navigate to once connected"""

    if parsed_url.query:
        path += "?" + parsed_url.query

    if not connection:
        secured: bool = parsed_url.scheme == "https"
        """Whether the url is secured via https"""

        connection_class = http.client.HTTPSConnection if secured else http.client.HTTPConnection
        """The type of connection to form"""

        connection: http.client.HTTPConnection = connection_class(
            host=parsed_url.hostname,
            port=parsed_url.port or (443 if secured else 80)
        )
        """The connection to the URL's host (NOT the requested resource)"""

    # Navigate to the requested resource on the connected host
    connection.request("GET", url=path)
    response: http.client.HTTPResponse = connection.getresponse()

    if response.status in (301, 302, 303, 307, 308):
        if redirect_count >= _MAXIMUM_REDIRECTS:
            raise http.client.HTTPException("Too many redirects")
        
        location: typing.Optional[str] = response.getheader("Location")

        if not location:
            raise http.client.HTTPException("Redirect response is missing the next Location")
        
        return get(url=location, destination=destination, redirect_count=redirect_count + 1)
      
    if response.status != 200:
        raise http.client.HTTPException(
            "HTTP Error {status}: {reason}".format(status=response.status, reason=response.reason)
        )
    
    data: bytes = response.read()
    connection.close()

    if destination is None:
        return data
    elif isinstance(destination, io.BytesIO):
        destination.write(data)
    elif isinstance(destination, io.StringIO):
        destination.write(data.decode())
    elif isinstance(destination, pathlib.Path):
        destination.write_bytes(data=data)
    elif callable(destination):
        return destination(data)
    else:
        raise TypeError(
            "The given destination cannot be used: {destination} (type={destination_type})".format(
                destination=destination,
                destination_type=type(destination)
            )
        )
    
    return None
