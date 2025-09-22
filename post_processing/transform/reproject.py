"""
Functions and objects used to reproject gridded data from one CRS to another

Important Concepts:

Affine:
    An affine transformation is a linear mapping from grid coordinates (indices) to real world coordinates
    (meters or degrees). It defines how a pixel at location (col, row) on a 2D grid maps to (x, y) in a projected space
"""
import typing
import pathlib
import logging
import warnings
import collections.abc as generic
import dataclasses

from threading import RLock

import rasterio.warp
import affine
import xarray
import numpy
import pyproj
from rasterio.errors import NotGeoreferencedWarning

from post_processing.configuration import settings
from post_processing.utilities.common import starmap_threaded
from post_processing.utilities.netcdf import load_netcdf

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
_LOAD_LOCK: RLock = RLock()

@dataclasses.dataclass
class Projection:
    path: pathlib.Path | None
    transformation: affine.Affine
    crs: rasterio.CRS
    x_values: xarray.DataArray
    y_values: xarray.DataArray
    crs_attributes: dict[str, typing.Any]
    crs_variable_name: str

    @property
    def proj4(self) -> str:
        """The proj4 specification for this projection"""
        return self.crs.to_proj4()

    @property
    def crs_string(self) -> str:
        """The crs_string used in CRS attributes"""
        return self.crs_attributes.get("esri_pe_string", self.crs_attributes.get("spatial_ref"))

    @property
    def is_north_up(self) -> bool:
        """
        Whether the grid leads North-to-South
        """
        return self.y_values.data[0] > self.y_values.data[-1]

    def __hash__(self):
        return hash((
            str(self.path),
            self.transformation,
            str(self.crs),
            *[f"{attribute_name}={attribute_value}" for attribute_name, attribute_value in self.crs_attributes.items()],
            self.crs_variable_name,
        ))

    @classmethod
    def read_dataset(
        cls,
        projection_dataset: xarray.Dataset,
        x_variable_name: str = None,
        y_variable_name: str = None,
        crs_variable_name: str = None,
        projection_attribute_name: str = None
    ) -> "Projection":
        x_values: xarray.DataArray = get_x_coordinate(
            dataset=projection_dataset,
            variable_name=x_variable_name
        )
        y_values: xarray.DataArray = get_y_coordinate(
            dataset=projection_dataset,
            variable_name=y_variable_name
        ).copy()
        crs: rasterio.CRS = get_crs(
            dataset=projection_dataset,
            crs_variable_name=crs_variable_name,
            projection_string_attribute=projection_attribute_name
        )
        transformation: affine.Affine = get_affine_transformation(
            dataset=projection_dataset,
            x_variable_name=x_variable_name,
            y_variable_name=y_variable_name,
        )
        crs_variable: xarray.DataArray = get_crs_variable(
            dataset=projection_dataset,
            variable_name=crs_variable_name
        )
        projection: Projection = Projection(
            path=None,
            x_values=x_values,
            y_values=y_values,
            crs=crs,
            transformation=transformation,
            crs_attributes=crs_variable.attrs.copy(),
            crs_variable_name=str(crs_variable.name),
        )
        return projection

    @classmethod
    def load_file(
        cls,
        path: pathlib.Path,
        x_variable_name: str = None,
        y_variable_name: str = None,
        crs_variable_name: str = None,
        projection_attribute_name: str = None
    ) -> "Projection":
        with _LOAD_LOCK:
            with load_netcdf(path, full_load=True) as projection_dataset:
                projection: Projection = cls.read_dataset(
                    projection_dataset=projection_dataset,
                    x_variable_name=x_variable_name,
                    y_variable_name=y_variable_name,
                    crs_variable_name=crs_variable_name,
                    projection_attribute_name=projection_attribute_name
                )
                projection.path = path
                return projection

    def matches(self, other: "Projection") -> bool:
        if not isinstance(other, self.__class__):
            raise TypeError(
                f"Cannot check if this (type={self.__class__.__qualname__}) matches a '{other.__class__.__name__}'"
            )

        if self.shape != other.shape:
            return False

        return numpy.array_equal(self.x_values.data, other.x_values.data) and numpy.array_equal(self.y_values.data, other.y_values.data)

    def __str__(self):
        return (
            f"{self.path.stem if isinstance(self.path, pathlib.Path) else self.crs_variable_name}"
            f"({self.y_values.shape[0]}, {self.x_values.shape[0]})"
        )

    @property
    def shape(self) -> tuple[int, int]:
        return self.y_values.shape[0], self.x_values.shape[0]

    def __len__(self):
        return self.y_values.shape[0] * self.x_values.shape[0]


class _ProjectionStore:
    def __init__(self):
        self.__store: dict[int, Projection] = {}
        self.__lock: RLock = RLock()

    def get(
        self,
        path: pathlib.Path,
        x_variable_name: str = None,
        y_variable_name: str = None,
        crs_variable_name: str = None,
        projection_attribute_name: str = None
    ) -> Projection:
        with self.__lock:
            key: int = hash((
                str(path),
                x_variable_name,
                y_variable_name,
                crs_variable_name,
                projection_attribute_name
            ))

            if key not in self.__store:
                projection: Projection = Projection.load_file(
                    path=path,
                    x_variable_name=x_variable_name,
                    y_variable_name=y_variable_name,
                    crs_variable_name=crs_variable_name,
                    projection_attribute_name=projection_attribute_name
                )
                self.__store[key] = projection

            return self.__store[key]

ProjectionStore: _ProjectionStore = _ProjectionStore()



def get_affine_transformation(
    dataset: xarray.Dataset,
    x_variable_name: str = None,
    y_variable_name: str = None,
    coordinate_is_centered: bool = True,
) -> affine.Affine:
    """
    Calculates an affine transformation based on data from a dataset

    :param dataset: The dataset containing the coordinate data
    :param x_variable_name: The name of the variable containing the x coordinate values
    :param y_variable_name: The name of the variable containing the y coordinate values
    :param coordinate_is_centered: Whether the coordinate is centered in the cell rather than in a corner
    :returns: An affine transformation that may be used to reproject coordinates
    """
    import xarray
    import affine

    # TODO: Add validations

    centering_adjustment: float = 0.5 if coordinate_is_centered else 1

    x_variable: xarray.DataArray = get_x_coordinate(dataset=dataset, variable_name=x_variable_name)
    y_variable: xarray.DataArray = get_y_coordinate(dataset=dataset, variable_name=y_variable_name)

    x_difference: float = x_variable.diff(x_variable.dims[0]).mean().item()
    y_difference: float = y_variable.diff(y_variable.dims[0]).mean().item()

    x_offset: float = x_difference * centering_adjustment
    y_offset: float = y_difference * centering_adjustment

    minimum_x: float = x_variable.min().item()
    maximum_x: float = x_variable.max().item()

    minimum_y: float = y_variable.min().item()
    maximum_y: float = y_variable.max().item()

    west: float = minimum_x - x_offset
    east: float = maximum_x + x_offset

    south: float = minimum_y - y_offset
    north: float = maximum_y + y_offset

    width: float = float(x_variable.sizes[x_variable.dims[0]])
    height: float = float(y_variable.sizes[y_variable.dims[0]])

    transform: affine.Affine = rasterio.transform.from_bounds(
        west=west,
        east=east,
        south=south,
        north=north,
        width=width,
        height=height,
    )

    if not all(numpy.isfinite(transform)) or transform == affine.Affine.identity():
        LOGGER.warning(f"A dataset produced an Affine with a NaN or inf in it - expect GDAL to complain")

    return transform

def get_y_coordinate(dataset: xarray.Dataset, variable_name: str = None) -> xarray.DataArray:
    """
    Get the Y variable from a dataset

    :param dataset: The dataset containing the coordinate data
    :param variable_name: The name of the variable containing the y coordinate values
    :returns: A variable containing the Y coordinate values
    """
    import xarray

    if variable_name is None and 'lat' in dataset.variables:
        variable_name = 'lat'
    elif variable_name is None and 'y' in dataset.variables:
        variable_name = 'y'
    elif variable_name is None:
        raise ValueError('y_variable_name must be specified and was not')

    if variable_name not in dataset.variables:
        raise KeyError(f"'{variable_name}' is not a valid variable name")

    variable: xarray.DataArray = dataset[variable_name]
    return variable

def get_x_coordinate(dataset: xarray.Dataset, variable_name: str = None) -> xarray.DataArray:
    """
    Get the X variable from a dataset

    :param dataset: The dataset containing the coordinate data
    :param variable_name: The name of the variable containing the x coordinate values
    :returns: A variable containing the X coordinate values
    """
    if variable_name is None and 'lon' in dataset.variables:
        variable_name = 'lon'
    elif variable_name is None and 'x' in dataset.variables:
        variable_name = 'x'
    elif variable_name is None:
        raise ValueError('x_variable_name must be specified and was not')

    if variable_name not in dataset.variables:
        raise KeyError(f"'{variable_name}' is not a valid variable name")

    variable: xarray.DataArray = dataset[variable_name]
    return variable

def get_crs_variable(dataset: xarray.Dataset, variable_name: str = None) -> xarray.DataArray:
    """
    Get the coordinate reference system variable from a dataset

    :param dataset: The dataset containing the coordinate data
    :param variable_name: The name of the variable containing the coordinate reference system
    :returns: The variable containing the metadata for the coordinate reference system
    """
    if variable_name is None and 'crs' in dataset.variables:
        variable_name = 'crs'
    elif variable_name is None and 'CRS' in dataset.variables:
        variable_name = 'CRS'
    elif variable_name is None and 'mercator' in dataset.variables:
        variable_name = 'mercator'
    elif variable_name is None:
        raise ValueError('crs_variable_name must be specified and was not')

    if variable_name not in dataset.variables:
        raise KeyError(f"'{variable_name}' is not a valid variable name")

    variable: xarray.DataArray = dataset[variable_name]
    return variable

def get_crs(
    dataset: "xarray.Dataset",
    crs_variable_name: str = None,
    projection_string_attribute: str = None
) -> rasterio.CRS:
    """
    Build the Coordinate Reference System object used for transformation

    :param dataset: The dataset containing the reference system
    :param crs_variable_name: The name of the variable containing crs information
    :param projection_string_attribute: The name of the attribute containing the projection string on the CRS variable
    :returns: The formal CRS object needed by rasterio to perform the reprojection
    """
    crs_variable: xarray.DataArray = get_crs_variable(dataset=dataset, variable_name=crs_variable_name)

    if projection_string_attribute is None and 'esri_pe_string' in crs_variable.attrs:
        projection_string_attribute = 'esri_pe_string'
    elif projection_string_attribute is None and 'spatial_ref' in crs_variable.attrs:
        projection_string_attribute = 'spatial_ref'
    elif projection_string_attribute is None:
        raise ValueError("The name of the project string attribute in the CRS variable must be specified and was not")

    projection_string: str = crs_variable.attrs[projection_string_attribute]

    crs = rasterio.CRS().from_wkt(projection_string, False)

    return crs


def coordinates_are_the_same(
    dataset: xarray.Dataset,
    reprojection_dataset: xarray.Dataset,
    x_coordinate_name: str = None,
    y_coordinate_name: str = None,
    reprojection_x_coordinate_name: str = None,
    reprojection_y_coordinate_name: str = None,
) -> bool:
    """
    Determine if the Coordinate reference system from two datasets are functionally the same

    :param dataset: The original set of data that whose coordinates we may want to change
    :param reprojection_dataset: A dataset that has a projection we want to match
    :param x_coordinate_name: The name of the variable containing the x coordinate values in the original dataset
    :param y_coordinate_name: The name of the variable containing the y coordinate values in the original dataset
    :param reprojection_x_coordinate_name: The name of the variable containing the x coordinate values in the target projection
    :param reprojection_y_coordinate_name: The name of the variable containing the y coordinate values in the target projection
    :returns: True if all the x and y values match in both datasets
    """
    import xarray
    import numpy

    original_x_variable: xarray.DataArray = get_x_coordinate(dataset=dataset, variable_name=x_coordinate_name)
    original_y_variable: xarray.DataArray = get_y_coordinate(dataset=dataset, variable_name=y_coordinate_name)

    reprojection_x_variable: xarray.DataArray = get_x_coordinate(
        dataset=reprojection_dataset,
        variable_name=reprojection_x_coordinate_name
    )

    reprojection_y_variable: xarray.DataArray = get_y_coordinate(
        dataset=reprojection_dataset,
        variable_name=reprojection_y_coordinate_name
    )

    # Check the shapes first since that's much faster to rule out
    if original_x_variable.shape != reprojection_x_variable.shape:
        return False

    if original_y_variable.shape != reprojection_y_variable.shape:
        return False

    if not numpy.array_equal(original_x_variable.values, reprojection_x_variable.values):
        return False

    if not numpy.array_equal(original_y_variable.values, reprojection_y_variable.values):
        return False

    return True


def get_fill_value(variable: "xarray.DataArray") -> typing.Any:
    """
    Get an appropriate fill value based on the data within the given variable

    :param variable: The variable to get the fill value for
    :returns: The appropriate fill value for the given variable
    """
    import numpy

    datatype: numpy.dtype = numpy.dtype(variable.dtype)

    if numpy.issubdtype(datatype, numpy.floating):
        return numpy.nan
    if numpy.issubdtype(datatype, numpy.integer) and datatype.kind == 'u':
        return 0
    if numpy.issubdtype(datatype, numpy.integer):
        return numpy.iinfo(datatype).min + 1
    if numpy.issubdtype(datatype, numpy.bool_):
        return False
    if numpy.issubdtype(datatype, numpy.str_):
        return ""
    if numpy.issubdtype(datatype, numpy.bytes_):
        return b""
    if numpy.issubdtype(datatype, numpy.object_):
        return None

    raise TypeError(f"Cannot determine an appropriate fill value for the {variable.name} variable (dtype={datatype})")


def form_and_regrid_band(
    source_variable: xarray.DataArray,
    input_projection: Projection,
    target_projection: Projection,
    resampling_strategy: rasterio.warp.Resampling = None,
    warp_worker_threads: int = 4
) -> xarray.DataArray:
    """
    Convert the xarray data into a rasterio band and convert all dimensions from one projection to another via rasterio

    :param source_variable: The variable to convert
    :param input_projection: The projection to convert from
    :param target_projection: The projection to convert to
    :param resampling_strategy: The resampling strategy to use for locations that weren't in the source
    :param warp_worker_threads: The number of threads to use for warping
    :returns: The transformed data
    """
    if resampling_strategy is None:
        resampling_strategy = rasterio.warp.Resampling.nearest

    # TODO: Ensure that all of the dimensions are in the right order

    if len(source_variable.shape) != 3:
        raise ValueError(f"'form_and_regrid_band' can only operate on a 3D variable - dimensions were: {source_variable.name}({source_variable.sizes})")

    original_dimensions: tuple[generic.Hashable, ...] = source_variable.dims

    dimension_names: list[generic.Hashable] = list(original_dimensions)
    dimension_names.remove(input_projection.x_values.name)
    dimension_names.remove(input_projection.y_values.name)

    new_dimension_order: tuple[generic.Hashable, generic.Hashable, generic.Hashable] = (
        dimension_names[0],
        input_projection.y_values.name,
        input_projection.x_values.name,
    )

    source_variable = source_variable.transpose(*new_dimension_order)

    fill_value = get_fill_value(source_variable)

    output_array: numpy.ndarray = numpy.full(
        shape=tuple([*list(map(lambda dim: source_variable.sizes[dim], dimension_names)), *target_projection.shape]),
        fill_value=fill_value,
        dtype=source_variable.dtype
    )

    rasterio.warp.reproject(
        source=source_variable.data,
        destination=output_array,
        src_transform=input_projection.transformation,
        src_crs=input_projection.crs,
        src_nodata=fill_value,
        dst_transform=target_projection.transformation,
        dst_crs=target_projection.crs,
        dst_nodata=fill_value,
        resampling_strategy=resampling_strategy,
        warp_worker_threads=warp_worker_threads
    )

    transformed_array: xarray.DataArray = xarray.DataArray(
        name=source_variable.name,
        data=output_array,
        coords=[
            source_variable.coords[dimension_names[0]],
            target_projection.y_values,
            target_projection.x_values,
        ],
        dims=new_dimension_order,
        attrs=source_variable.attrs.copy()
    )

    transformed_array = transformed_array.transpose(*original_dimensions)
    transformed_array.attrs['grid_mapping'] = target_projection.crs_variable_name
    transformed_array.encoding.update(source_variable.encoding)
    return transformed_array

def regrid_data(
    source_variable: xarray.DataArray,
    selectors: dict[str, int],
    x_variable_name: str,
    y_variable_name: str,
    source_crs: rasterio.CRS,
    target_crs: rasterio.CRS,
    source_transform: affine.Affine,
    target_transform: affine.Affine,
    output_shape: tuple[int, ...],
    resampling_strategy: rasterio.warp.Resampling = rasterio.warp.Resampling.nearest,
    warp_worker_threads: int = 4
) -> tuple[tuple[int, ...], numpy.ndarray]:
    """
    Take a slice out of an xarray variable and reproject it into a different grid

    :param source_variable: The source variable to retrieve a slice from
    :param selectors: Selection criteria used to extra the data needed to slice
    :param x_variable_name: The name of the variable containing the x coordinate values
    :param y_variable_name: The name of the variable containing the y coordinate values
    :param source_crs: The CRS of the source variable
    :param target_crs: The CRS of the target variable
    :param source_transform: The transform to apply to the source variable
    :param target_transform: The transform to apply to the target variable
    :param output_shape: The shape of the data that needs to be returned
    :param resampling_strategy: The resampling strategy to use to plot new points
    :param warp_worker_threads: The number of worker threads to use for warping
    """
    import numpy

    output_buffer: numpy.typing.NDArray = numpy.full(
        shape=output_shape,
        fill_value=numpy.nan,
        dtype=source_variable.dtype,
    )

    sliced_data: numpy.typing.NDArray = source_variable.isel(
        **selectors
    ).transpose(
        y_variable_name,
        x_variable_name
    ).values

    if len(sliced_data.shape) != 2:
        raise ValueError(
            f"Selecting spatial data for reprojection by '({', '.join(selectors.keys())})' resulted in a "
            f"{len(sliced_data.shape)} dimensional array. Only 2 dimensions are supported."
        )

    # rasterio.warp.reproject hits proj4, outside of python, and raises an unavoidable warning, so catch it - not our problem
    with warnings.catch_warnings():
        warnings.filterwarnings(
            action="ignore",
            message="You will likely lose important projection information",
            category=UserWarning
        )
        warnings.filterwarnings(
            action="ignore",
            message="Dataset has no geotransform, gcps, or rpcs. The identity matrix will be returned.",
            category=NotGeoreferencedWarning,
        )

        if settings.this_is_very_verbose:
            LOGGER.debug(f"Warping the grid for '{source_variable.name}'")

        rasterio.warp.reproject(
            source=sliced_data,
            destination=output_buffer,
            src_transform=source_transform,
            dst_transform=target_transform,
            src_crs=source_crs,
            dst_crs=target_crs,
            src_nodata=numpy.nan,
            dst_nodata=numpy.nan,
            resampling=resampling_strategy,
            num_threads=warp_worker_threads,
        )

        if settings.this_is_very_verbose:
            LOGGER.debug(f"The grid for '{source_variable.name}' has been warped to meet the new grid")

    if source_variable.dims.index(x_variable_name) < source_variable.dims.index(y_variable_name):
        output_buffer = output_buffer.T

    return tuple(map(int, selectors.values())), output_buffer


def reproject_variable(
    source_variable: xarray.DataArray,
    input_projection: Projection,
    target_projection: Projection,
    resampling_strategy: rasterio.warp.Resampling = None,
    warp_worker_threads: int = 4
) -> xarray.DataArray:
    """
    Create a new variable from the source variable but in the target's projection

    :param source_variable: The source variable to reproject
    :param input_projection: The projection that applies to the current data
    :param target_projection: The projection we wish to impose on the data
    :param resampling_strategy: How to resample the values into the new projections
    :param warp_worker_threads: The number of threads that may work on the reprojection warping at once
    :returns: The reprojected variable
    """
    import rasterio.warp
    import xarray
    import numpy

    # If this is just the definition of the X axis, just reassign the values
    if source_variable.name == input_projection.x_values.name:
        new_x_variable = xarray.DataArray(
            name=input_projection.x_values.name,
            dims=source_variable.dims,
            data=target_projection.x_values.data,
            attrs=target_projection.x_values.attrs,
        )
        new_x_variable.encoding.update(target_projection.x_values.encoding)
        return new_x_variable

    # If this is just the definition of the Y axis, just reassign the values
    if source_variable.name == input_projection.y_values.name:
        new_y_variable = xarray.DataArray(
            name=input_projection.y_values.name,
            dims=source_variable.dims,
            data=target_projection.y_values.data,
            attrs=target_projection.y_values.attrs,
        )
        new_y_variable.encoding.update(target_projection.y_values.encoding)
        return new_y_variable

    # You don't need to reproject if this particular variable isn't a grid, so just return what you have
    if len(source_variable.shape) < 2:
        return source_variable

    has_temporal_dimension: bool = any(
        "time" in str(dimension_name).lower()
        for dimension_name in source_variable.dims
    )

    # If the length is 2 and one of the variables describes time somehow, we know this isn't spatial so we can move on
    if len(source_variable.shape) == 2 and has_temporal_dimension:
        return source_variable

    if input_projection.x_values.name not in source_variable.dims:
        raise KeyError(
            f"Cannot reproject a dataset - the variable for the X-Axis ('{input_projection.x_values.name}') is not on the "
            f"{source_variable.name} variable"
        )

    if input_projection.y_values.name not in source_variable.dims:
        raise KeyError(
            f"Cannot reproject a dataset - the variable for the Y-Axis ('{input_projection.y_values.name}') is not on the "
            f"{source_variable.name} variable"
        )

    if input_projection.shape == target_projection.shape:
        reprojected_data: numpy.ndarray = source_variable.data
    else:
        # If no explicit resampling strategy is given, just go with the nearest value
        if resampling_strategy is None:
            resampling_strategy = rasterio.warp.Resampling.nearest

        output_shape: list[int] = []
        """
        The shape of the final output
        """

        non_spatial_selectors: list[tuple[str, int]] = []
        """
        A series of keys and sizes describing the dimensions, in order. 
        If the input coordinates are (time=18, x=4608, y=4092), this will result in [('time', 18)].
        We ensure order while maintaining the ability to find the right value
        """

        # Extract the correct output coordinates and determine what coordinates, in what order, to select data by
        # outside the scope of the spatial realm
        for dimension, count in source_variable.sizes.items():
            if dimension == input_projection.x_values.name:
                # Append the target's size rather than the current size since we're changing shape
                output_shape.append(target_projection.x_values.shape[0])
            elif dimension == input_projection.y_values.name:
                # Append the target's size rather than the current size since we're changing shape
                output_shape.append(target_projection.y_values.shape[0])
            else:
                # We'll select grids by these indices since they aren't involved in the projection
                non_spatial_selectors.append((str(dimension), count))

                # Add the original count since this dimension won't be changing
                output_shape.append(count)

        # Form combinations of non-spatial index groupings to select data by
        if non_spatial_selectors:
            from itertools import product
            index_combinations: typing.Iterable[tuple[int, ...]] = product(
                *[
                    range(size) for _, size in non_spatial_selectors
                ]
            )
        else:
            # If the source was a grid and a grid alone, collect an empty tuple - this will run the loop once and not
            # select specific values
            index_combinations: typing.Iterable[tuple[int, ...]] = [tuple()]

        slice_arguments: generic.Generator[dict[str, typing.Any]] = (
            {
                "source_variable": source_variable,
                "selectors": dict(zip(
                    map(lambda pair: pair[0], non_spatial_selectors),
                    index_combination
                )),
                "x_variable_name": input_projection.x_values.name,
                "y_variable_name": input_projection.y_values.name,
                "source_crs": input_projection.crs,
                "target_crs": target_projection.crs,
                "source_transform": input_projection.transformation,
                "target_transform": target_projection.transformation,
                "output_shape": target_projection.shape,
                "resampling_strategy": resampling_strategy,
                "warp_worker_threads": warp_worker_threads,
            }
            for index_combination in index_combinations
        )

        reprojected_slices: generic.Sequence[tuple[tuple[int, ...], numpy.ndarray]] = starmap_threaded(
            function=regrid_data,
            args=slice_arguments,
        )
        
        fill_value = get_fill_value(variable=source_variable)

        # Allocate space for the reprojected data in the shape of the non-spatial coordinates from the source and the
        # spatial coordinates for the target projection
        reprojected_data: numpy.ndarray = numpy.full(
            shape=tuple(output_shape),
            fill_value=fill_value,
            dtype=source_variable.dtype
        )
        """The matrix that will contain all of the values from the source in the new projection"""

        for coordinates, data in reprojected_slices:
            reprojected_data[coordinates] = data

    reprojected_variable: xarray.DataArray = xarray.DataArray(
        name=source_variable.name,
        data=reprojected_data,
        dims=source_variable.dims,
        attrs=source_variable.attrs
    )
    return reprojected_variable


def reproject_dataset(
    dataset: xarray.Dataset,
    reprojection_dataset_path: pathlib.Path,
    crs_variable_name: str = None,
    reprojection_crs_variable_name: str = None,
    projection_string_attribute: str = None,
    x_coordinate_name: str = None,
    y_coordinate_name: str = None,
    reprojection_string_attribute: str = None,
    reprojection_x_coordinate_name: str = None,
    reprojection_y_coordinate_name: str = None,
    resampling_strategy: rasterio.warp.Resampling = None,
) -> xarray.Dataset:
    """
    Reprojects a 3D dataset based on another coordinate reference system

    :param dataset: The dataset to reproject
    :param reprojection_dataset_path: The path to the reprojection data
    :param crs_variable_name: The name of the variable containing crs information
    :param reprojection_crs_variable_name: The name of the variable containing crs information within the reprojection dataset
    :param projection_string_attribute: The name of the attribute containing the projection string on the CRS variable
    :param x_coordinate_name: The name of the variable containing the x coordinate values
    :param y_coordinate_name: The name of the variable containing the y coordinate values
    :param reprojection_string_attribute: The name of the attribute in the reprojection dataset that contains the coordinate reference string
    :param reprojection_x_coordinate_name: The name of the variable containing the x coordinate values in the reprojection dataset
    :param reprojection_y_coordinate_name: The name of the variable containing the y coordinate values in the reprojection dataset
    :param resampling_strategy: How to determine what values to put into potentially new grid cells
    :returns: The reprojected dataset
    """
    input_projection: Projection = Projection.read_dataset(
        projection_dataset=dataset,
        x_variable_name=x_coordinate_name,
        y_variable_name=y_coordinate_name,
        crs_variable_name=crs_variable_name,
        projection_attribute_name=projection_string_attribute
    )

    target_projection: Projection = ProjectionStore.get(
        path=reprojection_dataset_path,
        x_variable_name=reprojection_x_coordinate_name,
        y_variable_name=reprojection_y_coordinate_name,
        crs_variable_name=reprojection_crs_variable_name,
        projection_attribute_name=reprojection_string_attribute
    )

    # If the values are the same, just update the metadata and head out
    #   The input CRS may reference a projection of 'Lambert_Conformal_Conic" and "Lambert_Conformal_Conic_2SP"
    #   while the target is "Sphere_Lambert_Conformal_Conic" and "Lambert_Conformal_Conic" and have the exact same
    #   coordinates. In this case, we just want to make sure the basic metadata is updated and we're done - no shape
    #   or value changes are needed
    if input_projection.matches(target_projection):
        # Copy all the values in order to divorce it from the source
        dataset = dataset.copy(deep=True)

        # Update the metadata on the coordinates - the values may be the same but the metadata may not
        dataset[input_projection.x_values.name].attrs.update(target_projection.x_values.attrs)
        dataset[input_projection.y_values.name].attrs.update(target_projection.y_values.attrs)
        dataset[input_projection.crs_variable_name].attrs.update(target_projection.crs_attributes)

        # Ensure that all coordinates have any sort of updated spatial references
        for coordinate in dataset.coords.values():
            for attribute_name, attribute_value in coordinate.attrs.items():
                if attribute_name in ('esri_pe_string', 'spatial_ref'):
                    new_spatial_ref = target_projection.crs_attributes.get(
                        'esri_pe_string',
                        target_projection.crs_attributes.get("spatial_ref")
                    )
                    coordinate.attrs[attribute_name] = new_spatial_ref
                elif attribute_name == 'grid_mapping':
                    coordinate.attrs[attribute_name] = target_projection.crs_variable_name

        # Ensure that all data variables have any sort of updated spatial references and that all spatial variables
        # reference the CRS
        for variable in dataset.data_vars.values():
            for attribute_name, attribute_value in variable.attrs.items():
                if attribute_name in ('esri_pe_string', 'spatial_ref'):
                    new_spatial_ref = target_projection.crs_attributes.get(
                        'esri_pe_string',
                        target_projection.crs_attributes.get("spatial_ref")
                    )
                    variable.attrs[attribute_name] = new_spatial_ref
            if input_projection.x_values.name in variable.dims and input_projection.y_values.name in variable.dims:
                variable.attrs['grid_mapping'] = target_projection.crs_variable_name

        # Ensure the all global attributes referencing a spatial reference reference the correct one
        for attribute_name, attribute_value in dataset.attrs.items():
            if attribute_name.lower() == 'proj4':
                dataset.attrs[attribute_name] = target_projection.crs.to_proj4()
            elif attribute_name.lower() == "esri_pe_string":
                dataset.attrs[attribute_name] = target_projection.crs_attributes.get(
                        'esri_pe_string',
                        target_projection.crs_attributes.get("spatial_ref")
                    )
            elif attribute_name.lower() == "spatial_ref":
                dataset.attrs[attribute_name] = target_projection.crs_attributes.get(
                        'spatial_ref',
                        target_projection.crs_attributes.get("esri_pe_string")
                    )

        return dataset

    new_coordinates: list[xarray.DataArray] = [
        form_and_regrid_band(
            source_variable=variable,
            input_projection=input_projection,
            target_projection=target_projection,
            resampling_strategy=resampling_strategy
        )
        for variable_name, variable in dataset.coords.items()
        if len(variable.dims) == 3
    ]
    """The new variables that will make up the coordinates in the reprojected dataset"""

    # Update all references to spatial reference systems to list the updated
    for coordinate in new_coordinates:
        for attribute_name, attribute_value in coordinate.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = target_projection.crs_attributes.get(
                    'esri_pe_string',
                    target_projection.crs_attributes.get("spatial_ref")
                )
                coordinate.attrs[attribute_name] = new_spatial_ref

    new_variables: list[xarray.DataArray] = [
        form_and_regrid_band(
            source_variable=variable,
            input_projection=input_projection,
            target_projection=target_projection,
            resampling_strategy=resampling_strategy
        )
        for variable_name, variable in dataset.data_vars.items()
        if len(variable.dims) == 3
    ]
    """The new variables that will contain the actual data in the reprojected dataset"""

    # Update all contained references to spatial reference systems and ensure that there is a grid_mapping attribute
    # on anything referencing both the x and y coordinates
    for variable in new_variables:
        for attribute_name, attribute_value in variable.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                variable.attrs[attribute_name] = target_projection.crs_string
            elif attribute_name.lower() == 'proj4':
                variable.attrs[attribute_name] = target_projection.proj4
        if input_projection.x_values.name in variable.dims and input_projection.y_values.name in variable.dims:
            variable.attrs['grid_mapping'] = target_projection.crs_variable_name

    # Add a new variable defining the updated coordinate reference system
    new_variables.append(xarray.DataArray(
        name=target_projection.crs_variable_name,
        data=b"",
        dims=tuple(),
        attrs=target_projection.crs_attributes
    ))

    new_attributes: dict[str, typing.Any] = dataset.attrs.copy()

    # Ensure that any global reference to a spatial reference system references the correct values
    for attribute_name, attribute_value in new_attributes.items():
        if attribute_name.lower() == 'proj4':
            new_attributes[attribute_name] = target_projection.proj4
        elif attribute_name.lower() in ("esri_pe_string", "spatial_ref"):
            new_attributes[attribute_name] = target_projection.crs_string

    # Construct the new dataset out of completely disconnected pieces
    new_dataset: xarray.Dataset = xarray.Dataset(
        data_vars={
            variable.name: variable
            for variable in new_variables
        },
        coords={
            coordinate.name: coordinate
            for coordinate in new_coordinates
        },
        attrs=new_attributes,
    )

    # Ensure that transformations and assignments did not wipe out all encoding settings for coordinates
    for coordinate_variable in new_dataset.coords.values():
        original_variable: xarray.DataArray = dataset.coords.get(coordinate_variable.name)

        if original_variable is None:
            LOGGER.warning(f"There is a new variable in a reprojected dataset: {coordinate_variable.name}")
            continue

        coordinate_variable.encoding.update(original_variable.encoding)

    # Ensure that transformations and assignments did not wipe out all encoding settings for data variables
    for data_variable in new_dataset.data_vars.values():
        original_variable: xarray.DataArray = dataset.data_vars.get(data_variable.name)

        if original_variable is None:
            LOGGER.warning(f"There is a new variable in a reprojected dataset: {data_variable.name}")
            continue

        data_variable.encoding.update(original_variable.encoding)

    return new_dataset


def reproject_data(
    dataset: "xarray.Dataset",
    reprojection_dataset_path: pathlib.Path,
    crs_variable_name: str = None,
    reprojection_crs_variable_name: str = None,
    projection_string_attribute: str = None,
    x_coordinate_name: str = None,
    y_coordinate_name: str = None,
    reprojection_string_attribute: str = None,
    reprojection_x_coordinate_name: str = None,
    reprojection_y_coordinate_name: str = None,
    resampling_strategy: "rasterio.warp.Resampling" = None,
) -> "xarray.Dataset":
    """
    Reprojects a 2D dataset based on another coordinate reference system

    :param dataset: The dataset to reproject
    :param reprojection_dataset_path: The path to the reprojection data
    :param crs_variable_name: The name of the variable containing crs information
    :param reprojection_crs_variable_name: The name of the variable containing crs information within the reprojection dataset
    :param projection_string_attribute: The name of the attribute containing the projection string on the CRS variable
    :param x_coordinate_name: The name of the variable containing the x coordinate values
    :param y_coordinate_name: The name of the variable containing the y coordinate values
    :param reprojection_string_attribute: The name of the attribute in the reprojection dataset that contains the coordinate reference string
    :param reprojection_x_coordinate_name: The name of the variable containing the x coordinate values in the reprojection dataset
    :param reprojection_y_coordinate_name: The name of the variable containing the y coordinate values in the reprojection dataset
    :param resampling_strategy: How to determine what values to put into potentially new grid cells
    :returns: The reprojected dataset
    """
    input_projection: Projection = Projection.read_dataset(
        projection_dataset=dataset,
        x_variable_name=x_coordinate_name,
        y_variable_name=y_coordinate_name,
        crs_variable_name=crs_variable_name,
        projection_attribute_name=projection_string_attribute
    )

    target_projection: Projection = ProjectionStore.get(
        path=reprojection_dataset_path,
        x_variable_name=reprojection_x_coordinate_name,
        y_variable_name=reprojection_y_coordinate_name,
        crs_variable_name=reprojection_crs_variable_name,
        projection_attribute_name=reprojection_string_attribute
    )

    # If the values are the same, just update the metadata and head out
    #   The input CRS may reference a projection of 'Lambert_Conformal_Conic" and "Lambert_Conformal_Conic_2SP"
    #   while the target is "Sphere_Lambert_Conformal_Conic" and "Lambert_Conformal_Conic" and have the exact same
    #   coordinates. In this case, we just want to make sure the basic metadata is updated and we're done - no shape
    #   or value changes are needed
    if input_projection.matches(target_projection):
        # Copy all the values in order to divorce it from the source
        dataset = dataset.copy(deep=True)

        # Update the metadata on the coordinates - the values may be the same but the metadata may not
        dataset[input_projection.x_values.name].attrs.update(target_projection.x_values.attrs)
        dataset[input_projection.y_values.name].attrs.update(target_projection.y_values.attrs)
        dataset[input_projection.crs_variable_name].attrs.update(target_projection.crs_attributes)

        # Ensure that all coordinates have any sort of updated spatial references
        for coordinate in dataset.coords.values():
            for attribute_name, attribute_value in coordinate.attrs.items():
                if attribute_name in ('esri_pe_string', 'spatial_ref'):
                    new_spatial_ref = target_projection.crs_attributes.get(
                        'esri_pe_string',
                        target_projection.crs_attributes.get("spatial_ref")
                    )
                    coordinate.attrs[attribute_name] = new_spatial_ref
                elif attribute_name == 'grid_mapping':
                    coordinate.attrs[attribute_name] = target_projection.crs_variable_name

        # Ensure that all data variables have any sort of updated spatial references and that all spatial variables
        # reference the CRS
        for variable in dataset.data_vars.values():
            for attribute_name, attribute_value in variable.attrs.items():
                if attribute_name in ('esri_pe_string', 'spatial_ref'):
                    new_spatial_ref = target_projection.crs_attributes.get(
                        'esri_pe_string',
                        target_projection.crs_attributes.get("spatial_ref")
                    )
                    variable.attrs[attribute_name] = new_spatial_ref
            if input_projection.x_values.name in variable.dims and input_projection.y_values.name in variable.dims:
                variable.attrs['grid_mapping'] = target_projection.crs_variable_name

        # Ensure the all global attributes referencing a spatial reference reference the correct one
        for attribute_name, attribute_value in dataset.attrs.items():
            if attribute_name.lower() == 'proj4':
                dataset.attrs[attribute_name] = target_projection.crs.to_proj4()
            elif attribute_name.lower() == "esri_pe_string":
                dataset.attrs[attribute_name] = target_projection.crs_attributes.get(
                        'esri_pe_string',
                        target_projection.crs_attributes.get("spatial_ref")
                    )
            elif attribute_name.lower() == "spatial_ref":
                dataset.attrs[attribute_name] = target_projection.crs_attributes.get(
                        'spatial_ref',
                        target_projection.crs_attributes.get("esri_pe_string")
                    )

        return dataset

    new_coordinates: list[xarray.DataArray] = [
        reproject_variable(
            source_variable=variable,
            input_projection=input_projection,
            target_projection=target_projection,
            resampling_strategy=resampling_strategy
        )
        for variable_name, variable in dataset.coords.items()
        if variable_name != input_projection.crs_variable_name
    ]
    """The new variables that will make up the coordinates in the reprojected dataset"""

    # Update all references to spatial reference systems to list the updated
    for coordinate in new_coordinates:
        for attribute_name, attribute_value in coordinate.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = target_projection.crs_attributes.get(
                    'esri_pe_string',
                    target_projection.crs_attributes.get("spatial_ref")
                )
                coordinate.attrs[attribute_name] = new_spatial_ref

    new_variables: list[xarray.DataArray] = [
        reproject_variable(
            source_variable=variable,
            input_projection=input_projection,
            target_projection=target_projection,
            resampling_strategy=resampling_strategy
        )
        for variable_name, variable in dataset.data_vars.items()
        if variable_name != input_projection.crs_variable_name
    ]
    """The new variables that will contain the actual data in the reprojected dataset"""

    # Update all contained references to spatial reference systems and ensure that there is a grid_mapping attribute
    # on anything referencing both the x and y coordinates
    for variable in new_variables:
        for attribute_name, attribute_value in variable.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                variable.attrs[attribute_name] = target_projection.crs_string
            elif attribute_name.lower() == 'proj4':
                variable.attrs[attribute_name] = target_projection.proj4
        if input_projection.x_values.name in variable.dims and input_projection.y_values.name in variable.dims:
            variable.attrs['grid_mapping'] = target_projection.crs_variable_name

    # Add a new variable defining the updated coordinate reference system
    new_variables.append(xarray.DataArray(
        name=target_projection.crs_variable_name,
        data=b"",
        dims=tuple(),
        attrs=target_projection.crs_attributes
    ))

    new_attributes: dict[str, typing.Any] = dataset.attrs.copy()

    # Ensure that any global reference to a spatial reference system references the correct values
    for attribute_name, attribute_value in new_attributes.items():
        if attribute_name.lower() == 'proj4':
            new_attributes[attribute_name] = target_projection.proj4
        elif attribute_name.lower() in ("esri_pe_string", "spatial_ref"):
            new_attributes[attribute_name] = target_projection.crs_string

    # Construct the new dataset out of completely disconnected pieces
    new_dataset: xarray.Dataset = xarray.Dataset(
        data_vars={
            variable.name: variable
            for variable in new_variables
        },
        coords={
            coordinate.name: coordinate
            for coordinate in new_coordinates
        },
        attrs=new_attributes,
    )

    # Ensure that transformations and assignments did not wipe out all encoding settings for coordinates
    for coordinate_variable in new_dataset.coords.values():
        original_variable: xarray.DataArray = dataset.coords.get(coordinate_variable.name)

        if original_variable is None:
            LOGGER.warning(f"There is a new variable in a reprojected dataset: {coordinate_variable.name}")
            continue

        coordinate_variable.encoding.update(original_variable.encoding)

    # Ensure that transformations and assignments did not wipe out all encoding settings for data variables
    for data_variable in new_dataset.data_vars.values():
        original_variable: xarray.DataArray = dataset.data_vars.get(data_variable.name)

        if original_variable is None:
            LOGGER.warning(f"There is a new variable in a reprojected dataset: {data_variable.name}")
            continue

        data_variable.encoding.update(original_variable.encoding)

    return new_dataset
