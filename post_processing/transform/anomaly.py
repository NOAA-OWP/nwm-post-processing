"""
Objects and functions used to bin values by percentile
"""
import typing
import logging
import pathlib
import dataclasses

import collections.abc as generic

from threading import RLock
from threading import current_thread

from post_processing.schema.base import member
from post_processing.utilities.common import timed_function

if typing.TYPE_CHECKING:
    import xarray
    import numpy

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

LOAD_LOCK = RLock()

@dataclasses.dataclass
class ThresholdDefinition:
    """
    Information about how to derive a threshold from a netcdf file
    """
    data_path: pathlib.Path
    level: typing.Union[int, float, "numpy.float32"]
    variable: str
    time_coordinate: str = dataclasses.field(default='time')
    # TODO: Should this be a dict of numpy arrays rather than data arrays?
    _data: dict[int, "xarray.DataArray"] = member(default_factory=dict)
    _lock: RLock = member(default_factory=RLock)

    @classmethod
    def generate_init_key(
        cls,
        data_path: pathlib.Path,
        level: typing.Union[int, float, "numpy.float32"],
        variable: str,
        time_coordinate: str = "time"
    ) -> int:
        return hash((data_path, level, variable, time_coordinate))

    def update_stats(self, day_of_year: int, stats: "xarray.DataArray"):
        with self._lock:
            LOGGER.debug(f"Updating the statistics for day {day_of_year} of {self.data_path.name}")
            self._data[day_of_year] = stats

    def to_dict(self) -> typing.Mapping[str, typing.Any]:
        """
        Convert the definition to a dictionary in pure python types and avoiding private data
        """
        representation: dict[str, typing.Any] = {
            "data_path": str(self.data_path),
            "level": self.level if isinstance(self.level, (int, float)) else float(str(self.level)),
            "variable": self.variable,
            "time_coordinate": self.time_coordinate,
        }
        return representation

    def __post_init__(self):
        import numpy
        if not isinstance(self.level, numpy.float32):
            self.level = numpy.float32(self.level)
        if isinstance(self.data_path, str):
            self.data_path = pathlib.Path(self.data_path)

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, ThresholdDefinition):
            raise TypeError(
                f"Cannot compare '{self}' against '{other}' (type={type(other)})"
            )
        return self.level == other.level

    def __hash__(self):
        return hash((self.level,))

    def __lt__(self, other: typing.Any) -> bool:
        if not isinstance(other, ThresholdDefinition):
            raise TypeError(
                f"Cannot compare '{self}' against '{other}' (type={type(other)})"
            )
        return self.level < other.level

    def __le__(self, other: typing.Any) -> bool:
        return self == other or self < other

    def __gt__(self, other):
        return not (self <= other)

    def __ge__(self, other):
        return not (self < other)

    def __str__(self):
        return f"{self.data_path.name}::{self.variable}({self.time_coordinate}) => {self.level}"

    def __repr__(self):
        return str(self)

    def preload_stats(self, earliest_day: int, latest_day: int, **path_metadata):
        """
        Load in all data between the earliest day and the latest day for future use

        :param earliest_day: The earliest day, inclusive, to load
        :param latest_day: The latest day, inclusive, to load
        :param path_metadata: Metadata to load if the path to the data is templated
        """
        LOGGER.debug(f"{current_thread().name}: Preloading threshold data between day {earliest_day} and day {latest_day} for {self}")
        file_path: pathlib.Path = pathlib.Path(str(self.data_path).format(**path_metadata))
        days_of_year: generic.Sequence[int] = list(range(earliest_day, latest_day + 1))
        self._load_range(file_path=file_path, days_of_year=days_of_year)
        LOGGER.debug(f"{current_thread().name}: Done preloading threshold data between day {earliest_day} and day {latest_day} for {self}")

    def _load_range(self, file_path: pathlib.Path, days_of_year: typing.Sequence[int]):
        with self._lock:
            days_of_year = [
                day_of_year
                for day_of_year in days_of_year
                if day_of_year not in self._data.keys()
            ]

            if not days_of_year:
                # Everything has already been loaded
                return

            if len(days_of_year) == 1:
                days_of_year = days_of_year[0]

            import numpy
            import xarray
            from post_processing.utilities.netcdf import operate_on_variable

            def get_days_of_year(array: xarray.DataArray) -> xarray.DataArray:
                return array.sel(**{self.time_coordinate: days_of_year}).astype(numpy.float32).compute()

            specific_statistics: xarray.DataArray = operate_on_variable(
                path=file_path,
                variable_name=self.variable,
                operation=get_days_of_year
            )

            if self.time_coordinate in specific_statistics.dims:
                for day in days_of_year:
                    self._data[day] = specific_statistics.sel({self.time_coordinate: day}).copy()
            else:
                # The time coordinate may have been reduced out of the dataset by the `.sel` if there was just a
                # single day. If so, there's only one day to assign
                self._data[days_of_year] = specific_statistics.copy()

    def get_stats(self, day_of_year: int, **path_metadata) -> "xarray.DataArray":
        """
        Get statistical value from a netcdf file based on the day of the year
        """
        with self._lock:
            if day_of_year in self._data:
                return self._data[day_of_year]

            file_path: pathlib.Path = pathlib.Path(str(self.data_path).format(**path_metadata))

            day_range: list[int] | int = []

            if day_of_year == 0:
                day_range.append(0)
                if 1 not in self._data:
                    day_range.append(1)
                if 2 not in self._data:
                    day_range.append(2)
            elif day_of_year == 365:
                day_range.append(365)
                if 0 not in self._data:
                    day_range.append(0)
                if 1 not in self._data:
                    day_range.append(1)
            else:
                day_range.append(day_of_year)

                next_day: int = day_of_year + 1 if day_of_year < 365 else 0
                if next_day not in self._data:
                    day_range.append(next_day)

                next_day: int = next_day + 1 if day_of_year < 365 else 0
                if next_day not in self._data:
                    day_range.append(next_day)

            self._load_range(file_path=file_path, days_of_year=day_range)

        return self._data[day_of_year]


def make_apply_thresholds(
    scores: typing.Sequence["numpy.floating"],
    default_score: float
) -> typing.Callable[..., "numpy.ndarray"]:
    """
    Make the universal function used by numpy to use thresholds to establish binning. This is a function
    factory due to array mismatches that occur when using the scores and default_score variables.

    :param scores: The ranking of each threshold in descending order
    :param default_score: The score to give the bins if they exceed the highest percentile
    :returns: A numpy ufunc compatible function
    """
    import numpy
    import xarray

    if len(set(scores)) != len(scores):
        raise ValueError(f"Cannot apply thresholds - there cannot be duplicate scores: {scores}")

    if not isinstance(scores, numpy.ndarray):
        scores: numpy.ndarray = numpy.array(scores)

    def _apply_thresholds(variable: xarray.DataArray, *thresholds: numpy.ndarray) -> numpy.ndarray:
        """
        The ufunc passed to xarray and dask to vectorize the bin comparisons.

        Anomaly definitions cannot be passed in since this will be called as a ufunc

        :param variable: The variable to score
        :param args: a tuple containing a series of raw threshold values,
            scores within a list or array in the order of the threshold arrays, and the default value
        :returns: An array of scores within each threshold
        """
        if len(thresholds) != len(scores):
            raise ValueError(
                f"Cannot apply thresholds - the number of thresholds differs from the number of scores. {len(thresholds)} vs {scores}"
            )

        if numpy.sort(scores)[::-1].tolist() != scores.tolist():
            raise ValueError(f"Cannot apply thresholds - scores are not in ascending order: {scores}")

        output_array: numpy.ndarray = numpy.full(variable.shape, default_score, dtype=numpy.result_type(*scores))
        incorrect_lengths: list[int] = []
        for score_index, threshold in enumerate(thresholds):
            if variable.size != threshold.size:
                incorrect_lengths.append(scores[score_index].item())
                continue
            mask = variable < threshold
            output_array[mask] = scores[score_index]

        if incorrect_lengths:
            raise ValueError(
                f"Could not apply all thresholds - The following scores were of the wrong length: {incorrect_lengths}"
            )

        return output_array
    return _apply_thresholds


def get_day_of_year(dataset: "xarray.Dataset", variable: str) -> int:
    """
    Get the day of the year from a time variable
    """
    assert variable in dataset, f"There is not '{variable}' variable in the given dataset"
    assert dataset[variable].shape == (1,), f"Cannot find the day of year in the '{variable}' variable - there is more than one value"
    day_of_year: numpy.ndarray = dataset[variable].dt.dayofyear.values
    if isinstance(day_of_year, typing.Sequence):
        day_of_year = day_of_year[0]
    day: int = day_of_year.item()
    return day

@timed_function()
def calculate_anomaly(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    variable_to_bin: str,
    thresholds: typing.Sequence[ThresholdDefinition],
    default_score: float,
    time_variable: str = 'time',
    dimension_names: typing.Union[str, typing.Iterable[str]] = 'feature_id',
    output_variable_name: str = "streamflow_anomaly",
    field_metadata: dict[str, typing.Any] = None,
    encoding: dict[str, typing.Any] = None,
    operational_metadata: dict[str, typing.Any] = None,
) -> "pathlib.Path":
    import xarray
    import numpy
    from post_processing.utilities.netcdf import load_netcdf

    if operational_metadata is None:
        operational_metadata = {}

    try:
        with LOAD_LOCK:
            dataset = load_netcdf(path=input_path, full_load=True, chunks=False)
    except:
        LOGGER.error(f"Could not load the netcdf data at '{input_path.resolve()}'")
        raise

    if variable_to_bin not in dataset:
        raise KeyError(
            f"There is no variable name '{variable_to_bin}' within the '{input_path}'. Available variables: {dataset.variables}"
        )
    variable: xarray.DataArray = dataset[variable_to_bin]

    if len(variable.shape) > 1:
        raise NotImplementedError(
            f"Cannot bin on more than one dimension at this time. "
            f"Reorganize operations so that anomaly binning occurs prior to file consolidation."
        )

    if field_metadata is None:
        field_metadata = {}

    if "long_name" not in field_metadata:
        if "long_name" in variable.attrs:
            long_name: str = f"{variable.attrs['long_name']} Anomaly"
        elif "standard_name" in variable.attrs:
            long_name: str = f"{variable.attrs['standard_name']} Anomaly"
        else:
            long_name: str = "Anomaly"
        field_metadata['long_name'] = long_name.title()

    field_metadata = {
        **variable.attrs,
        **field_metadata,
    }

    if not encoding:
        encoding = variable.encoding

    minimum_id = variable[dimension_names].min()
    variable_size: int = variable.size
    threshold_arrays: list[numpy.ndarray] = []
    scores: list[numpy.floating] = []

    # TODO: Initial threshold processing can probably be multithreaded
    #   NOTE: Multithreading will likely lead to segfaults based on how data is loaded. Expect this refactor to be complicated
    thresholds = sorted(thresholds, key=lambda threshold: threshold.level, reverse=True)
    day_of_year: int = get_day_of_year(dataset=dataset, variable=time_variable)

    for threshold in thresholds:
        daily_values: xarray.DataArray = threshold.get_stats(day_of_year=day_of_year, **operational_metadata)
        daily_values = daily_values.where(daily_values[dimension_names] >= minimum_id, drop=True)

        if daily_values.size < variable_size:
            LOGGER.warning(
                f"The size of '{threshold}' (size={daily_values.size}) is too small to match "
                f"'{input_path.name}::{variable_to_bin}({time_variable}) (size={variable_size})' - reindexing to align ids"
            )
            try:
                daily_values = daily_values.reindex(
                    **{
                        daily_values.dims[0]: variable[variable.dims[0]]
                    }
                )
                threshold.update_stats(day_of_year=day_of_year, stats=daily_values)
            except Exception as e:
                if "index has duplicate values" in str(e):
                    unique_variable_index_values, count = numpy.unique(variable[variable.dims[0]].values.ravel(), return_counts=True)
                    duplicate_variable_indices = unique_variable_index_values[count > 1]
                    if duplicate_variable_indices.size > 0:
                        LOGGER.error(
                            f"Could not reindex the daily statistics - the variable had duplicate ids:\n{duplicate_variable_indices}"
                        )
                    unique_threshold_index_values, count = numpy.unique(daily_values[daily_values.dims[0]].values.ravel(), return_counts=True)
                    duplicate_threshold_indices = unique_threshold_index_values[count > 1]
                    if duplicate_threshold_indices.size > 0:
                        LOGGER.error(
                            f"Could not reindex the daily statistics - the statistics had duplicate ids:\n{duplicate_threshold_indices}"
                        )
                LOGGER.error(f"Failed to reindex the {threshold} for {input_path}: {e}")
                raise
        threshold_arrays.append(daily_values.values)
        scores.append(threshold.level)

    apply_thresholds: typing.Callable[[xarray.DataArray, *numpy.ndarray], numpy.ndarray] = make_apply_thresholds(
        scores=scores,
        default_score=default_score,
    )

    input_dimensions: typing.Sequence[typing.Sequence] = [
        [],     # for the initial variable
        *[
            []  # For each threshold array
            for _ in thresholds
        ]
    ]

    anomaly_scores: numpy.ndarray = xarray.apply_ufunc(
        apply_thresholds,
        variable,
        *threshold_arrays,
        input_core_dims=input_dimensions,
        output_dtypes=[numpy.result_type(*scores)],
        dask="parallelized"
    )

    output_array: xarray.DataArray = xarray.DataArray(
        data=anomaly_scores,
        name=output_variable_name,
        dims=dimension_names,
        attrs=field_metadata,
    )

    try:
        updated_dataset: xarray.Dataset = dataset.assign(**{output_array.name: output_array})
    except:
        LOGGER.error(
            f"Could not attach the new anomaly values to the data in '{input_path.resolve()}'"
        )
        raise

    if 'stage' in (operational_metadata or {}):
        updated_dataset.attrs["process_step"] = operational_metadata["stage"]
    elif 'stage' in (field_metadata or {}):
        updated_dataset.attrs['process_step'] = field_metadata["stage"]

    try:
        from post_processing.utilities.netcdf import save_netcdf
        updated_dataset[output_array.name].encoding.update(encoding)
        save_netcdf(path=output_path, dataset=updated_dataset)
        updated_dataset.close()
    except:
        LOGGER.error(f"Could not save the dataset with the newly calculated anomaly data to '{output_path.resolve()}'")
        raise

    return output_path

@timed_function()
def assign_anomaly(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    variable_to_bin: str,
    thresholds: typing.Sequence[ThresholdDefinition],
    default_score: float,
    time_variable: str = 'time',
    dimension_names: typing.Union[str, typing.Iterable[str]] = 'feature_id',
    output_variable_name: str = "streamflow_anomaly",
    field_metadata: dict[str, typing.Any] = None,
    encoding: dict[str, typing.Any] = None
) -> pathlib.Path:
    """
    Open a netcdf file, use data from other netcdf files to flag anomalies,
    and save the resultant values to the same dataset

    :param input_path: path to netcdf file
    :param output_path: Where to save the new data
    :param variable_to_bin: name of the variable to calculate the anomaly off of
    :param thresholds: list of definitions used to identify threshold limits and catagorical values
    :param default_score: The value to assign to a location within the output variable if it exceeded all given
    thresholds
    :param time_variable: The name of the variable containing the valid time of each value
    :param dimension_names: The list of the names of dimensions that should be on the output variable
    :param output_variable_name: The name of the variable in the output that contains the results
    :param field_metadata: Metadata to put on the output variable that defines context
    :param encoding: Specific directions on how to save the netcdf variable when written to disk
    :returns: The path to the saved data
    """

    try:
        written_path: pathlib.Path = calculate_anomaly(
            input_path=input_path,
            output_path=output_path,
            variable_to_bin=variable_to_bin,
            thresholds=thresholds,
            default_score=default_score,
            time_variable=time_variable,
            dimension_names=dimension_names,
            output_variable_name=output_variable_name,
            field_metadata=field_metadata,
            encoding=encoding,
        )
    except:
        LOGGER.error(
            f"Could not calculate the anomaly of '{input_path.name}::{variable_to_bin}({dimension_names})' "
            f"in regards to {thresholds}"
        )
        raise

    return written_path
