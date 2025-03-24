"""
Classes used to define what output netcdf files should look like
"""
import typing
import dataclasses
import logging
import pathlib

import post_processing.enums

T = typing.TypeVar("T")
"""A generic type"""


LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


def deserialize(cls: typing.Type[T], data: typing.Dict) -> T:
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls} is not a dataclass")
    
    initialization_kwargs: typing.Dict = {}

    for field in dataclasses.fields(cls):
        if field.name not in data:
            continue

        value = data[field.name]

        dataclass_type_args: typing.List[typing.Type] = [
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


@dataclasses.dataclass
class Variable:
    """
    Represents a Netcdf Variable
    """
    name: str
    """The name of the variable"""
    datatype: str
    """The type of data stored ('char', 'int', float', etc)"""
    dimensions: typing.List[Dimension] = dataclasses.field(default_factory=[])
    """The names of dimensions and their expected lengths in indexing order"""
    attributes: typing.Dict[str, typing.Union[str, int, typing.List[typing.Union[str, int]]]] = dataclasses.field(default_factory=dict)
    """Key value pairs describing the data within the variable"""
    coordinates: typing.List[str] = dataclasses.field(default_factory=list)
    """The names of coordinate variables that this relies upon"""

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
    region: post_processing.enums.Region
    """The region over which this data is valid"""