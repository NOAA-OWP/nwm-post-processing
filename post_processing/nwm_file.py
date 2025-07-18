"""
Defines objects and functions used to describe a NWM file
"""
import typing
import dataclasses
import pathlib
import re

from datetime import datetime

from post_processing import enums
from post_processing.utilities import common


@dataclasses.dataclass
class NWMFile:
    """
    Represents the metadata baked into an NWM file's filename
    """
    cycle: typing.Union[int, datetime]
    model_output_type: enums.ModelOutputType
    configuration: enums.Configuration
    region: enums.Region
    frame: typing.Optional[str] = dataclasses.field(default=None)
    t_minus: typing.Optional[str] = dataclasses.field(default=None)
    member: typing.Optional[int] = dataclasses.field(default=None)
    path: typing.Optional[pathlib.Path] = dataclasses.field(default=None)

    def __post_init__(self):
        if self.frame is None and self.t_minus is None:
            raise ValueError("frame and t_minus cannot both be None")

    @property
    def group_hash(self) -> int:
        """
        Get a hash code that will be unique to NWM files that should be grouped together

        Groups are defined by:

        - cycle
        - model_output_type
        - configuration
        - region
        - member

        NOTE: Mixing forecasts from different days will include forecasts from those different days
        """
        return hash((
            self.cycle,
            self.model_output_type,
            self.configuration,
            self.region,
            self.member,
        ))

    @classmethod
    def parse(cls, path: typing.Union[pathlib.Path, str]) -> "NWMFile":
        """
        Read a path and convert it to an NWMFile
        """
        if isinstance(path, str):
            path = pathlib.Path(path)

        name: str = path.name

        match: typing.Optional[re.Match] = common.NWM_FILENAME_PATTERN.match(name)

        if match is None:
            raise ValueError(
                f"{path} could not be parsed as a normal NWM File by matching to "
                f"r'{common.NWM_FILENAME_PATTERN.pattern}'"
            )

        raw_data: typing.Dict[str, str] = match.groupdict()

        cycle: int = int(raw_data[common.CYCLE_PATTERN_VARIABLE])

        configuration: enums.Configuration = enums.Configuration.from_string(raw_data[common.CONFIGURATION_PATTERN_VARIABLE])
        output_type: enums.ModelOutputType = enums.ModelOutputType.from_string(raw_data[common.OUTPUT_TYPE_PATTERN_VARIABLE])
        region: enums.Region = enums.Region.from_string(raw_data[common.REGION_PATTERN_VARIABLE])

        caught_member: typing.Optional[str] = raw_data.get(common.MEMBER_PATTERN_VARIABLE)
        member: typing.Optional[int] = int(caught_member) if caught_member is not None else None

        caught_frame: typing.Optional[str] = raw_data.get(common.FRAME_PATTERN_VARIABLE)
        frame: typing.Optional[str] = caught_frame if caught_frame is not None else None

        caught_tminus: typing.Optional[str] = raw_data.get(common.TMINUS_PATTERN_VARIABLE)
        t_minus: typing.Optional[str] = caught_tminus if caught_tminus is not None else None

        return cls(
            cycle=cycle,
            member=member,
            configuration=configuration,
            model_output_type=output_type,
            region=region,
            frame=frame,
            t_minus=t_minus,
            path=path
        )

    def __str__(self):
        if isinstance(self.cycle, datetime):
            cycle: str = self.cycle.strftime("%Y%m%d%H")
        else:
            cycle: str = str(self.cycle).zfill(2)

        name: str = f"nwm.t{cycle}z.{self.configuration.value}.{self.model_output_type.value}"

        if not isinstance(self.member, type(None)):
            name += f"_{self.member}"

        name += "."

        if self.frame is not None:
            if self.region in (enums.Region.Hawaii, enums.Region.HawaiiAPRFC):
                name += f"f{str(self.frame).zfill(5)}"
            else:
                name += f"f{str(self.frame).zfill(2)}"
        else:
            name += f"tm{str(self.t_minus).zfill(2)}"

        name += f".{self.region.value}.nc"
