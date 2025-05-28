"""
Details an 'InputManifest', which holds all the information describing what is being processed
"""
import typing
import dataclasses
import pathlib

from datetime import datetime

from post_processing.enums import Region
from post_processing.enums import Configuration
from post_processing.enums import ModelOutputType

from post_processing.utilities.common import datetime64_to_datetime
from post_processing.utilities.common import sort_nwm_filepaths
from post_processing.schema.base import BaseModel

REFERENCE_TIME_VARIABLE: str = "reference_time"

@dataclasses.dataclass
class InputManifest(BaseModel):
    """
    Details about the initial state of the data
    """
    region: Region
    """Where the values are valid"""
    configuration: Configuration
    """The configuration of the model that generated the data (Analysis/Assim, Short Range, Long Range, etc)"""
    output_type: ModelOutputType
    """What type of data was created (channel_rt, forcing, land)"""
    cycle: str
    """What cycle of the model that this takes place"""
    files: typing.Sequence[pathlib.Path]
    """The files that will serve as input for post processing"""
    member: typing.Optional[int] = dataclasses.field(default=None)
    """The ensemble member that may be getting processed"""
    _reference_time: typing.Optional[datetime] = None

    @property
    def reference_time(self) -> datetime:
        """
        The reference time of the NWM output
        """
        return self._reference_time

    def __post_init__(self):
        self._validate()

    def _validate(self):
        """
        Make sure that the values are valid
        """
        import xarray
        try:
            import dask
            has_dask = True
        except ImportError:
            has_dask = False

        reference_times: typing.Set[datetime] = set()
        output_types: typing.Set[ModelOutputType] = set()
        configurations: typing.Set[Configuration] = set()
        regions: typing.Set[Region] = set()

        file_issues: typing.Dict[pathlib.Path, typing.List[str]] = {}

        for file in self.files:
            if has_dask:
                data = xarray.open_dataset(file, chunks={})
            else:
                data = xarray.open_dataset(file)

            if REFERENCE_TIME_VARIABLE not in data.coords:
                if file not in file_issues:
                    file_issues[file] = []
                file_issues[file].append(f"Missing a coordinate by the name: {REFERENCE_TIME_VARIABLE}")

            reference_time: xarray.DataArray = data[REFERENCE_TIME_VARIABLE]
            for time in reference_time.values:
                python_datetime: datetime = datetime64_to_datetime(numpy_date=time)
                reference_times.add(python_datetime)

        error_message: str = ""

        if error_message:
            raise ValueError(error_message)

        self._reference_times = reference_times.pop()
        self.files = sort_nwm_filepaths(filepaths=self.files)



