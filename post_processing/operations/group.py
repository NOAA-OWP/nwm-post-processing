"""
Defines a ProfileOperation that is used to groups inputs by lead for smaller operations
"""
import os
import typing
import collections.abc as generic
import pathlib
import logging
import dataclasses

from datetime import timedelta

from post_processing.nwm_file import NWMFile
from post_processing.utilities.logging import get_logger
from post_processing.schema import profile as base_profile
from post_processing.enums import TimeUnit
from post_processing.schema.base import member
from post_processing.utilities.common import starmap
from post_processing.configuration import settings

LOGGER: logging.Logger = get_logger(__file__)


def get_group_name(
    group_duration: timedelta,
    last_lead: timedelta
) -> str:
    total_hours: int = int(last_lead.total_seconds() / 3600)
    if group_duration == timedelta(hours=24) and total_hours >= 24 and total_hours % 24 == 0:
        return f"day{total_hours // 24}"
    return f"{total_hours}hours"


def generate_groups_by_lead(
    paths: generic.Iterable[pathlib.Path],
    lead_duration: timedelta,
) -> generic.Mapping[str, generic.Sequence[pathlib.Path]]:
    """
    Split NWM file paths into groups based off of a duration of acceptable leads
    """
    groups: dict[str, generic.Sequence[pathlib.Path]] = {}
    files: list[NWMFile] = sorted(map(NWMFile.parse, paths))

    current_group: list[NWMFile] = []
    current_upper_limit: timedelta = lead_duration
    while files:
        current_file: NWMFile = files.pop(0)
        if current_file.lead > current_upper_limit:
            group_name: str = get_group_name(group_duration=lead_duration, last_lead=current_group[-1].lead)
            groups[group_name] = [file.path for file in current_group]
            current_group = []
            current_upper_limit += lead_duration
        current_group.append(current_file)

    if current_group:
        group_name: str = get_group_name(
            group_duration=lead_duration,
            last_lead=current_group[-1].lead
        )
        groups[group_name] = [file.path for file in current_group]

    return groups


@dataclasses.dataclass
class GroupByLeadOperation(base_profile.PathToPathOperation):
    @classmethod
    def operation(cls) -> base_profile.OperationType:
        return base_profile.OperationType.GROUP_BY

    def _validate(self):
        if isinstance(self.time_unit, str):
            self.time_unit = TimeUnit(self.time_unit)

        self._duration = self.time_unit * self.amount_of_time
        errors: list[Exception] = []
        if len(self.on_each) == 0:
            errors.append(
                ValueError(
                    f"Encountered an invalid {self.__class__.__qualname__} operation - there is no configured logic"
                )
            )
        for operation_index, operation in enumerate(self.on_each):
            try:
                if isinstance(operation, typing.Mapping):
                    operation = base_profile.load_operation(specification=operation)
                    self.on_each[operation_index] = operation
                elif not isinstance(operation, base_profile.ProfileOperation):
                    raise ValueError(
                        f"Encountered an invalid sub-operation for a {self.__class__.__qualname__} - item "
                        f"{operation_index}  holds a '{type(operation)}', which cannot "
                        f"be converted into a {base_profile.ProfileOperation.__qualname__}"
                    )
            except Exception as exception:
                errors.append(exception)

        if len(errors) == 1:
            raise errors[0]
        elif errors:
            from post_processing.utilities.common import condense_exceptions
            raise condense_exceptions(
                message=f"Encountered an invalid {self.__class__.__qualname__} operation",
                exceptions=errors
            )

    def __call__(
        self,
        profile: base_profile.Profile,
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: list[base_profile.ProfileOperation],
        metadata: dict[str, typing.Any]
    ) -> generic.Sequence[pathlib.Path]:
        file_groups: generic.Mapping[str, generic.Sequence[pathlib.Path]] = generate_groups_by_lead(
            paths=data,
            lead_duration=self._duration,
        )

        keyword_arguments: list[dict] = []

        for group_name, file_group in file_groups.items():
            group_metadata: dict[str, typing.Any] = {
                "group": group_name,
                **metadata.copy(),
            }
            keyword_arguments.append({
                "operations": self.on_each,
                "profile": profile,
                "process_identifier": process_identifier,
                "work_directory": work_directory,
                "data": file_group,
                "previous_operations": previous_operations.copy(),
                "metadata": group_metadata,
            })

        results: generic.Sequence[list[pathlib.Path]] = starmap(
            function=base_profile.call_generic_operations,
            args=keyword_arguments,
            thread_count=settings.maximum_additional_threads
        )

        flattened_results: generic.Sequence[pathlib.Path] = [
            file
            for group_results in results
            for file in group_results
        ]

        return flattened_results

    def __hash__(self):
        try:
            parent_hash: int = super().__hash__()
        except:
            parent_hash: int = 0

        child_hashes: tuple[int, ...] = tuple(map(hash, self.on_each))
        return hash((
            parent_hash,
            self.time_unit,
            self.amount_of_time,
            *child_hashes,
        ))

    def __str__(self):
        description: str = f"{self.operation_id}: " if self.operation_id else ""
        description += (
            f"Group files into data {self.amount_of_time} {self.time_unit} at a time and perform the following "
            f"operations on each:{os.linesep}"
        )
        each_description: list[str] = list(map(lambda operation: f"    - {operation}", self.on_each))
        description += os.linesep.join(each_description)
        return description

    on_each: list[base_profile.ProfileOperation] = dataclasses.field()
    time_unit: TimeUnit = dataclasses.field()
    amount_of_time: typing.Union[int, float] = dataclasses.field(default=1.0)
    _duration: timedelta = member(default=None)

