# National Water Model Post Processing

**Description**:  Post Processing takes National Water Model data, combines the output, and subsets them based on region in order to make model data easier to work with

Other things to include:

  - **Technology stack**: Indicate the technological nature of the software, including primary programming language(s) and whether the software is intended as standalone or as a module in a framework or other ecosystem.
  - **Status**:  Pre-Alpha


## Dependencies

### Languages

This is mostly written in python with the more complicated operations written in C++ in order to make the application more viable from a performance standpoint.
Keep other languages (especially shell scripts) to a minimum to reduce complexity.

### Libraries

The main third party libraries used are [Xarray](https://docs.xarray.dev/en/stable/) and [Pandas](https://pandas.pydata.org/docs/). Targeted deployment environments
run the risk of being highly restricted in terms of what libraries are permissable. As a result, keep reliance on third party libraries to an absolute minimum. `requests`
and `httpx` may be 100x more effective and easier to maintain than your own networking code, but writing your own GET logic here is worth it if it allows us to avoid
needing to request extra libraries in our shared deployment environments

## Installation

See [INSTALL](INSTALL.md).

## Configuration

### Python Configuration

The [configuration](post_processing/configuration.py) module provides a common entry point for accessing python related 
environment and application settings. Reference it in order to know what all is available and add to it as more options are needed.
The `post_processing.configuration.settings` is functionally equivalent to `os.environ` as well.

### Python Logs

Python logging is defined, by default, within [python_log_config.json](resources/python_log_config.json).
It follows the basic python logging dictionary configuration. An alternative logging configuration may be used by
setting the `PP_LOG_CONFIG_PATH` environment variable (case insensitive). 

## Usage

Show users how to use the software.
Be specific.
Use appropriate formatting when showing code snippets.

## How to test the software

From the root of the project, type:

```shell
python -m unittest discover -s test -p "test_*.py"
```

To run the unit tests on the python code. This command tells the active python version to use the `unittest` testing library
(as opposed to something like pytest) and discover unit tests under every file matching the glob of "test_*.py" 
under the `./test` directory
