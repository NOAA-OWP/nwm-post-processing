#!/usr/bin/env python3
"""
Convert a geopackage layer into a netcdf mask
"""
import typing
import pathlib
import argparse
import logging
import os

import geopandas
import xarray

from post_processing.configuration import settings


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


class Arguments:
    """
    The application arguments needed to run the script
    """
    def __init__(self, *args, **kwargs) -> None:
        self.input_path: typing.Optional[pathlib.Path] = kwargs.pop("input_path", None)
        self.output_path: typing.Optional[pathlib.Path] = kwargs.pop("output_path", None)
        self.layer: typing.Optional[str] = kwargs.pop("layer", None)
        self.query: typing.Optional[str] = kwargs.pop("query", None)
        self.dimensions: typing.List[str] = kwargs.pop("dimensions", [])
        self.fields: typing.List[str] = kwargs.pop("fields", [])

        self._parse_args(args=args)
        self._validate()

    def _validate(self):
        assert self.input_path is not None and self.input_path.is_file()
        assert bool(self.layer)
        assert len(self.dimensions) >= 1

    def _parse_args(self, args: typing.Sequence):
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description=__doc__,
        )

        added_required_field: bool = False

        if self.input_path is None:
            parser.add_argument(
                "input_path",
                type=pathlib.Path,
                help="The path to the input geopackage"
            )
            added_required_field = True

        if self.output_path is None:
            parser.add_argument(
                "output_path",
                type=pathlib.Path,
                help="Where the mask should be written"
            )
            added_required_field = True

        if not bool(self.layer):
            parser.add_argument(
                "layer",
                type=str,
                help="The layer that has the information to place into the mask"
            )
            added_required_field = True

        if not bool(self.dimensions):
            parser.add_argument(
                "dimensions",
                type=str,
                nargs="+",
                help="The dimension that defines what values to mask on"
            )
            added_required_field = True

        if not bool(self.fields):
            parser.add_argument(
                "-f",
                "--fields",
                dest="fields",
                nargs="*",
                help="The fields to include in the mask from the geopackage"
            )
            added_required_field = added_required_field or len(args) >= 1

        if not bool(self.query):
            parser.add_argument(
                "--query",
                "-c",
                dest="query",
                type=str,
                help="The sql query used to reduce what does is in the mask"
            )
            added_required_field = added_required_field or len(args) >= 1

        if not added_required_field:
            return

        arguments: argparse.Namespace = parser.parse_args(args or None)

        for key, value in vars(arguments).items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(f"There is no '{key}' attribute in {__file__}::{self.__class__.__name__}")


def load_input_data(
    input_path: pathlib.Path,
    layer: str,
    query: str = None
) -> geopandas.GeoDataFrame:
    """
    Load the data from the input and subset it if necessary

    :param input_path: Where to load the data from
    :param layer: The geopackage layer to load from
    :param query: An optional query to use to restrict the data coming from the layer
    :returns: The input data loaded into a geodataframe
    """
    LOGGER.info(f"Loading data from the '{layer}' table from {input_path}")
    input_data: geopandas.GeoDataFrame = geopandas.read_file(input_path, layer=layer)

    if query:
        LOGGER.info(f"Only data matching '{query}' will be placed into the mask")
        input_data = input_data.query(query)

    return input_data


def convert_dataframe_to_xarray(
    input_data: geopandas.GeoDataFrame,
    dimensions: typing.Sequence[str],
    fields: typing.Sequence[str] = None,
    field_attributes: typing.Mapping[str, typing.Dict[str, typing.Any]] = None,
    field_encoding: typing.Mapping[str, typing.Dict[str, typing.Any]] = None,
    attributes: typing.Dict[str, typing.Any] = None
) -> xarray.Dataset:
    """
    Convert the read layer into an xarray.Dataset to later be converted to netcdf

    :param input_data: The dataframe containing the definitions for the mask
    :param dimensions: What fields in the input data to use as dimensions in the netcdf file
    :param fields: What fields to use as extra context, if any
    :param field_attributes: Attributes for any fields or dimensions
    :param field_encoding: Encoding for any fields or dimensions
    :param attributes: Global level attributes to attach to the netcdf files
    :returns: The dataframe converted into an xarray.Dataset
    """
    if field_attributes is None:
        field_attributes = {}

    if field_encoding is None:
        field_encoding = {}

    if fields is None:
        fields = []

    if not all(dimension in input_data.columns for dimension in dimensions):
        missing_dimensions: typing.List[str] = list(filter(
            lambda dimension: dimension not in input_data.columns,
            dimensions
        ))
        raise KeyError(
            f"Cannot use the following fields as dimensions - they aren't in the input: {', '.join(missing_dimensions)}"
        )

    if not all(field_name in input_data.columns for field_name in fields):
        missing_fields: typing.List[str] = list(filter(
            lambda field_name: field_name not in input_data.columns,
            fields
        ))
        raise KeyError(
            f"Cannot use the following fields as variables - they aren't in the input: {', '.join(missing_fields)}"
        )

    coordinates: typing.Dict[str, xarray.Variable] = {
        field_name: xarray.Variable(
            dims=field_name,
            data=input_data[field_name].values,
            attrs=field_attributes.get(field_name),
            encoding=field_encoding.get(field_name)
        )
        for field_name in dimensions
    }

    variables: typing.Dict[str, xarray.Variable] = {
        field_name: xarray.Variable(
            dims=dimensions,
            data=input_data[field_name].values,
            attrs=field_attributes.get(field_name),
            encoding=field_encoding.get(field_name)
        )
        for field_name in fields
    }

    LOGGER.info(f"Creating the dataset...")
    dataset: xarray.Dataset = xarray.Dataset(
        data_vars=variables,
        coords=coordinates,
        attrs=attributes
    )

    return dataset


def save_data(output_data: xarray.Dataset, output_path: pathlib.Path):
    """
    Write the generated data to disk

    :param output_data: The data to write to disk
    :param output_path: Where to write the data
    """
    output_data.to_netcdf(output_path)
    LOGGER.info(f"Wrote the mask to: {output_path}")


def main(*args, **kwargs) -> int:
    """
    The full application logic
    """
    arguments: Arguments = Arguments(*args, **kwargs)

    try:
        input_data: geopandas.GeoDataFrame = load_input_data(
            input_path=arguments.input_path,
            layer=arguments.layer,
            query=arguments.query,
        )
        output_data: xarray.Dataset = convert_dataframe_to_xarray(
            input_data=input_data,
            dimensions=arguments.dimensions,
            fields=arguments.fields,
        )
        save_data(
            output_data=output_data,
            output_path=arguments.output_path
        )
        print(f"Written data:{os.linesep}{output_data}")
    except:
        LOGGER.error(f"Could not create a mask", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=settings.log_format,
        datefmt=settings.date_format,
    )
    import sys
    sys.exit(main())
