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

from post_processing.configuration import settings

if typing.TYPE_CHECKING:
    import affine
    import xarray
    import pyproj
    import numpy
    import rasterio.warp

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def get_affine_transformation(
    dataset: "xarray.Dataset",
    x_variable_name: str = None,
    y_variable_name: str = None
) -> "affine.Affine":
    """
    Calculates an affine transformation based on data from a dataset

    :param dataset: The dataset containing the coordinate data
    :param x_variable_name: The name of the variable containing the x coordinate values
    :param y_variable_name: The name of the variable containing the y coordinate values
    :returns: An affine transformation that may be used to reproject coordinates
    """
    import xarray
    import affine

    x_variable: xarray.DataArray = get_x_coordinate(dataset=dataset, variable_name=x_variable_name)
    y_variable: xarray.DataArray = get_y_coordinate(dataset=dataset, variable_name=y_variable_name)

    x_difference: float = x_variable.diff(x_variable.dims[0]).mean().item()
    y_difference: float = y_variable.diff(y_variable.dims[0]).mean().item()

    x0: float = x_variable.item(0)
    y0: float = y_variable.item(0)

    if y0 < 0:
        y_difference = -abs(y_difference)
    else:
        y_difference = abs(y_difference)

    translation: affine.Affine = affine.Affine.translation(x0, y0)
    scale: affine.Affine = affine.Affine.scale(x_difference, y_difference)
    transform: affine.Affine = translation * scale
    return transform

def get_y_coordinate(dataset: "xarray.Dataset", variable_name: str = None) -> "xarray.DataArray":
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

def get_x_coordinate(dataset: "xarray.Dataset", variable_name: str = None) -> "xarray.DataArray":
    """
    Get the X variable from a dataset

    :param dataset: The dataset containing the coordinate data
    :param variable_name: The name of the variable containing the x coordinate values
    :returns: A variable containing the X coordinate values
    """
    import xarray

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

def get_crs_variable(dataset: "xarray.Dataset", variable_name: str = None) -> "xarray.DataArray":
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
) -> "pyproj.CRS":
    """
    Build the Coordinate Reference System object used for transformation

    :param dataset: The dataset containing the reference system
    :param crs_variable_name: The name of the variable containing crs information
    :param projection_string_attribute: The name of the attribute containing the projection string on the CRS variable
    :returns: The formal CRS object needed by rasterio to perform the reprojection
    """
    import xarray
    import pyproj

    crs_variable: xarray.DataArray = get_crs_variable(dataset=dataset, variable_name=crs_variable_name)

    if projection_string_attribute is None and 'esri_pe_string' in crs_variable.attrs:
        projection_string_attribute = 'esri_pe_string'
    elif projection_string_attribute is None and 'spatial_ref' in crs_variable.attrs:
        projection_string_attribute = 'spatial_ref'
    elif projection_string_attribute is None:
        raise ValueError("The name of the project string attribute in the CRS variable must be specified and was not")

    projection_string: str = crs_variable.attrs[projection_string_attribute]

    crs: pyproj.CRS = pyproj.CRS.from_string(projection_string)
    return crs


def coordinates_are_the_same(
    dataset: "xarray.Dataset",
    reprojection_dataset: "xarray.Dataset",
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

    if "_FillValue" in variable.attrs:
        return variable.attrs["_FillValue"]
    if "_FillValue" in variable.encoding:
        return variable.encoding['_FillValue']
    if "missing_value" in variable.attrs:
        return variable.attrs["missing_value"]
    if "missing_value" in variable.encoding:
        return variable.encoding['missing_value']

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
    source_variable: "xarray.DataArray",
    source_transform: "affine.Affine",
    source_crs: "pyproj.crs.CRS",
    target_transform: "affine.Affine",
    target_crs: "pyproj.crs.CRS",
    target_x_coordinate: "xarray.DataArray",
    target_y_coordinate: "xarray.DataArray",
    x_variable_name: str = None,
    y_variable_name: str = None,
    resampling_strategy: "rasterio.warp.Resampling" = None
) -> "xarray.DataArray":
    """
    Create a new variable from the source variable but in the target's projection

    :param source_variable: The source variable to reproject
    :param source_transform: The source affine transform
    :param source_crs: The source coordinate reference system
    :param target_transform: The target affine transform
    :param target_crs: The target coordinate reference system
    :param target_x_coordinate: The variable that describes the desired X coordinate
    :param target_y_coordinate: The variable that describes the desired Y coordinate
    :param x_variable_name: The name of the variable containing the x coordinate values in the source
    :param y_variable_name: The name of the variable containing the y coordinate values in the source
    :param resampling_strategy: How to resample the values into the new projections
    :returns: The reprojected variable
    """
    import rasterio.warp
    import xarray
    import numpy

    # If the caller didn't supply an x variable name, try to find it
    if x_variable_name is None:
        if 'lon' in source_variable.coords:
            x_variable_name = 'lon'
        elif 'x' in source_variable.coords:
            x_variable_name = 'x'
        elif len(source_variable.shape) < 2:
            # Couldn't find a reference to the X axis and this isn't a raster variable - no need to change values
            return source_variable
        else:
            raise KeyError(
                f"No X coordinate could be identified for the '{source_variable.name}' variable - please specify one"
            )

    # If the caller didn't supply a y variable name, try to find it
    if y_variable_name is None:
        if 'lat' in source_variable.coords:
            y_variable_name = 'lat'
        elif 'y' in source_variable.coords:
            y_variable_name = 'y'
        elif len(source_variable.shape) < 2:
            # Couldn't find a reference to the Y axis and this isn't a raster variable - no need to change values
            return source_variable
        else:
            raise KeyError(
                f"No Y coordinate could be identified for the '{source_variable.name}' variable - please specify one"
            )

    # If this is just the definition of the X axis, just reassign the values
    if source_variable.name == x_variable_name:
        new_x_variable = xarray.DataArray(
            name=x_variable_name,
            dims=source_variable.dims,
            data=target_x_coordinate.data,
            attrs=target_x_coordinate.attrs,
        )
        new_x_variable.encoding.update(target_x_coordinate.encoding)
        return new_x_variable

    # If this is just the definition of the Y axis, just reassign the values
    if source_variable.name == y_variable_name:
        new_y_variable = xarray.DataArray(
            name=y_variable_name,
            dims=source_variable.dims,
            data=target_y_coordinate.data,
            attrs=target_y_coordinate.attrs,
        )
        new_y_variable.encoding.update(target_y_coordinate.encoding)
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

    if x_variable_name not in source_variable.dims:
        raise KeyError(
            f"Cannot reproject a dataset - the variable for the X-Axis ('{x_variable_name}') is not on the "
            f"{source_variable.name} variable"
        )

    if y_variable_name not in source_variable.dims:
        raise KeyError(
            f"Cannot reproject a dataset - the variable for the Y-Axis ('{y_variable_name}') is not on the "
            f"{source_variable.name} variable"
        )

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

    lat_lon_shape: tuple[int, int] = (target_y_coordinate.size, target_x_coordinate.size)
    """The shape of the output data in spatial terms. Must come in y-axis, x-axis order"""

    should_transpose: bool = source_variable.dims.index(x_variable_name) < source_variable.dims.index(y_variable_name)
    """Whether the reprojected data should be transposed to fit the original structure"""

    # Extract the correct output coordinates and determine what coordinates, in what order, to select data by
    # outside the scope of the spatial realm
    for dimension, count in source_variable.sizes.items():
        if dimension == x_variable_name:
            # Append the target's size rather than the current size since we're changing shape
            output_shape.append(target_x_coordinate.size)
        elif dimension == y_variable_name:
            # Append the target's size rather than the current size since we're changing shape
            output_shape.append(target_y_coordinate.size)
        else:
            # We'll select grids by these indices since they aren't involved in the projection
            non_spatial_selectors.append((str(dimension), count))

            # Add the original count since this dimension won't be changing
            output_shape.append(count)

    # Determine the overall background value for when values are not available
    fill_value: typing.Any = get_fill_value(variable=source_variable)
    """The default value for every cell in the variable"""

    if settings.this_is_verbose:
        LOGGER.debug(f"Setting the fill/no data value of '{source_variable.name}' to '{fill_value}' when reprojecting it")

    # Allocate space for the reprojected data in the shape of the non-spatial coordinates from the source and the
    # spatial coordinates for the target projection
    reprojected_data: numpy.ndarray = numpy.full(
        shape=tuple(output_shape),
        fill_value=fill_value,
        dtype=source_variable.dtype
    )
    """The matrix that will contain all of the values from the source in the new projection"""

    # Form combinations of non-spatial index groupings to select data by
    if non_spatial_selectors:
        from itertools import product
        index_combinations: typing.Iterable[tuple[int, ...]] = product(*[
            range(size) for _, size in non_spatial_selectors
        ])
    else:
        # If the source was a grid and a grid alone, collect an empty tuple - this will run the loop once and not
        # select specific values
        index_combinations: typing.Iterable[tuple[int, ...]] = [tuple()]

    reprojected_slice_buffer: numpy.ndarray = numpy.full(
        shape=lat_lon_shape,
        fill_value=fill_value,
        dtype=source_variable.dtype
    )
    """An array that contains a reprojected slice of data"""

    # Reproject each spatial slice
    for index_combination in index_combinations:  # type: tuple[int, ...]
        # Select all data for the spatial slice by selecting, by index, on the non-spatial dimensions
        selection_arguments: dict[str, int] = dict(zip(
            map(lambda pair: pair[0], non_spatial_selectors),
            index_combination
        ))

        # If our coordinates are (time, x, y), this will select by time and result in an array of (x, y).
        # If our coordinates are (x, y), this will result in an array of (x, y). If our coordinates are
        # (whatever, time, x, y), this will result in an array of (x, y).
        sliced_data: numpy.ndarray = source_variable.isel(**selection_arguments).values

        if len(sliced_data.shape) != 2:
            raise ValueError(
                f"Selecting spatial data for reprojection by '({', '.join(selection_arguments.keys())})' resulted in a "
                f"{len(sliced_data.shape)} dimensional array. Only 2 dimensions are supported."
            )

        # rasterio assumes (y,x). If we earlier determined that our input is (x,y), transpose the input
        if should_transpose:
            sliced_data = sliced_data.T

        updated_data, _ = rasterio.warp.reproject(
            source=sliced_data,
            destination=reprojected_slice_buffer,
            src_transform=source_transform,
            dst_transform=target_transform,
            src_crs=source_crs,
            dst_crs=target_crs,
            src_nodata=fill_value,
            dst_nodata=fill_value,
            resampling=resampling_strategy,
            parallel=True
        )

        if should_transpose:
            updated_data = updated_data.T

        reprojected_data[index_combination] = updated_data.copy()
        reprojected_slice_buffer.fill(fill_value)

    reprojected_variable: xarray.DataArray = xarray.DataArray(
        name=source_variable.name,
        data=reprojected_data,
        dims=source_variable.dims,
        attrs=source_variable.attrs
    )
    return reprojected_variable


def reproject_data(
    dataset: "xarray.Dataset",
    reprojection_dataset: "xarray.Dataset",
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
    :param reprojection_dataset: A dataset with the target projection
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
    import xarray
    import pyproj
    import affine

    original_x_variable: xarray.DataArray = get_x_coordinate(dataset=dataset, variable_name=x_coordinate_name)
    """The variable defining the X-Axis in the original data"""

    original_y_variable: xarray.DataArray = get_y_coordinate(dataset=dataset, variable_name=y_coordinate_name)
    """The variable defining the Y-Axis in the original data"""

    original_crs: xarray.DataArray = get_crs_variable(dataset=dataset, variable_name=crs_variable_name)
    """The variable defining the coordinate reference system in the original data"""

    reprojection_x_variable: xarray.DataArray = get_x_coordinate(
        dataset=reprojection_dataset,
        variable_name=reprojection_x_coordinate_name
    )
    """The desired X-Axis to project into"""

    reprojection_y_variable: xarray.DataArray = get_y_coordinate(
        dataset=reprojection_dataset,
        variable_name=reprojection_y_coordinate_name
    )
    """The desired Y-Axis to project into"""

    reprojection_crs_variable: xarray.DataArray = get_crs_variable(
        dataset=reprojection_dataset,
        variable_name=reprojection_crs_variable_name
    )
    """The desired coordinate reference system to project into"""

    crs_attributes: dict[str, typing.Any] = reprojection_crs_variable.attrs
    """The attributes that help define the desired coordinate reference system"""

    source_transformation: affine.Affine = get_affine_transformation(
        dataset=dataset,
        x_variable_name=x_coordinate_name,
        y_variable_name=y_coordinate_name
    )
    """The structural-to-real-world coordinate mapping for the original dataset"""

    source_crs: pyproj.CRS = get_crs(
        dataset=dataset,
        crs_variable_name=crs_variable_name,
        projection_string_attribute=projection_string_attribute
    )
    """The coordinate reference system that is meant to be changed"""

    reprojection_transformation: affine.Affine = get_affine_transformation(
        dataset=reprojection_dataset,
        x_variable_name=reprojection_x_coordinate_name,
        y_variable_name=reprojection_y_coordinate_name,
    )
    """The structural-to-real-world coordinate mapping for the desired projection"""

    reprojection_crs: pyproj.CRS = get_crs(
        dataset=reprojection_dataset,
        crs_variable_name=reprojection_crs_variable_name,
        projection_string_attribute=reprojection_string_attribute
    )
    """The coordinate reference system to project into"""

    # Determine if the two CRS' are essentially the same. Labels may be different, but all the points and their
    # meanings may be identical
    coordinates_match: bool = coordinates_are_the_same(
        dataset=dataset,
        reprojection_dataset=reprojection_dataset,
        x_coordinate_name=x_coordinate_name,
        y_coordinate_name=y_coordinate_name,
        reprojection_x_coordinate_name=reprojection_x_coordinate_name,
        reprojection_y_coordinate_name=reprojection_y_coordinate_name,
    )

    # If the values are the same, just update the metadata and head out
    #   The input CRS may reference a projection of 'Lambert_Conformal_Conic" and "Lambert_Conformal_Conic_2SP"
    #   while the target is "Sphere_Lambert_Conformal_Conic" and "Lambert_Conformal_Conic" and have the exact same
    #   coordinates. In this case, we just want to make sure the basic metadata is updated and we're done - no shape
    #   or value changes are needed
    if coordinates_match:
        # Copy all the values in order to divorce it from the source
        dataset = dataset.copy(deep=True)

        # Update the metadata on the coordinates - the values may be the same but the metadata may not
        dataset[original_x_variable.name].attrs.update(reprojection_x_variable.attrs)
        dataset[original_y_variable.name].attrs.update(reprojection_y_variable.attrs)
        dataset[original_crs.name].attrs.update(reprojection_crs_variable.attrs)

        # Ensure that all coordinates have any sort of updated spatial references
        for coordinate in dataset.coords.values():
            for attribute_name, attribute_value in coordinate.attrs.items():
                if attribute_name in ('esri_pe_string', 'spatial_ref'):
                    new_spatial_ref = crs_attributes.get('esri_pe_string', crs_attributes.get("spatial_ref"))
                    coordinate.attrs[attribute_name] = new_spatial_ref
                elif attribute_name == 'grid_mapping':
                    coordinate.attrs[attribute_name] = reprojection_crs_variable.name

        # Ensure that all data variables have any sort of updated spatial references and that all spatial variables
        # reference the CRS
        for variable in dataset.data_vars.values():
            for attribute_name, attribute_value in variable.attrs.items():
                if attribute_name in ('esri_pe_string', 'spatial_ref'):
                    new_spatial_ref = crs_attributes.get('esri_pe_string', crs_attributes.get("spatial_ref"))
                    variable.attrs[attribute_name] = new_spatial_ref
            if original_x_variable.name in variable.dims and original_y_variable.name in variable.dims:
                variable.attrs['grid_mapping'] = reprojection_crs_variable.name

        # Ensure the all global attributes referencing a spatial reference reference the correct one
        for attribute_name, attribute_value in dataset.attrs.items():
            if attribute_name.lower() == 'proj4':
                dataset.attrs[attribute_name] = reprojection_crs.to_proj4()
            elif attribute_name.lower() == "esri_pe_string":
                dataset.attrs[attribute_name] = crs_attributes.get("esri_pe_string", crs_attributes.get("spatial_ref"))
            elif attribute_name.lower() == "spatial_ref":
                dataset.attrs[attribute_name] = crs_attributes.get("spatial_ref", crs_attributes.get("esri_pe_string"))

        return dataset

    new_coordinates: list[xarray.DataArray] = [
        reproject_variable(
            source_variable=variable,
            source_transform=source_transformation,
            source_crs=source_crs,
            target_transform=reprojection_transformation,
            target_crs=reprojection_crs,
            target_x_coordinate=reprojection_x_variable,
            target_y_coordinate=reprojection_y_variable,
            x_variable_name=original_x_variable.name,
            y_variable_name=original_y_variable.name,
            resampling_strategy=resampling_strategy
        )
        for variable_name, variable in dataset.coords.items()
        if variable_name != original_crs.name
    ]
    """The new variables that will make up the coordinates in the reprojected dataset"""

    # Update all references to spatial reference systems to list the updated
    for coordinate in new_coordinates:
        for attribute_name, attribute_value in coordinate.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = crs_attributes.get('esri_pe_string', crs_attributes.get("spatial_ref"))
                coordinate.attrs[attribute_name] = new_spatial_ref

    new_variables: list[xarray.DataArray] = [
        reproject_variable(
            source_variable=variable,
            source_transform=source_transformation,
            source_crs=source_crs,
            target_transform=reprojection_transformation,
            target_crs=reprojection_crs,
            target_x_coordinate=reprojection_x_variable,
            target_y_coordinate=reprojection_y_variable,
            x_variable_name=original_x_variable.name,
            y_variable_name=original_y_variable.name,
        )
        for variable_name, variable in dataset.data_vars.items()
        if variable_name != original_crs.name
    ]
    """The new variables that will contain the actual data in the reprojected dataset"""

    # Update all contained references to spatial reference systems and ensure that there is a grid_mapping attribute
    # on anything referencing both the x and y coordinates
    for variable in new_variables:
        for attribute_name, attribute_value in variable.attrs.items():
            if attribute_name in ('esri_pe_string', 'spatial_ref'):
                new_spatial_ref = crs_attributes.get('esri_pe_string', crs_attributes.get("spatial_ref"))
                variable.attrs[attribute_name] = new_spatial_ref
            elif attribute_name.lower() == 'proj4':
                variable.attrs[attribute_name] = reprojection_crs.to_proj4()
        if original_x_variable.name in variable.dims and original_y_variable.name in variable.dims:
            variable.attrs['grid_mapping'] = reprojection_crs_variable.name

    # Add a new variable defining the updated coordinate reference system
    new_variables.append(xarray.DataArray(
        name=reprojection_crs_variable.name,
        data=reprojection_crs_variable.data,
        dims=reprojection_crs_variable.dims,
        attrs=reprojection_crs_variable.attrs,
        coords=reprojection_crs_variable.coords,
    ))

    new_attributes: dict[str, typing.Any] = dataset.attrs.copy()

    # Ensure that any global reference to a spatial reference system references the correct values
    for attribute_name, attribute_value in new_attributes.items():
        if attribute_name.lower() == 'proj4':
            new_attributes[attribute_name] = reprojection_crs.to_proj4()
        elif attribute_name.lower() == "esri_pe_string":
            new_attributes[attribute_name] = crs_attributes.get("esri_pe_string", crs_attributes.get("spatial_ref"))
        elif attribute_name.lower() == "spatial_ref":
            new_attributes[attribute_name] = crs_attributes.get("spatial_ref", crs_attributes.get("esri_pe_string"))

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
