#!/usr/bin/env python3
"""
Enumerations used to describe data
"""
import typing
import enum

from datetime import timedelta
from datetime import datetime

import numpy


_PRINT_REPLACEMENTS: dict[str, str] = {
    "no_da": "No Data Assimilation",
    "analysis_assim": "Analysis and Assimilation",
    "extend": "Extended",
    "conus": "CONUS",
    "rt": "Routing",
    "abrfc": "Arkansas Red-Basin River Forecast Center",
    "aprfc": "Alaska Pacific River Forecast Center",
    "cbrfc": "Colorado Basin River Forecast Center",
    "cnrfc": "California Nevada River Forecast Center",
    "lmrfc": "Lower Mississippi River Forecast Center",
    "marfc": "Mid-Atlantic River Forecast Center",
    "mbrfc": "Missouri Basin River Forecast Center",
    "ncrfc": "North Central River Forecast Center",
    "nerfc": "Northeast River Forecast Center",
    "nwrfc": "Northwest River Forecast Center",
    "ohrfc": "Ohio River Valley River Forecast Center",
    "serfc": "Southeast River Forecast Center",
    "wgrfc": "West Gulf River Forecast Center",
}
"""A mapping of keys and replacements that will make text easier to understand for humans"""


class PostProcessingEnumeration(enum.Enum):
    """
    Abstract class defining common methods and behavior for application specific enums
    """
    @classmethod
    def from_string(cls, value: str):
        """
        Get an instance of this enum based off of its value
        """
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"'{value}' is not a valid value within {cls.__name__}")
    
    @classmethod
    def pattern(cls) -> str:
        """
        Get a regular expression demonstrating how to extract this value from a string
        """
        return rf"(?P<{cls.__name__}>{'|'.join([member.value for member in cls])})"

    def describe(self) -> str:
        from post_processing.utilities.common import format_identifier_to_title
        value_parts: list[str] = format_identifier_to_title(self.value).split(" ")

        for part_index, part in enumerate(value_parts):
            for key, replacement in _PRINT_REPLACEMENTS.items():
                if part.lower() == key.lower():
                    value_parts[part_index] = replacement
                    break

        description: str = " ".join(value_parts)
        return description

    def __str__(self):
        return self.value


class ModelOutputType(PostProcessingEnumeration):
    """
    Enumerates what type of data was modeled
    """
    ChannelRouting = "channel_rt"
    Forcing = "forcing"
    Land = "land"
    Reservoir = "reservoir"
    FullReservoir = "reservoir.full"


class Configuration(PostProcessingEnumeration):
    """
    Enumerates the different ways a model may be configured for forecast/simulation length and input parameters
    """
    ShortRange = "short_range"
    ShortRangeNoDA = "short_range_no_da"
    LongRange = "long_range"
    MediumRange = "medium_range"
    MediumRangeNoDA = "medium_range_no_da"
    MediumRangeBlend = "medium_range_blend"
    MediumRangeNDFD = "medium_range_ndfd"
    AnalysisAssimilation = "analysis_assim"
    AnalysisAssimilationNoDA = "analysis_assim_no_da"
    ExtendedAnalysisAssimilation = "analysis_assim_extend"
    ExtendedAnalysisAssimilationNoDA = "analysis_assim_extend_no_da"
    LongAnalysisAssimilation = "analysis_assim_long"
    LongAnalysisAssimilationNoDA = "analysis_assim_long_no_da"


class Region(PostProcessingEnumeration):
    """Enumerates the different ways Regions/RFCs/General areas may be represented within strings"""
    ABRFC = "abrfc"
    """Arkansas Red-Basin River Forecast Center"""
    APRFC = "aprfc"
    """Alaska Pacific River Forecast Center"""
    CBRFC = "cbrfc"
    """Colorado Basin River Forecast Center"""
    CNRFC = "cnrfc"
    """California Nevada River Forecast Center"""
    LMRFC = "lmrfc"
    """Lower Mississippi River Forecast Center"""
    MARFC = "marfc"
    """Mid-Atlantic River Forecast Center"""
    MBRFC = "mbrfc"
    """Missouri Basin River Forecast Center"""
    NCRFC = "ncrfc"
    """North Central River Forecast Center"""
    NERFC = "nerfc"
    """Northeast River Forecast Center"""
    NWRFC = "nwrfc"
    """Northwest River Forecast Center"""
    OHRFC = "ohrfc"
    """Ohio River Valley River Forecast Center"""
    SERFC = "serfc"
    """Southeast River Forecast Center"""
    WGRFC = "wgrfc"
    """West Gulf River Forecast Center"""
    AlaskaAPRFC = "alaska.aprfc"
    """The state of Alaska in relation to APRFC"""
    Alaska = "alaska"
    """The state of Alaska"""
    HawaiiAPRFC = "hawaii.aprfc"
    """The state of hawaii in relation to APRFC"""
    Hawaii = "hawaii"
    """The state of Hawaii"""
    PuertoRico = "puertorico"
    """The territory of Puerto Rico"""
    PuertoRicoSERFC = "puertorico.serfc"
    """The territory of Puerto Rico in relation to SERFC"""
    CONUS = "conus"
    """Continental United States"""


class RFC(enum.Enum):
    """
    Enumerates the ways that River Forecast Centers may be declared with their 2-character abbreviation
    """
    ABRFC = "AB"
    """Arkansas Red-Basin River Forecast Center"""
    APRFC = "AP"
    """Alaska-Pacific River Forecast Center"""
    CBRFC = "CB"
    """Colorado Basin River Forecast Center"""
    CNRFC = "CN"
    """California Nevada River Forecast Center"""
    LMRFC = "LM"
    """Lower Mississippi River Forecast Center"""
    MARFC = "MA"
    """Mid-Atlantic River Forecast Center"""
    MBRFC = "MB"
    """Missouri Basin River Forecast Center"""
    NCRFC = "NC"
    """North Central River Forecast Center"""
    NERFC = "NE"
    """Norteast River Forecast Center"""
    NWRFC = "NW"
    """Nortwest River Forecast Center"""
    OHRFC = "OH"
    """Ohio River Valley River Forecast Center"""
    SERFC = "SE"
    """Southeast River Forecast Center"""
    WGRFC = "WG"
    """West Gulf River Forecast Center"""

    @classmethod
    def from_string(cls, string: str, strict: bool = True) -> typing.Optional["RFC"]:
        """
        Try to match on a value given a case-insensitive string

        :param string: The string to attempt to match on
        :param strict: Raise an exception if a match is not found
        :returns: A member of the enum if it is found
        """
        for member in cls:
            if member.value.lower() == string.lower():
                return member
            elif member.name.lower() == string.lower():
                return member

        if strict:
            raise KeyError(f"There is no {cls.__qualname__} with the name '{string}'")

        return None

    def __str__(self):
        return self.value

class Verbosity(enum.IntEnum):
    """
    Describes the range of log statements that may be applicable within the span of different type of log statements

    Example:
        >>> if volume >= Verbosity.LOUD:
        ...     message = "detailed message"
        ... elif volume >= Verbosity.NORMAL:
        ...     message = "regular message"
        ... else:
        ...     message = None
        ...
        ... if message is not None:
        ...     print(message)
        >>> if volume > Verbosity.SILENT:
        ...     print("Here is another example")
    """
    SILENT = -2
    """Indicates a message that should be output even if everything is supposed to be completely silent"""
    QUIET = -1
    """Indicates a message that should be output even if this are supposed to be fairly quiet"""
    NORMAL = 0
    """Indicates a normal output volume for a message"""
    VERBOSE = 1
    """Indicates a message that should only be output if we are supposed to be fairly verbose"""
    LOUD = 2
    """Indicates a message that should only be output if we are being extremely verbose"""
    ALL = 3
    """Indicates a message that should only be output if we're being over the top"""

    @classmethod
    def from_string(cls, string: str) -> "Verbosity":
        """
        Get a verbosity value from a string
        """
        clean_string: str = string.lower().strip()

        for member in cls:
            member_name: str = member.name.lower()
            member_value: str = str(member.value)

            if clean_string in (member_name, member_value):
                return member
        raise KeyError(f"'{string}' is not a valid value for '{cls.__name__}'")


class TimeUnit(enum.Enum):
    """
    Represents a single unit of time rather than a duration

    An hour before now, for example, would be:
        >>> datetime.now() - TimeUnit.HOURS
    """
    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"

    def __new__(cls, name: str):
        obj = object.__new__(cls)
        obj._value_ = name

        obj._timedelta = timedelta(**{name: 1})
        obj._seconds = obj._timedelta.total_seconds()
        obj._alias = name[0]

        return obj

    @property
    def seconds(self) -> float:
        return self._seconds

    @property
    def delta(self) -> timedelta:
        return self._timedelta

    def __str__(self):
        return self.value

    def to_numpy(self) -> numpy.timedelta64:
        return numpy.timedelta64(self.delta)

    def __int__(self) -> int:
        return int(self.seconds)

    def __float__(self) -> float:
        return self.seconds

    def __hash__(self) -> int:
        return hash(self.delta)

    def __eq__(self, other) -> bool:
        if not isinstance(other, (TimeUnit, str, float, int, numpy.number, timedelta, numpy.timedelta64)):
            return False

        if isinstance(other, str):
            return self.value.lower() == other.lower() or self._alias.lower() == other.lower()
        elif isinstance(other, timedelta):
            return self.delta == other
        elif isinstance(other, numpy.timedelta64):
            return self.to_numpy() == other
        elif isinstance(other, (int, numpy.integer)):
            return int(self.seconds) == other
        return self.seconds == other

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    def __gt__(self, other) -> bool:
        if not isinstance(other, (TimeUnit, float, int, numpy.number, timedelta, numpy.timedelta64)):
            raise TypeError(f"Cannot tell if '{self}' (type={self.__class__.__name__}) is greater than '{other}' (type={type(other)}).")

        if isinstance(other, timedelta):
            return self.delta > other
        elif isinstance(other, numpy.timedelta64):
            return self.to_numpy() > other
        elif isinstance(other, (int, numpy.integer)):
            return int(self.seconds) > other
        return self.seconds > other

    def __lt__(self, other) -> bool:
        if not isinstance(other, (TimeUnit, float, int, numpy.number, timedelta, numpy.timedelta64)):
            raise TypeError(f"Cannot tell if '{self}' (type={self.__class__.__name__}) is greater than '{other}' (type={type(other)}).")

        if isinstance(other, timedelta):
            return self.delta < other
        elif isinstance(other, numpy.timedelta64):
            return self.to_numpy() < other
        elif isinstance(other, (int, numpy.integer)):
            return int(self.seconds) < other
        return self.seconds < other

    def __ge__(self, other) -> bool:
        return self == other or self > other

    def __le__(self, other) -> bool:
        return self == other or self < other

    def __add__(self, other) -> timedelta | numpy.timedelta64 | datetime | numpy.datetime64:
        if not isinstance(other, (timedelta, numpy.timedelta64, datetime, numpy.datetime64)):
            return NotImplemented

        if isinstance(other, (numpy.datetime64, numpy.timedelta64)):
            return self.to_numpy() + other

        return self.delta + other

    def __sub__(self, other) -> timedelta | numpy.timedelta64 | datetime | numpy.datetime64:
        if not isinstance(other, (timedelta, numpy.timedelta64, datetime, numpy.datetime64)):
            return NotImplemented

        if isinstance(other, (numpy.datetime64, numpy.timedelta64)):
            return self.to_numpy() - other

        return self.delta - other

    def __radd__(self, other):
        if not isinstance(other, (timedelta, numpy.timedelta64, datetime, numpy.datetime64)):
            return NotImplemented

        if isinstance(other, (numpy.datetime64, numpy.timedelta64)):
            return other + self.to_numpy()

        return other + self.delta

    def __rsub__(
        self,
        other: timedelta | numpy.timedelta64 | datetime | numpy.datetime64,
    ) -> timedelta | numpy.timedelta64 | datetime | numpy.datetime64:
        if not isinstance(other, (timedelta, numpy.timedelta64, datetime, numpy.datetime64)):
            return NotImplemented

        if isinstance(other, (numpy.datetime64, numpy.timedelta64)):
            return other - self.to_numpy()

        return other - self.delta

    def __mul__(self, other: int | float | numpy.number) -> timedelta | numpy.timedelta64:
        if not isinstance(other, (int, float, numpy.number)):
            return NotImplemented

        if isinstance(other, numpy.number):
            return self.to_numpy() * other

        return self.delta * other

    def __truediv__(
        self,
        other: typing.Union[int, float, "TimeUnit", numpy.number, timedelta, numpy.timedelta64],
    ) -> timedelta | numpy.timedelta64 | float:
        if not isinstance(other, (int, float, TimeUnit, numpy.number, timedelta, numpy.timedelta64)):
            return NotImplemented

        if numpy.isnan(other):
            return self.to_numpy() / other
        if isinstance(other, (numpy.number, numpy.datetime64)):
            return self.to_numpy() / other
        if isinstance(other, TimeUnit):
            return self.seconds / other.seconds

        return self.delta / other

    def __rmul__(self, other: int | float | numpy.number) -> timedelta:
        if not isinstance(other, (int, float, numpy.number)):
            return NotImplemented

        if isinstance(other, numpy.number):
            return other * self.to_numpy()

        return other * self.delta

    def __rtruediv__(
        self, other: int | float | numpy.number | timedelta | numpy.timedelta64
    ) -> timedelta | float | numpy.timedelta64 | numpy.floating:
        if not isinstance(other, (int, float, numpy.number, timedelta, numpy.timedelta64)):
            return NotImplemented

        if isinstance(other, (numpy.number, numpy.timedelta64)):
            return other / self.to_numpy()

        return other / self.delta

