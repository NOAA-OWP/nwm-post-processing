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

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

_MAXIMUM_REDIRECTS: int = 5
"""The maximum amount of times that a web response can redirct"""

def get(
    url: str,
    destination: typing.Union[io.BytesIO, io.StringIO, pathlib.Path, typing.Callable[[bytes], typing.Any]] = None,
    redirect_count: int = 0
) -> typing.Optional[bytes]:
    """
    Run a GET request to retrieve data from a network address

    :param url: The address of the resource to retrieve
    :param destination: Where to store the retrieved data
    :param redirect_count: The number of times this request has been redirected
    :returns: The raw data in bytes form if no destination is given
    """
    parsed_url: urllib.parse.ParseResult = urllib.parse.urlparse(url=url)
    """The broken down URL"""

    secured: bool = parsed_url.scheme == "https"
    """Whether the url is secured via https"""

    path: str = parsed_url.path or '/'
    """The path to navigate to once connected"""

    if parsed_url.query:
        path += "?" + parsed_url.query

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
        with open(destination, 'wb') as output_file:
            output_file.write(data)
    elif isinstance(destination, typing.Callable):
        destination(data)
    else:
        raise TypeError(
            "The given destination cannot be used: {destination} (type={destination_type})".format(
                destination=destination,
                destination_type=type(destination)
            )
        )
    
    return None