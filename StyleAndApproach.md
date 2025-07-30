# Programming Style and Approach

## Python Version

The targeted version of Python will be 3.12 until changed otherwise within this document by a primary project manager
or maintainer. The targeted version is not a factor that this team has control over and must be accounted for.

## Dependencies

This application suite is targeted towards a shared environment with little to no control. 
That means that access to libraries is **severely** limited. _If_ an operation may be accomplished reasonably
without the need for a third party library, write common functions with as simple of an interface as possible.
A good example of this is the interpretation of `.env` files. Libraries such as `dotenv`
make things far more performant and provide an easy interface we would not have to maintain.
The barrier for the approval and addition of new libraries is high enough for it not be worth the ask,
however.

Contact the primary project manager and maintainers before seeking to add a new dependency.

## Functions over Classes

Functions with no effect on their environment are easier to parallelize. Even with little to no intent to parallelize,
**prefer functions over classes**. Classes are immensely helpful in terms of managing state information and state
information is just a fact of life. Keep them to a minimum if possible. You never know what will need to be passed
across a network or into a new process.

If and when state needs to be managed, consider using data transfer objects 
(such as `dataclasses`, `namedtuple`s, `TypedDict`s, etc) rather than standard classes. This will help guide design
to favor functions over classes.

## Flat Data

As above, you cannot always predict what will be passed out of memory space and when. The deserialization of complex data
always comes with a cost. Some structures you can't really do anything about, such as DataFrames or Xarray Datasets,
but information _**you**_ pass along is something you can manage. Prefer flatter and wider structures in comparison
to neatly organized and nested structures. Flat and wide structures are just easier to transfer without cleverness
or third party libraries.

## Program Flow

Keep application flow as straightforward as possible. Under **no** circumstances are you to call scripts or functions
dynamically. Needing to structure calls to `subprocess` or `os.system` with dynamic naming 
(such as `subprocess.run(f"{path_to_program}/do_{program}_18hours.sh")`) is indicitive of other problems that should be 
addressed. 

> Historical Context: This project would most likely not exist if previous incarnations avoided this approach.

The measure of success is inversely proportional to the amount of time it takes new eyes to identify where logic starts, 
where it ends, and what is affected.

## Scripting

If you have a script that requires features such as loops, collections, shared code, or user input, you have a script 
that needs to be written in python. Shell scripting may only be used for straightforward orchestration, not procedural
logic. Each line of shell scripting that requires a user to understand the significance of `[ ... ]` vs `[[ ... ]]`
or the behavior of programs like `awk` or `sed` is a line of code that makes this project harder to maintain.

That is not to say that helper shell scripts are not allowed - if a helpful bash script may be written to make common 
operations easier, by all means, add it. The _second_ it because a vital operation, however, it **must** be added as a 
straightforward python helper script.

These helper scripts may be found under ./scripts/

## C++

Only break out of python and into C++ explicitly (i.e. not `cython`) when an operation needs to occur that Python
cannot adequetely and efficiently handle. A primary example will be some geospatial comparisons. Without libraries
such as `polars`, `rasterio`, `geopandas`, etc, some vital filters and transformations cannot occur within Python
without pure python control flow, even when working with libraries such as `numpy`. This will become a staggering 
bottleneck. In these situations, save out a short lived file, call the application from within python in order to
maintain a traceable program flow, and read the output. 

Keep the C++ code focused - if a C++ program is intended to do something like subset Netcdf files, do not include
file transfer logic. Yes, this has been an issue in the past.

## Unit Tests

It's easier to write unit tests as you're writing what they'd test or at the end of the day. Knowing that the code
you've written works is an obvious step when moving on to the next step of logic. Writing a short test for it 
**immediately** is the easiest way to a) make sure that it does what you intend for it to do, b) enforce good practices,
and c) keep up with testing.

Writing tests at the end of the day also helps so that you can still be productive after you're absolutely mentally 
exhausted.

Tests don't have to be perfect - just make sure they're there *and correct*. The only thing worse than no tests are wrong
tests.

## Dead Code

Delete dead code as soon as possible. This application suite will end up being complicated and dead code serves as nothing
but a collection of red herrings when it comes to development and debugging. The second functions and files are no longer
needed, delete them.

## Style

### It's not 1987

Write. Descriptive. Variable. And. Function. Names. Source code size is negligible and will **never** be a bottleneck
in terms of what this project aims to accomplish. Do **not** use acronyms and symbolic lettering in cases where they are
not already established public identifiers. For instance, 'ABRFC' is an official name recognized by the US Government, 
so it may be used. "df", despite being used almost universally to mean "dataframe", should not be used as it does not explain
the intended contents.

| **Bad** | **Good**                         | **Example**               |
|---------|----------------------------------|---------------------------|
| `i`     | `row_index`, `item_index`        | `row_index`               |
| `j`     | `column_index`, `inner_index`    | `column_index`            |
| `x`     | `x_coordinate`, `x_position`     | `x_coordinate`            |
| `y`     | `y_coordinate`, `y_position`     | `y_coordinate`            |
| `z`     | `z_coordinate`, `depth_level`    | `z_coordinate`            |
| `t`     | `timestamp`, `time_step`         | `time_step`               |
| `delta` | `{item}_difference`              | `temperature_difference`  |
| `sigma` | `sum_of_{quantity}`              | `sum_of_flows`            |
| `idx`   | `{collection}_index`             | `station_index`           |
| `df`    | `{purpose}_data`                 | `streamflow_data`         |
| `np`    | `numpy`                          | `numpy`                   |
| `pd`    | `pandas`                         | `pandas`                  |

### Loop nesting is indicative of the need for functions

Sometimes you need a loop within a loop within a loop. It's a fact of life. Every extra tab over, however, creates
cognitive complexity. Start breaking code off into functions after the second loop.

### Magic Literals

**ONLY** include hardcoded literals in extreme circumstances. By no means should you **ever** have code that looks like
`output_data["streamflow"] = some_function()`. Even if it will only be used a few times, embed `"streamflow"` into a
constant near the top of the file and instead opt for `output_data[FLOW_VARIABLE] = some_function()`. This will
prevent fat fingering, make it easier to change code, and extend logic. This should be the case for all strings, floats,
and integers.

### Docstrings

Make sure to add triple-quoted docstrings to not only functions and modules, but also to variables and constants. This will help
IDE users. For example:

```python
DEFAULT_SHAPEFILE_URL: str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwm/para_post-processed/RFC"
"""The default root URL for where to look for shapefiles"""
```

When entered this way, IDE users should be able to hover over its use anywhere in the code and see that documentation.

### Type Hinting

Type hint as much as possible. This makes code tracing and debugging easier. It will also make it easier to find issues
through tools such as `MyPy`. Functions should have detailed type hints for **EVERY** input parameter and its output.
Complex type hints can always be defined elsewhere. The following are both acceptable:

```python
def some_function(
    x_coordinate: float,
    y_coordinate: float,
    timestep: int,
    stream_data: pandas.DataFrame,
    attributes: typing.Dict[str, typing.Union[int, str, float, typing.Dict[str, typing.Union[float, int]]]]
) -> typing.Optional[typing.Union[str, typing.List[str]]]:
    """
    This is an example for type hinting

    :param x_coordinate: The position of a value along the x-axis
    :param y_coordinate: The position of a value along the y-axis
    :param timestep: The timestep index at which the value exists
    :param stream_data: The data to index
    :param attributes: Additional metadata
    :returns: Descriptions of the found data
    """
    ...
```

```python
Attributes = typing.Mapping[str, typing.Union[int, str, float, typing.Mapping[str, typing.Union[float, int]]]]
"""A mapping between strings and supported types of descriptive values"""

OneOrMoreStrings = typing.Union[str, typing.Sequence[str]]
"""This will either be a single string or a collection of them"""

def some_function(
    x_coordinate: float,
    y_coordinate: float,
    timestep: int,
    stream_data: pandas.DataFrame,
    attributes: Attributes
) -> typing.Optional[OneOrMoreStrings]:
    """
    This is an example for type hinting

    :param x_coordinate: The position of a value along the x-axis
    :param y_coordinate: The position of a value along the y-axis
    :param timestep: The timestep index at which the value exists
    :param stream_data: The data to index
    :param attributes: Additional metadata
    :returns: Descriptions of the found data
    """
    ...
```

Notice that *even the examples have docstrings*?

> Use vague types if possible. If a list is created in a function and the intent for that data is to only ever be read,
> hint that you are returning a typing.Sequence. This will give a little leeway as to what's constructed and offers
> guidance to the programmer using the function.

### Function Size

If this were easy, your expertise wouldn't be needed. Functions can and will get complicated. No hard or fast rule
is really required, but try to keep functions relatively short. It's not only relatively easy to write brain
dead tests for very short and concise functions, but they are easier to debug. You should definitely start thinking
of breaking up functions once you start reaching ~100 lines (comments and whitespace excluded). Breaking up
functions also has the benefit of making it easier to write code that looks like psuedo-code.

### Logging

Always use dedicated logging per source. You can make enable and disable loggers by name and even filter logs per name.
Naming your logs and keeping them specific make it easier to wade throught the muck and mire.

Every script with an entrypoint should have code near the top, prior to the creation of a module wide logger that looks like:

```python
from post_processing.utilities.logging import setup_logging

if __name__ == "__main__":
    setup_logging()
```

or

```python
import logging

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=settings.log_format,
        datefmt=settings.date_format
    )
```

Choose the first approach for application code, generally within the `post_processing` package, and the second for helper scripts,
such as in `./scripts`. This will ensure that everything is logged as intended and helper logs do not obscure application logs

### Have Predictable Values

Command line parsers like the one from argparse may not be forthcoming with what values to expect on the parsed data. The
same holds true on a lot of dicts.

If a block of data has expected fields and types, put it into a data transfer object, preferrably a dataclass or very simple class.

### NetCDF IO

When performing read and write operations upon NetCDF, ensure that you use `post_processing.utilities.netcdf` and that 
you take advantage of the work directories, temporary directories, and the dataset context manager.

The safest approach to working upon a preexisting NetCDF file is:

```python
import shutil
import tempfile
import pathlib

import xarray

from post_processing.utilities import netcdf
from post_processing.configuration import settings

def do_something(data: xarray.Dataset) -> xarray.Dataset:
    """This is just an example"""
    # do stuff
    return data

input_path: pathlib.Path = pathlib.Path("/path/to/some/file.nc")
output_path: pathlib.Path = pathlib.Path("/path/to/some/directory/whatever.nc")

# This is just an example for the work directory - there is usually already a definition
work_directory: pathlib.Path = settings.intermediate_directory

with tempfile.TemporaryDirectory(str(work_directory)) as temporary_directory:
    temporary_path: pathlib.Path = pathlib.Path(temporary_directory)
    temporary_output_path: pathlib.Path = temporary_path / output_path.name

    with netcdf.load_netcdf(path=input_path) as input_data:
        updated_file: xarray.Dataset = do_something(input_data)
        updated_file.to_netcdf(path=temporary_output_path, engine=settings.default_netcdf_engine)
    shutil.move(temporary_output_path, output_path)
```

It seems like a lot, but it saves a lot of heartache. First and foremost: HDF5 and NetCDF4 have IO gotchas due to the 
underlying IO implementation far and away out of control of anything in Python. Closing a file is not guaranteed to 
actually flush buffers even if `to_netcdf` was called. Go to write to a file that was supposed to be closed and you 
may be surprised to find that xarray still has a hold on it and your encounter an error when writing. Instead of 
dealing with that, follow this pattern.

1. Create a temporary directory
2. Designate a path within the temporary directory to save your work to
3. Open a netcdf file as a context manager
4. Perform your logic
5. Save the product to the temporary path
6. Exit the context manager
7. Call shutil to manually move the data over outside the scope of NetCDF and HDF5

NetCDF is a fragile format - you can still corrupt your data by doing this, but it aligns best with the timing. 
It ensures that all your data is available upon saving and that all work has been flushed prior to attempting to 
get your work to the correct location.

Ensure that the directory for the temporary directory is set as well. File movement and copies don't always work if 
the underlying file systems are different. For best compatibility, run the application and keep your working paths 
on the same disk.
