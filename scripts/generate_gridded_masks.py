#!/usr/bin/env python3
"""
Split a Netcdf file up by a shapefile
"""
import typing
import pathlib
import logging
import sys
import asyncio
import argparse
import os

from datetime import datetime
from urllib.parse import urlparse
from urllib.parse import ParseResult

import geopandas
import xarray
import numpy

from affine import Affine
from pyproj import crs
from rasterio.features import rasterize

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

DATETIME_FORMAT: str = "%Y-%m-%d %H:%M:%S%z"

DEFAULT_RFC_BOUNDS_PATH: str = "https://www.weather.gov/source/gis/Shapefiles/Misc/rf05mr24.zip"
"""The address to the official RFC bounds shapefile"""


class Arguments:
    def __init__(self, *args, **kwargs) -> None:
        self.path: pathlib.Path = kwargs.get("path")
        self.shapefile: typing.Union[str, pathlib.Path] = kwargs.get("shapefile", DEFAULT_RFC_BOUNDS_PATH)
        self.output: pathlib.Path = kwargs.get("output")
        self.geometry_variable: str = kwargs.get("geometry_variable", "geometry")
        self.label_variable: str = kwargs.get("label_variable", "BASIN_ID")
        self.crs_variable: str = kwargs.get("crs_variable", "crs")
        self.crs_attribute: str = kwargs.get("crs_attribute", "esri_pe_string")
        self.x_variable: str = kwargs.get("x_variable", "x")
        self.y_variable: str = kwargs.get("y_variable", "y")
        self._parse_args(args=args)
        self._validate()

    def _validate(self):
        if not self.path.is_file():
            raise FileNotFoundError(f"Could not find an input NetCDF file: {self.path}")

        if is_address(self.shapefile):
            import requests
            with requests.head(str(self.shapefile)) as response:
                if response.status_code >= 400:
                    raise FileNotFoundError(f"Could not access a shapefile from {self.shapefile}")
        elif not self.shapefile.is_file():
            raise FileNotFoundError(f"Could not find a shapefile describing boundaries: {self.shapefile}")
        else:
            self.shapefile = pathlib.Path(self.shapefile)

    def _parse_args(self, args: typing.Sequence[str]) -> None:
        parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)

        parser.add_argument(
            f"{'--' if self.path is not None else ''}path",
            type=pathlib.Path,
            default=self.path,
            help="The path to the netcdf file"
        )
        parser.add_argument(
            "--shapefile",
            "-s",
            type=str,
            default=self.shapefile,
            help="The shapefile to use to slice up the netcdf"
        )

        parser.add_argument(
            f"{'--' if self.output is not None else ''}output",
            type=pathlib.Path,
            default=self.output,
            help="The output path"
        )

        parser.add_argument(
            "--geometry_variable",
            "-g",
            default=self.geometry_variable,
            help="The name of the variable in the shapefile holding geometry data"
        )

        parser.add_argument(
            "--label_variable",
            "-l",
            default=self.label_variable,
            help="The name of the variable in the shapefile to group on"
        )

        parser.add_argument(
            "--crs_variable",
            "-c",
            default=self.crs_variable,
            help="The name of the variable in the netcdf holding coordinate data"
        )

        parser.add_argument(
            "--crs_attribute",
            "-a",
            default=self.crs_attribute,
            help="The name of the attribute on the CRS variable that describes the coordinate reference system"
        )

        parser.add_argument(
            "--x_variable",
            "-x",
            default=self.x_variable,
            help="The name of the variable in the netcdf holding x coordinate data"
        )

        parser.add_argument(
            "--y_variable",
            "-y",
            default=self.y_variable,
            help="The name of the variable in the netcdf holding y coordinate data"
        )

        parameters: argparse.Namespace = parser.parse_args(args or None)

        for key, value in vars(parameters).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(
                    f"There is no attribute on {pathlib.Path(__file__).stem}.{self.__class__.__name__} named '{key}'"
                )


def is_address(path: str | pathlib.Path) -> bool:
    """
    Determines if a string path is a web address or not

    :param path: The path to a resource
    :return: True if the path is a web address or not
    """
    if not isinstance(path, str):
        path = str(path)
    parsed_path: ParseResult = urlparse(path)
    return parsed_path.scheme.startswith("http")


async def run(
    netcdf_path: pathlib.Path,
    shapefile_path: pathlib.Path,
    output_path: pathlib.Path,
    geometry_variable: str = "geometry",
    label_variable: str = "BASIN_ID",
    crs_variable: str = "crs",
    crs_attribute: str = "esri_pe_string",
    x_variable: str = "x",
    y_variable: str = "y",
) -> pathlib.Path:
    """
    Run the application logic directly

    :param netcdf_path: Path to the netcdf file
    :param shapefile_path: Path to the shapefile
    :param output_path: Path to where to save the result
    :param geometry_variable: Name of the variable in the shapefile holding geometry data
    :param label_variable: The name of the variable in the shapefile holding grouping data
    :param crs_variable: The name of the variable in the netcdf file holding coordinate data
    :param crs_attribute: The name of the attribute on the CRS variable that describes the coordinate reference system
    :param x_variable: The name of the variable in the netcdf file holding x coordinate data
    :param y_variable: The name of the variable in the netcdf file holding y coordinate data
    :returns: The path to the produced netcdf file
    """
    LOGGER.info(f"Using the input grid from '{netcdf_path}'")
    netcdf_data: xarray.Dataset = xarray.open_dataset(netcdf_path)
    netcdf_data.load()

    crs_value: str = netcdf_data[crs_variable].attrs[crs_attribute]

    reference_system: crs.CRS = crs.CRS.from_string(crs_value)

    x_values: numpy.typing.NDArray[numpy.float64] = netcdf_data[x_variable].values
    y_values: numpy.typing.NDArray[numpy.float64] = netcdf_data[y_variable].values
    x_size: int = netcdf_data[x_variable].size
    y_size: int = netcdf_data[y_variable].size
    pixel_width: float = float(numpy.mean(numpy.diff(x_values)))
    pixel_height: float = float(numpy.mean(numpy.diff(y_values)))

    output_shape: tuple[int, int] = (y_size, x_size)

    y_descends: bool = pixel_height < 0

    if y_descends:
        pixel_height = -abs(pixel_height)
        center_of_y_origin: float = y_values.max() + pixel_height / 2
    else:
        center_of_y_origin: float = y_values.min() + pixel_height / 2

    center_of_x_origin: float = x_values.min() + pixel_width / 2

    transform: Affine = Affine(
        pixel_width,
        0,
        center_of_x_origin,
        0,
        pixel_height,
        center_of_y_origin,
    )

    LOGGER.info(f"Loading the shapefile from '{shapefile_path}'")
    shapefile: geopandas.GeoDataFrame = geopandas.read_file(shapefile_path)
    shapefile = shapefile.to_crs(reference_system)
    shapefile = shapefile[[geometry_variable, label_variable]]

    split_dimensions: tuple[str, ...] = (y_variable, x_variable)
    split_coordinates: dict[str, xarray.DataArray] = {
        x_variable: netcdf_data[x_variable].copy(deep=True),
        y_variable: netcdf_data[y_variable].copy(deep=True),
    }
    split_data: dict[str, xarray.DataArray] = {
        crs_variable: netcdf_data[crs_variable].copy(deep=True),
    }
    split_attributes: dict[str, typing.Any] = {
        "TITLE": "Mask for Y/X Gridded Variables",
        "Conventions": "CF-1.8",
        "author": os.environ.get("USER", os.environ.get("USERNAME", "UNKNOWN")),
    }

    if 'proj4' in netcdf_data.attrs.keys():
        split_attributes['proj4'] = netcdf_data.attrs['proj4']

    for row_index, row in shapefile.iterrows(): # type: geopandas.GeoSeries
        geometry = row[geometry_variable]
        label: str = row[label_variable]
        LOGGER.info(f"Generating the mask for '{label}'")
        shape: tuple = (geometry.__geo_interface__,)

        mask = rasterize(
            shapes=shape,
            out_shape=output_shape,
            transform=transform,
            fill=0,
            dtype="uint8",
            all_touched=True
        )

        variable: xarray.DataArray = xarray.DataArray(
            data=mask,
            dims=split_dimensions,
            coords={
                y_variable: netcdf_data.coords[y_variable],
                x_variable: netcdf_data.coords[x_variable]
            },
            name=label,
            attrs={
                "long_name": f"Polygon Mask for {label}",
                "inside_value": numpy.uint8(1),
                "outside_value": numpy.uint8(0),
                "grid_mapping": crs_variable,
                "units": "1",
                "flag_values": "0 1",
                "flag_meanings": "outside inside",
                "coordinates": " ".join(split_dimensions),
            }
        )
        variable.encoding['dtype'] = 'uint8'

        split_data[label] = variable

    output_data: xarray.Dataset = xarray.Dataset(
        data_vars=split_data,
        coords=split_coordinates,
        attrs={
            "date": datetime.now().astimezone().strftime(DATETIME_FORMAT),
            **split_attributes
        }
    )

    LOGGER.info(f"Masks generated:{os.linesep}{output_data}")

    for variable_name, variable in output_data.data_vars.items():
        if variable.dtype == numpy.uint8:
            variable.encoding['dtype'] = 'uint8'
            variable.encoding['zlib'] = True
            variable.encoding['shuffle'] = True,
            variable.encoding['contiguous'] = False
            variable.encoding['complevel'] = numpy.uint8(5)
            variable.encoding['chunksizes'] = (numpy.uint16(512), numpy.uint16(512))
        elif variable_name in netcdf_data:
            original_variable: xarray.DataArray = netcdf_data[variable_name]
            output_data[variable_name].encoding = original_variable.encoding.copy()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_data.to_netcdf(output_path)
    LOGGER.info(f"Masks saved to '{output_path}'")
    return output_path


def main() -> int:
    parameters: Arguments = Arguments()

    try:
        result: pathlib.Path = asyncio.run(run(
            netcdf_path=parameters.path,
            shapefile_path=parameters.shapefile,
            output_path=parameters.output,
            geometry_variable=parameters.geometry_variable,
            label_variable=parameters.label_variable,
            crs_variable=parameters.crs_variable,
            crs_attribute=parameters.crs_attribute,
            x_variable=parameters.x_variable,
            y_variable=parameters.y_variable,
        ))
        LOGGER.info(f"Masked data has been written to '{result}'")
    except KeyboardInterrupt:
        LOGGER.info(f"Received user interrupt. Now exiting {__file__}...")
    except asyncio.CancelledError as cancellation:
        LOGGER.info(cancellation)
    except Exception as e:
        LOGGER.info(e, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] - %(name)s - %(levelname)s - %(message)s",
        datefmt=DATETIME_FORMAT,
    )
    sys.exit(main())
