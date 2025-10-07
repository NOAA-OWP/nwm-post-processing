"""
Subsetting logic specifically for vector data input
"""
import typing
import collections.abc as generic
import logging
import pathlib
import dataclasses
import threading
import os

import numpy
import xarray

from post_processing.transform.subsetting.cache import MASK_PROVIDER
from post_processing.transform.subsetting.cache import describe_variable
from post_processing.utilities.common import timed_function
from post_processing.configuration import settings
from post_processing.schema.base import BaseModel
from post_processing.schema.base import member

MODULE_PATH: pathlib.Path = pathlib.Path(__file__)

LOGGER: logging.Logger = logging.getLogger(
    f"{MODULE_PATH.parent.parent.parent.name}.{MODULE_PATH.parent.parent.name}.{MODULE_PATH.parent.name}.{MODULE_PATH.stem}"
)


@dataclasses.dataclass
class SubsettingContext(BaseModel):
    """
    A data transfer object used to pass coupled function inputs
    """
    mask_path: pathlib.Path
    """The path to the mask to use"""
    input_path: pathlib.Path
    """The path to the input data"""
    input_coordinate: str
    """The variable/coordinate to use from the input to determine what locations to keep"""
    work_directory: pathlib.Path
    """Where to save work"""
    output_path: pathlib.Path
    """Where to write the result"""
    identifiers: generic.Mapping[str, typing.Any]
    """A mapping between names and their values that may be used to build up names"""

    drop: bool = dataclasses.field(default=True)
    """Whether to drop values that don't match or just leave them blank"""
    mask_coordinate: typing.Optional[str] = dataclasses.field(default=None)
    """The variable/coordinate within the mask to match on the input coordinate"""

    __input_dimensions: generic.Mapping[str, int] = member(default=None)
    """A mapping between the names of dimensions in the input to the number of values"""
    __input_variables: generic.Mapping[str, str] = member(default=None)
    """The names of variables in the inputs mapped to human-friendly representations"""
    mask_dimensions: generic.Mapping[str, int] = member(default=None)
    """A mapping between the names of dimensions in the mask to the number of values"""
    mask_variables: generic.Mapping[str, str] = member(default=None)
    """The names of variables in the mask mapped to human-friendly representations"""
    mask_ids: numpy.ndarray = member(default=None)
    """IDs from within the mask coordinate that may be matched on"""
    __input_ids: numpy.ndarray = member(default=None)
    """IDs from within the input that may be matched on"""
    __input_data: xarray.Dataset = member(default=None)
    """The full input data"""
    __lock: threading.RLock = member(default_factory=threading.RLock)
    """A lock used to maintain some semblance of thread safety"""

    def _validate(self):
        if self.mask_coordinate is None:
            self.mask_coordinate = self.input_coordinate

    def __load_members__(self):
        super().__load_members__()
        self.mask_ids = MASK_PROVIDER.get_mask(path=self.mask_path, variable=self.mask_coordinate)
        self.mask_dimensions = MASK_PROVIDER.get_dimensions(path=self.mask_path, variable=self.mask_coordinate)
        self.mask_variables = MASK_PROVIDER.get_variables(self.mask_path, variable=self.mask_coordinate)

    @property
    def input_coordinate_description(self) -> str:
        return self.input_variables[self.input_coordinate]

    @property
    def input_description(self) -> str:
        return f"{self.input_path.name}::{self.input_coordinate_description}"

    @property
    def mask_coordinate_description(self) -> str:
        return self.mask_variables[self.mask_coordinate]

    @property
    def mask_description(self) -> str:
        return f"{self.mask_path.name}::{self.mask_coordinate_description}"

    @property
    def input_ids(self) -> numpy.ndarray:
        with self.__lock:
            if self.__input_ids is None:
                self.__open_input()
            return self.__input_ids

    @property
    def input_dimensions(self) -> generic.Mapping[str, int]:
        with self.__lock:
            if self.__input_dimensions is None:
                self.__open_input()
            return self.__input_dimensions

    @property
    def input_variables(self) -> generic.Mapping[str, str]:
        with self.__lock:
            if self.__input_variables is None:
                self.__open_input()
            return self.__input_variables

    def __set_input_ids(self, input_data: xarray.Dataset):
        import numpy
        if self.input_coordinate in input_data.sizes and self.input_coordinate not in input_data.variables:
            raise ValueError(
                f"Cannot subset '{self.input_path}' by '{self.input_coordinate}' - it is a dimension and not a variable."
            )

        if self.input_coordinate not in self.__input_data:
            self.__input_data.close()
            del self.__input_data
            raise KeyError(
                f"Cannot use the '{self.input_coordinate}' variable for location ids within '{self.input_path}' - "
                f"there is no '{self.input_coordinate}' variable"
            )
        self.__input_ids = input_data[self.input_coordinate].to_numpy()

        if settings.this_is_verbose:
            LOGGER.debug("Finding missing ids")

        # NOTE: This will only work on 1D arrays - not grids
        missing_mask_ids: numpy.typing.NDArray[numpy.integer] = numpy.setdiff1d(
            self.mask_ids,
            self.input_ids,
            assume_unique=True
        )
        """A collection of ids that are in the mask but not the input"""

        # Log a warning and reduce the size of the mask if the mask has ids not in the input
        if len(missing_mask_ids) > 0:
            if missing_mask_ids.size > 20:
                missing_mask_ids = missing_mask_ids[:20]
                continue_text = f"{os.linesep}[...]{os.linesep}"
            else:
                continue_text = ""

            missing_id_line_joiner: str = f"{os.linesep}[{self.mask_coordinate} missing from '{self.input_path.name}']    "
            LOGGER.warning(
                f"There are {len(missing_mask_ids)} missing '{self.input_coordinate}' values within {self.input_path}::{self.input_coordinate} "
                f"from the mask at {self.mask_path}. An evaluation of the mask might be required as requested data will not "
                f"be in the output. Missing IDs:"
                f"{missing_id_line_joiner}{missing_id_line_joiner.join(map(str, missing_mask_ids))}{continue_text}{os.linesep}"
                f"Samples:{os.linesep}"
                f"{self.mask_path.name}: {self.mask_ids[:5]}{os.linesep}"
                f"{self.input_path.name}: {self.input_ids[:5]}{os.linesep}"
                f"Are you using the right variables and/or dimensions?{os.linesep}"
                f"Mask Variables: {self.mask_dimensions}, {', '.join(self.mask_variables)}{os.linesep}"
                f"Input Variables: {self.input_dimensions}, {list(self.input_variables)}{os.linesep}"
            )
            self.mask_ids = self.mask_ids[numpy.isin(self.mask_ids, self.input_ids)]
        elif settings.this_is_very_verbose:
            LOGGER.debug("All mask IDs are available")

    def __open_input(self) -> xarray.Dataset:
        with self.__lock:
            if self.__input_data is not None:
                return self.__input_data

            self.__input_data = xarray.open_dataset(
                self.input_path,
                engine=settings.default_netcdf_engine,
                chunks="auto" if settings.lazy_load_netcdf else None
            )
            self.__input_dimensions = {str(dimension): count for dimension, count in self.__input_data.sizes.items()}
            self.__input_variables = {
                str(variable_name): describe_variable(name=str(variable_name), data=variable_data)
                for variable_name, variable_data in self.__input_data.variables.items()
            }
            self.__set_input_ids(self.__input_data)

            return self.__input_data

    def __enter__(self):
        self.__lock.acquire()
        self.__open_input()
        return self.__input_data

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__lock.release()

    def close(self):
        with self.__lock:
            if self.__input_data is not None:
                self.__input_data.close()

    def __del__(self):
        with self.__lock:
            self.close()
            del self.__input_data




@timed_function()
def _subset_by_label(
    context: SubsettingContext,
) -> typing.Optional[xarray.Dataset]:
    with context as input_data:
        dimensions_to_rename: dict[str, str] = {}

        try:
            if context.input_coordinate not in input_data.indexes and len(input_data.coords[context.input_coordinate].dims) == 1:
                target_dimension: str = str(input_data.coords[context.input_coordinate].dims[0])
                input_data = input_data.swap_dims({target_dimension: context.input_coordinate})
                dimensions_to_rename[context.input_coordinate] = target_dimension
            elif context.input_coordinate not in input_data.indexes:
                return None
        except Exception as error:
            pass

        try:
            if settings.this_is_verbose:
                LOGGER.debug(
                    f"Extracting data from '{context.input_path}' "
                    f"that matches the allowable ids from '{context.mask_path}'"
                )

            new_coordinates: dict[str, xarray.DataArray] = {
                str(name): xarray.DataArray(
                    data=select_via_numpy(
                        variable,
                        {
                            context.input_coordinate: context.mask_ids
                        }
                    ),
                    attrs=variable.attrs.copy(),
                    name=name,
                    dims=tuple(list(variable.dims))
                )
                for name, variable in input_data.coords.items()
            }

            data_variables: dict[str, xarray.DataArray] = {}
            for name, variable in input_data.data_vars.items():
                data = select_via_numpy(
                    input_data=variable,
                    selectors={
                        context.input_coordinate: context.mask_ids
                    }
                )
                dimensions: tuple[typing.Hashable, ...] = tuple(list(variable.dims))
                coordinates: list[xarray.DataArray] = [
                    new_coordinates[str(dimension)] for dimension in dimensions
                ]

                for coordinate_name in variable.coords.keys():
                    if len([coordinate for coordinate in coordinates if coordinate.name == coordinate_name]) == 0:
                        coordinates.append(new_coordinates[str(coordinate_name)])

                attributes: dict[str, typing.Any] = variable.attrs.copy()

                try:
                    data_variables[name] = xarray.DataArray(
                        name=name,
                        data=data,
                        dims=dimensions,
                        coords=coordinates,
                        attrs=attributes
                    )
                except Exception as error:
                    LOGGER.error(
                        f"Could not copy a subset of the '{name}' variable from {context.input_path}: {error}. "
                        f"Dimensions=[{', '.join(map(lambda dim: str(dim) + '=' + str(new_coordinates[dim].shape), dimensions))}]. "
                        f"Data Shape: {data.shape}"
                    )
                    raise
            subset_data: xarray.Dataset = xarray.Dataset(
                data_vars=data_variables,
                coords=new_coordinates,
                attrs=input_data.attrs.copy(),
            )
            for variable_name in subset_data.data_vars.keys():
                subset_data[variable_name].encoding.update(input_data[variable_name].encoding)

            subset_data.encoding.update(input_data.encoding)
        except Exception as error:
            import numpy
            if "not all values found in index" in str(error):
                missing_input_ids: numpy.typing.NDArray[numpy.integer] = context.mask_ids[
                    ~numpy.isin(context.mask_ids, context.input_ids)
                ]
                if len(missing_input_ids) > 20:
                    missing_input_ids = missing_input_ids[:20]
                    continue_text = f"{os.linesep}[...]{os.linesep}"
                else:
                    continue_text = ""

                LOGGER.error(
                    f"Cannot subset the input data by label - the following IDs are missing in the input:{os.linesep}"
                    f"    - {(os.linesep + '    - ').join(list(map(str, missing_input_ids)))}{continue_text}{os.linesep}"
                    f"Samples:{os.linesep}"
                    f"{context.mask_path.name}: {context.mask_ids[:5]}{os.linesep}"
                    f"{context.input_path.name}: {context.input_ids[:5]}{os.linesep}"
                    f"Are you using the right variables and/or dimensions?{os.linesep}"
                    f"    Mask Variables: {context.mask_dimensions}, {', '.join(context.mask_variables.values())}{os.linesep}"
                    f"    Input Variables: {context.input_dimensions}, {list(context.input_variables.values())}{os.linesep}"
                    f"The mask must be a subset of the inputs"
                )
                return None
            raise

        if dimensions_to_rename:
            LOGGER.debug(
                f"Dimensions that were relabeled to aid in selection now need to be switched back: "
                f"{dimensions_to_rename}"
            )
            subset_data = subset_data.rename_dims(dims_dict=dimensions_to_rename)

        if len(subset_data[context.input_coordinate].values) == 0:
            raise Exception(
                f"The mask at '{context.mask_path}' is invalid for the data at '{context.input_path}' - "
                f"none of the IDs within '{context.mask_path.name}::{context.mask_coordinate}' are available within {context.input_path.name}::{context.input_coordinate}. {os.linesep}"
                f"Samples:{os.linesep}"
                f"{context.mask_path.name}: {context.mask_ids[:5]}{os.linesep}"
                f"{context.input_path.name}: {context.input_ids[:5]}{os.linesep}"
                f"Are you using the right variables and/or dimensions?{os.linesep}"
                f"Mask Variables: {context.mask_dimensions}, {', '.join(context.mask_variables)}{os.linesep}"
                f"Input Variables: {input_data.sizes}, {list(input_data.variables.keys())}{os.linesep}"
            )

        return subset_data

@timed_function()
def subset_vector_file_into_file_by_value(
    input_file: pathlib.Path,
    mask: pathlib.Path,
    coordinate: str,
    work_directory: pathlib.Path,
    mask_coordinate: str = None,
    output_filename: str = None,
    identifiers: generic.Mapping[str, typing.Any] = None,
    output_pattern: str = None,
    drop: bool = True,
) -> pathlib.Path:
    """
    Subset a one-dimensional netcdf file on disk by a mask that is also on disk

    :param input_file: The path to the file to subset
    :param mask: the path to the file containing what values to include in the coordinate
    :param coordinate: The name of the coordinate variable within the input file that will be masked
    :param work_directory: The directory where data may be written
    :param mask_coordinate: The name of the coordinate variable in the mask containing the coordinates to keep
    :param output_filename: What to name the extracted data. The name will be generated as a mix between the mask and input file if not provided
    :param identifiers: Dictionary of identifiers to use when generating a name
    :param output_pattern: The format string to use when generating a name
    :param drop: Whether to drop data that was filtered out
    :returns: The path to the subset data
    """
    if mask_coordinate is None:
        mask_coordinate = coordinate

    if identifiers is None:
        identifiers = {}

    if output_filename is None:
        if output_pattern and identifiers:
            output_filename = output_pattern.format(**identifiers)
        else:
            output_filename = f"{identifiers.get('input_name', input_file.stem)}.{identifiers.get('mask_name', mask.stem)}.nc"

    output_path: pathlib.Path = work_directory / output_filename

    context: SubsettingContext = SubsettingContext(
        input_path=input_file,
        mask_path=mask,
        input_coordinate=coordinate,
        mask_coordinate=mask_coordinate,
        work_directory=work_directory,
        output_path=output_path,
        identifiers=identifiers,
        drop=drop
    )

    from post_processing.utilities import netcdf

    if settings.this_is_very_verbose:
        LOGGER.debug(
            f"Formatting the path for the masked version of '{input_file}'{os.linesep}"
            f"Available Identifiers:{os.linesep}"
            f"    - {(os.linesep + '    - ').join([str(key) + ': ' + str(value) for key, value in identifiers.items()])}"
            f"{os.linesep}"
        )

    if settings.this_is_verbose:
        LOGGER.debug(f"Loading the '{input_file}' and '{mask}'")

    import tempfile
    import shutil

    with tempfile.TemporaryDirectory(dir=context.work_directory) as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name

        with context as input_data:
            dimensions_to_rename: dict[str, str] = {}

            if coordinate not in input_data.indexes and len(input_data.coords[coordinate].dims) == 1:
                target_dimension: str = str(input_data.coords[coordinate].dims[0])
                input_data = input_data.swap_dims({target_dimension: coordinate})
                dimensions_to_rename[coordinate] = target_dimension

            subset_data: xarray.Dataset = _subset_by_label(context=context)

            if subset_data is None:
                LOGGER.warning(
                    f"Selecting '{coordinate}' data based on a 'where' query - this might result in a significant slowdown."
                )
                subset_data: xarray.Dataset = input_data.where(
                    input_data[coordinate].compute().isin(context.mask_ids),
                    drop=True
                )
                LOGGER.debug(f"Selected valid locations based on 'where' statement'")

            if len(subset_data[coordinate].values) == 0:
                raise Exception(
                    f"The mask at '{context.mask_path}' is invalid for the data at '{context.input_path}' - "
                    f"none of the IDs within '{context.mask_description}' are available within {context.input_description}. {os.linesep}"
                    f"Samples:{os.linesep}"
                    f"    {mask.name}: {context.mask_ids[:5]}{os.linesep}"
                    f"    {input_file.name}: {context.input_ids[:5]}{os.linesep}"
                    f"Are you using the right variables and/or dimensions?{os.linesep}"
                    f"Mask Variables: {context.mask_dimensions}, {', '.join(context.mask_variables.values())}{os.linesep}"
                    f"Input Variables: {input_data.sizes}, {', '.join(context.input_variables.values())}{os.linesep}"
                )

            if settings.this_is_verbose:
                LOGGER.debug(f"Saving extracted data that matches '{context.mask_path}' to '{output_path}'")

            if 'stage' in (context.identifiers or {}):
                subset_data.attrs['process_step'] = context.identifiers.get('stage')

            for variable_name, variable in subset_data.variables.items():
                if "chunksizes" in variable.encoding:
                    subset_data[variable_name].encoding["chunksizes"] = tuple(variable.sizes.values())

                if "preferred_chunks" in variable.encoding:
                    subset_data[variable_name].encoding["preferred_chunks"] = dict(variable.sizes)

                if "original_shape" in variable.encoding:
                    subset_data[variable_name].encoding["original_shape"] = tuple(variable.sizes.values())

            netcdf.write(dataset=subset_data, target=context.output_path)

        if settings.this_is_verbose:
            LOGGER.debug(f"Masked data saved to '{context.output_path}'")

    return context.output_path


def select_via_numpy(
    input_data: xarray.DataArray,
    selectors: generic.Mapping[str, generic.Sequence | slice]
) -> numpy.ndarray:
    raw_data: numpy.ndarray = input_data.data
    indices: list[generic.Sequence | slice] = []
    for dimension_name in input_data.sizes.keys():
        raw_coordinate_values: numpy.ndarray = input_data.coords[dimension_name].values
        selector: generic.Sequence | slice = selectors.get(str(dimension_name), slice(None))

        if isinstance(selector, slice):
            start = numpy.searchsorted(raw_coordinate_values, selector.start) if selector.start is not None else None
            stop = numpy.searchsorted(raw_coordinate_values, selector.stop) if selector.stop is not None else None
            indices.append(slice(start, stop))
        else:
            indices.append(numpy.nonzero(numpy.isin(raw_coordinate_values, selector))[0])
    return raw_data[tuple(indices)]
