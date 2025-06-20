#!/usr/bin/env python3
"""
Correct jagged csv files
"""
import os
import typing
import pathlib
import logging
import sys
import argparse
import glob

import pandas

LOGGER: logging.Logger = logging.getLogger(pathlib.Path(__file__).stem)


class Arguments:
    def __init__(self, *args, **kwargs):
        self.output_path: typing.Optional[pathlib.Path] = kwargs.pop("output_path", None)
        self.filenames: typing.List[pathlib.Path] = kwargs.pop("filenames", [])
        self.jagged_column_index: int = kwargs.pop("jagged_column_index", -1)
        self._parse_args(args=args)
        self._validate()

    def _validate(self):
        expanded_filenames: typing.List[pathlib.Path] = []
        for filename in self.filenames:
            if '*' in str(filename):
                for matching_name in glob.glob(str(filename)):
                    expanded_path: pathlib.Path = pathlib.Path(matching_name)
                    if expanded_path.is_file():
                        expanded_filenames.append(expanded_path)
                    else:
                        LOGGER.info(f"'{matching_name}' was collected by the glob '{filename}', but wasn't a file so it won't be added")
            else:
                expanded_filenames.append(filename)
        self.filenames = expanded_filenames
        assert len(self.filenames) > 0, "No valid filenames specified"
        assert all(path.is_file() for path in self.filenames), "Invalid output paths detected. All must be files"

    def _parse_args(self, args):
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            description=__doc__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
        parser.add_argument(
            "jagged_column_index",
            type=int,
            help="The index of the column that contains multiple values"
        )
        parser.add_argument(
            "output_path",
            type=pathlib.Path,
            help="Where to put the finished product"
        )
        parser.add_argument(
            "filenames",
            nargs="+",
            type=str,
            help="The CSV file(s) to correct",
        )

        parsed_parameters: argparse.Namespace = parser.parse_args(args or None)

        self.jagged_column_index = parsed_parameters.jagged_column_index
        self.output_path: pathlib.Path = parsed_parameters.output_path
        here: pathlib.Path = pathlib.Path()
        for filename in parsed_parameters.filenames:
            potential_path: pathlib.Path = pathlib.Path(filename)
            if potential_path.is_absolute():
                self.filenames.append(potential_path)
            else:
                self.filenames.extend(here.rglob(filename))

def normalize_columns(
    jagged_file: pathlib.Path,
    jagged_column_index: int,
    output_frame: typing.Optional[pandas.DataFrame] = None
) -> pandas.DataFrame:
    """
    Load a csv file and correct the jagged columns

    :param jagged_file: The path to a file containing jagged data but claiming to be csv
    :param jagged_column_index: The index of the column that contains multiple values
    :param output_frame: Optional pandas DataFrame that already contains corrected data
    :returns: A dataframe containing the corrected data + any data within the optional output_frame
    """
    try:
        jagged_data = pandas.read_csv(jagged_file, header=0, dtype=str)
        valid_columns: typing.List[str] = jagged_data.columns[:jagged_column_index].tolist()

        rows: typing.List[typing.List[str]] = []

        for row_index, row in jagged_data.iterrows():
            valid_columns_in_row: typing.List[str] = row[valid_columns].tolist()
            extra_columns: typing.List[str] = row[jagged_column_index:].dropna().tolist()

            for value in extra_columns:
                rows.append(valid_columns_in_row + [value])

        corrected_data: pandas.DataFrame = pandas.DataFrame(
            rows,
            columns=[*valid_columns, jagged_data.columns[jagged_column_index]]
        )

        if output_frame is None:
            return corrected_data

        output_frame = pandas.concat([corrected_data, output_frame], axis=1)
        return output_frame
    except Exception as e:
        raise Exception(f"Could not operate on file: {jagged_file}") from e


def correct_and_merge_jagged_data(
    jagged_file_paths: typing.Union[typing.Iterable[pathlib.Path], typing.Iterator[pathlib.Path]],
    jagged_column_index: int,
    output_path: pathlib.Path
):
    """
    Fix jagged data in csv files and write the corrections into a csv file

    :param jagged_file_paths: The paths to the data to correct
    :param jagged_column_index: The index of the column that contains multiple values
    :param output_path: Where to put the results
    """
    results: typing.Optional[pandas.DataFrame] = None

    for jagged_file_path in jagged_file_paths:
        results = normalize_columns(
            jagged_file=jagged_file_path,
            jagged_column_index=jagged_column_index,
            output_frame=results
        )

    if len(results) == 0:
        raise ValueError("Data was not correctly loaded")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output_path, index=False)


def main(*args, **kwargs) -> int:
    """
    Run the main application logic
    """
    parameters: Arguments = Arguments(*args, **kwargs)

    try:
        correct_and_merge_jagged_data(
            jagged_file_paths=parameters.filenames,
            jagged_column_index=parameters.jagged_column_index,
            output_path=parameters.output_path
        )
    except Exception as e:
        LOGGER.error(e, exc_info=True)
        return 1

    LOGGER.info(
        f"The following jagged CSV files were corrected and written to {parameters.output_path}:{os.linesep}"
        f"    - {(os.linesep + '    - ').join(map(str, parameters.filenames))}"
    )
    return 0

if __name__ == "__main__" or __name__.endswith("__main__"):
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )
    sys.exit(main())
