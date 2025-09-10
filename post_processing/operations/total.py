"""
The functions and objects used to define the operation to total a variables values over time
"""
import dataclasses
import typing
import collections.abc as generic
import pathlib

from post_processing.utilities import logging
from post_processing.schema import profile as base_profiles
from schema.profile import OperationType

LOGGER: logging.Logger = logging.get_logger(__file__)


@dataclasses.dataclass
class TotalOverTimeOperation(base_profiles.PathToPathOperation, base_profiles.FileOutputMixin):
    """
    Integrates the total value of a variable over time
    """
    @classmethod
    def operation(cls) -> OperationType:
        return base_profiles.OperationType.TOTAL_OVER_TIME

    def __call__(
        self,
        profile: base_profiles.Profile,
        process_identifier: str,
        work_directory: pathlib.Path,
        data: typing.Sequence[pathlib.Path],
        previous_operations: generic.Sequence[base_profiles.ProfileOperation],
        metadata: dict[str, typing.Any]
    ) -> generic.Sequence[pathlib.Path]:
        pass


    def __hash__(self) -> int:
        try:
            parent_hash: int = super().__hash__()
        except AttributeError:
            parent_hash = 0

        return hash((
            parent_hash,
            self.rate_variable_name,
            self.total_variable_name,
            self.output_unit,
            self.input_time_unit,
            self.time_unit,
            self.amount_of_time,
            self.total_variable_attributes
        ))

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return hash(self) == hash(other)

    rate_variable_name: str
    total_variable_name: str
    output_unit: str
    input_time_unit: str = dataclasses.field(default="seconds")
    time_unit: str = dataclasses.field(default="hours")
    amount_of_time: int = dataclasses.field(default=1)
    total_variable_attributes: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


