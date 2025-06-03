"""
Classes/enums that express that structure of netcdf files
"""
import typing
import logging
import dataclasses
import pathlib
import enum
import re

from .operation_helpers import EditMode
from .operation_helpers import get_header

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


class AttributeTypeGroup(enum.IntEnum):
    """
    Identifiers that may be used to categorize and score different netcdf attribute types
    """
    STRINGS = enum.auto()
    """Represents types of data that can have just about any ascii value"""
    REAL_NUMBERS = enum.auto()
    """Represents floating point numbers"""
    UNSIGNED_NATURAL_NUMBERS = enum.auto()
    """Represents unsigned whole numbers (minimum value is 0)"""
    SIGNED_NATURAL_NUMBERS = enum.auto()
    """Represents signed whole numbers"""

    def is_numeric(self) -> bool:
        return self != self.__class__.STRINGS

    def is_natural(self) -> bool:
        return self in (self.__class__.UNSIGNED_NATURAL_NUMBERS, self.__class__.SIGNED_NATURAL_NUMBERS)

    def is_floating_point(self) -> bool:
        return self == self.__class__.REAL_NUMBERS

    def __str__(self):
        return self.name.replace('_', ' ').title()


@dataclasses.dataclass
class NetcdfTypeDetails:
    """
    Describes details about a singular netcdf type
    """
    dtype: str
    code: str
    rank: int
    group: AttributeTypeGroup
    alternate_names: typing.List[str] = dataclasses.field(default_factory=list)
    print_unit: bool = dataclasses.field(default=True)

    def __str__(self):
        return f"{self.group.name}: {self.dtype}"

    def is_numeric(self) -> bool:
        return self.group.is_numeric()

    def is_natural(self) -> bool:
        return self.group.is_natural()

    def is_floating_point(self) -> bool:
        return self.group.is_floating_point()

    def _types_are_compatible(self, other: "NetcdfTypeDetails") -> bool:
        if not isinstance(other, NetcdfTypeDetails):
            LOGGER.debug(f"Cannot compare '{other}' (type={type(other)}) to {repr(self)} (type={type(self)})")
            return False

        if self.group != other.group:
            return False
        return True

    def __eq__(self, other):
        compatible: bool = self._types_are_compatible(other=other)
        if not compatible:
            return False
        return self.rank == other.rank

    def __gt__(self, other):
        compatible: bool = self._types_are_compatible(other=other)
        if not compatible:
            return False
        return self.rank > other.rank

    def __lt__(self, other):
        compatible: bool = self._types_are_compatible(other=other)
        if not compatible:
            return False
        return self.rank < other.rank

    def __le__(self, other):
        return self == other or self < other

    def __ge__(self, other):
        return self == other or self > other

    def __ne__(self, other):
        are_equal = self == other
        return not are_equal

class NetcdfType(enum.Enum):
    """
    The types that may be used for netcdf variables
    """
    FLOAT = NetcdfTypeDetails(dtype="real", code="f", group=AttributeTypeGroup.REAL_NUMBERS, rank=0, print_unit=False, alternate_names=["float", "float32"])
    DOUBLE = NetcdfTypeDetails(dtype="double", code="d", group=AttributeTypeGroup.REAL_NUMBERS, rank=1)
    BYTE = NetcdfTypeDetails(dtype="int8", code="b", group=AttributeTypeGroup.SIGNED_NATURAL_NUMBERS, rank=0)
    SHORT = NetcdfTypeDetails(dtype="int16", code="s", group=AttributeTypeGroup.SIGNED_NATURAL_NUMBERS, rank=1)
    INTEGER = NetcdfTypeDetails(dtype="int", code="i", group=AttributeTypeGroup.SIGNED_NATURAL_NUMBERS, rank=2, alternate_names=['int32'], print_unit=False)
    INTEGER_64 = NetcdfTypeDetails(dtype="int64", code="ll", group=AttributeTypeGroup.SIGNED_NATURAL_NUMBERS, rank=3)
    UNSIGNED_BYTE = NetcdfTypeDetails(dtype="uint8", code="ub", group=AttributeTypeGroup.UNSIGNED_NATURAL_NUMBERS, rank=0)
    UNSIGNED_SHORT = NetcdfTypeDetails(dtype="uint16", code="us", group=AttributeTypeGroup.UNSIGNED_NATURAL_NUMBERS, rank=1)
    UNSIGNED_INTEGER = NetcdfTypeDetails(dtype="uint32", code="ui", group=AttributeTypeGroup.UNSIGNED_NATURAL_NUMBERS, rank=2, alternate_names=['uint'])
    UNSIGNED_INTEGER_64 = NetcdfTypeDetails(dtype="uint64", code="ull", group=AttributeTypeGroup.UNSIGNED_NATURAL_NUMBERS, rank=3)
    CHAR = NetcdfTypeDetails(dtype="char", code="c", group=AttributeTypeGroup.STRINGS, rank=0, print_unit=False)
    STRING = NetcdfTypeDetails(dtype="string", code="string", group=AttributeTypeGroup.STRINGS, rank=1, print_unit=False)

    def __eq__(self, other):
        if not isinstance(other, NetcdfType):
            raise TypeError(f"Cannot compare '{other}' (type={type(other)}) to {repr(self)} (type={type(self)})")
        return self.value == other.value

    def __gt__(self, other):
        if not isinstance(other, NetcdfType):
            raise TypeError(f"Cannot compare '{other}' (type={type(other)}) to {repr(self)} (type={type(self)})")
        return self.value > other.value

    def __lt__(self, other):
        if not isinstance(other, NetcdfType):
            raise TypeError(f"Cannot compare '{other}' (type={type(other)}) to {repr(self)} (type={type(self)})")
        return self.value < other.value

    def __ne__(self, other):
        are_equal = self == other
        return not are_equal

    def __ge__(self, other):
        return self == other or self > other

    def __le__(self, other):
        return self == other or self < other

    @property
    def dtype(self) -> str:
        return self.value.dtype

    @property
    def code(self) -> str:
        return self.value.code

    @property
    def print_unit(self) -> bool:
        return self.value.print_unit

    @property
    def group(self) -> AttributeTypeGroup:
        return self.value.group

    def is_numeric(self) -> bool:
        return self.value.is_numeric()

    def is_natural(self) -> bool:
        return self.value.is_natural()

    def is_floating_point(self) -> bool:
        return self.value.is_floating_point()

    @classmethod
    def get_widest_type(
        cls,
        types: typing.Union["NetcdfType", typing.Iterable["NetcdfType"]],
        *other_types: "NetcdfType"
    ) -> "NetcdfType":
        """
        Get the widest type provided in the list of attribute types

        Widest type?

        Say that there are 3 attributes that are of the same general data, but different type (`short`, `int`, `long`).
        This finds the largest of the types in the group. In this case, it would be `long`. A wider type will
        encompass ALL values of a more narrow type. If two types have mutually exclusive values, they are not
        compatible.

        How do you tell the widest type in different types of data, like strings, doubles, and ints?

        You don't. An error will be thrown if incompatible types are compared. Unique types are:

        - Signed Natural Numbers
        - Unsigned Natural Numbers
        - Real Numbers
        - Strings

        Why are signed and unsigned separate? Their ranges are inherently incompatible. What is wider, the range of
        [0, 255] or [-128, 127]? Both have mutual exclusive ranges.

        :param types: The types to compare
        :param other_types: The other types to compare if all are positional
        :returns: The attribute type that can contain the values of all provided types
        """
        all_types: typing.Set[NetcdfType] = set(other_types)

        if isinstance(types, NetcdfType):
            all_types.add(types)
        elif not isinstance(types, typing.Iterable):
            raise ValueError(
                f"Cannot find the widest type - passed in types must be either a {cls.__qualname__} or collection of "
                f"{cls.__qualname__} but received {types} (type={type(types)})"
            )
        else:
            all_types.update(types)

        all_types.difference_update({None})

        invalid_types: typing.List[str] = [
            f"{considered_type} (type={type(considered_type)})"
            for considered_type in all_types
            if not isinstance(considered_type, cls)
        ]

        if invalid_types:
            raise ValueError(
                f"Cannot find the widest NCO type - received the following invalid values: "
                f"{', '.join(invalid_types)}"
            )

        widest_type: NetcdfType = max(all_types)

        return widest_type


    @classmethod
    def from_string(cls, string: str) -> "NetcdfType":
        """
        Get an attribute type from a string

        :param string: The string to parse
        :returns: The attribute type
        """
        string = string.strip().lower()
        mapping: typing.Dict[str, NetcdfType] = {}

        for member in cls:
            value: NetcdfTypeDetails = member.value
            mapping[value.dtype] = member
            mapping[value.code] = member
            for alternate_name in value.alternate_names:
                mapping[alternate_name] = member

        attribute_type: NetcdfType = mapping.get(string, None)

        if attribute_type is None:
            raise AttributeError(
                f"There is no attribute type in NCO that may be referred to as '{string}'"
            )
        return attribute_type

@dataclasses.dataclass
class Attribute:
    """
    Represents a netcdf attribute, whether it's on the global or variable level
    """
    name: str
    value: str
    unit: typing.Optional[NetcdfType] = dataclasses.field(default=None)
    owner: typing.Optional[str] = dataclasses.field(default='global')

    def __post_init__(self):
        if not self.owner:
            self.owner = 'global'
        value, unit = self._detect_unit()
        self.value = value
        self.unit = unit

    def __str__(self):
        if self.unit is None or not self.unit.print_unit:
            unit: str = ''
        else:
            unit: str = self.unit.code.upper()
        return f"{self.owner or ''}:{self.name} = {self.value}{unit}"

    def __repr__(self):
        return self.__str__()

    def _detect_unit(self, value: str = None) -> typing.Tuple[str, NetcdfType]:
        """
        Determine the proper unit provided by the attribute
        """
        if value is None:
            value = self.value

        if '"' in value:
            return value, NetcdfType.CHAR

        # At this point we know the value is numeric

        # If there is a ', ' at this point, we know we're detailing with an array
        #   We can be sure that this isn't something inside a crs, for example, because that would have been caught above
        if ", " in value:
            values: typing.List[str] = []
            dtype: typing.Optional[NetcdfType] = None

            for contained_value in value.split(", "):
                clear_value, unit = self._detect_unit(value=contained_value)
                values.append(clear_value)
                dtype = unit if dtype is None else max(unit, dtype)

            return ','.join(values), dtype

        typed_value_pattern: re.Pattern = re.compile(r"^(?P<value>-?\d+(\.\d*|e-?\d+)?)(?P<dtype>[a-zA-Z]+)$")

        numeric_type_match: typing.Optional[re.Match] = typed_value_pattern.match(value)

        if numeric_type_match:
            dtype: str = numeric_type_match.groupdict()['dtype']
            unitless_value: str = numeric_type_match.groupdict()['value']
            attribute_type = NetcdfType.from_string(string=dtype)
            return unitless_value, attribute_type

        if re.search(r"^-?\d*\.\d{7,}(e-?\d+)?$", value):
            return value, NetcdfType.DOUBLE

        if re.search(r"^-?\d*\.\d*(e-?\d+)?$", value):
            return value, NetcdfType.DOUBLE

        natural_number_pattern: typing.Optional[re.Pattern] = re.search(r"^-?\d+(e-?\d+)?$", value)

        if natural_number_pattern is None:
            return value, NetcdfType.CHAR

        # Estimate the type based on the range of values
        #   Unsigned bytes are rarely seen, so we're sticking with signed
        real_value: int = int(float(value))

        if real_value > 2_147_483_647 or real_value < -2_147_483_648:
            return value, NetcdfType.INTEGER_64

        return value, NetcdfType.INTEGER


    @property
    def ncatted_argument(self) -> str:
        """
        The argument to set this attribute in the ncatted program
        """
        return f'-a {self.name},{self.owner},{EditMode.OVERWRITE},{self.unit.code},"{self.value}"'


@dataclasses.dataclass
class DataVariable:
    """
    Represents the name of a variable in a netcdf file that contains data
    """
    name: str
    """The name of the variable"""
    type: NetcdfType
    """The type of data contained within this variable"""
    dimensions: typing.List[str]
    """The names of the dimensions used as coordinates"""
    attributes: typing.List[Attribute]
    """The attributes for the variable"""

    def __getitem__(self, item: str) -> typing.Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(f"There is no {item} variable in a {self.__class__.__qualname__}")

    def __setitem__(self, key: str, value: typing.Any) -> None:
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            raise KeyError(f"There is no {key} variable in a {self.__class__.__qualname__}")

    @property
    def encoded_to_integer(self) -> bool:
        """
        Whether this variable has been encoded as an integer despite containing floating point data
        """
        is_integer_variable: bool = self.type.group in (
            AttributeTypeGroup.SIGNED_NATURAL_NUMBERS,
            AttributeTypeGroup.UNSIGNED_NATURAL_NUMBERS
        )

        if not is_integer_variable:
            return False

        return any(filter(lambda attr: attr.name == 'scale_factor', self.attributes))



@dataclasses.dataclass
class NetcdfSummary:
    """
    Contains a parsed header of a netcdf file, showing details of its dimensions and data variables
    """
    unlimited_dimensions: typing.List[str]
    """All dimensions that don't have a limit and may be used as records"""
    all_dimensions: typing.List[str]
    """The names of all dimensions"""
    data_variables: typing.List[DataVariable]
    """All variables that contain data (i.e. not coordinates) paired with their dimensions"""
    attributes: typing.List[Attribute]
    """Global level attributes for a netcdf file"""
    path: pathlib.Path
    """The path to the netcdf file that this summary belongs to"""

    def __str__(self):
        return str(self.path)

    @classmethod
    def load(cls, path: typing.Union[str, pathlib.Path]) -> "NetcdfSummary":
        """
        Load netcdf data into a summary object
        """
        import re

        header: str = get_header(target=path)
        dimension_name_parameter: str = 'dimension_name'
        count_parameter: str = 'count'
        variable_name_parameter: str = 'variable_name'
        dimension_list_parameter: str = 'dimension_list'
        attribute_name_parameter: str = "attribute_name"
        attribute_value_parameter: str = "attribute_value"
        dtype_parameter: str = "dtype"
        dimension_pattern: re.Pattern = re.compile(rf"\s+(?P<{dimension_name_parameter}>\w+) = (?P<{count_parameter}>\w+)\s+;")
        variable_definition_pattern: re.Pattern = re.compile(
            rf"\s+(?P<{dtype_parameter}>\w+) (?P<{variable_name_parameter}>\w+)(\((?P<{dimension_list_parameter}>[^)]+)\))? ;"
        )
        attribute_pattern: re.Pattern = re.compile(
            r"\s+"
            rf"(?P<{variable_name_parameter}>\w+)?"
            rf":(?P<{attribute_name_parameter}>[^ ]+) = "
            rf'"?(?P<{attribute_value_parameter}>(?<=").+(?=")|(?<!").+(?= ;))"? ;'
        )

        dimension_matches: typing.Sequence[typing.Mapping[str, str]] = [
            match.groupdict()
            for match in dimension_pattern.finditer(header)
        ]

        variable_matches: typing.Sequence[typing.Mapping[str, str]] = [
            match.groupdict()
            for match in variable_definition_pattern.finditer(header)
        ]

        dimension_names: typing.List[str] = [
            group[dimension_name_parameter]
            for group in dimension_matches
        ]

        attributes: typing.Dict[str, typing.List[Attribute]] = {}
        global_attributes: typing.List[Attribute] = []

        for line in header.splitlines():
            match: typing.Optional[re.Match] = attribute_pattern.match(line)
            if match:
                variable_name: typing.Optional[str] = match.groupdict()[variable_name_parameter]
                attribute_name: str = match.groupdict()[attribute_name_parameter]
                attribute_value: str = match.groupdict()[attribute_value_parameter]

                if variable_name is None:
                    global_attributes.append(Attribute(name=attribute_name, value=attribute_value))
                else:
                    attributes.setdefault(variable_name, []).append(
                        Attribute(
                            name=attribute_name,
                            value=attribute_value,
                            owner=variable_name
                        )
                    )

        unlimited_dimension_names: typing.List[str] = [
            group[dimension_name_parameter]
            for group in dimension_matches
            if group[count_parameter] == "UNLIMITED"
        ]

        data_variables: typing.List[DataVariable] = [
            DataVariable(
                name=group[variable_name_parameter],
                type=NetcdfType.from_string(group[dtype_parameter]),
                dimensions=[dimension.strip() for dimension in group[dimension_list_parameter].split(",")] if group[dimension_list_parameter] else [],
                attributes=attributes.get(group[variable_name_parameter], {}),
            )
            for group in variable_matches
            if group[variable_name_parameter] not in dimension_names
        ]

        return cls(
            unlimited_dimensions=unlimited_dimension_names,
            all_dimensions=dimension_names,
            data_variables=data_variables,
            attributes=global_attributes,
            path=path if isinstance(path, pathlib.Path) else pathlib.Path(path)
        )

    @classmethod
    def load_summaries(cls, paths: typing.Sequence[typing.Union[str, pathlib.Path]]) -> typing.Sequence["NetcdfSummary"]:
        """
        Load a series of netcdf data into summary objects
        """
        summaries: typing.List[NetcdfSummary] = list(map(NetcdfSummary.load, paths))
        return summaries
