"""
Details an 'InputManifest', which holds all the information describing what is being processed
"""
import os
import typing
import dataclasses
import pathlib
import logging

from datetime import datetime

from post_processing.enums import Region
from post_processing.enums import Configuration
from post_processing.enums import ModelOutputType

from post_processing.utilities.common import sort_nwm_filepaths
from post_processing.schema.base import BaseModel
from post_processing.nwm_file import NWMFile

REFERENCE_TIME_VARIABLE: str = "reference_time"

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

@dataclasses.dataclass
class InputManifest(BaseModel):
    """
    Details everything about a set of files that will be operated upon
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

    @classmethod
    def from_files(cls, files: typing.Sequence[pathlib.Path]) -> "InputManifest":
        """
        Create a manifest from a set of files
        """
        nwm_files: list[NWMFile] = list(map(
            lambda path: NWMFile.parse(path=path),
            files
        ))
        included_groups: set[int] = set(map(lambda nwm_file: nwm_file.group_hash, nwm_files))
        if len(included_groups) > 1:
            raise ValueError(
                f"Cannot load the following files into a manifest - they represent multiple incompatible groups:{os.linesep}"
                f"    - {(os.linesep + '    - ').join(map(str, files))}"
            )
        sample: NWMFile = nwm_files[0]
        manifest: InputManifest = InputManifest(
            region=sample.region,
            configuration=sample.configuration,
            output_type=sample.model_output_type,
            cycle=str(sample.cycle).zfill(2),
            files=files,
            member=sample.member,
        )
        return manifest

    def _validate(self):
        """
        Make sure that the values are valid
        """
        #self.files = sort_nwm_filepaths(filepaths=self.files)
        if isinstance(self.member, (str, float)):
            self.member = int(float(self.member))



