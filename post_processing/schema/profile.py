"""
Defines a profile for how a certain combination of Configuration + Model Output Type + Region should behave
"""
import abc
import importlib
import itertools
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

from post_processing.utilities.common import starmap
from post_processing.utilities.common import partition
from post_processing.utilities.common import get_template_variables
from post_processing.utilities.common import to_json
from post_processing.configuration import settings

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)
InputType = typing.TypeVar("InputType")
OutputType = typing.TypeVar("OutputType")
OPERATION_KEY: typing.Final[str] = "operation"
"""The key for a ProfileOperation dictionary stating what the ProfileOperation is supposed to do"""

TEXT_FORMAT_VARIABLE_PATTERN: re.Pattern = re.compile(r"\{(?P<name>[\w_]+)(:[^}]*)?}")

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


class InPlaceOperationMixin:
    """
    A mixin for data classes adding a field determining whether the files should be operated on in place or into a new file
    """
    in_place: bool = dataclasses.field(default=False)
    """
    Dictates whether the changes made to the data should be applied in place or if the changes should go into a new file
    """
    output_pattern: typing.Optional[str] = dataclasses.field(default=None)
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

    def render_output_name(self, **context: typing.Any) -> str:
        """
        Attempt to render a filename from the output pattern

        Example:
            >>> instance = InPlaceOperationMixin(in_place=False, output_pattern="{in_place}_{one}_{two}.nc")
            >>> instance.render_output_name(one="three", two="four")
            False_three_four.nc

        :param context: key-value pairs describing variable values that might be needed to fulfill variables within the template
        :returns: The formatted output name
        """
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
    @classmethod
    @abc.abstractmethod
    def operation(cls) -> OperationType:
        """Get the type of operation the ProfileOperation fulfills"""

    def __str__(self):
        return self.operation().replace('_', ' ').title()

    def __hash__(self):
        values_to_hash: typing.Tuple[str, ...] = (self.__class__.__name__, to_json(self))
        return hash(values_to_hash)

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


class EchoOperation(ProfileOperation[InputType, InputType]):
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
            self.level = logging.getLevelName(self.level)

    message: str
    level: typing.Union[int, str] = dataclasses.field(default=logging.INFO)
    logger_name: typing.Optional[str]
    _logger: logging.Logger = member(default_factory=lambda: LOGGER)


class RaiseOperation(ProfileOperation[InputType, InputType]):
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


@dataclasses.dataclass(unsafe_hash=True)
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
        files: typing.Sequence[pathlib.Path],
        previous_operations: typing.List[ProfileOperation],
        metadata: typing.Dict[str, typing.Any]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Split each received NWMFile into other files based on the collection of masks. There should be len(masks) * len(files) returned files

        :param profile: The profile that called for this operation
        :param process_identifier: An identifier tying together other output for this post processing task
        :param work_directory: Where intermediate products may be saved
        :param files: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The given paths
        """

        subset_arguments: typing.List[typing.Dict[str, typing.Any]] = [
            {
                "input_file": input_file,
                "mask": mask,
                "coordinate": self.dimension,
                "work_directory": work_directory
            }
            for input_file, (mask, identifiers) in itertools.product(files, self._identifier_mapping.items())
        ]

        from post_processing.transform import subset_file_into_file_by_mask
        subset_paths: typing.Sequence[pathlib.Path] = starmap(
            function=subset_file_into_file_by_mask,
            args=subset_arguments,
            threaded=True
        )

        arguments_for_each: typing.List[typing.Dict[str, typing.Any]] = [
            {
                "operations": self.each,
                "profile": profile,
                "process_identifier": process_identifier,
                "work_directory": work_directory,
                "data": [subset_path],
                "previous_operations": list(previous_operations),
                "metadata": metadata.copy()
            }
            for subset_path in subset_paths
        ]

        starmap(function=call_operations, args=arguments_for_each, threaded=True)
        return files

    def __post_init__(self):
        missing_masks: typing.List[pathlib.Path] = [path for path in self.masks if not path.is_file()]

        assert not any(missing_masks), f"A {self.__class__.__name__} is missing a required mask(s): {missing_masks}"

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

    masks: typing.List[pathlib.Path] = dataclasses.field()
    identifier_pattern: str = dataclasses.field()
    output_pattern: str = dataclasses.field()
    each: typing.List[typing.Union[ProfileOperation]]
    dimension: str = dataclasses.field(default="feature_id")
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
        :param files: The files to operate on
        :param metadata: Metadata provided from previous operations that may be used as helpful hints
        :returns: The Paths for each created object
        """
        filename: str = self.file_name_pattern.format_map(metadata)
        output_path: pathlib.Path = work_directory / filename

        from post_processing.transform import merge_files_into_file
        merge_files_into_file(files=data, output_file=output_path)
        return [output_path]

    file_name_pattern: str = dataclasses.field()

@dataclasses.dataclass(unsafe_hash=True)
class DropOperation(NCOOperation, InPlaceOperationMixin):
    """
    Tells how to drop variables
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.DROP

    def __str__(self):
        if self.exclude:
            return f"Drop all data variables except {', '.join(self.fields)}"
        return (
            f"{self.operation().replace('_', ' ').title()}: "
            f"Drop the following data variables: {', '.join(self.fields)}"
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

        arguments: typing.List[typing.Dict[str, typing.Any]] = []

        for file in data:
            arguments_for_file: typing.Dict[str, typing.Any] = {
                "input_file": file,
                "output_file": self.get_output_path(
                    work_directory=work_directory,
                    input_path=file,
                    process_identifier=process_identifier,
                    **metadata
                ),
                "variables": self.fields,
            }

            arguments.append(arguments_for_file)

        starmap(
            function=drop_function,
            args=arguments
        )

        return data

    fields: typing.List[str] = dataclasses.field()
    exclude: bool = dataclasses.field(default=False)

@dataclasses.dataclass(unsafe_hash=True)
class RenameOperation(NCOOperation, InPlaceOperationMixin):
    """
    Tells how to rename variables or dimensions
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.RENAME

    def __str__(self):
        rename_mapping: typing.Sequence[str] = [
            f"{key} to {value}"
            for key, value in self.mapping.items()
        ]

        return f"{self.operation().replace('_', ' ').title()}: Renaming {', '.join(rename_mapping)}"

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
        # TODO: Check if this is iterating right and efficiently
        #  - can we rename multiple times in one call?
        #  - Should we starmap all files at once?
        for file in data:
            args: typing.List[typing.Dict[str, typing.Union[str, pathlib.Path]]] = [
                {
                    "input_file": file,
                    "old_name": original_name,
                    "new_name": new_name,
                    "output_file": self.get_output_path(
                        work_directory=work_directory,
                        input_path=file,
                        process_identifier=process_identifier,
                        **metadata
                    )
                }
                for original_name, new_name in self.mapping.items()
            ]
            starmap(
                function=nco.rename_variable,
                args=args
            )
        return data

    mapping: typing.Dict[str, str] = dataclasses.field()

@dataclasses.dataclass(unsafe_hash=True)
class AttributeOperation(NCOOperation, InPlaceOperationMixin):
    """
    Tells how to add, modify, or remove attributes on variables or globally
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.ATTRIBUTE

    def __str__(self):
        description = (
            f"{self.operation().replace('_', ' ').title()}: "
            f"{self.mode.replace('_', ' ').title()} the {self.attribute_name} attribute value"
        )

        if self.field.lower().strip() == "global":
            description += " in the global scope "
        else:
            description += f" on {self.field} "

        description += f" to be {self.attribute_value} (type={self.attribute_type})"
        return description


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

        starmap(
            function=nco.add_or_modify_attribute,
            args=arguments,
            threaded=True
        )

        return data

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
        return (
            f"{self.operation().replace('_', ' ').title()}: Save files to {self.directory} with the "
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
        saved_files: typing.List[pathlib.Path] = []
        for file in data:
            file_specific_metadata: typing.Dict[str, typing.Any] = dict(metadata)

            if self._compiled_pattern:
                matching_identifiers: typing.Optional[re.Match] = self._compiled_pattern.search(file.name)

                if matching_identifiers:
                    file_specific_metadata.update(matching_identifiers.groupdict())
                else:
                    LOGGER.warning(
                        f"No identifiers were found in '{file.name}' with the pattern: '{self.identifier_pattern}'"
                    )

            filename: str = self.filename_pattern.format(**file_specific_metadata)
            path: pathlib.Path = self.directory / filename
            shutil.copy(file, path)
            saved_files.append(path)
            LOGGER.debug(f"Wrote {file} to {path}")
        return saved_files

    directory: pathlib.Path = dataclasses.field()
    filename_pattern: str
    identifier_pattern: typing.Optional[str] = dataclasses.field(default=None)
    _compiled_pattern: typing.Optional[re.Pattern] = member(default=None)

@dataclasses.dataclass(unsafe_hash=True)
class BranchOperation(ProfileOperation[InputType, typing.Sequence[pathlib.Path]]):
    """
    An operation that lets you feed input data through multiple mutually exclusive operations
    """
    branches: typing.Dict[str, typing.List[ProfileOperation]] = dataclasses.field()

    def __str__(self):
        return f"{self.operation().replace('_', ' ').title()} -> [{', '.join(self.branches.keys())}]"

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
            if not isinstance(branch_logic[-1], (NCOOperation, OutOfPythonOperation)):
                invalid_ends_names.append(branch_name)

        if invalid_ends_names:
            message = (
                f"Received invalid branching logic within a {self.__class__.__qualname__} - "
                f"branches must end in operations that return lists of paths. Invalid branches: "
                f"{', '.join(invalid_ends_names)}"
            )
            raise ValueError(message)


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
        import concurrent.futures
        future_results: typing.Dict[str, concurrent.futures.Future[typing.Sequence[pathlib.Path]]] = {}

        results: typing.List[pathlib.Path] = []
        failing_branches: typing.List[Exception] = []

        with concurrent.futures.ThreadPoolExecutor() as executor:
            for branch_name, branch_logic in self.branches.items():
                future_result = executor.submit(
                    call_operations,
                    operations=branch_logic,
                    profile=profile,
                    process_identifier=process_identifier,
                    work_directory=work_directory,
                    data=data,
                    previous_operations=previous_operations,
                    metadata=metadata.copy()
                )
                future_results[branch_name] = future_result

            while future_results:
                branch_name, future_result = future_results.popitem()
                try:
                    returned_paths: typing.Sequence[pathlib.Path] = future_result.result(timeout=1)
                    preexisting_paths, new_paths = partition(lambda path: path in results, returned_paths)
                    if preexisting_paths:
                        LOGGER.warning(
                            f"Processing from the '{branch_name}' branch in '{profile}' for {metadata['cycle']}z on "
                            f"{metadata['date']} encountered duplicate results at: "
                            f"{', '.join(map(str, preexisting_paths))}"
                        )
                    results.extend(new_paths)
                except concurrent.futures.TimeoutError:
                    future_results[branch_name] = future_result
                except BaseException as error:
                    new_error = RuntimeError(
                        f"Could not perform the branching operation named '{branch_name}' "
                        f"from the '{profile}' profile: {error}"
                    )
                    new_error.with_traceback(error.__traceback__)
                    failing_branches.append(new_error)

        if failing_branches:
            raise ExceptionGroup(
                f"One or more branches failed to process for {profile} on cycle {metadata['cycle']}z on "
                f"{metadata['date']}",
                failing_branches
            )
        return results

@dataclasses.dataclass(unsafe_hash=True)
class LoadOperation(ProfileOperation[typing.Sequence[pathlib.Path], xarray.Dataset]):
    """
    An operation that loads data within paths into a single xarray dataset
    """
    load_arguments: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.LOAD

    def __str__(self):
        return f"{self.operation().replace('_', ' ').title()}: Load files into python"

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
        if len(data) == 1:
            return xarray.open_dataset(data[0])
        # Your IDE may complain about the `data` parameter - it is a false positive. A sequence of paths is fine
        return xarray.open_mfdataset(data, combine='by_coords', **self.load_arguments)

@dataclasses.dataclass(unsafe_hash=True)
class PythonOperation(ProfileOperation[InputType, OutputType], abc.ABC):
    """
    Base class for operations implemented via python
    """
    function_name: str
    kwargs: typing.Dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    _function: PythonHandler[InputType, OutputType] = member(default=None)

    def __str__(self):
        return f"{self.operation().replace('_', ' ').title()}: {self.function_name}"

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
        result: OutputType = self._function(
            profile,
            process_identifier,
            work_directory,
            data,
            previous_operations,
            metadata,
            **self.kwargs
        )
        return result

@dataclasses.dataclass(unsafe_hash=True)
class IntoPythonOperation(PythonOperation[typing.Sequence[pathlib.Path], xarray.Dataset]):
    """
    An operation that transforms raw files into python objects
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.INTO_PYTHON

@dataclasses.dataclass(unsafe_hash=True)
class ToPythonOperation(PythonOperation[xarray.Dataset, xarray.Dataset]):
    """
    An operation that transforms python objects and returns python objects
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.TO_PYTHON


@dataclasses.dataclass(unsafe_hash=True)
class OutOfPythonOperation(PythonOperation[xarray.Dataset, typing.Sequence[pathlib.Path]]):
    """
    An operation that transforms python objects and returns paths to objects
    """
    @classmethod
    def operation(cls) -> OperationType:
        return OperationType.OUT_OF_PYTHON


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

    def __str__(self):
        description = (
            f"{self.output_type} generated for {self.configuration}"
            f"{' for ensemble ' + str(self.member) + ' ' if self.member is not None else ''}"
            f"across {self.region}"
        )
        return description

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
        if not isinstance(first_operation, (NCOOperation, BranchOperation)) or first_operation.operation() == OperationType.INTO_PYTHON:
            raise ValueError(
                f"The first operation in {self} must operate on files, but the first operation was instead "
                f"{type(first_operation)}({first_operation})"
            )

    def __call__(
        self,
        date: datetime,
        cycle: typing.Union[int, str],
        files: typing.Sequence[pathlib.Path]
    ) -> typing.Sequence[pathlib.Path]:
        """
        Perform all operations configured for this profile

        :param files: The files to operate on
        :returns: The list of resultant files
        """
        metadata: typing.Dict[str, typing.Any] = {
            self.output_type.__class__.__name__: str(self.output_type.value),
            self.region.__class__.__name__: self.region.value,
            self.configuration.__class__.__name__: self.configuration.value,
            "member": self.member,
            "cycle": str(cycle).zfill(2),
            "date": date.strftime(self.date_format),
        }
        process_identifier: str = str(hash((
            date,
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
                metadata=metadata
            )

            if isinstance(output, xarray.Dataset):
                global_output_directory: typing.Optional[pathlib.Path] = settings.output_directory
                has_global_output: bool = global_output_directory is not None and global_output_directory.is_dir()
                has_configured_output: bool = self.output_directory is not None and self.output_directory.is_dir()
                if not (has_global_output or has_configured_output):
                    raise ValueError(
                        f"Cannot save output for {self} for cycle {cycle} on {date.strftime(self.date_format)} - "
                        f"there is no where configured and accessible to write to"
                    )

                filename: str = self.get_output_filename(**metadata)

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
        current_data = operation(
            profile=profile,
            process_identifier=process_identifier,
            work_directory=work_directory,
            data=current_data,
            previous_operations=previous_operations,
            metadata=metadata
        )

        previous_operations.append(operation)

    return current_data


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
        LOGGER.warning(message)

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
    subclasses: typing.Dict[OperationType, typing.Type[ProfileOperation]] = {
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
           and operation_type != OperationType.NCO
    }
    return subclasses

