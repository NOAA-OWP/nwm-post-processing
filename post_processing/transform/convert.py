"""
Functions and objects to convert from one unit to another
"""
import typing
import pathlib
import logging
import dataclasses

if typing.TYPE_CHECKING:
    import xarray

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

UNIT_NAME_ATTRIBUTE: str = "unit_name"
"""The name of the proper NetCDF attribute containing the name of the unit that the value is measured in"""


@dataclasses.dataclass
class ConversionFactor:
    """
    Describes the formula for how to convert from one unit to another

    The formula follows:
        converted value = ((value + initial_adjustment) * factor) + final_adjustment

    Celsius to Fahrenheit will look like:
        fahrenheit = ((celsius + 0.0) * 1.8) + 32

    Fahrenheit to Celsius will look like:
        celsius = ((fahrenheit - 32.0) * 0.55556) + 0.0
    """
    from_unit: list[str]
    """A list of unit names that describe a singular input unit ('CMS', 'm3 s-1', 'm3/s', 'm^3/s', etc)"""
    to_unit: list[str]
    """A list of unit names that describe a singular output unit ('CFS', 'ft3 s-1', 'ft3/s', 'ft^3/s', etc)"""
    factor: float
    """The major factor to multiply by"""
    initial_adjustment: float = dataclasses.field(default=0.0)
    """An initial value to add to before multiplying"""
    final_adjustment: float = dataclasses.field(default=0.0)
    """A final value to add after multiplying"""

    def __post_init__(self):
        if not self.from_unit:
            raise ValueError("Cannot create a conversion factor - no 'from_units' were provided.")

        if not self.to_unit:
            raise ValueError("Cannot create a conversion factor - no 'to_units' were provided.")

        if not self.factor:
            raise ValueError(f"Cannot create a conversion factor - {self.factor} is not a valid factor")

        self.from_unit = list(map(lambda unit: unit.lower(), self.from_unit))
        self.to_unit = list(map(lambda unit: unit.lower(), self.to_unit))

    def convert(self, variable: "xarray.DataArray", target_unit_name: str = None) -> "xarray.DataArray":
        """
        Convert the xarray variable to the new unit

        :param variable: The variable to convert
        :param target_unit_name: The unit name to convert to
        :return: The converted variable
        """
        if not target_unit_name:
            target_unit_name = self.to_unit[0]

        if UNIT_NAME_ATTRIBUTE not in variable.attrs:
            raise ValueError(
                f"Cannot convert the values in the '{variable.name}' variable to '{target_unit_name}' - "
                f"the input variable doesn't have a unit"
            )

        if variable.attrs[UNIT_NAME_ATTRIBUTE] not in self.from_unit:
            raise TypeError(
                f"Cannot convert the values in the '{variable.name}' variable to '{target_unit_name}' - "
                f"this may only convert from the following units, not '{variable.attrs[UNIT_NAME_ATTRIBUTE]}': "
                f"{', '.join(self.from_unit)}"
            )

        import numpy

        if not numpy.issubdtype(variable.dtype, numpy.number):
            raise TypeError(
                f"Cannot convert data from '{variable.name}' from '{variable.attrs[UNIT_NAME_ATTRIBUTE]}' to "
                f"'{target_unit_name}' - '{variable.name} is not numeric ({variable.dtype})"
            )

        variable.data = ((variable.data + self.initial_adjustment) * self.factor) + self.final_adjustment
        variable.attrs[UNIT_NAME_ATTRIBUTE] = target_unit_name

        return variable

class _Conversions:
    """
    A mechanic used to store and search for unit conversions
    """
    def __init__(self):
        self.__factors: list[ConversionFactor] = []
        self.__load_factors()

    def __load_factors(self):
        """
        Populate factors
        """
        self.__factors.extend([
            ConversionFactor(
                to_unit=["cms", "m3/s", "m^3/s", "m3 s-1"],
                from_unit=["cfs", "ft3/s", "ft^3/s", "ft3 s-1"],
                factor=35.3146667,
            )
        ])

    def find(self, from_unit: str, to_unit: str) -> ConversionFactor:
        """
        Find an appropriate unit conversion that will convert data from the `from_unit` to `to_unit`

        :param from_unit: The name of the unit that the data is already in
        :param to_unit: The desired unit for the data
        :returns: The appropriate conversion factor
        """
        for conversion_factor in self.__factors:
            if to_unit.lower() in conversion_factor.to_unit and from_unit.lower() in conversion_factor.from_unit:
                return conversion_factor

        raise KeyError(f"Could not find a conversion factor that converts {from_unit} to {to_unit}")

CONVERSIONS: _Conversions = _Conversions()
"""The object containing common unit conversions"""

def convert_variable_unit(
    variable: "xarray.DataArray",
    to_unit: str
) -> "xarray.DataArray":
    """
    Convert the values in the variable to the new unit

    :param variable: The variable to convert
    :param to_unit: The desired unit for the data
    :return: The converted variable
    """
    if UNIT_NAME_ATTRIBUTE not in variable.attrs:
        LOGGER.warning(f"Cannot convert the values in '{variable.name}' to '{to_unit}' - there are no defined units.")
        return variable

    unit_name: str = str(variable.attrs[UNIT_NAME_ATTRIBUTE])
    if unit_name.isdigit():
        LOGGER.warning(
            f"Cannot convert the values in '{variable.name}' from '{unit_name}' to '{to_unit}' - "
            f"it uses a categorical unit, not a physical unit"
        )
        return variable

    conversion_factor: ConversionFactor = CONVERSIONS.find(from_unit=unit_name, to_unit=to_unit)

    import xarray
    converted_values: xarray.DataArray = conversion_factor.convert(variable, target_unit_name=unit_name)

    assert converted_values.attrs.get(UNIT_NAME_ATTRIBUTE) == unit_name, f"The unit was not properly renamed for '{variable.name}' to '{to_unit}'"

    return converted_values
