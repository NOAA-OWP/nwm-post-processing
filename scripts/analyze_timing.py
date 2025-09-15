#!/usr/bin/env python3
"""
Define functions and tools used to analyze timing logs
"""
import typing
import collections.abc as generic
import pathlib
import logging
import re
import sys
import json
import time

import concurrent.futures as futures

from datetime import datetime
from datetime import timedelta

import numpy
import pandas

from dateutil.parser import parse as parse_date

try:
    from post_processing.configuration import settings
except ImportError:
    from types import SimpleNamespace
    settings: SimpleNamespace = SimpleNamespace()
    settings.logging_config_path = None
    settings.application_path = pathlib.Path.cwd()


ISO_8601_DURATION_PATTERN: re.Pattern = re.compile(r"P((?P<days>\d+)D)?(T((?P<hours>\d+)H)?((?P<minutes>\d+)M)?((?P<seconds>\d+(\.\d+)?)S)?)?")
LINE_PATTERN: re.Pattern = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-\d{4}) \| "
    r"(?P<file_path>[^|]+) ?\| "
    r"(?P<line_number>\d+) \| "
    r"(?P<code_path>[\w.]+) \| "
    r"(?P<function_name>\w+) \| "
    r"(?P<call>[^|]+) ?\| "
    r"(?P<status>\w+) \| "
    r"(?P<duration>[PTDHMS.\d]+)"
    r"( \| (?P<pid>\d+))?"
)

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)

CELL_TRANSFORMERS: dict[str, generic.Callable[[str], typing.Any]] = {
    "date": parse_date,
    "line_number": lambda value: int(float(value)),
    "duration": lambda value: parse_duration(value),
    "pid": lambda value: int(float(value)),
}

def identity(value: str) -> str:
    return value


def parse_duration(iso_8601_duration: str) -> numpy.timedelta64:
    match: re.Match | None = ISO_8601_DURATION_PATTERN.match(iso_8601_duration.strip())
    if not match:
        raise ValueError(f'{iso_8601_duration} does not appear to be an ISO-8601 duration')
    parameters = {
        key: float(value or 0)
        for key, value in match.groupdict().items()
    }
    python_duration: timedelta = timedelta(**parameters)
    return numpy.timedelta64(python_duration)

def parse_file(path: pathlib.Path | str) -> pandas.DataFrame | None:
    LOGGER.info(f"Parsing {path}")
    if isinstance(path, str):
        path = pathlib.Path(path)

    if not isinstance(path, pathlib.Path):
        raise TypeError(
            f"Cannot parse a file - the input must be pathlike but instead received '{path}' (type={type(path)})"
        )

    contents: str = path.read_text()

    match: re.Match | None = LINE_PATTERN.search(contents)

    if match is None:
        raise ValueError(
            f"The contents of '{path}' do not match the timing format"
        )

    rows: list[dict[str, typing.Any]] = []

    while match is not None:
        row: dict[str, typing.Any] = dict(match.groupdict())

        for key, value in row.items():
            if value is not None:
                value = value.strip()
                value = CELL_TRANSFORMERS.get(key, identity)(value)
                row[key] = value
        rows.append(row)
        contents = contents[match.end():].strip()
        match = LINE_PATTERN.search(contents)

    if not rows:
        return None

    data: pandas.DataFrame = pandas.DataFrame(rows)
    LOGGER.info(f"Parsed {len(data)} rows from '{path}'")
    return data

def get_log_files(
    log_config_path: str | pathlib.Path = settings.logging_config_path,
    handler_name: str = 'timing_file'
) -> generic.Sequence[pathlib.Path]:
    if isinstance(log_config_path, str):
        log_config_path = pathlib.Path(log_config_path)

    log_configuration: dict[str, typing.Any] = json.loads(log_config_path.read_text())
    handlers: dict[str, dict[str, typing.Any]] = log_configuration['handlers']
    timing_log_config: dict[str, typing.Any] = handlers.get(handler_name, None)
    if timing_log_config is None:
        raise ValueError(
            f"Could not find a configured timing log"
        )
    filename: str = timing_log_config['filename']
    log_files: generic.Sequence[pathlib.Path] = settings.application_path.glob(f"{filename}*")
    return log_files

def get_all_timing(
    log_config_path: str | pathlib.Path = settings.logging_config_path,
    handler_name: str = 'timing_file'
) -> pandas.DataFrame:
    log_files: generic.Sequence[pathlib.Path] = get_log_files(log_config_path=log_config_path, handler_name=handler_name)
    logs: list[pandas.DataFrame] = []
    with futures.ProcessPoolExecutor(max_workers=3) as executor:
        logs.extend(list(executor.map(parse_file, log_files)))

    if logs is None:
        raise RuntimeError(
            f"Could not generate a log dataset from the '{handler_name}' handler in '{log_config_path}'"
        )

    log: pandas.DataFrame = pandas.concat(logs)

    return log


def get_default_aggregations() -> generic.Mapping[str, tuple[str, str]]:
    aggregations: dict[str, tuple[str, str]] = {
        "max_duration": ("duration", "max"),
        "min_duration": ("duration", "min"),
        "count_items": ("duration", "size"),
        "average": ("duration", "mean"),
        "total": ("duration", "sum"),
    }
    return aggregations


def get_default_grouping() -> generic.Sequence[str]:
    grouping: list[str] = [
        "file_path",
        "code_path",
        "line_number",
        "function_name",
        "status"
    ]
    return grouping


def get_default_sorting() -> generic.Mapping[str, bool]:
    sorting: dict[str, bool] = {
        "average": False,
        "count_items": False
    }
    return sorting


def get_default_earliest_record() -> datetime:
    earliest: datetime = datetime.today().astimezone() - timedelta(days=1)
    return earliest

def get_default_minimum_duration() -> numpy.timedelta64:
    minimum: timedelta = timedelta(seconds=8)
    return numpy.timedelta64(minimum)

def get_default_functions_to_ignore() -> generic.Sequence[str]:
    functions_to_ignore: list[str] = [
        "run",
    ]
    return functions_to_ignore

def get_stats(
    raw_data: pandas.DataFrame,
    aggregations: generic.Mapping[str, tuple[str, str]] = None,
    group_columns: generic.Sequence[str] = None,
    sorting: generic.Mapping[str, bool] = None,
    pid: int = None,
    earliest_record: datetime = None,
    minimum_duration: timedelta = None,
    functions_to_ignore: generic.Sequence[str] = None,
) -> pandas.DataFrame:
    """
    Generate basic statistics
    """
    if not aggregations:
        aggregations = get_default_aggregations()

    if not group_columns:
        group_columns = get_default_grouping()

    if not sorting:
        sorting = get_default_sorting()

    if not isinstance(earliest_record, (numpy.datetime64, datetime)):
        earliest_record = get_default_earliest_record()

    if not isinstance(minimum_duration, (numpy.timedelta64, timedelta)):
        minimum_duration = get_default_minimum_duration()

    if not functions_to_ignore:
        functions_to_ignore = get_default_functions_to_ignore()

    mask: pandas.Series = (raw_data['date'] >= earliest_record) & (~raw_data['function_name'].isin(functions_to_ignore))

    if isinstance(pid, int):
        mask = mask & (raw_data['pid'] == pid)

    raw_data = raw_data[mask]
    raw_data = raw_data.groupby(group_columns)
    raw_data = raw_data.agg(**aggregations)
    raw_data = raw_data[raw_data['min_duration'] >= minimum_duration]
    raw_data = raw_data.reset_index()
    raw_data = raw_data.sort_values(by=list(sorting.keys()), ascending=list(sorting.values()))
    return raw_data


def main() -> int:
    logs: pandas.DataFrame = get_all_timing()
    stats: pandas.DataFrame = get_stats(logs)

    with pandas.option_context("display.max_columns", None, "display.width", 300):
        print(stats.head(15))
    return 0

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z",
    )
    sys.exit(main())
