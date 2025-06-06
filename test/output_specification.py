"""
Classes used to define what output netcdf files should look like
"""
import os
import typing
import dataclasses
import logging
import pathlib
import json
import multiprocessing
import multiprocessing.pool

import numpy
import xarray
from pandas.core.dtypes.common import is_integer_dtype

import post_processing.enums
from post_processing import nco
from post_processing.nwm_file import NWMFile

T = typing.TypeVar("T")
"""A generic type"""


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def xarray_dtype_from_str(dtype_str: str) -> numpy.dtype:
    """
    Map a string like 'int', 'float', 'double', 'char' to a NumPy dtype for xarray.

    Args:
        dtype_str (str): String type name.

    Returns:
        numpy.dtype: Corresponding NumPy dtype.

    Raises:
        ValueError: If inumpyut is not recognized.
    """
    mapping = {
        "byte": numpy.int8,
        "ubyte": numpy.uint8,
        "short": numpy.int16,
        "ushort": numpy.uint16,
        "int": numpy.int32,
        "uint": numpy.uint32,
        "int64": numpy.int64,
        "uint64": numpy.uint64,
        "float": numpy.float32,
        "double": numpy.float64,
        "char": numpy.dtype("S"),
        "string": numpy.dtype("S"),  # variable-length strings in NetCDF3
        "str": numpy.dtype("S"),  # variable-length strings in NetCDF3
        "bool": numpy.bool_,
        "datetime64": numpy.datetime64,
        "datetime64[ns]": numpy.datetime64,
        "datetime64[ms]": numpy.datetime64
    }

    key = dtype_str.lower().strip()
    if key in mapping:
        return numpy.dtype(mapping[key])
    raise ValueError(f"Unsupported data type string: '{dtype_str}'")


def deserialize(cls: typing.Type[T], data: typing.Union[str, pathlib.Path, typing.Dict]) -> T:
    """
    Convert a path or dictionary to a dataclass

    :param cls: The type of class to deserialize into
    :param data: The data or path to deserialize
    :return: The deserialized class
    """
    if isinstance(data, str):
        data = pathlib.Path(data)

    if isinstance(data, pathlib.Path):
        data = json.loads(data.read_text())

    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls} is not a dataclass")
    
    initialization_kwargs: typing.Dict = {}

    for field in dataclasses.fields(cls):
        if field.name not in data:
            continue

        value = data[field.name]

        dataclass_type_args: typing.List[typing.Type[T]] = [
            inner_type
            for inner_type in typing.get_args(field.type)
            if dataclasses.is_dataclass(inner_type)
        ]
        type_origin: typing.Optional[typing.Type] = typing.get_origin(field.type)

        if dataclasses.is_dataclass(field.type) and isinstance(value, dict):
            initialization_kwargs[field.name] = deserialize(field.type, value)
        elif isinstance(value, typing.Iterable) and not isinstance(value, str) and issubclass(type_origin, typing.Sequence) and dataclass_type_args:
            initialization_kwargs[field.name] = []

            for entry_index, entry in enumerate(value):
                acceptable_value = entry
                if isinstance(entry, dict):
                    for possible_type in dataclass_type_args:
                        try:
                            acceptable_value = deserialize(possible_type, entry)
                            break
                        except:
                            LOGGER.debug(f'"{entry}" could not be deserialized as a {possible_type}')
                initialization_kwargs[field.name].append(acceptable_value)

        else:
            initialization_kwargs[field.name] = value

    return cls(**initialization_kwargs)


@dataclasses.dataclass
class Dimension:
    """
    Represents a Netcdf Dimension
    """
    name: str
    """The name of the dimension"""
    size: int
    """The length of the dimension"""
    unlimited: bool = dataclasses.field(default=False)
    """Whether this is an unlimited/record dimension"""


@dataclasses.dataclass
class Variable:
    """
    Represents a Netcdf Variable
    """
    name: str
    """The name of the variable"""
    datatype: str
    """The type of data stored ('char', 'int', float', etc)"""
    dimensions: typing.List[Dimension] = dataclasses.field(default_factory=list)
    """The names of dimensions and their expected lengths in indexing order"""
    attributes: typing.Dict[str, typing.Union[str, int, typing.List[typing.Union[str, int]]]] = dataclasses.field(default_factory=dict)
    """Key value pairs describing the data within the variable"""
    encoding: typing.Dict[str, typing.Union[str, int, typing.List[typing.Union[str, int]]]] = dataclasses.field(default_factory=dict)
    """Key value pairs describing how the data should be stored"""
    coordinates: typing.List[str] = dataclasses.field(default_factory=list)
    """The names of coordinate variables that this relies upon"""

    @classmethod
    def from_data_array(cls, array: xarray.DataArray) -> "Variable":
        """
        Create a variable specification from a data array

        :param array: The data array to mimic in specification form
        :returns: A new variable specification from the given data array
        """
        name: str = str(array.name) if array.name is not None else 'array'
        attributes: typing.Dict[str, typing.Any] = {
            key: value.tolist() if isinstance(value, numpy.ndarray) else value
            for key, value in array.attrs.items()
            if key != 'source'
        }
        datatype: str = array.dtype.str
        dimensions: typing.List[Dimension] = [
            Dimension(name=str(key), size=value)
            for key, value in array.sizes.items()
        ]
        coordinates: typing.List[str] = list(map(str, array.sizes.keys()))
        encoding: typing.Dict[str, typing.Any] = {
            key: value.tolist() if isinstance(value, numpy.ndarray) else value
            for key, value in array.encoding.items()
            if key != 'source'
        }
        return cls(
            name=name,
            attributes=attributes,
            datatype=datatype,
            dimensions=dimensions,
            coordinates=coordinates,
            encoding=encoding,
        )

    def generate_raw_data(self, step: int, max_steps: int) -> numpy.ndarray:
        """
        Generate arbitrary data
        """
        jitter_fraction: float = 0.05
        data_type: numpy.dtype = xarray_dtype_from_str(self.datatype)

        shape: typing.Tuple[int, ...] = tuple(dimension.size for dimension in self.dimensions)

        if shape == tuple():
            return data_type.type()

        is_dimension: bool = len(shape) == 1 and self.dimensions[0].name == self.name

        if 'valid_range' in self.attributes:
            absolute_low_bound = int(self.attributes['valid_range'][0])
            absolute_high_bound = int(self.attributes['valid_range'][1])
        elif 'valid_min' in self.attributes and 'valid_max' in self.attributes:
            absolute_low_bound = int(self.attributes['valid_min'])
            absolute_high_bound = int(self.attributes['valid_max'])
        else:
            absolute_low_bound = 0 if is_dimension else 200
            absolute_high_bound = shape[0] if is_dimension else 8000

        if len(shape) == 1 and self.dimensions[0].name == self.name:
            # This needs to be sequential values as this the set of values for a dimension
            count: int = shape[0]

            # If there is only a single value, there's a good chance this is a record variable. Just choose the one that is in the correct step in the range
            if count == 1:
                absolute_range: numpy.ndarray = numpy.linspace(absolute_low_bound, absolute_high_bound, num=max_steps, dtype=data_type)
                return absolute_range[step:step + 1]
            else:
                values: numpy.ndarray = numpy.linspace(absolute_low_bound, absolute_high_bound, num=shape[0], dtype=data_type)
                values = values.astype(data_type)
                return values

        random_number_generator: numpy.random.Generator = numpy.random.default_rng(abs(hash((self.name, self.datatype))))

        initial_range: int = absolute_high_bound - absolute_low_bound
        fourth = initial_range / 4.0
        fourth_range = fourth * 0.8
        low_midpoint = absolute_low_bound + fourth
        high_midpoint = absolute_high_bound - fourth

        if numpy.issubdtype(data_type, numpy.number):
            if numpy.issubdtype(data_type, numpy.integer):
                generator_function = random_number_generator.integers
            elif numpy.issubdtype(data_type, numpy.floating):
                generator_function = random_number_generator.uniform
            else:
                raise NotImplementedError(f"The generation of '{data_type}' numbers is not implemented.")

            low_range: numpy.ndarray = generator_function(
                low=low_midpoint - fourth_range,
                high=low_midpoint + fourth_range,
                size=shape
            )
            high_range: numpy.ndarray = generator_function(
                low=high_midpoint - fourth_range,
                high=high_midpoint + fourth_range,
                size=shape
            )

            raw_data: numpy.ndarray = generator_function(low_range, high_range)
            span: numpy.ndarray = high_range - low_range

            for _ in range(step):
                jitter: numpy.ndarray = random_number_generator.uniform(-jitter_fraction, jitter_fraction, size=shape) * span
                jitter = jitter.astype(dtype=data_type)
                raw_data += jitter
                raw_data = numpy.clip(raw_data, low_range, high_range)

            raw_data = raw_data.astype(dtype=data_type)
            return raw_data

        if numpy.issubdtype(data_type, numpy.bool_):
            return random_number_generator.choice([numpy.True_, numpy.False_], size=shape)

        if numpy.issubdtype(data_type, numpy.bytes_):
            raise NotImplementedError(f"Random byte generation has not been implemented yet")

        if numpy.issubdtype(data_type, numpy.datetime64):
            raise NotImplementedError(f"Random Datetime generation has not been implemented yet")

        return numpy.empty(shape, dtype=data_type)


    def generate_array(
        self,
        coordinates: typing.Mapping[str, typing.Sequence],
        step: int,
        max_steps: int
    ) -> xarray.DataArray:
        """
        Generate a data array representing this variable
        """
        LOGGER.info(f"Generating the data for {self.name} ({self.datatype})")

        raw_data = self.generate_raw_data(step=step, max_steps=max_steps)
        array: xarray.DataArray = xarray.DataArray(
            data=raw_data,
            name=self.name,
            attrs={
                key: shrink_value(value=value)
                for key, value in self.attributes.items()
            },
            coords={
                coordinate_name: coordinate
                for coordinate_name, coordinate in coordinates.items()
                if coordinate_name in self.coordinates
            } or None,
            dims=list(map(lambda dim: dim.name, self.dimensions)) or tuple(),
        )

        array.encoding.update(self.encoding)

        return array

@dataclasses.dataclass
class Dataset:
    """
    Represents a Post Processing Output Netcdf
    """
    dimensions: typing.List[Dimension]
    """A list of all dimensions within this dataset"""
    coordinates: typing.List[Variable]
    """The variables marked as coordinates within this dataset"""
    variables: typing.List[Variable]
    """The variables stored within this dataset"""
    attributes: typing.Dict[str, typing.Union[int, str]]
    """Global Attributes for this dataset"""
    configuration: post_processing.enums.Configuration
    """What configuration this output came from"""
    model_output_type: post_processing.enums.ModelOutputType
    """The type of model output that this data represents"""
    region: typing.Union[post_processing.enums.Region, post_processing.enums.RFC]
    """The region over which this data is valid"""
    member: typing.Optional[int] = dataclasses.field(default=None)
    """The ensemble member that this dataset reflects"""

    def __str__(self):
        return (
            f"Dataset: "
            f"{self.configuration}."
            f"{self.model_output_type}{'_' + str(self.member) if self.member is not None else ''}."
            f"{self.region}"
        )

    @classmethod
    def from_netcdf(cls, path: pathlib.Path) -> "Dataset":
        """
        Read a netcdf file and convert it into a Dataset specification

        :param path: Path to the netcdf file
        :returns: Dataset specification
        """
        if not path.is_file():
            raise FileNotFoundError(f"File {path} not found - cannot create dataset specification")

        data: xarray.Dataset = xarray.open_dataset(path)
        dimensions: typing.List[Dimension] = list(Dimension(name=str(key), size=value) for key, value in data.sizes.items())
        attributes: typing.Dict[str, typing.Any] = {
            key: value.tolist() if isinstance(value, numpy.ndarray) else value
            for key, value in data.encoding.items()
            if key != 'source'
        }
        unlimited_dimensions: typing.Set[str] = data.encoding.get("unlimited_dims", set())

        for unlimited_dimension in unlimited_dimensions:
            matching_dimension: typing.Optional[Dimension] = next(
                (dimension for dimension in dimensions if dimension.name == unlimited_dimension),
                None
            )
            if matching_dimension is not None:
                matching_dimension.unlimited = True

        coordinates: typing.List[Variable] = [
            Variable.from_data_array(array=array)
            for array in data.coords.values()
        ]

        variables: typing.List[Variable] = [
            Variable.from_data_array(array=array)
            for array in data.data_vars.values()
        ]

        file_data: NWMFile = NWMFile.parse(path=path)

        return cls(
            dimensions=dimensions,
            coordinates=coordinates,
            variables=variables,
            attributes=attributes,
            configuration=file_data.configuration,
            model_output_type=file_data.model_output_type,
            region=file_data.region,
            member=file_data.member,
        )

    def generate_netcdf(
        self,
        output_directory: pathlib.Path,
        cycle: int = 0,
        length: int = 18,
        step: int = 1,
        overwrite: bool = False
    ) -> typing.Sequence[pathlib.Path]:
        """
        Generate a series of netcdf files based off of this configuration

        This won't accurately model Hawaii

        :param output_directory: Where to save the files
        :param cycle: Which cycle the dataset will belong to
        :param length: How many frames should be in the dataset
        :param step: The number of hours between each frame
        :param overwrite: Whether to overwrite preexisting files
        :returns: The paths to the newly created files
        """
        filenames: typing.List[str] = [
            (
                f"nwm."
                f"t{str(cycle).zfill(2)}z."
                f"{self.configuration}."
                f"{self.model_output_type}{'_' + str(self.member) if self.member else ''}."
                f"f{str(frame_number).zfill(6 if self.region == post_processing.enums.Region.Hawaii else 3)}."
                f"{self.region}."
                f"nc"
            )
            for frame_number in range(step, step * length + 1, step)
        ]

        output_paths: typing.List[pathlib.Path] = [
            output_directory / filename
            for filename in filenames
        ]

        if all(map(lambda path: path.exists(), output_paths)) and not overwrite:
            LOGGER.info(f"Data for {self} already exists in {output_directory}")
            return output_paths

        LOGGER.info(f"Generating data for {len(filenames)} files")
        coordinate_values: typing.Dict[str, typing.Union[typing.Sequence[xarray.DataArray], xarray.DataArray]] = {}

        for coordinate in self.coordinates:
            LOGGER.info(f"Generating coordinate information for {coordinate.name}")
            data_type = xarray_dtype_from_str(coordinate.datatype)
            if 'time' in coordinate.name:
                import pandas
                if coordinate.name == 'time':
                    values: typing.Sequence[typing.Sequence[int]] = [
                        [int(date.timestamp()) // 60]
                        for date in pandas.date_range(pandas.Timestamp.today().date(), periods=length, freq=f"{step}h")
                    ]
                else:
                    values: typing.Sequence[int] = [
                        int(date.timestamp()) // 60
                        for date in pandas.date_range(pandas.Timestamp.today().date(), periods=coordinate.dimensions[0].size, freq="h")
                    ]
            else:
                if not is_integer_dtype(data_type):
                    raise ValueError(
                        f"The data type for the '{coordinate.name}' coordinate must be an integer, but received '{data_type}'."
                    )
                values: typing.Sequence[int] = numpy.linspace(
                    1,
                    coordinate.dimensions[0].size,
                    coordinate.dimensions[0].size,
                    dtype=data_type
                )

            if isinstance(values[-1], typing.Sequence):
                coordinate_values[coordinate.name] = [
                    xarray.DataArray(
                        data=inner_values,
                        name=coordinate.name,
                        attrs=coordinate.attributes,
                        dims=[dimension.name for dimension in coordinate.dimensions],
                    )
                    for inner_values in values
                ]
            else:
                coordinate_values[coordinate.name] = xarray.DataArray(
                    data=values,
                    name=coordinate.name,
                    attrs=coordinate.attributes,
                    dims=[dimension.name for dimension in coordinate.dimensions],
                )

        dataset_files: typing.List[pathlib.Path] = []
        output_directory.mkdir(parents=True, exist_ok=True)

        keyword_arguments: typing.Sequence[typing.Dict[str, typing.Any]] = [
            {
                "filename": output_path,
                "coordinates": {
                    coordinate_name: coordinates if isinstance(coordinates, xarray.DataArray) else coordinates[file_index]
                    for coordinate_name, coordinates in coordinate_values.items()
                },
                "variable_definitions": self.variables,
                "global_attributes": self.attributes,
                "unlimited_dimensions": list(dimension.name for dimension in self.dimensions if dimension.unlimited),
                "step": file_index,
                "max_steps": len(output_paths),
                "overwrite": overwrite
            }
            for file_index, output_path in enumerate(output_paths)
        ]

        with multiprocessing.Pool() as pool:
            future_paths: typing.List[multiprocessing.pool.AsyncResult[pathlib.Path]] = [
                pool.apply_async(
                    func=write_file,
                    kwds=kwargs
                )
                for kwargs in keyword_arguments
            ]

            while future_paths:
                future: multiprocessing.pool.AsyncResult[pathlib.Path] = future_paths.pop(0)
                try:
                    result: pathlib.Path = future.get(timeout=1)
                    dataset_files.append(result)
                except multiprocessing.TimeoutError:
                    future_paths.append(future)


        dataset_files = sorted(dataset_files, key=lambda path: path.stem)
        return dataset_files


def shrink_value(value: typing.Any) -> typing.Any:
    """
    Reduce the size of an input value if necessary

    :param value: The value whose type to shrink
    :returns: The shrunken value, properly typed if necessary
    """
    if isinstance(value, int):
        if -2_147_483_648 <= value <= 2_147_483_647:
            return numpy.int32(value)
        return numpy.int64(value)
    if isinstance(value, float):
        return numpy.float64(value)
    return value

def write_file(
    filename: pathlib.Path,
    coordinates: typing.Dict[str, xarray.DataArray],
    variable_definitions: typing.Sequence[Variable],
    global_attributes: typing.Dict[str, typing.Any],
    unlimited_dimensions: typing.Union[str, typing.Sequence[str]],
    step: int,
    max_steps: int,
    overwrite: bool = False
) -> pathlib.Path:
    """
    Generate fake data based on components from an output specification

    :param filename: Where to put the generated data
    :param coordinates: Common coordinate data
    :param variable_definitions: The definitions for what variables should be included
    :param global_attributes: Attributes that should be present on the global scope
    :param unlimited_dimensions: One or more dimensions that should be considered as being unlimited, i.e. the record dimension
    :param step: The step that this file represents in the order of generation
    :param max_steps: The maximum amount of steps being created. This will show {step} out of {max_steps}
    :param overwrite: Whether to overwrite the data if it already exists
    :returns: The path to the generated data
    """
    if filename.exists() and not overwrite:
        LOGGER.debug(f"Data already exists for {filename} - reusing that")
        return filename

    LOGGER.debug(f"Generating data for {filename}")

    global_attributes = {
        attribute_name: shrink_value(attribute_value)
        for attribute_name, attribute_value in global_attributes.items()
    }

    variables: typing.Dict[str, xarray.DataArray] = {}

    for variable in variable_definitions:
        variables[variable.name] = variable.generate_array(coordinates=coordinates, step=step, max_steps=max_steps)

    dataset: xarray.Dataset = xarray.Dataset(
        data_vars=variables,
        coords=coordinates,
        attrs=global_attributes,
    )

    if isinstance(unlimited_dimensions, str):
        unlimited_dimensions = [unlimited_dimensions]

    try:
        dataset.to_netcdf(path=filename, unlimited_dims=unlimited_dimensions)
    except:
        LOGGER.error(f"Failed to write data to {filename}. Data:{os.linesep}{dataset}")
        raise
    header: str = nco.get_header(filename)
    LOGGER.debug(f"Saved dataset to {filename}:{os.linesep}{header}")
    return filename
