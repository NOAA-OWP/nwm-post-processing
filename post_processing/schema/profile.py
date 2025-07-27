"""
Defines a profile for how a certain combination of Configuration + Model Output Type + Region should behave
"""
import abc
import importlib
import itertools
import os
import re
import shutil
import types
import typing
import dataclasses
import enum
import pathlib
import logging
import functools

from datetime import datetime

import xarray

from post_processing.schema.base import BaseModel
from post_processing.schema.base import member
from post_processing.schema.base import get_fields

from post_processing.enums import Region
from post_processing.enums import ModelOutputType
from post_processing.enums import Configuration

from post_processing import nco
from post_processing import schema

from post_processing.utilities.common import starmap
from post_processing.utilities.common import partition
from post_processing.utilities.common import get_template_variables
from post_processing.utilities.common import to_json
from post_processing.configuration import settings

if typing.TYPE_CHECKING:
    import numpy
    from post_processing.transform import anomaly

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
InputType = typing.TypeVar("InputType")
OutputType = typing.TypeVar("OutputType")
OPERATION_KEY: typing.Final[str] = "operation"
"""The key for a ProfileOperation dictionary stating what the ProfileOperation is supposed to do"""

@typing.runtime_checkable
class OperationHandler(typing.Protocol[InputType, OutputType]):
    """
    Defines the function signature of the key function of a Profile Operation
    """
    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> OutputType: ...

@typing.runtime_checkable
class PythonHandler(typing.Protocol[InputType, OutputType]):
    """
    Defines the function signature of a python function that can handle Profile Operations
    """
    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any],
        **kwargs
    ) -> OutputType: ...


class OperationType(enum.StrEnum):
    """
    Enumerates the different types of operations that may be performed
    """
    EXTRACT = "extract"
    """Extract data from netcdf files and process subset each separately"""
    MERGE = "merge"
    """Combine multiple netcdf files into one"""
    DROP = "drop"
    """Drop variables from a netcdf file"""
    RENAME = "rename"
    """Rename a variable or attribute within a netcdf file"""
    ATTRIBUTE = "attribute"
    """Update or add an attribute to either the global or variable scope in a netcdf file"""
    SAVE = "save"
    """Save a netcdf file to a targetted location"""
    BRANCH = "branch"
    """Perform mutually exclusive operations on passed in data"""
    INTO_PYTHON = "into_python"
    """Transform file based netcdf files into python based netcdf structures"""
    TO_PYTHON = "to_python"
    """Pass python based netcdf structures to another python function"""
    OUT_OF_PYTHON = "out_of_python"
    """Convert netcdf structures within python code to files"""
    FUNCTION = "function"
    """Call a function on the input data"""
    LOAD = "load"
    """Load one or more netcdf files into memory"""
    WRITE = "write"
    """Write a netcdf file to disk (may conflict with 'save')"""
    NCO = "not_implemented"
    """A dummy operation type used as the base for NCO operations"""
    ECHO = "echo"
    """Output a message"""
    RAISE = "raise"
    """Raise an exception"""
    ON_EACH = "on_each"
    """Run each contained operation on each input separately"""
    ANOMALY = "anomaly"
    """Bin values by threshold"""
    PEEK = "peek"
    """Print information about the current set of data to the logs"""


@dataclasses.dataclass
class InPlaceOperationMixin:
    """
    A mixin for data classes adding a field determining whether the files should be operated on in place or into a new file
    """
    in_place: bool = dataclasses.field(default=False, kw_only=True)
    """
    Dictates whether the changes made to the data should be applied in place or if the changes should go into a new file
    """
    output_pattern: typing.Optional[str] = dataclasses.field(default=None, kw_only=True)
    """
    The file name pattern to use when not making a change in place
    """

    def get_output_path(self, work_directory: pathlib.Path, input_path: pathlib.Path, **context) -> pathlib.Path:
        if self.in_place:
            return input_path

        filename: str = self.render_output_name(
            input_file=input_path.stem,
            **context
        )
        return work_directory / filename

    @property
    def output_pattern_variables(self) -> typing.Sequence[str]:
        if self.output_pattern is None:
            return []
        return get_template_variables(self.output_pattern)

    def render_output_name(self, input_file: str, **context: typing.Any) -> str:
        """
        Attempt to render a filename from the output pattern

        Example:
            >>> instance = InPlaceOperationMixin(in_place=False, output_pattern="{in_place}_{one}_{two}.nc")
            >>> instance.render_output_name(one="three", two="four")
            False_three_four.nc

        :param context: key-value pairs describing variable values that might be needed to fulfill variables within the template
        :returns: The formatted output name
        """
        if self.output_pattern is None:
            return f"{context.get('stage', '')}{input_file}.nc"

        template_arguments: typing.Dict[str, typing.Optional[str]] = {
            variable_name: None
            for variable_name in self.output_pattern_variables
        }

        missing_arguments: typing.List[str] = []

        for key in template_arguments:
            if key in context:
                template_arguments[key] = context[key]
            elif hasattr(self, key):
                template_arguments[key] = getattr(self, key)
            elif key in globals():
                template_arguments[key] = globals()[key]
            else:
                missing_arguments.append(key)

        if missing_arguments:
            raise ValueError(
                f"Cannot render output name - missing the following arguments for '{self.output_pattern}': "
                f"{', '.join(missing_arguments)}"
            )

        formatted_name: str = self.output_pattern.format(**template_arguments)
        return formatted_name


@dataclasses.dataclass
class ProfileOperation(BaseModel, OperationHandler[InputType, OutputType], abc.ABC):
    """
    Represents an operation that a profile may perform
    """
    comment: typing.Optional[str] = dataclasses.field(default=None, kw_only=True)
    """A comment from the writer explaining what this operation does"""
    operation_id: typing.Optional[str] = member(default=None, kw_only=True)
    """A specialized identifier for this operation"""
    disable: bool = dataclasses.field(default=False, kw_only=True)
    """Disable operation of this operation"""

    def assign_id(self, parent_id: str):
        if parent_id == "":
            self.operation_id = "1"
        elif parent_id.isdigit():
            parent_id: int = int(float(parent_id))
            self.operation_id = f"{parent_id + 1}"
        elif parent_id.endswith("."):
            self.operation_id = f"{parent_id}1"
        elif '.' in parent_id:
            split_ids: typing.List[str] = parent_id.split(".")
            final_id: int = int(float(split_ids[-1]))
            split_ids[-1] = str(final_id + 1)
            self.operation_id = ".".join(split_ids)

        sub_index: int = 0
        for attribute_name, attribute in self.__dict__.items():
            if not isinstance(attribute, typing.Iterable):
                continue

            values_are_operations: bool = isinstance(attribute, typing.Mapping) and all(
                isinstance(entry, ProfileOperation) for entry in attribute.values()
            )
            values_are_lists_of_operations: bool = isinstance(attribute, typing.Mapping) and all(
                isinstance(entry, typing.Sequence) and all(
                    isinstance(inner_entry, ProfileOperation)
                    for inner_entry in entry
                )
                for entry in attribute.values()
            )
            if values_are_lists_of_operations:
                for collection in attribute.values():
                    sub_index += 1
                    operation_id = f"{self.operation_id}.{sub_index}"
                    sub_sub_index: int = 0
                    for entry in collection:
                        entry.assign_id(parent_id=f"{operation_id}.{sub_sub_index}")
                        sub_sub_index += 1

            elif values_are_operations:
                for entry in attribute.values():
                    entry.assign_id(parent_id=f"{self.operation_id}.{sub_index}")
                    sub_index += 1

            if all(isinstance(entry, OperationHandler) for entry in attribute):
                for entry in attribute:
                    entry.assign_id(parent_id=f"{self.operation_id}.{sub_index}")
                    sub_index += 1

    @classmethod
    @abc.abstractmethod
    def operation(cls) -> OperationType:
        """Get the type of operation the ProfileOperation fulfills"""

    def __str__(self):
        return f"{self.operation_id + ': ' if self.operation_id else ''}{self.operation().replace('_', ' ').title()}"

    def __hash__(self):
        values_to_hash: typing.Tuple[str, ...] = (self.__class__.__name__, to_json(self))
        return hash(values_to_hash)

    def visit(
        self,
        operator: typing.Callable[["ProfileOperation"], typing.Any],
        condition: typing.Callable[["ProfileOperation"], bool] = None
    ) -> None:
        """
        Perform some action on the operation and all of its children that are also operations

        :param operator: The operation to perform
        :param condition: A condition to apply the visitor operator
        """
        if condition is None:
            condition = lambda x: True

        if condition(self):
            operator(self)

        for attribute_name, attribute in self.__dict__.items():
            if not isinstance(attribute, typing.Iterable):
                continue
            if isinstance(attribute, typing.Mapping) and all(isinstance(entry, OperationHandler) for entry in attribute):
                for entry in attribute.values():
                    entry.visit(operator=operator, condition=condition)
            if all(isinstance(entry, OperationHandler) for entry in attribute):
                for entry in attribute:
                    entry.visit(operator=operator, condition=condition)


    @abc.abstractmethod
    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> OutputType:
        """
        Split each received NWMFile into other files based on the collection of masks. There should be len(masks) * len(files) returned files

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param previous_operations: A list of operations run previously
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """


@dataclasses.dataclass(unsafe_hash=True)
class EchoOperation(ProfileOperation[InputType, InputType]):
    """
    A profile operation that outputs formatted log messages. Useful for alerts and progress messages
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.ECHO

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> InputType:
        message_metadata: typing.Dict[str, typing.Any] = {
            "profile": str(profile),
            "process_identifier": process_identifier,
            "work_directory": str(work_directory),
            "previous_operations": "->".join(map(str, previous_operations)),
            **metadata,
        }

        if profile.source_file is not None:
            message_metadata["source_file"] = str(profile.source_file)

        self._logger.log(self.level, self.message.format(**message_metadata))
        return data

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.logger_name:
            self._logger = logging.getLogger(self.logger_name)

        if not isinstance(self.level, int):
            self.level = logging.getLevelName(self.level.upper())

    def __str__(self):
        return (
            f"{self.operation_id + ': ' if self.operation_id else ''}"
            f"Print \"{logging.getLevelName(self.level) if isinstance(self.level, int) else self.level} => "
            f"{self.message}\""
        )

    message: str = dataclasses.field()
    level: typing.Union[int, str] = dataclasses.field(default=logging.INFO)
    logger_name: typing.Optional[str] = dataclasses.field(default=None)
    _logger: logging.Logger = member(default_factory=lambda: LOGGER)

@dataclasses.dataclass(unsafe_hash=True)
class RaiseOperation(ProfileOperation[InputType, InputType]):
    """
    A profile operation that raises exceptions.

    Used as an option for placeholders
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.RAISE

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> InputType:
        message_metadata: typing.Dict[str, typing.Any] = {
            "profile": str(profile),
            "process_identifier": process_identifier,
            "work_directory": str(work_directory),
            "previous_operations": "->".join(map(str, previous_operations)),
            **metadata,
        }
        message: str = self.message.format(**message_metadata)

        # TODO: Write a custom exception for this
        raise Exception(message)

    message: str


class NCOOperation(ProfileOperation[typing.Sequence[pathlib.Path], typing.Sequence[pathlib.Path]]):
    """
    Base class for file-set to file-set operations that mimic or wrap functions from CLI Netcdf Operators
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.NCO

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        pass

@dataclasses.dataclass
class ExtractOperation(NCOOperation):
    """
    Describes how to extract and operate on individual pieces of data
    """

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.EXTRACT

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Split each received NWMFile into other files based on the collection of masks. There should be len(masks) * len(files) returned files

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The given paths
        """
        try:
            frame_pattern: re.Pattern = re.compile(r"(?<=\.)(tm|f)\d+(?=\.)")
            def get_frame_identifier(filename: str) -> str:
                match: typing.Optional[re.Match] = frame_pattern.search(filename)
                if match:
                    return match.group(0)
                return ""

            from post_processing.enums import RFC
            subset_arguments: typing.List[typing.Dict[str, typing.Any]] = [
                {
                    "input_file": input_file,
                    "mask": mask,
                    "coordinate": self.dimension,
                    "work_directory": work_directory,
                    "mask_coordinate": self.mask_coordinate,
                    "identifiers": {
                        **metadata,
                        "mask_name": mask.stem,
                        "input_name": input_file.stem,
                        "frame": get_frame_identifier(input_file.name),
                        "RFC": RFC.from_string(mask.stem, strict=False),
                        **identifiers
                    },
                    "output_pattern": self.output_pattern,
                }
                for input_file, (mask, identifiers) in itertools.product(data, self._identifier_mapping.items())
            ]

            from post_processing.transform import subset_file_into_file_by_mask
            subset_paths: typing.Sequence[pathlib.Path] = starmap(
                function=subset_file_into_file_by_mask,
                args=subset_arguments,
                thread_count=settings.maximum_additional_threads
            )

            arguments_for_each: typing.List[typing.Dict[str, typing.Any]] = [
                {
                    "operations": self.each,
                    "profile": profile,
                    "process_identifier": process_identifier,
                    "work_directory": work_directory,
                    "data": [subset_path],
                    "previous_operations": list(previous_operations),
                    "metadata": {
                        **metadata,
                        "file_name": subset_path.stem,
                        "frame": get_frame_identifier(subset_path.name),
                        "RFC": RFC.from_string(subset_path.stem, strict=False)
                    }
                }
                for subset_path in subset_paths
            ]

            results: typing.Sequence[typing.Sequence[typing.Union[pathlib.Path]]] = starmap(
                function=call_operations,
                args=arguments_for_each,
                thread_count=settings.maximum_additional_threads
            )
        except Exception as exception:
            if 'failure in' not in str(exception).lower():
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return [path for inner_results in results for path in inner_results]

    def __post_init__(self):
        from post_processing.utilities.common import expand_paths
        self.masks = expand_paths(self.masks, base_path=settings.application_path)

        missing_masks: typing.List[pathlib.Path] = [path for path in self.masks if not path.is_file()]

        assert self.masks and not any(missing_masks), f"A {self.__class__.__name__} is missing a required mask(s): {missing_masks}"

        try:
            self._pattern = re.compile(self.identifier_pattern)
        except BaseException as e:
            raise ValueError(f"Cannot use '{self.identifier_pattern}' to find identifiers in masks") from e

        if not self._pattern.groupindex:
            raise ValueError(
                f"'{self.identifier_pattern}' is not a valid pattern for finding identifiers in mask files - "
                f"it has not parameter groups. "
                f"Please define parameter groups via strings like '(?P<variable_name>pattern)'"
            )

        masks_without_identifiers: typing.List[pathlib.Path] = []

        for mask in self.masks:
            mask_name: str = mask.stem
            match: typing.Optional[re.Match] = self._pattern.search(mask_name)

            if not match:
                masks_without_identifiers.append(mask)
                continue

            self._identifier_mapping[mask] = {
                key: '' if value is None else value
                for key, value in match.groupdict().items()
            }

        if masks_without_identifiers:
            raise ValueError(
                f"The following files did not contain identifiers: "
                f"{', '.join(map(pathlib.Path.name.fget, masks_without_identifiers))}"
            )

        if not self.each:
            raise ValueError(f"There must be at least one operation to perform on split data")

        for operation_index, operation in enumerate(self.each):
            if isinstance(operation, typing.Mapping):
                operation = load_operation(specification=operation)
                self.each[operation_index] = operation
            elif not isinstance(operation, ProfileOperation):
                raise ValueError(
                    f"Encountered an invalid sub-operation for a {self.__class__.__qualname__} - item "
                    f"{operation_index} holds a '{type(operation)}', which cannot be converted into a "
                    f"{ProfileOperation.__qualname__}"
                )

    def __hash__(self):
        try:
            parent_hash: int = super().__hash__()
        except:
            parent_hash = 0

        return hash((
            parent_hash,
            *self.masks,
            self.identifier_pattern,
            self.output_pattern,
            *self.each,
            self.dimension
        ))

    def __str__(self):
        return (
            f"{self.operation_id + ': ' if self.operation_id else ''}"
            f"Extract data by location based on the '{self.dimension}' dimension in the input and the "
            f"'{self.mask_coordinate}' dimension within:{os.linesep}"
            f"    - {(os.linesep + '    - ').join(map(str, self.masks))}{os.linesep}"
            f"And save the results to files named like: {self.output_pattern}"
        )

    masks: typing.List[pathlib.Path] = dataclasses.field()
    identifier_pattern: str = dataclasses.field()
    """A pattern used to extract metadata from the mask filename"""
    output_pattern: str = dataclasses.field()
    each: typing.List[typing.Union[ProfileOperation]]
    dimension: str = dataclasses.field(default="feature_id")
    mask_coordinate: typing.Optional[str] = dataclasses.field(default=None)
    _pattern: typing.Optional[re.Pattern] = member(default=None)
    _identifier_mapping: typing.Dict[pathlib.Path, typing.Dict[str, str]] = member(default_factory=dict)

@dataclasses.dataclass(unsafe_hash=True)
class MergeOperation(NCOOperation):
    """
    Tells how to combine data
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.MERGE

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ""
        return f"{prefix}Merge all input files together"

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Combine all received files into one

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        from post_processing.utilities.netcdf import load_metadata

        try:
            operation_metadata: typing.Dict[str, typing.Any] = load_metadata(path=data)
            operation_metadata.update(metadata)
            filename: str = self.file_name_pattern.format_map(operation_metadata)
            output_path: pathlib.Path = work_directory / filename

            from post_processing.transform import merge_files_into_file
            merge_files_into_file(files=data, output_file=output_path)
        except Exception as e:
            if 'failure in' not in str(e).lower():
                e.args = (f"Failure in:{os.linesep}{self}{os.linesep}{e.args[0]}", *e.args[1:])
            raise e
        return [output_path]

    file_name_pattern: str = dataclasses.field()


@dataclasses.dataclass(unsafe_hash=True)
class Peek(NCOOperation):
    """

    """
    show_summary: bool = dataclasses.field(default=True)
    show_state: bool = dataclasses.field(default=True)
    show_metadata: bool = dataclasses.field(default=True)

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ""
        if self.show_state and self.show_summary and self.show_metadata:
            return f"{prefix}Log a summary, the current state of all processed data, and all available metadata"
        elif self.show_state and self.show_summary:
            return f"{prefix}Log a summary and the current state of all processed data"
        elif self.show_state and self.show_metadata:
            return f"{prefix}Log the current state of all processed data and all available metadata"
        elif self.show_summary and self.show_metadata:
            return f"{prefix}Log a summary and all available metadata"
        return f"{prefix}Log all available metadata"

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.PEEK

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        from post_processing.utilities.netcdf import load_netcdf
        LOGGER.warning(f"Peeking into operations. Do not do this in production!")

        if self.show_summary:
            parameter_information: str = f"""
Profile:             {profile.output_type} data for the {profile.configuration} configuration over {profile.region}
Process identifier:  {process_identifier}
Work directory:      {work_directory}
Previous Operations: 
    - {(os.linesep + '    - ').join(list(map(str, previous_operations)))}
Files:
    - {(os.linesep + '    - ').join(list(map(str, data)))}

"""
            LOGGER.info(parameter_information)

        if self.show_state:
            for path in data:
                with load_netcdf(path) as netcdf_file:
                    LOGGER.info(f"{os.linesep * 2}{path}:{os.linesep * 2}{netcdf_file}")

        if self.show_metadata:
            metadata_information: str = f"""
    - {(os.linesep + '    - ').join(list(map(lambda pair: str(pair[0]) + ': ' + str(pair[1]), metadata.items())))}
"""
            LOGGER.info(metadata_information)
        return data

@dataclasses.dataclass
class DropOperation(NCOOperation, InPlaceOperationMixin):
    """
    Tells how to drop variables
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.DROP

    def __hash__(self):
        try:
            parent_hash: int = super().__hash__()
        except:
            parent_hash = 0
        return hash((
            parent_hash,
            *self.fields,
            self.exclude
        ))

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ""
        if self.exclude:
            return f"{prefix}Drop all data variables except {', '.join(self.fields)}"
        return (
            f"{prefix}Drop the following data variables: {', '.join(self.fields)}"
        )

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Remove variables from netcdf files

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        if self.exclude:
            drop_function = nco.keep_only_variables
        else:
            drop_function = nco.remove_variables


        input_output_mapping: typing.Mapping[pathlib.Path, pathlib.Path] = {
            file: self.get_output_path(
                work_directory=work_directory,
                input_path=file,
                process_identifier=process_identifier,
                **metadata
            )
            for file in data
        }

        arguments: typing.List[typing.Dict[str, typing.Any]] = [
            {
                "input_file": input_path,
                "output_file": output_path,
                "variables": self.fields
            }
            for input_path, output_path in input_output_mapping.items()
        ]

        try:
            starmap(
                function=drop_function,
                args=arguments
            )
        except Exception as exception:
            if "failure in" not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return list(input_output_mapping.values())

    fields: typing.List[str] = dataclasses.field()
    exclude: bool = dataclasses.field(default=False)

@dataclasses.dataclass
class RenameOperation(NCOOperation, InPlaceOperationMixin):
    """
    Tells how to rename variables or dimensions
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.RENAME

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ''
        rename_mapping: typing.Sequence[str] = [
            f"the {'variable' if self.rename_variable else 'dimension'} '{key}' to '{value}'"
            for key, value in self.mapping.items()
        ]

        return f"{prefix}Rename {', '.join(rename_mapping)}"

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Rename variables from netcdf files

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        arguments: typing.List[typing.Dict[str, typing.Any]] = [
            {
                "input_path": file,
                "output_path": self.get_output_path(
                    work_directory=work_directory,
                    input_path=file,
                    process_identifier=process_identifier,
                    **metadata
                ),
                "mapping": self.mapping
            }
            for file in data
        ]

        from post_processing.transform.rename import rename_variable
        from post_processing.transform.rename import rename_dimension

        try:
            new_files: typing.Sequence[pathlib.Path] = starmap(
                function=rename_variable if self.rename_variable else rename_dimension,
                args=arguments
            )
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return new_files

    def __hash__(self) -> int:
        return hash((self.operation(), *[pair for pair in self.mapping.items()]))

    mapping: typing.Dict[str, str] = dataclasses.field()
    rename_variable: bool = dataclasses.field(default=True)

@dataclasses.dataclass(unsafe_hash=True)
class AttributeOperation(NCOOperation, InPlaceOperationMixin):
    """
    Tells how to add, modify, or remove attributes on variables or globally
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.ATTRIBUTE

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ''
        description = (
            f"{self.operation().replace('_', ' ').title()}: "
            f"{self.mode.replace('_', ' ').title()} the {self.attribute_name} attribute value"
        )

        if self.field.lower().strip() == "global":
            description += " in the global scope "
        else:
            description += f" on {self.field} "

        description += f" to be {self.attribute_value} (type={self.attribute_type})"
        return f"{prefix}{description}"


    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Add, update, or remove attributes on variables or globally

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        arguments: typing.List[typing.Dict[str, typing.Union[str, pathlib.Path]]] = [
            {
                "input_file": file,
                "attribute_name": self.attribute_name,
                "attribute_value": self.attribute_value,
                "variable_name": self.field,
                "mode": self.mode,
                "attribute_type": self.attribute_type,
                "output_file": self.get_output_path(
                    work_directory=work_directory,
                    input_path=file,
                    process_identifier=process_identifier,
                    **metadata
                )
            }
            for file in data
        ]

        try:
            new_paths: typing.Sequence[pathlib.Path] = starmap(
                function=nco.add_or_modify_attribute,
                args=arguments,
                thread_count=True
            )
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return new_paths

    def __post_init__(self):
        if not isinstance(self.attribute_type, nco.NetcdfType):
            if isinstance(self.attribute_type, str):
                self.attribute_type = nco.NetcdfType.from_string(self.attribute_type)
            else:
                raise ValueError(f"'{self.attribute_type}' is not a valid NCO attribute type")

        if not isinstance(self.mode, nco.EditMode):
            if isinstance(self.mode, str):
                self.mode = nco.EditMode.from_string(self.mode)
            else:
                raise ValueError(f"'{self.mode}' is not a valid NCO Edit Mode")

    attribute_name: str
    field: typing.Optional[str] = dataclasses.field(default="global")
    attribute_value: typing.Optional[typing.Any] = dataclasses.field(default=None)
    attribute_type: nco.NetcdfType = dataclasses.field(default_factory=lambda: nco.NetcdfType.STRING)
    mode: nco.EditMode = dataclasses.field(default_factory=lambda: nco.EditMode.OVERWRITE)


@dataclasses.dataclass(unsafe_hash=True)
class SaveOperation(NCOOperation):
    """
    Save the given files in another location
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.SAVE

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ''
        return (
            f"{prefix}Save files to {self.directory} with the "
            f"following file name pattern: {self.filename_pattern}"
        )

    def __post_init__(self):
        if self.identifier_pattern:
            compiled_pattern: re.Pattern = re.compile(self.identifier_pattern)
            if not compiled_pattern.groupindex:
                raise ValueError(
                    f"'{self.identifier_pattern}' is not a valid identifier pattern - groups must be identified by it. "
                    f"Please include clauses like '(?P<identifier_name>pattern)'"
                )
            self._compiled_pattern = compiled_pattern

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Save the given files in another location

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        import shutil
        from post_processing.configuration import settings
        from post_processing.enums import Verbosity
        from post_processing.utilities.netcdf import load_metadata
        from post_processing.utilities.common import NWM_FILENAME_PATTERN

        saved_files: typing.List[pathlib.Path] = []
        try:
            for file in data:
                file_specific_metadata: typing.Dict[str, typing.Any] = {}
                file_name_match: typing.Optional[re.Match] = NWM_FILENAME_PATTERN.search(file.name)

                if file_name_match:
                    file_specific_metadata.update(file_name_match.groupdict())

                file_specific_metadata.update({
                    **load_metadata(path=file),
                    **metadata,
                    "file_name": file.name,
                    "file_stem": file.stem
                })

                if settings.verbosity >= Verbosity.ALL:
                    LOGGER.debug(
                        f"{os.linesep}"
                        f"Available Metadata:{os.linesep}"
                        f"    - {(os.linesep + '    - ').join([str(key) + ': ' + str(value) for key, value in metadata.items()])}"
                        f"{os.linesep}"
                    )

                if self._compiled_pattern:
                    matching_identifiers: typing.Optional[re.Match] = self._compiled_pattern.search(file.name)

                    if matching_identifiers:
                        file_specific_metadata.update(matching_identifiers.groupdict())
                    else:
                        LOGGER.warning(
                            f"No identifiers were found in '{file.name}' with the pattern: '{self.identifier_pattern}'"
                        )

                if 'rfc' in file_specific_metadata and not file_specific_metadata.get("RFC", None):
                    from post_processing.enums import RFC
                    rfc_abbreviation: typing.Optional[RFC] = RFC.from_string(file_specific_metadata['rfc'], strict=False)
                    if rfc_abbreviation:
                        file_specific_metadata["RFC"] = rfc_abbreviation

                try:
                    filename: str = self.filename_pattern.format(**file_specific_metadata)
                except KeyError as e:
                    from post_processing.utilities.common import to_json
                    LOGGER.error(
                        f"Could not generate a new file name used to save: {e}{os.linesep}"
                        f"Output Pattern: '{self.filename_pattern}'{os.linesep}"
                        f"Available Options: {to_json(file_specific_metadata)}"
                    )
                    raise

                try:
                    output_directory: pathlib.Path = pathlib.Path(str(self.directory).format(**file_specific_metadata))
                except KeyError as e:
                    LOGGER.error(
                        f"Key for file path template ('{str(self.directory)}') not found. Available variables:{os.linesep}"
                        f"    - {(os.linesep + '    - ').join(list(map(lambda pair: str(pair[0]) + ': ' + str(pair[1]), file_specific_metadata.items())))}"
                    )
                    raise

                output_directory.mkdir(parents=True, exist_ok=True)
                path: pathlib.Path = output_directory / filename

                if path in saved_files:
                    from pprint import pformat
                    raise FileExistsError(
                        f"Attempted to save to '{path}', but it was already saved to within this operation. "
                        f"It is likely that there is a naming error{os.linesep}"
                        f"Template: {pathlib.Path(self.directory) / self.filename_pattern}{os.linesep}"
                        f"Available Metadata:{os.linesep}"
                        f"{pformat(file_specific_metadata, indent=4, sort_dicts=True)}"
                    )

                try:
                    shutil.copy(file, path)
                except:
                    LOGGER.error(f"Could not copy '{file}' ({'exists' if file.is_file() else 'does not exist'}) to '{path}'")
                    raise
                saved_files.append(path)
                LOGGER.debug(f"Wrote {file} to {path}")
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        missing_files: typing.List[pathlib.Path] = [
            saved_path
            for saved_path in saved_files
            if not saved_path.exists()
        ]
        
        if missing_files:
            raise FileExistsError(
                f"Save operation failed. The following files are missing:{os.linesep}"
                f"    - {(os.linesep + '    - ').join(map(str, missing_files))}"
            )

        if self.return_new_paths:
            return saved_files

        return data

    directory: pathlib.Path = dataclasses.field()
    filename_pattern: str
    return_new_paths: bool = dataclasses.field(default=True)
    identifier_pattern: typing.Optional[str] = dataclasses.field(default=None)
    _compiled_pattern: typing.Optional[re.Pattern] = member(default=None)

@dataclasses.dataclass
class BranchOperation(ProfileOperation[InputType, typing.Sequence[pathlib.Path]]):
    """
    An operation that lets you feed input data through multiple mutually exclusive operations
    """
    branches: typing.Dict[str, typing.List[ProfileOperation]] = dataclasses.field()

    def __hash__(self):
        try:
            parent_hash: int = super().__hash__()
        except:
            parent_hash = 0

        hash_values: typing.List[typing.Hashable] = [parent_hash]

        for branch_name, branch_logic in self.branches.items():
            for logic_entry in branch_logic:
                hash_values.append(hash((branch_name, logic_entry)))

        hash_value: int = hash(tuple(hash_values))
        return hash_value

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ""
        branch_descriptions: typing.Sequence[str] = [
            (
                f"{name}:{os.linesep}"
                f"{'=' * len(name)}{os.linesep}"
                f"{os.linesep.join(map(str, branch))}{os.linesep}"
                f"{'-' * len(name)}{os.linesep}"
            )
            for name, branch in self.branches.items()
        ]
        return (
            f"{prefix}Perform the following logic separately:{os.linesep}"
            f"{os.linesep}"
            f"{os.linesep.join(branch_descriptions)}{os.linesep}"
            f"{os.linesep}"
        )

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.BRANCH

    def __post_init__(self):
        for branch_name, branch_logic in self.branches.items():
            for operation_index, operation in enumerate(branch_logic):
                if isinstance(operation, typing.Mapping):
                    operation = load_operation(specification=operation)
                    self.branches[branch_name][operation_index] = operation
                elif not isinstance(operation, ProfileOperation):
                    raise ValueError(
                        f"Encountered an invalid sub-operation for a {self.__class__.__qualname__} - item "
                        f"{operation_index} in the '{branch_name}' branch holds a '{type(operation)}', which cannot "
                        f"be converted into a {ProfileOperation.__qualname__}"
                    )

        invalid_ends_names: typing.List[str] = []
        for branch_name, branch_logic in self.branches.items():
            if len(branch_logic) == 0:
                invalid_ends_names.append(branch_name)
            elif not isinstance(branch_logic[-1], (NCOOperation, EchoOperation, Peek)):
                invalid_ends_names.append(branch_name)

        if invalid_ends_names:
            message = (
                f"Received invalid branching logic within a {self.__class__.__qualname__} - "
                f"branches must end in operations that return lists of paths. Invalid branches: "
                f"{', '.join(invalid_ends_names)}"
            )
            raise ValueError(message)

    def branch_concurrently(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> OutputType:
        """
        Feed input parameters into different branches of logic across multiple threads

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post-processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :param previous_operations: Operations that have already been performed
        :returns: The Paths for each created object
        """
        import concurrent.futures
        future_results: typing.Dict[str, concurrent.futures.Future[typing.Sequence[pathlib.Path]]] = {}

        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                for branch_name, branch_logic in self.branches.items():
                    future_result = executor.submit(
                        call_operations,
                        operations=branch_logic,
                        profile=profile,
                        process_identifier=process_identifier,
                        work_directory=work_directory,
                        data=data,
                        previous_operations=previous_operations.copy(),
                        metadata=metadata.copy()
                    )
                    future_results[branch_name] = future_result

                def process_error(error: Exception) -> Exception:
                    from post_processing.utilities.common import condense_exceptions
                    new_error_message: str = (
                        f"Could not perform the branching operation named '{branch_name}' "
                        f"from the {profile.output_type} data from the '{profile.configuration}' configuration over {profile.region} profile: {error}"
                    )
                    return condense_exceptions(new_error_message, [error])

                from post_processing.utilities.common import cycle_futures

                def log_duplicates(
                    branch: str,
                    returned_paths: typing.Sequence[pathlib.Path],
                    current_paths: typing.Sequence[typing.Sequence[pathlib.Path]]
                ) -> typing.Sequence[pathlib.Path]:
                    current_paths = set(path for resultant_paths in current_paths for path in resultant_paths)
                    preexisting_paths, new_paths = partition(lambda path: path in current_paths, returned_paths)
                    if preexisting_paths:
                        LOGGER.warning(
                            f"Processing from the '{branch}' branch in 'profile' for {metadata['cycle']}z on "
                            f"{metadata['date']} encountered duplicate results at: "
                            f"{', '.join(map(str, preexisting_paths))}"
                        )
                    return returned_paths

                results, exceptions = cycle_futures(
                    futures=future_results,
                    transform=log_duplicates,
                    exception_handler=process_error
                )

                if exceptions:
                    from post_processing.utilities.common import condense_exceptions
                    raise condense_exceptions(
                        f"One or more branches failed to process for profile on cycle {metadata['cycle']}z",
                        exceptions
                    )

                melted_results: typing.Sequence[pathlib.Path] = list(set([
                    path
                    for branch_results in results
                    for path in branch_results
                ]))
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return melted_results

    def branch_sequentially(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Feed input parameters into different branches of logic without threading

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post-processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :param previous_operations: Operations that have already been performed
        :returns: The Paths for each created object
        """
        results: typing.List[pathlib.Path] = []

        try:
            for branch_name, branch_logic in self.branches.items():
                branch_results: typing.Sequence[pathlib.Path] = call_operations(
                    operations=branch_logic,
                    profile=profile,
                    process_identifier=process_identifier,
                    work_directory=work_directory,
                    data=data,
                    previous_operations=previous_operations.copy(),
                    metadata=metadata.copy()
                )

                recurring_paths: typing.List[pathlib.Path] = list(filter(
                    lambda path: path in results,
                    branch_results
                ))

                if recurring_paths:
                    LOGGER.warning(
                        f"Processing from the '{branch_name}' branch in the profile for {profile.output_type} data from the "
                        f"{profile.configuration} configuration over {profile.region} for t{metadata['cycle']}z on "
                        f"{metadata['reference_time__date']} encountered duplicate results at: "
                        f"{', '.join(map(str, recurring_paths))}"
                    )

                results.extend(
                    path
                    for path in branch_results
                    if path not in results
                )
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return results

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Feed input parameters into different branches of logic

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param data: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        branch_function = self.branch_concurrently if settings.allow_threading else self.branch_sequentially
        results = branch_function(
            profile=profile,
            process_identifier=process_identifier,
            work_directory=work_directory,
            data=data,
            previous_operations=previous_operations,
            metadata=metadata
        )
        return results


@dataclasses.dataclass(unsafe_hash=True)
class LoadOperation(ProfileOperation[typing.Sequence[pathlib.Path], typing.Iterator[xarray.Dataset]]):
    """
    An operation that loads data within paths into a single xarray dataset
    """
    load_arguments: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.LOAD

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ''
        return f"{prefix}Load files into python"

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> xarray.Dataset:
        if not data:
            raise ValueError(f"No files were given to load within a load operation in '{profile}'")

        from post_processing.utilities.netcdf import load_netcdf

        try:
            data: xarray.Dataset = load_netcdf(data, **self.load_arguments)
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception

        return data


@dataclasses.dataclass
class OnEachOperation(
    ProfileOperation[typing.Union[typing.Iterable[InputType]], typing.Union[typing.Iterable[OutputType]]]
):
    """
    An operation that applies each function to each input separately and returns the combined results

    Differs from Branch in that each operation is performed on each input in a vacuum rather than each operation
    being performed on the set of input at once
    """

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.ON_EACH

    def __post_init__(self):
        for operation_index, operation in enumerate(self.on_each):
            if isinstance(operation, typing.Mapping):
                operation = load_operation(specification=operation)
                self.on_each[operation_index] = operation
            elif not isinstance(operation, ProfileOperation):
                raise ValueError(
                    f"Encountered an invalid sub-operation for a {self.__class__.__qualname__} - item "
                    f"{operation_index}  holds a '{type(operation)}', which cannot "
                    f"be converted into a {ProfileOperation.__qualname__}"
                )

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Union[typing.Iterator[InputType], typing.Iterable[InputType]],
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Union[typing.Iterator[OutputType], typing.Iterable[OutputType]]:
        try:
            results: typing.Sequence[OutputType] = fan_out_operations(
                operations=self.on_each,
                profile=profile,
                process_identifier=process_identifier,
                work_directory=work_directory,
                data=data,
                previous_operations=previous_operations,
                metadata=metadata,
                thread_count=self.thread_count,
            )
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception
        return results

    def __hash__(self):
        try:
            parent_hash = super().__hash__()
        except:
            parent_hash = 0

        values_to_hash = tuple([parent_hash, *[hash(operation) for operation in self.on_each]])
        return hash(values_to_hash)

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ''
        descriptions: typing.Sequence[str] = list(map(str, self.on_each))
        longest_line: int = max(max(map(len, list(description.splitlines()))) for description in descriptions)
        operation_descriptions: typing.Sequence[str] = [
            (
                f"{'-' * (longest_line + 5)}{os.linesep}"
                f"{operation}{os.linesep}"
                f"{'-' * (longest_line + 5)}{os.linesep}"
            )
            for operation in self.on_each
        ]
        return (
            f"{prefix}Perform the following on each file and each file alone:{os.linesep * 2}"
            f"{os.linesep.join(operation_descriptions)}"
        )

    on_each: typing.List[ProfileOperation] = dataclasses.field(default_factory=list)
    thread_count: int = dataclasses.field(default_factory=lambda: settings.maximum_additional_threads)


@dataclasses.dataclass
class AnomalyOperation(NCOOperation):
    """
    Attaches anomaly variables to netcdf files based on thresholds
    """
    variable_name: str
    thresholds: typing.List["anomaly.ThresholdDefinition"]
    default_score: int
    output_pattern: str
    time_variable: str = dataclasses.field(default="time")
    dimension_names: typing.Union[str, typing.Sequence[str]] = dataclasses.field(default='feature_id')
    output_variable_name: str = dataclasses.field(default="streamflow_anomaly")
    anomaly_metadata: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    encoding: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ''
        return (
            f"{prefix}Create anomaly categories across the following thresholds:{os.linesep*2}"
            f"    - {(os.linesep + '    - ').join(map(str, self.thresholds))}{os.linesep*2}"
            f"With a default category of {self.default_score}, matching on {self.dimension_names} across {self.time_variable}{os.linesep}"
            f"And saving to paths like: {self.output_pattern}"
        )

    def __hash__(self):
        return hash((
            self.variable_name,
            *self.thresholds,
            self.default_score,
            self.output_pattern,
            self.time_variable,
            self.dimension_names,
            self.output_variable_name,
            *[pair for pair in self.anomaly_metadata.items()],
            *[pair for pair in self.encoding.items()],
        ))

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.ANOMALY

    def __post_init__(self):
        if len(self.thresholds) == 0:
            raise ValueError(
                f"At least one threshold must be specified for an {self.__class__.__qualname__}"
            )
        from post_processing.transform.anomaly import ThresholdDefinition
        for threshold_index, threshold in enumerate(self.thresholds):
            if isinstance(threshold, typing.Mapping):
                threshold = ThresholdDefinition(**threshold)
                self.thresholds[threshold_index] = threshold
            elif not isinstance(threshold, ThresholdDefinition):
                raise TypeError(
                    f"'{threshold}' (type={type(threshold)}) is not a valid value in "
                    f"{self.__class__.__qualname__}.thresholds. It must be indicated via a Mapping or a "
                    f"ThresholdDefinition"
                )

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: typing.List["ProfileOperation"],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """

        """
        from post_processing.transform import anomaly
        output_paths: typing.List[pathlib.Path] = []
        frame_pattern: re.Pattern = re.compile(
            r"(?P<model>[a-zA-Z]+)\."
            r"(?P<configuration>\w+)\."
            r"(?P<model_output_type>\w+)(_(?P<member>\d+))?\."
            r"(?P<frame>f\d+|tm\d+)\."
            r"(?P<region>[a-zA-Z]+)\."
            r"nc"
        )
        try:
            for input_path in data:
                file_specific_metadata: typing.Dict[str, typing.Any] = {
                    **metadata
                }
                identification_match: typing.Optional[re.Match] = frame_pattern.search(input_path.name)
                if identification_match:
                    identifiers: typing.Mapping[str, typing.Any] = identification_match.groupdict()
                    file_specific_metadata.update(identifiers)

                desired_path: pathlib.Path = work_directory / self.output_pattern.format(**file_specific_metadata)
                anomaly.calculate_anomaly(
                    input_path=input_path,
                    output_path=desired_path,
                    variable_to_bin=self.variable_name,
                    thresholds=self.thresholds,
                    default_score=self.default_score,
                    time_variable=self.time_variable,
                    dimension_names=self.dimension_names,
                    output_variable_name=self.output_variable_name,
                    field_metadata=self.anomaly_metadata,
                    encoding=self.encoding,
                    operational_metadata=file_specific_metadata,
                )
                if not desired_path.exists():
                    raise OSError(f"There is no generated anomaly data at {desired_path}")
                output_paths.append(desired_path)
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception
        return output_paths


@dataclasses.dataclass
class FunctionOperation(ProfileOperation[InputType, OutputType]):
    """
    Pass input through python code by passing preconfigured keyword arguments and mapped variables
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.FUNCTION

    function_name: str
    kwargs: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    argument_mapping: typing.Dict[str, str] = dataclasses.field(default_factory=dict)
    _function: PythonHandler[InputType, OutputType] = member(default=None)

    def __hash__(self):
        try:
            parent_hash = super().__hash__()
        except:
            parent_hash = 0

        values_to_hash: typing.Tuple[typing.Hashable, ...] = tuple([
            parent_hash,
            *[(key, value) for key, value in self.argument_mapping.items()],
            *[(key, value) for key, value in self.kwargs.items()]
        ])
        return hash(values_to_hash)

    def __str__(self):
        prefix: str = f"{self.operation_id}: " if self.operation_id else ""
        mapping_description: str = f',{os.linesep}    '.join(
            map(
                lambda pair: f"{pair[0]}={pair[1]}",
                {
                    **self.kwargs,
                    **self.argument_mapping
                }.items()
            )
        )
        function_description: str = f"{self.function_name}({os.linesep}    {mapping_description}{os.linesep})"
        return f"{prefix}Call {function_description}"

    def __post_init__(self):
        self._function = get_function_by_name(function_name=self.function_name)

    def __call__(
        self,
        profile: "Profile",
        process_identifier: str,
        work_directory: pathlib.Path,
        data: InputType,
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any],
    ) -> OutputType:
        from post_processing.utilities.common import get_property_values

        mapping_source: typing.Dict[str, typing.Any] = {
            **get_property_values(settings),
            **metadata,
            'profile': profile,
            'process_identifier': process_identifier,
            'work_directory': work_directory,
            'data': data
        }

        if isinstance(data, pathlib.Path):
            mapping_source['input_name'] = data.name
            mapping_source['input_stem'] = data.stem
            mapping_source['input_suffix'] = data.suffix

        kwargs = {
            key: value.format(**mapping_source) if isinstance(value, str) else value
            for key, value in self.kwargs.items()
        }

        args: typing.Dict[str, typing.Any] = {}

        for target_variable_name, available_variable_name in self.argument_mapping.items():
            if available_variable_name in mapping_source.keys():
                mapped_value = mapping_source[available_variable_name]
                if callable(mapped_value):
                    mapped_value = mapped_value()
                if isinstance(mapped_value, str):
                    mapped_value = mapped_value.format(**mapping_source)
                args[target_variable_name] = mapped_value
            elif hasattr(self, available_variable_name):
                target_variable = getattr(self, available_variable_name)
                if callable(target_variable):
                    target_variable = target_variable()
                if isinstance(target_variable, str):
                    target_variable = target_variable.format(**mapping_source)
                args[target_variable_name] = target_variable
            else:
                raise KeyError(
                    f"There is not a variable available named '{available_variable_name}' to map to "
                    f"'{target_variable_name}' for '{self.function_name}'. Should it have been a 'kwarg' value "
                    f"instead of a mapping?"
                )

        kwargs.update(args)
        try:
            result: OutputType = self._function(**kwargs)
        except Exception as exception:
            if 'failure in' not in str(exception):
                exception.args = (f"Failure in:{os.linesep}{self}{os.linesep}{exception.args[0]}", *exception.args[1:])
            raise exception
        return result


@dataclasses.dataclass
class Profile(BaseModel):
    """
    Defines behavior for how a certain combination of Configuration + Model Output Type + Region
    """
    configuration: Configuration = dataclasses.field()
    output_type: ModelOutputType = dataclasses.field()
    region: Region = dataclasses.field()
    operations: typing.List[ProfileOperation] = dataclasses.field()
    output_file_pattern: str = dataclasses.field(default="nwm.t{date}{cycle}z.{configuration}.{output_type}.{region}.nc")
    member: typing.Optional[int] = dataclasses.field(default=None)
    date_format: str = dataclasses.field(default="%Y%m%d")
    output_directory: typing.Optional[pathlib.Path] = dataclasses.field(default=None)
    intermediate_directory: typing.Optional[pathlib.Path] = dataclasses.field(default_factory=lambda: settings.intermediate_directory)
    source_file: typing.Optional[pathlib.Path] = dataclasses.field(default=None)
    comment: typing.Optional[str] = dataclasses.field(default=None, kw_only=True)

    def __str__(self):
        description = (
            f"{self.output_type.name} generated for {self.configuration.name}"
            f"{' for ensemble ' + str(self.member) + ' ' if self.member is not None else ''} "
            f"across {self.region.name}:"
        )
        operation_description: str = (os.linesep*2).join(map(str, self.operations))

        if self.comment:
            comment: str = (
                f"{os.linesep}{'-' * (max(max(map(len, desc.splitlines())) for desc in operation_description.splitlines()) + 5)}{os.linesep}"
                f"{self.comment}"
            )
        else:
            comment = ''

        return (
            f"{description}{os.linesep}"
            f"{'=' * (len(description) + 5)}{os.linesep}"
            f"{operation_description}{os.linesep}"
            f"{os.linesep}"
            f"{comment}"
        )

    def assign_ids(self):
        initial_id: int = 0
        for operation in self.operations:
            operation.assign_id(parent_id=str(initial_id))
            initial_id += 1

    def __post_init__(self):
        if not len(self.operations):
            raise ValueError(f"{self} must have at least one operation")

        if not isinstance(self.configuration, Configuration):
            if isinstance(self.configuration, str):
                self.configuration = Configuration.from_string(self.configuration)
            else:
                raise ValueError(
                    f"'{self.configuration}' (type={type(self.configuration)}) is not a valid type of configuration"
                )

        if not isinstance(self.output_type, ModelOutputType):
            if isinstance(self.output_type, str):
                self.output_type = ModelOutputType.from_string(self.output_type)
            else:
                raise ValueError(
                    f"'{self.output_type}' (type={type(self.output_type)}) is not a valid output type"
                )

        if not isinstance(self.region, Region):
            if isinstance(self.region, str):
                self.region = Region.from_string(self.region)
            else:
                raise ValueError(
                    f"'{self.region}' (type={type(self.region)}) is not a valid region"
                )

        for operation_index, operation in enumerate(self.operations):
            if isinstance(operation, typing.Mapping):
                operation = load_operation(specification=operation)
                self.operations[operation_index] = operation
            elif not isinstance(operation, ProfileOperation):
                raise ValueError(
                    f"Operation {operation_index} under {self} must be a {ProfileOperation.__qualname__}, "
                    f"but instead received {type(operation)}({operation})"
                )

        first_operation: ProfileOperation = self.operations[0]
        first_operation_type_is_entry: bool = isinstance(
            first_operation,
            (NCOOperation, BranchOperation, EchoOperation, FunctionOperation, LoadOperation, OnEachOperation)
        )
        if not first_operation_type_is_entry or first_operation.operation() == OperationType.INTO_PYTHON:
            raise ValueError(
                f"The first operation in {self} must operate on files, but the first operation was instead "
                f"{type(first_operation).__qualname__}({first_operation})"
            )
        self.assign_ids()

    def run(
        self,
        cycle: typing.Union[str, int],
        files: typing.Sequence[pathlib.Path],
        **additional_metadata
    ) -> typing.Sequence[pathlib.Path]:
        """
        Perform all operations configured for this profile

        :param cycle: The cycle of the data to be evaluated (t00z, t06z, etc)
        :param files: The files to operate on
        :param additional_metadata: Additional values that may be passed to use as metadata for value replacement
        :returns: The list of resultant files
        """
        from post_processing.utilities.netcdf import load_metadata
        input_metadata: typing.Dict[str, typing.Any] = load_metadata(path=files)
        metadata: typing.Dict[str, typing.Any] = {
            **settings.paths,
            **additional_metadata,
            self.output_type.__class__.__name__: str(self.output_type.value),
            self.region.__class__.__name__: self.region.value,
            self.configuration.__class__.__name__: self.configuration.value,
            "member": self.member,
            "cycle": str(cycle).zfill(2)
        }

        input_metadata.update(metadata)
        process_identifier: str = str(hash((
            cycle,
            self.configuration,
            self.output_type,
            self.region,
            self.member,
            *files,
            *self.operations
        )))
        work_directory: pathlib.Path = self.intermediate_directory / process_identifier
        work_directory.mkdir(parents=True, exist_ok=True)

        try:
            previous_operations: typing.List[ProfileOperation] = []
            output: typing.Union[typing.Sequence[pathlib.Path], xarray.Dataset] = call_operations(
                operations=self.operations,
                profile=self,
                process_identifier=process_identifier,
                work_directory=work_directory,
                data=files,
                previous_operations=previous_operations,
                metadata=input_metadata
            )

            if isinstance(output, xarray.Dataset):
                global_output_directory: typing.Optional[pathlib.Path] = settings.output_directory
                has_global_output: bool = global_output_directory is not None and global_output_directory.is_dir()
                has_configured_output: bool = self.output_directory is not None and self.output_directory.is_dir()
                if not (has_global_output or has_configured_output):
                    raise ValueError(
                        f"Cannot save output for {self} for cycle {cycle} - there is no where configured and accessible to write to"
                    )

                filename: str = self.get_output_filename(**input_metadata)

                if has_configured_output:
                    output_directory: pathlib.Path = self.output_directory
                else:
                    output_directory: pathlib.Path = global_output_directory

                output_path: pathlib.Path = output_directory / filename
                output.to_netcdf(output_path)
                return [output_path]
            return output
        finally:
            shutil.rmtree(work_directory)

    def __call__(
        self,
        date: datetime,
        cycle: typing.Union[int, str],
        files: typing.Sequence[pathlib.Path]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Perform all operations configured for this profile

        :param date: The date that the cycle is for
        :param cycle: The cycle of the data to be evaluated (t00z, t06z, etc)
        :param files: The files to operate on
        :returns: The list of resultant files
        """
        return self.run(date=date, cycle=cycle, files=files)

    def visit(
        self,
        operator: typing.Callable[[ProfileOperation], typing.Any],
        condition: typing.Callable[[ProfileOperation], bool] = None
    ) -> None:
        """
        Visit every operation that this profile performs and apply the operator to it if possible and necessary

        :param operator: The action to perform on profile operations
        :param condition: A condition that determines what operations the operator should be called on
        """
        for operation in self.operations:
            operation.visit(operator=operator, condition=condition)

    def get_output_filename(
        self,
        date: typing.Union[str, datetime],
        cycle: typing.Union[int, str],
        **kwargs
    ) -> str:
        """
        Get the name of what the output of this profile should look like

        :param date: The date of the output of the model
        :param cycle: The cycle of the model within the day ['00', '23']
        :param kwargs: Additional keyword arguments
        :return: The output filename
        """
        if isinstance(date, datetime):
            date = date.strftime(self.date_format)

        if isinstance(cycle, int):
            cycle = str(cycle).zfill(2)

        replacement_parameters: typing.Dict[str, typing.Any] = {**kwargs, "cycle": cycle, "date": date}

        replacement_parameters.setdefault('configuration', str(self.configuration.value))
        replacement_parameters.setdefault('output_type', str(self.output_type.value))
        replacement_parameters.setdefault('region', str(self.region.value))

        return self.output_file_pattern.format(
            date=date,
            cycle=cycle,
            **kwargs
        )


def load_profiles(profile_path: typing.Union[str, pathlib.Path] = settings.profile_path) -> typing.Sequence[Profile]:
    """
    Load all available profiles

    :param profile_path: The path to directory that contains all the profiles
    :returns: All available profiles
    """
    if not isinstance(profile_path, pathlib.Path):
        profile_path = pathlib.Path(profile_path)

    profiles: typing.List[Profile] = []

    for directory_member in profile_path.iterdir():
        if not directory_member.is_file():
            LOGGER.debug(f"Not loading {directory_member} since it is not a file")
            continue

        if not directory_member.suffix == ".json":
            LOGGER.debug(f"Not loading {directory_member} since it is not a JSON file")
            continue

        try:
            profile: Profile = Profile.from_json(directory_member)
        except Exception as e:
            if 'return lists of paths' in str(e):
                LOGGER.error(e)
                continue
            raise

        profile.source_file = directory_member
        profiles.append(profile)

    if not profiles:
        raise FileNotFoundError(f"No profiles found in {profile_path}")

    return profiles


def get_function_by_name(
    function_name: str,
    context: typing.Dict[str, typing.Any] = None
) -> typing.Callable:
    """
    Get a function by its qualified name

    Example:
        >>> get_function_by_name("post_processing.netcdf.merge_files")
        <function merge_files at 0x7f550e82c400>
        >>> # Anything imported with `from` or `as` may be shortened
        >>> # Since `from post_processing import netcdf` is defined up top, the following may be used:
        >>> get_function_by_name("netcdf.merge_files")
        <function merge_files at 0x7f550e82c400>
        >>> from post_processing import nco
        >>> get_function_by_name("netcdf.merge_files")
        <function merge_files at 0x7f550e82c400>
        >>> from post_processing.transform import merge_files
        >>> get_function_by_name("merge_files")
        <function merge_files at 0x7f550e82c400>

    :param function_name: The qualified name of the function
    :param context: Where to look for the function - defaults to the global scope
    :return: The function
    """
    if context is None:
        context = globals()

    function_parts: typing.List[str] = [word for word in function_name.split(".") if word]

    if not function_parts:
        raise ValueError(f"'{function_name}' is not a name for a function")

    if len(function_parts) == 1:
        if function_parts[0] not in context:
            raise KeyError(f"'{function_name}' is not defined within the global context")
        function = context[function_parts[0]]
        if not callable(function):
            raise ValueError(f"The object '{function_name}' is not callable")
        return function

    import_path: str = '.'.join(function_parts[:-1])

    module: typing.Optional[types.ModuleType] = None

    for function_part in function_parts[:-1]:
        if module is None:
            if function_part not in context:
                module = importlib.import_module(import_path)
                break
            else:
                module = context[function_part]
        else:
            if isinstance(module, typing.Mapping):
                module = module.get(function_part, None)
                if module is None:
                    module = importlib.import_module(import_path)
                    break
            else:
                module = getattr(module, function_part, None)
                if module is None:
                    module = importlib.import_module(import_path)
                    break

    if isinstance(module, typing.Mapping):
        function: typing.Optional[typing.Callable] = module.get(function_parts[-1], None)
    else:
        function: typing.Optional[typing.Callable] = getattr(module, function_parts[-1], None)

    if function is None:
        raise AttributeError(f"'{function_name}' is not defined within '{import_path}'")

    if not callable(function):
        raise ValueError(f"The object '{function_name}' is not callable")

    return function


def fan_out_operations(
    operations: typing.Iterable[ProfileOperation],
    profile: Profile,
    process_identifier: str,
    work_directory: pathlib.Path,
    data: typing.Union[typing.Iterator[InputType], typing.Iterable[InputType]],
    previous_operations: typing.List[ProfileOperation],
    metadata: typing.Dict[str, typing.Any],
    thread_count: int = 0
) -> typing.Sequence[OutputType]:
    """
    Call each operation on each member from data and return the accumulated results

    :param operations: The operations to perform on each input
    :param profile: The profile that defined this set of operations
    :param process_identifier: The process identifier that defines this set of operations
    :param work_directory: The directory where intermediate values may be written
    :param data: The data to process
    :param previous_operations: The previously processed operations
    :param metadata: The metadata that may be used for purposes like identification
    :param thread_count: How many threads to parallelize across
    :returns: The accumulated results from each series of operations
    """
    results: typing.Sequence[OutputType] = starmap(
        function=call_generic_operations,
        args=[
            {
                "operations": list(operations),
                "profile": profile,
                "process_identifier": process_identifier,
                "work_directory": work_directory,
                "data": data_member,
                "previous_operations": previous_operations,
                "metadata": metadata,
            }
            for data_member in data
        ],
        thread_count=thread_count
    )
    return results


def call_generic_operations(
    operations: typing.Iterable[ProfileOperation],
    profile: Profile,
    process_identifier: str,
    work_directory: pathlib.Path,
    data: InputType,
    previous_operations: typing.List[ProfileOperation],
    metadata: typing.Dict[str, typing.Any]
) -> OutputType:
    current_data = data

    for operation in operations:
        metadata['stage'] = operation.operation_id
        current_data = operation(
            profile=profile,
            process_identifier=process_identifier,
            work_directory=work_directory,
            data=current_data,
            previous_operations=previous_operations,
            metadata=metadata
        )

        if not any(op.operation_id == operation.operation_id for op in previous_operations):
            previous_operations.append(operation)
        else:
            LOGGER.debug(f"Not adding a record of a call to '{operation.operation_id}) {operation.__class__.__qualname__}' - there is already a record")

    return current_data


def call_operations(
    operations: typing.Iterable[ProfileOperation],
    profile: Profile,
    process_identifier: str,
    work_directory: pathlib.Path,
    data: typing.Sequence[pathlib.Path],
    previous_operations: typing.List[ProfileOperation],
    metadata: typing.Dict[str, typing.Any]
) -> typing.Union[typing.Sequence[pathlib.Path], xarray.Dataset]:
    """
    Perform post-processing operations based on parameters defined within a profile

    :param operations: The operations to perform
    :param profile: The profile defining the key parameters of what to do
    :param process_identifier: The identifier of the process to perform
    :param work_directory: The directory to write intermediate output to
    :param data: The data to operate upon (generally file paths at this point)
    :param previous_operations: The list of operations already performed
    :param metadata: The metadata to reference when building up names based on characteristics
    """
    current_data: typing.Union[typing.Sequence[pathlib.Path], xarray.Dataset] = list(data)

    for operation in operations:
        metadata['stage'] = operation.operation_id
        if operation.disable:
            LOGGER.warning(f"{operation.__class__.__qualname__} disabled:{os.linesep}{operation}")
            continue

        current_data = operation(
            profile=profile,
            process_identifier=process_identifier,
            work_directory=work_directory,
            data=current_data,
            previous_operations=previous_operations,
            metadata=metadata
        )

        if not any(op.operation_id == operation.operation_id for op in previous_operations):
            previous_operations.append(operation)
        else:
            LOGGER.warning(f"Not adding a record of a call to '{operation.operation_id}) {operation.__class__.__qualname__}' - there is already a record")

    return current_data


def get_profile(
    manifest: schema.InputManifest,
    profile_path: typing.Union[str, pathlib.Path] = settings.profile_path
) -> typing.Sequence[Profile]:
    """
    Get 0 or more profiles that may operate on the passed in file

    Returning 0 profiles means that there weren't any profiles for this type of file

    :param manifest: A gathering of all metadata for what will be processed
    :param profile_path: The path to where all profiles are stored
    :returns: 0 or more profiles that may operate on the file path that is passed in
    """
    if isinstance(profile_path, str):
        profile_path = pathlib.Path(profile_path)

    profiles: typing.List[Profile] = [
        profile
        for profile in load_profiles(profile_path=profile_path)
        if profile.configuration == manifest.configuration
           and profile.region == manifest.region
           and profile.member == manifest.member
           and profile.output_type == manifest.output_type
    ]

    return profiles


@functools.cache
def load_profile(source: typing.Union[pathlib.Path, str, typing.Dict[str, typing.Any]]) -> Profile:
    """
    Deserialize a Profile

    :param source: The source to deserialize from, whether it be a path to a file or the raw data itself
    :return: The deserialized Profile
    """
    if isinstance(source, str):
        import json
        try:
            source = json.loads(source)
        except json.decoder.JSONDecodeError:
            source = pathlib.Path(source)

    if isinstance(source, pathlib.Path):
        import json
        source = json.loads(source.read_text())

    if not isinstance(source, dict):
        raise TypeError(f"Cannot convert a '{type(source)}' to Profile")

    profile_fields = get_fields(Profile)

    required_fields: typing.List[dataclasses.Field] = [
        field
        for field in profile_fields
        if field.default == dataclasses.MISSING and field.default_factory == dataclasses.MISSING
    ]

    missing_field_descriptions: typing.List[str] = [
        f"{field.name}: {field.type}"
        for field in required_fields
        if field.name not in source
    ]

    if missing_field_descriptions:
        raise KeyError(
            f"Cannot create a {Profile.__qualname__} - the following fields are missing: "
            f"{', '.join(missing_field_descriptions)}"
        )

    constructor_parameters: typing.Dict[str, typing.Any] = {
        field.name: source[field.name]
        for field in profile_fields
        if field.name in source
    }

    profile = Profile(**constructor_parameters)
    return profile


def load_operation(specification: typing.Mapping[str, typing.Any]) -> ProfileOperation:
    """
    Deserialize a ProfileOperation dictionary into a ProfileOperation object

    :param specification: A dictionary containing the variables required to create a ProfileOperation
    :returns: A ProfileOperation object deserialized from the given specification
    """
    operation_types: typing.Dict[OperationType, typing.Type[ProfileOperation]] = get_profile_operation_types()

    if OPERATION_KEY in specification:
        operation_type: OperationType = OperationType(specification[OPERATION_KEY])
    else:
        raise KeyError(f"There is no '{OPERATION_KEY}' key in an encountered ProfileOperation dictionary")

    operation_class: typing.Type[ProfileOperation] = operation_types[operation_type]

    fields: typing.Sequence[dataclasses.Field] = get_fields(operation_class)

    required_fields, optional_fields = partition(
        lambda field: field.default == dataclasses.MISSING and field.default_factory == dataclasses.MISSING,
        fields
    )

    missing_fields: typing.List[str] = [
        field.name
        for field in required_fields
        if field.name not in specification
           and field.init is True
    ]

    if missing_fields:
        raise KeyError(
            f"Cannot deserialize a {operation_class.__qualname__} as the following "
            f"keys are missing: {', '.join(missing_fields)}"
        )

    extra_fields: typing.List[str] = [
        f"{key}: {type(specification[key])}"
        for key in specification.keys()
        if key != 'operation' and
           not any([field.name == key for field in fields])
    ]

    if extra_fields:
        message = (
            f"The following extra fields were encountered in the specification for a {operation_class.__qualname__}: "
            f"{', '.join(extra_fields)}"
        )
        LOGGER.debug(message)

    constructor_arguments: typing.Dict[str, typing.Any] = {
        field.name: specification[field.name]
        for field in fields
        if field.name in specification
    }

    return operation_class(**constructor_arguments)


@functools.cache
def get_profile_operation_types(
    root: typing.Type[ProfileOperation] = ProfileOperation
) -> typing.Dict[OperationType, typing.Type[ProfileOperation]]:
    """
    Get all the concrete operation types

    :param root: The base object whose concrete subclasses to look for
    :returns: All non-abstract implementations of the root ProfileOperation
    """
    subclasses: typing.Dict[typing.Optional[OperationType], typing.Type[ProfileOperation]] = {
        subclass.operation(): subclass
        for subclass in root.__subclasses__()
    }

    immediate_subclasses: typing.Sequence[typing.Type[ProfileOperation]] = list(subclasses.values())

    for subclass in immediate_subclasses:
        sub_subclasses: typing.Dict[OperationType, typing.Type[ProfileOperation]] = get_profile_operation_types(subclass)
        preexisting_operations: typing.List[typing.Tuple[OperationType, typing.Type[ProfileOperation], typing.Type[ProfileOperation]]] = []

        for operation_type, operation_class in sub_subclasses.items():
            conflicting_operation: typing.Type[ProfileOperation] = subclasses.get(operation_type)
            if conflicting_operation is not None:
                preexisting_operations.append((operation_type, operation_class, conflicting_operation))

        if preexisting_operations:
            conflicting_type_messages: typing.List[str] = [
                f"{operation_type}: {conflicting_type.__qualname__} vs {preexisting_type.__qualname__}"
                for operation_type, conflicting_type, preexisting_type in preexisting_operations
            ]
            message = (
                f"Cannot load in Profile Operation Types - there are conflicts on the following types and there "
                f"can only be one ProfileOperation class per operation type: {', '.join(conflicting_type_messages)}"
            )
            raise KeyError(message)

        subclasses.update(sub_subclasses)

    subclasses = {
        operation_type: subclass
        for operation_type, subclass in subclasses.items()
        if operation_type is not None
           and subclass is not None
           and operation_type != OperationType.NCO
    }
    return subclasses
