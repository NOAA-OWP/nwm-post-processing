#!/usr/bin/env python3
"""
Objects and functions used to allow a user to filter what files move on to a future operation
"""
import pathlib
import typing
import collections.abc as generic
import logging
import dataclasses
import enum
import operator
from functools import cache
import os

from post_processing.nwm_file import NWMFile
from post_processing.utilities.logging import get_logger
from post_processing.schema import base as base_schema
from post_processing.schema import profile as profiles

LOGGER: logging.Logger = get_logger(__file__)

class FilterOperation(enum.StrEnum):
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    EQUAL = "=="
    NOT_EQUAL = "!="
    IN = "in"
    CONTAINS = "contains"

    @classmethod
    def from_string(cls, string: str):
        lowercase_string: str = string.lower()
        for member in cls:
            if member.name.lower() == lowercase_string or member.value.lower() == lowercase_string:
                return member
        raise ValueError(f"There are no filter operations that match '{string}'")

class FileField(enum.StrEnum):
    CONFIGURATION = "configuration"
    MODEL_OUTPUT_TYPE = "model_output_type"
    TMINUS = "tminus"
    FRAME = "frame"
    REGION = "region"
    SLICE = "slice"
    CYCLE = "cycle"

def in_(first: typing.Any, second: generic.Container) -> bool:
    return first in second

@cache
def filter_function(operation: FilterOperation) -> generic.Callable[[typing.Any, typing.Any], bool]:
    match operation:
        case FilterOperation.GREATER_THAN:
            return operator.gt
        case FilterOperation.GREATER_THAN_OR_EQUAL:
            return operator.ge
        case FilterOperation.LESS_THAN:
            return operator.lt
        case FilterOperation.LESS_THAN_OR_EQUAL:
            return operator.ne
        case FilterOperation.EQUAL:
            return operator.eq
        case FilterOperation.NOT_EQUAL:
            return operator.ne
        case FilterOperation.IN:
            return in_
        case FilterOperation.CONTAINS:
            return operator.contains
    raise KeyError(f"There is no filter function for the operation named '{operation}'")


@typing.runtime_checkable
class Condition(typing.Protocol):
    def __call__(self, values: generic.Mapping[str, typing.Any]) -> bool:
        ...


@base_schema.postprocessing_model()
class FilterCondition(base_schema.BaseModel):
    field: FileField
    condition: FilterOperation
    value: typing.Any
    _function: generic.Callable[[typing.Any, typing.Any], bool] = base_schema.member()

    @property
    def function(self) -> generic.Callable[[typing.Any, typing.Any], bool]:
        if self._function is None:
            self._function = filter_function(self.field)
        return self._function

    def __call__(self, values: generic.Mapping[str, typing.Any]):
        if self.field not in values:
            raise KeyError(
                f"Cannot filter based upon `{self.field.value}` {self.condition.value} '{self.value}' - "
                f"there is no {self.field.value} field in the given values. Available fields:{os.linesep}"
                f"    - {(os.linesep + '    - ').join(values.keys())}"
            )
        value = values.get(self.field)

        try:
            return self.function(self.value, value)
        except TypeError as type_error:
            raise TypeError(
                f"Cannot perform '{self.condition}' on '{self.field}' - "
                f"values are incompatible. field {self.field} is a {type(value)} and the condition is a {type(self.value)}"
            ) from type_error

@base_schema.postprocessing_model()
class OrCondition(base_schema.BaseModel):
    condition: typing.Literal["or"]
    conditions: list[Condition]

    def __call__(self, values: generic.Mapping[str, typing.Any]) -> bool:
        return any(condition(values) for condition in self.conditions)

@base_schema.postprocessing_model()
class AndCondition(base_schema.BaseModel):
    condition: typing.Literal["and"]
    conditions: list[Condition]

    def __call__(self, values: generic.Mapping[str, typing.Any]) -> bool:
        return all(condition(values) for condition in self.conditions)

@base_schema.postprocessing_model()
class FilterOperation(profiles.PathToPathOperation):
    def _validate(self):
        #TODO: write logic here to handle FilterCondition, AndCondition, and OrCondition objects
        raise NotImplementedError(
            f"The {self.__class__.__qualname__} has not been fully implemented and is not yet ready for use."
        )

    def __call__(
        self,
        profile: profiles.Profile,
        process_identifier: str,
        work_directory: pathlib.Path,
        data: generic.Sequence[pathlib.Path],
        previous_operations: list[profiles.ProfileOperation],
        metadata: dict[str, typing.Any]
    ) -> generic.Sequence[pathlib.Path]:
        valid_paths: list[pathlib.Path] = []

        for path in data:
            file_specific_metadata: dict[str, typing.Any] = metadata.copy()
            filename_metadata: NWMFile = NWMFile.parse(path)
            for file_field in FileField:
                if not hasattr(filename_metadata, file_field.value):
                    raise RuntimeError(
                        f"File field mismatch - {type(filename_metadata)} objects don't have a '{file_field.value}' field"
                    )
                file_specific_metadata[file_field.value] = getattr(filename_metadata, file_field.value)
            if self.on(file_specific_metadata):
                valid_paths.append(path)

        if not valid_paths:
            LOGGER.warning(f"{self} filtered out all incoming paths - no data will be processed here on out")

        return valid_paths

    @classmethod
    def operation(cls) -> profiles.OperationType:
        return profiles.OperationType.FILE_FILTER

    on: Condition
