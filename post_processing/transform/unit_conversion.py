"""
Functions and objects to convert from one unit to another
"""
import typing
import pathlib
import logging
import dataclasses

import collections.abc as generic

if typing.TYPE_CHECKING:
    import xarray

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

UNIT_NAME_ATTRIBUTE: str = "units"
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

        if variable.attrs[UNIT_NAME_ATTRIBUTE].lower() not in self.from_unit:
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

        original_encoding: dict[str, typing.Any] = variable.encoding.copy()
        variable.data = ((variable.data + self.initial_adjustment) * self.factor) + self.final_adjustment
        variable.attrs[UNIT_NAME_ATTRIBUTE] = target_unit_name
        variable.encoding.update(original_encoding)
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
                to_unit=["cfs", "ft3/s", "ft^3/s", "ft3 s-1"],
                from_unit=["cms", "m3/s", "m^3/s", "m3 s-1"],
                factor=35.3146667,
            ),
            ConversionFactor(
                to_unit=["C", "c"],
                from_unit=["K", "k"],
                factor=1.0,
                final_adjustment=-272.15,
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
    to_unit: str,
    from_unit: str = None
) -> "xarray.DataArray":
    """
    Convert the values in the variable to the new unit

    :param variable: The variable to convert
    :param to_unit: The desired unit for the data
    :param from_unit: The name of the unit that the data is already in
    :return: The converted variable
    """
    if UNIT_NAME_ATTRIBUTE not in variable.attrs and UNIT_NAME_ATTRIBUTE not in variable.encoding:
        raise KeyError(f"Cannot convert the values in '{variable.name}' to '{to_unit}' - there are no defined units.")

    if not from_unit:
        from_unit: str = str((variable.attrs | variable.encoding)[UNIT_NAME_ATTRIBUTE])

    if from_unit.isdigit():
        raise ValueError(
            f"Cannot convert the values in '{variable.name}' from '{from_unit}' to '{to_unit}' - "
            f"it uses a categorical unit, not a physical unit"
        )

    conversion_factor: ConversionFactor = CONVERSIONS.find(from_unit=from_unit, to_unit=to_unit)

    import xarray
    converted_values: xarray.DataArray = conversion_factor.convert(variable, target_unit_name=to_unit)

    assert converted_values.attrs.get(UNIT_NAME_ATTRIBUTE) == to_unit, f"The unit was not properly renamed for '{variable.name}' to '{to_unit}'"

    return converted_values


def convert_file(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    conversions: generic.Sequence[tuple[str, str]],
    work_directory: pathlib.Path,
) -> pathlib.Path:
    """
    Convert all specified variables in a file

    :param input_path: The path to the input file
    :param output_path: Where to save the results
    :param conversions: A listing of the names of the variables to convert and what unit to convert them to
    :param work_directory: Where to save intermediate products
    :return: The path to the converted file
    """
    import shutil
    import xarray
    import tempfile

    from post_processing.utilities.netcdf import load_netcdf
    from post_processing.utilities.netcdf import save_netcdf

    with tempfile.TemporaryDirectory(dir=work_directory) as temporary_directory:
        temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
        temporary_output_path: pathlib.Path = temporary_path / output_path.name

        LOGGER.debug(
            f"Opening '{input_path}' to convert {', '.join(map(lambda pair: pair[0], conversions))} to new units."
        )
        with load_netcdf(input_path) as input_data:
            for variable_name, new_unit in conversions:
                if variable_name not in input_data.data_vars:
                    raise KeyError(f"Cannot convert the '{variable_name}' variable to '{new_unit}' - it is not in '{input_path}'")
                data: xarray.DataArray = input_data.data_vars[variable_name]
                LOGGER.debug(f"Converting '{input_path.stem}::{variable_name}' to '{new_unit}'")
                converted_data = convert_variable_unit(variable=data, to_unit=new_unit)
                original_encoding: dict[str, typing.Any] = converted_data.encoding.copy()
                input_data[variable_name] = converted_data
                input_data.encoding = original_encoding
            LOGGER.debug(f"Saving the updated '{input_path.name}' data to a temporary location")
            save_netcdf(path=temporary_output_path, dataset=input_data)
        LOGGER.debug(f"Saving the temporary '{input_path.name}' data to {output_path}")
        shutil.move(temporary_output_path, output_path)
    return output_path

