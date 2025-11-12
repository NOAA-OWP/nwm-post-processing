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
import collections.abc as generic
import dataclasses
import warnings

from threading import RLock

import rasterio.warp
import affine
import xarray
import numpy

from post_processing.utilities import netcdf

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

    def __del__(self):
        self.x_values.close()
        del self.x_values

        self.y_values.close()
        del self.y_values

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
            with netcdf.load(target=path, full_load=True) as projection_dataset:
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
    """
    A cache of projections that may be used to load reusable data while limiting
    """
    def __init__(self):
        self.__store: dict[int, Projection] = {}
        self.__lock: RLock = RLock()

    def clean(self, projection_key: int = None):
        """
        Empty out the cache in order to clear space
        """
        with self.__lock:
            keys: list[int] = list(self.__store.keys())

            for key in keys:
                del self.__store[key]

    def remove(
        self,
        path: pathlib.Path,
        x_variable_name: str = None,
        y_variable_name: str = None,
        crs_variable_name: str = None,
        projection_attribute_name: str = None
    ) -> bool:
        """
        Remove a specific projection from the store
        """
        key: int = hash((
            str(path),
            x_variable_name,
            y_variable_name,
            crs_variable_name,
            projection_attribute_name
        ))
        if key in self.__store:
            with self.__lock:
                if key in self.__store:
                    projection: Projection = self.__store.pop(key)
                    del projection
                    return True
        return False

    def get(
        self,
        path: pathlib.Path,
        x_variable_name: str = None,
        y_variable_name: str = None,
        crs_variable_name: str = None,
        projection_attribute_name: str = None
    ) -> Projection:
        """
        Get the appropriate projection based on a basic description of what wanted

        :param path: Path to the dataset that has the structure of the projection
        :param x_variable_name: Name of the x coordinate variable
        :param y_variable_name: Name of the y coordinate variable
        :param crs_variable_name: Name of the crs coordinate variable
        :param projection_attribute_name: Name of the projection attribute on the crs coordinate variable
        :return: The matching projection
        """
        key: int = hash((
            str(path),
            x_variable_name,
            y_variable_name,
            crs_variable_name,
            projection_attribute_name
        ))
        with self.__lock:
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
"""A central store for common projections"""

def clean():
    import os
    LOGGER.info(f"Removing the ProjectionStore on PID {os.getpid()}")
    ProjectionStore.clean()

def remove_projection(
        path: pathlib.Path,
        x_variable_name: str = None,
        y_variable_name: str = None,
        crs_variable_name: str = None,
        projection_attribute_name: str = None
) -> bool:
    """
    Remove a projection from the global store that is no longer needed
    """
    try:
        removal: bool = ProjectionStore.remove(
            path=path,
            x_variable_name=x_variable_name,
            y_variable_name=y_variable_name,
            crs_variable_name=crs_variable_name,
            projection_attribute_name=projection_attribute_name
        )
    except:
        LOGGER.error(f"Failed to remove a Projection stored at {path}", exc_info=True)
        removal = False

    return removal


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
    centering_adjustment: float = 0.5 if coordinate_is_centered else 0
    """An adjustment that may be needed in order to get the outer edge of the bounding box"""

    x_variable: xarray.DataArray = get_x_coordinate(dataset=dataset, variable_name=x_variable_name)
    y_variable: xarray.DataArray = get_y_coordinate(dataset=dataset, variable_name=y_variable_name)

    pixel_width: float = abs(numpy.mean(numpy.diff(x_variable.data)))
    pixel_height: float = abs(numpy.mean(numpy.diff(y_variable.data)))

    x_offset: float = pixel_width * centering_adjustment
    """The amount of distance on the x-axis that will be needed to get the true edge of a tile"""
    y_offset: float = pixel_height * centering_adjustment
    """The amount of distance on the y-axis that will be needed to get the true edge of a tile"""

    western_edge: float = numpy.min(x_variable.data)
    """The western edge of the total bounding box"""

    # If the coordinates are centered in the tile, step to the left to get the true edge of the bounding box
    western_edge -= x_offset

    eastern_edge: float = numpy.max(x_variable.data)
    """The eastern edge of the total bounding box"""

    # If the coordinates are centered in the tile, step to the right to get the true edge of the bounding box
    eastern_edge += x_offset

    southern_edge: float = numpy.min(y_variable.data)
    """The southern edge of the total bounding box"""

    # If the coordinates are centered in the tile, step down to get the true edge of the bounding box
    southern_edge -= y_offset

    northern_edge: float = numpy.max(y_variable.data)
    """The northern edge of the total bounding box"""

    # If the coordinates are centered in the tile, step up to get the true edge of the bounding box
    northern_edge += y_offset

    column_count: int = x_variable.shape[0]
    row_count: int = y_variable.shape[0]

    transform: affine.Affine = rasterio.transform.from_bounds(
        west=western_edge,
        east=eastern_edge,
        south=southern_edge,
        north=northern_edge,
        width=column_count,
        height=row_count,
    )

    # from_bounds always returns an Affine that is North to South. If the data isn't north to south, flip the affine
    is_north_up: bool = y_variable.data[0] > y_variable.data[-1]

    # If the data isn't actually laid out in a fashion where the first row is northern-most, the projection will be
    # flipped. Flip here or else the data will end up upside down
    if not is_north_up:
        transform = transform * affine.Affine.translation(0, row_count)
        transform = transform * affine.Affine.scale(1, -1)

    if not all(numpy.isfinite(transform)):
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
    dataset: xarray.Dataset,
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


def get_fill_value(variable: xarray.DataArray) -> typing.Any:
    """
    Get an appropriate fill value based on the data within the given variable

    :param variable: The variable to get the fill value for
    :returns: The appropriate fill value for the given variable
    """
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


def reproject_variable(
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

    if len(source_variable.shape) not in (2, 3):
        raise ValueError(
            f"'form_and_regrid_band' can only operate on 2D and 3D variables - dimensions were: "
            f"{source_variable.name}({source_variable.sizes})"
        )

    original_dimensions: tuple[generic.Hashable, ...] = source_variable.dims

    dimension_names: list[generic.Hashable] = list(original_dimensions)
    dimension_names.remove(input_projection.x_values.name)
    dimension_names.remove(input_projection.y_values.name)

    new_dimension_order: tuple[generic.Hashable, generic.Hashable, generic.Hashable] = (
        *dimension_names,
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

    with warnings.catch_warnings():
        warnings.filterwarnings(
            action="ignore",
            category=rasterio.errors.NotGeoreferencedWarning
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
            resampling=resampling_strategy,
            num_threads=warp_worker_threads
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


def reproject_variable_group(
    group: generic.Mapping[generic.Hashable, xarray.DataArray],
    input_projection: Projection,
    output_projection: Projection,
    resampling_strategy: rasterio.warp.Resampling,
) -> dict[str, xarray.DataArray]:
    """
    Step through a group of xarray.DataArray variables (such as xarray.Dataset.coords or xarray.Dataset.data_vars)
    and reproject the variables that match the spatial specifications

    :param group: The group of variables to reproject (such as xarray.Dataset.coords or xarray.Dataset.data_vars)
    :param input_projection: The projection to convert from
    :param output_projection: The projection to convert to
    :param resampling_strategy: How to fill in new positions in the reprojected grid
    :returns: A new set of xarray.DataArray variables
    """
    new_group: list[xarray.DataArray] = [
        reproject_variable(
            source_variable=variable,
            input_projection=input_projection,
            target_projection=output_projection,
            resampling_strategy=resampling_strategy
        )
        for variable_name, variable in group.items()
        if len(variable.dims) in (2, 3)
           and input_projection.x_values.name in variable.dims
           and input_projection.y_values.name in variable.dims
    ]

    # Add the variables that could not be reprojected into the new list of variables
    for name, variable in group.items():
        if name in (input_projection.x_values.name, input_projection.y_values.name):
            continue
        if len([new_variable for new_variable in new_group if new_variable.name == name]) == 0:
            new_group.append(variable.copy())

    # Update all references to spatial reference systems to list the updated
    for new_variable in new_group:
        for attribute_name, attribute_value in new_variable.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = output_projection.crs_attributes.get(
                    'esri_pe_string',
                    output_projection.crs_attributes.get("spatial_ref")
                )
                new_variable.attrs[attribute_name] = new_spatial_ref

    mapped_group: dict[str, xarray.DataArray] = {
        str(new_variable.name): new_variable
        for new_variable in new_group
    }
    return mapped_group


def update_coordinate_references(
    dataset: xarray.Dataset,
    input_projection: Projection,
    output_projection: Projection,
    output_crs_variable_name: str = None
) -> xarray.Dataset:
    """
    Update coordinates and variables to match the possibly new metadata

    :param dataset: The dataset to update
    :param input_projection: The projection to update from
    :param output_projection: The projection to update to
    :param output_crs_variable_name: An optional name for the output crs variable if it needs to differ from the output projection
    :returns: A copy of the dataset with appropriate metadata
    """
    # Copy all the values in order to divorce it from the source
    dataset = dataset.copy(deep=True)

    if output_crs_variable_name is None:
        output_crs_variable_name = output_projection.crs_variable_name

    # Update the metadata on the coordinates - the values may be the same but the metadata may not
    dataset[input_projection.x_values.name].attrs.update(output_projection.x_values.attrs)
    dataset[input_projection.y_values.name].attrs.update(output_projection.y_values.attrs)

    crs_attributes: dict[str, typing.Any] = input_projection.crs_attributes.copy()
    crs_attributes.update(output_projection.crs_attributes)

    if input_projection.crs_variable_name != output_crs_variable_name:
        dataset = dataset.rename({input_projection.crs_variable_name: output_crs_variable_name})

    dataset[output_crs_variable_name].attrs.update(crs_attributes)

    # Ensure that all coordinates have any sort of updated spatial references
    for coordinate in dataset.coords.values():
        for attribute_name, attribute_value in coordinate.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = output_projection.crs_attributes.get(
                    'esri_pe_string',
                    output_projection.crs_attributes.get("spatial_ref")
                )
                coordinate.attrs[attribute_name] = new_spatial_ref
            elif attribute_name == 'grid_mapping':
                coordinate.attrs[attribute_name] = output_crs_variable_name

    # Ensure that all data variables have any sort of updated spatial references and that all spatial variables
    # reference the CRS
    for variable in dataset.data_vars.values():
        for attribute_name, attribute_value in variable.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = output_projection.crs_attributes.get(
                    'esri_pe_string',
                    output_projection.crs_attributes.get("spatial_ref")
                )
                variable.attrs[attribute_name] = new_spatial_ref
        if input_projection.x_values.name in variable.dims and input_projection.y_values.name in variable.dims:
            variable.attrs['grid_mapping'] = output_crs_variable_name

    # Ensure the all global attributes referencing a spatial reference reference the correct one
    for attribute_name, attribute_value in dataset.attrs.items():
        if attribute_name.lower() == 'proj4':
            dataset.attrs[attribute_name] = output_projection.crs.to_proj4()
        elif attribute_name.lower() == "esri_pe_string":
            dataset.attrs[attribute_name] = output_projection.crs_attributes.get(
                    'esri_pe_string',
                    output_projection.crs_attributes.get("spatial_ref")
                )
        elif attribute_name.lower() == "spatial_ref":
            dataset.attrs[attribute_name] = output_projection.crs_attributes.get(
                    'spatial_ref',
                    output_projection.crs_attributes.get("esri_pe_string")
                )

    return dataset


def reproject_gridded_variables(
    dataset: xarray.Dataset,
    input_projection: Projection,
    output_projection: Projection,
    resampling_strategy: rasterio.warp.Resampling,
    output_crs_variable_name: str = None
) -> xarray.Dataset:
    """
    Force gridded variables to comply with a new projection

    :param dataset: The dataset to reproject
    :param input_projection: The projection that the data was originally in
    :param output_projection: The projection that the data should move to
    :param resampling_strategy: How to determine what values should occupy coordinates that weren't in the original data
    :param output_crs_variable_name: The CRS variable name if it needs to differ from the output projection
    :returns: A new dataset containing the old data within a new grid projection
    """
    if not output_crs_variable_name:
        output_crs_variable_name = output_projection.crs_variable_name

    new_coordinates: dict[str, xarray.DataArray] = reproject_variable_group(
        group=dataset.coords,
        input_projection=input_projection,
        output_projection=output_projection,
        resampling_strategy=resampling_strategy
    )
    """The new variables that will make up the coordinates in the reprojected dataset"""

    new_variables: dict[str, xarray.DataArray] = reproject_variable_group(
        group=dataset.data_vars,
        input_projection=input_projection,
        output_projection=output_projection,
        resampling_strategy=resampling_strategy
    )
    """The new variables that will contain the actual data in the reprojected dataset"""

    # Add a new variable defining the updated coordinate reference system
    new_variables[output_crs_variable_name] = xarray.DataArray(
        name=output_crs_variable_name,
        data=b"",
        dims=tuple(),
        attrs=output_projection.crs_attributes
    )

    new_attributes: dict[str, typing.Any] = dataset.attrs.copy()

    # Ensure that any global reference to a spatial reference system references the correct values
    for attribute_name, attribute_value in new_attributes.items():
        if attribute_name.lower() == 'proj4':
            new_attributes[attribute_name] = output_projection.proj4
        elif attribute_name.lower() in ("esri_pe_string", "spatial_ref"):
            new_attributes[attribute_name] = output_projection.crs_string

    # Construct the new dataset out of completely disconnected pieces
    new_dataset: xarray.Dataset = xarray.Dataset(
        data_vars=new_variables,
        coords=new_coordinates,
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
            LOGGER.debug(f"There is a new variable in a reprojected dataset: {data_variable.name}")
            continue

        data_variable.encoding.update(original_variable.encoding)

    return new_dataset



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
    output_crs_variable_name: str = None
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
    :param output_crs_variable_name: The name of the CRS that should be in the output file
    :returns: The reprojected dataset
    """
    if not output_crs_variable_name:
        output_crs_variable_name = reprojection_crs_variable_name

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
        reprojected_data: xarray.Dataset = update_coordinate_references(
            dataset=dataset,
            input_projection=input_projection,
            output_projection=target_projection,
            output_crs_variable_name=output_crs_variable_name
        )
    else:
        reprojected_data: xarray.Dataset = reproject_gridded_variables(
            dataset=dataset,
            input_projection=input_projection,
            output_projection=target_projection,
            resampling_strategy=resampling_strategy,
            output_crs_variable_name=output_crs_variable_name
        )
    del input_projection
    return reprojected_data
