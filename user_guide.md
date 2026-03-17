# National Water Model Post Processing User Guide

## Purpose

This project turns raw National Water Model (NWM) NetCDF outputs into downstream-ready products by applying JSON-defined processing profiles. A profile tells the application what to do for one specific combination of:

- NWM configuration, such as `short_range` or `analysis_assim`
- Output type, such as `channel_rt`, `forcing`, or `land`
- Region, such as `conus`, `alaska`, `hawaii`, or `puertorico`
- Optional ensemble member, such as `1`, `2`, `3`, or `4`

At runtime, the application:

1. Parses the input filename.
2. Finds all files in the same cycle.
3. Builds a manifest from the filename metadata.
4. Loads every profile in the configured profile directory.
5. Selects the profile or profiles whose `configuration`, `output_type`, `region`, and `member` match the manifest.
6. Runs the matching profile operations in order.
7. Writes final products to the output directory you provide.

The most important practical consequence is this: the application is driven by filename metadata plus profile JSON, not by directory layout alone.

## What You Need Before You Start

You need the following:

- Python 3.11 or newer
- A working virtual environment or equivalent Python environment
- The dependencies declared by this repository
- One or more valid NWM NetCDF input files
- A writable output directory
- A valid profile directory
- Any supplemental resources referenced by the selected profiles, such as masks, routelinks, thresholds, or projection reference datasets

Some operations also rely on external scientific Python and geospatial libraries installed through this project's dependencies.

## Installation

From the repository root:

```shell
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip
pip install -e .
```

This installs the package in editable mode and makes the `post-process` command available in that environment.

## Repository Layout

The directories you will work with most often are:

- `post_processing/`: the Python package and runtime logic
- `resources/profiles/`: built-in processing profiles
- `resources/projections/`: built-in reference datasets used by reprojection operations
- `resources/`: masks, logging config, routelinks, thresholds, and other runtime assets
- `test/`: unit tests
- `Configuration.md`: detailed configuration reference
- `README.md`: overview, examples, and profile discussion

If you plan to create new profiles or new projections, you will usually work in `resources/profiles/`, `resources/projections/`, and your environment configuration.

## How the CLI Works

The main entry points are:

```shell
post-process INPUT_FILE OUTPUT_DIRECTORY
```

or:

```shell
python -m post_processing INPUT_FILE OUTPUT_DIRECTORY
```

Example:

```shell
post-process /path/to/input/nwm.t00z.short_range.channel_rt.f001.conus.nc /path/to/output
```

### Important Runtime Behavior

The application does not process only the one file you pass in. It uses that file to identify the cycle and then discovers the other matching files for that cycle.

For example, if you pass:

```shell
/path/to/input/nwm.t00z.short_range.channel_rt.f018.conus.nc
```

the application will group together the files that belong to the same cycle, configuration, output type, region, and member. That is why you can generally pass any one file from the cycle you want to process.

### Supported Utility Commands

The CLI also supports these subcommands:

```shell
post-process settings
post-process version
post-process validate
```

- `settings`: prints the resolved runtime settings
- `version`: prints the application version and current git commit, when available
- `validate`: attempts to deserialize every profile in the configured profile directory and reports unreadable or invalid profiles

### Useful Runtime Flags

```shell
post-process INPUT_FILE OUTPUT_DIRECTORY --summarize
post-process INPUT_FILE OUTPUT_DIRECTORY --peek
post-process INPUT_FILE OUTPUT_DIRECTORY --max-workers 4
post-process INPUT_FILE OUTPUT_DIRECTORY --analyze
```

- `--summarize`: prints a human-readable summary of the matching profile instead of executing it
- `--peek` or `-p`: logs a header-style preview of each produced output file
- `--max-workers`: caps the multiprocessing worker count used when multiprocessing is available
- `--analyze`: collects runtime profiling information and writes a `.profile` file

## Input File Naming Requirements

Input filenames must match the NWM naming convention expected by the parser. The code uses the following metadata elements from the filename:

- `cycle`
- `configuration`
- `output_type`
- optional `member`
- `frame` or `tminus`
- `region`

The expected structure is effectively:

```text
nwm.t<cycle>z.<configuration>.<output_type[_member]>.<f###|tm###>.<region>.nc
```

Examples:

- `nwm.t02z.short_range.channel_rt.f001.conus.nc`
- `nwm.t06z.medium_range_blend.channel_rt_3.f018.conus.nc`
- `nwm.t12z.analysis_assim.channel_rt.tm00.alaska.nc`
- `nwm.t12z.analysis_assim_no_da.channel_rt.tm0145.hawaii.nc`

### Special Note on Hawaii and Sub-Hourly Frames

Some Hawaii products use sub-hourly frame encodings such as `f00330` or `tm0245`. The project supports this naming pattern.

### Common Mistake

If the filename metadata is wrong, the wrong profile will be selected or no profile will be selected at all. A valid NetCDF file with an incorrect filename is still operationally invalid for this application.

## Configuration and Environment

Runtime settings are exposed through environment variables and the `settings` object in `post_processing.configuration`.

The project supports three common ways to configure settings:

1. Export environment variables manually.
2. Source one of the provided shell scripts such as `environment.sh`, `dev_environment.sh`, or `hpc_environment.sh`.
3. Create a local `.env` file in the application directory.

### `.env` versus `environment.sh`

Use `.env` for user-specific settings that should stay local.

Use `environment.sh`-style scripts when you want shell expansion, reusable team settings, or deployment-oriented setup.

The key difference is that `.env` values are not shell-expanded, while sourced shell scripts are.

### Most Important Settings

The settings that matter most for day-to-day usage are:

- `PP_RESOURCE_PATH`: root directory for supplemental resources
- `PP_PROFILE_PATH`: directory containing profile JSON files
- `PP_MASK_PATH`: directory containing masks used by extraction and subsetting operations
- `PP_ROUTELINK_PATH`: directory containing routelink files
- `PP_THRESHOLD_PATH`: directory containing threshold files for anomaly calculations
- `PP_LOG_CONFIG_PATH`: logging configuration path
- `PP_INTERMEDIATE_DIRECTORY`: where temporary intermediate products are written
- `PP_ALLOW_THREADING`: whether to allow threading in places that support it

### Recommended Configuration Strategy

Use setting-based path templates inside profiles instead of hard-coded absolute paths.

Good:

```json
"reference_dataset_path": "{resource_path}/projections/mercator.nc"
```

Less portable:

```json
"reference_dataset_path": "/some/host/specific/path/resources/projections/mercator.nc"
```

This matters because profile JSON is often reused across environments.

## Quick Start Workflow

For a normal run:

1. Install the project into a Python environment.
2. Ensure your input file is valid NetCDF and correctly named.
3. Ensure the needed profile exists under `PP_PROFILE_PATH`.
4. Ensure referenced resources exist, such as masks or projection reference files.
5. Run `post-process validate`.
6. Run `post-process INPUT OUTPUT --summarize` to confirm the selected profile does what you expect.
7. Run the actual processing command.
8. Optionally rerun with `--peek` if you want quick output inspection in logs.

## How Profile Matching Works

Profile selection is strict on four fields:

- `configuration`
- `output_type`
- `region`
- `member`

At runtime, the application constructs an input manifest from the source filename and then selects every profile whose values match that manifest.

### Matching Details

- `configuration` must match the parsed NWM configuration.
- `output_type` must match the parsed output type.
- `region` must match the parsed region.
- `member` must match exactly after string normalization.

That means:

- Non-ensemble products usually use `"member": null`
- Ensemble products must use the right member number in the profile, such as `1`, `2`, `3`, or `4`

If any one of those fields is wrong, the profile will not be selected.

## Understanding a Profile

A profile is a JSON document that declares:

- What class of input it applies to
- What ordered operations should be performed
- Optional descriptive comments and output defaults

The required top-level fields are:

- `configuration`
- `output_type`
- `region`
- `operations`

Common optional top-level fields include:

- `member`
- `comment`
- `output_file_pattern`
- `date_format`
- `output_directory`
- `intermediate_directory`

### Minimal Profile Shape

```json
{
  "configuration": "short_range",
  "output_type": "forcing",
  "region": "conus",
  "member": null,
  "operations": [
    {
      "operation": "save",
      "directory": "{output_path}",
      "filename_pattern": "{file_name}"
    }
  ]
}
```

### First Operation Rule

The first operation in a profile must be an entry-style operation that can begin from files, such as a path-based operation, a branch, a function call, a load, or `on_each`.

If the first operation expects in-memory data rather than files, profile validation fails.

## Profile Operations Available in This Project

The current schema supports these operation types:

- `extract`: extract or mask subsets into separate outputs
- `subset`: subset gridded data into smaller chunks
- `merge`: merge multiple NetCDF inputs
- `drop`: drop variables
- `rename`: rename variables, dimensions, or related objects depending on configuration
- `attribute`: update attributes
- `save`: copy or save files into final output locations with filename templates
- `branch`: run separate operation chains under named branches
- `function`: call a Python function by import path
- `load`: load files into memory
- `write`: write a dataset to disk
- `echo`: log a message
- `raise`: deliberately raise an error
- `on_each`: apply a nested sequence separately to each input file
- `anomaly`: compute anomaly-style products using thresholds
- `peek`: log state, metadata, and summaries
- `reproject`: reproject gridded data onto a reference grid and CRS
- `unit_conversion`: convert variable units
- `total_over_time`: accumulate a rate variable over time
- `adjust_dimensions`: change dataset dimensions
- `calculate_time_bound`: add time-bound information
- `group_by`: group inputs by time or naming-derived characteristics
- `stream`: perform online or grouped streaming calculations
- `combine`: combine multiple variables into one
- `file_filter`: filter which files continue to later stages

You do not need every operation to build a new profile. Most new profiles are created by copying the nearest existing profile and changing only the operations that differ.

## Profile Authoring Concepts

### Operations Run in Order

Profiles are pipelines. The output from one operation becomes the input to the next unless the operation explicitly says otherwise.

### Many Operations Work on Temporary Files

Intermediate work happens in a generated work directory under `PP_INTERMEDIATE_DIRECTORY`. On successful completion, that work directory is removed. If the run fails, the intermediate directory is often left behind, which is useful for debugging.

### Metadata and Templates

Operations can render templates such as:

- `{output_path}`
- `{resource_path}`
- `{mask_path}`
- `{routelink_path}`
- `{threshold_path}`
- `{Configuration}`
- `{ModelOutputType}`
- `{Region}`
- `{cycle}`
- `{member}`
- `{reference_time__date}`
- `{file_name}`
- `{input_name}`

These values come from several places:

- application settings
- input filename metadata
- NetCDF metadata extracted from the input files
- identifiers extracted by regex patterns in operations
- operation-specific context such as stage number or frame

If a template variable is missing, the operation fails with a message showing which values were available.

### Comments and Disabled Operations

Most operations support:

- `comment`: descriptive text for summaries and maintainers
- `disable`: a switch for temporarily disabling an operation

Be careful with `disable`:

- in debug mode, disabled operations generate warnings
- outside debug mode, disabled operations cause runtime failure

Disabled operations are therefore not a safe long-term way to keep alternate logic in production profiles.

## Recommended Process for Creating a New Profile

Use this workflow.

### Step 1: Find the Closest Existing Profile

Start by copying the profile that is operationally nearest to what you want. Usually the closest match is based on:

- same configuration
- same output type
- same region
- same member pattern

Examples:

- If you are building a new CONUS forcing product, start from an existing `*.forcing.conus.json` profile.
- If you are building an Alaska channel routing product, start from an existing `*.channel_rt.alaska.json` profile.

### Step 2: Name the Profile File Clearly

Profile filenames are not used for matching directly, but clear names matter for maintenance. Follow the established repository naming convention:

```text
<configuration>.<output_type>[optional_member].<region>.json
```

Examples:

- `short_range.forcing.conus.json`
- `medium_range.channel_rt_2.alaska.json`
- `analysis_assim.channel_rt.alaska.json`

### Step 3: Set the Top-Level Matching Fields Correctly

These values determine whether the profile is selected at runtime.

Example:

```json
{
  "configuration": "short_range",
  "output_type": "forcing",
  "region": "conus",
  "member": null,
  "operations": [
  ]
}
```

Use `null` for non-member products. Use an integer for member-specific profiles.

### Step 4: Build the Operations Pipeline

Decide what the product needs:

- variable drops
- renames
- unit conversion
- reprojection
- grouping
- extraction or subsetting
- save behavior

Keep the pipeline as small as possible at first. Get a minimal path working, then add complexity.

### Step 5: Prefer Templates Over Hard-Coded Paths

Use settings-backed paths such as:

- `{resource_path}/projections/my_projection.nc`
- `{mask_path}/my_mask.nc`
- `{routelink_path}/RouteLink_CONUS.nc`

Avoid environment-specific absolute paths unless the profile truly cannot be portable.

### Step 6: Validate the Profile

Run:

```shell
post-process validate
```

This catches malformed JSON and schema mismatches that prevent profile deserialization.

### Step 7: Summarize Before Running

Run:

```shell
post-process /path/to/an/input/file.nc /path/to/output --summarize
```

Confirm:

- the intended profile is selected
- the operation order is correct
- directory and filename templates look right
- branch outputs match your intent

### Step 8: Run Against a Small Real Sample

Use a representative file from the exact product type you are targeting. This is especially important for operations that depend on real variable names, coordinates, or CRS metadata.

## Example: A Typical Forcing Profile Structure

This pattern appears frequently for gridded forcing products:

1. Drop everything except the needed variables and coordinates.
2. Derive new fields, such as totals over time.
3. Reproject onto a target grid.
4. Group or subset results.
5. Save the finished products.

Example fragment:

```json
{
  "operations": [
    {
      "operation": "drop",
      "exclude": true,
      "fields": ["time", "x", "y", "reference_time", "crs", "T2D", "RAINRATE"]
    },
    {
      "operation": "total_over_time",
      "rate_variable_name": "RAINRATE",
      "total_variable_name": "PRECIP",
      "output_unit": "mm",
      "input_quantity_unit": "mm",
      "input_time_unit": "seconds",
      "time_unit": "hours",
      "amount_of_time": 1
    },
    {
      "operation": "reproject",
      "reference_dataset_path": "{resource_path}/projections/mercator.nc"
    },
    {
      "operation": "save",
      "directory": "{output_path}",
      "filename_pattern": "{file_name}"
    }
  ],
  "configuration": "short_range",
  "output_type": "forcing",
  "region": "conus",
  "member": null
}
```

## Creating a New Projection Reference Dataset

The word projection means two related things in this project:

1. The target coordinate reference system, or CRS
2. The exact target grid layout, including x coordinates, y coordinates, shape, and affine transform

The `reproject` operation does not just want a CRS string. It wants a NetCDF file that describes the full target grid.

### Built-In Projection Reference Files

The repository already includes these reference datasets under `resources/projections/`:

- `mercator.nc`
- `sphere_lambert.nc`
- `alaska_mercator.nc`
- `alaska_sphere_stereographic.nc`
- `hawaii_mercator.nc`
- `hawaii_sphere_lambert.nc`
- `prvi_mercator.nc`
- `prvi_sphere_lambert.nc`

If one of those already matches your target product, use it rather than creating a new one.

### What a Valid Projection Reference Dataset Must Contain

At minimum, the reference dataset must contain:

1. An x-coordinate variable
2. A y-coordinate variable
3. A CRS variable
4. CRS metadata with a projection string that rasterio can interpret

By default, the code looks for:

- x coordinate: `x`, or `lon` if present
- y coordinate: `y`, or `lat` if present
- CRS variable: `crs`, `CRS`, or `mercator`
- CRS string attribute: `esri_pe_string`, or `spatial_ref`

The coordinate arrays are used to derive the affine transform. That means the dataset must describe a regular grid well enough for spacing to be inferred from the coordinate values.

### Practical Requirements for New Projection Files

When creating a new projection reference dataset, make sure it has all of the following:

- 1D x coordinate values in projected units or longitude values, depending on the grid design
- 1D y coordinate values in projected units or latitude values, depending on the grid design
- a CRS variable with WKT stored in `esri_pe_string` or `spatial_ref`
- coordinate names that either match the defaults or are explicitly declared in the profile's `reproject` operation
- the target grid extent and resolution you actually want the output products to use

### Recommended Authoring Process for a New Projection File

Use this process.

#### Step 1: Decide the Target Grid

Write down:

- target CRS
- horizontal resolution
- x extent
- y extent
- grid dimensions
- whether coordinates represent cell centers

The reprojection logic assumes coordinate arrays describe a regular, evenly spaced grid and computes the affine transform from them.

#### Step 2: Create a NetCDF File That Encodes Only the Grid Definition

The file does not need to contain science variables for this purpose. It only needs enough information to describe the target projection and grid.

At minimum, include:

- an x variable
- a y variable
- a CRS variable with appropriate attributes

If your product conventions use names other than `x`, `y`, and `crs`, that is acceptable, but you must override those names in the profile.

#### Step 3: Store the File Somewhere Stable

The usual place is:

```text
resources/projections/
```

If you store it elsewhere, set `PP_RESOURCE_PATH` or reference the external location explicitly.

#### Step 4: Reference It from a Profile

Typical configuration:

```json
{
  "operation": "reproject",
  "reference_dataset_path": "{resource_path}/projections/my_projection.nc"
}
```

#### Step 5: Override Names Only If Necessary

If your input or target datasets use non-default variable names, you can override them:

```json
{
  "operation": "reproject",
  "reference_dataset_path": "{resource_path}/projections/my_projection.nc",
  "crs_variable": "grid_mapping",
  "crs_string_attribute": "spatial_ref",
  "x_variable": "longitude",
  "y_variable": "latitude",
  "reference_crs_variable": "projection_definition",
  "reference_crs_string_attribute": "spatial_ref",
  "reference_x_variable": "xcoord",
  "reference_y_variable": "ycoord",
  "output_crs_variable_name": "projection_definition"
}
```

### What the Reproject Operation Actually Uses

When `reproject` runs, it uses:

- the input file's CRS variable and coordinate names
- the reference dataset's CRS variable and coordinate names
- an affine transform derived from each dataset's x and y coordinate arrays
- rasterio to warp the data onto the target grid

Variables are reprojected when they include the spatial dimensions expected by the operation.

### Important Limits and Failure Modes for New Projections

Keep these in mind.

#### Missing Reference File

If `reference_dataset_path` resolves to a path that does not exist, runtime fails with a file-not-found error.

#### Missing CRS Variable or Attribute

If the dataset lacks the CRS variable or the expected CRS string attribute, reprojection fails.

#### Wrong Coordinate Names

If the file uses names other than the defaults and the profile does not override them, reprojection fails.

#### Irregular or Unexpected Coordinate Arrays

The affine transform is inferred from coordinate spacing. If coordinate arrays are malformed or not what you intend, output alignment will be wrong even if the code technically runs.

#### Validation Does Not Fully Prove Reprojection Will Work

`post-process validate` verifies that profiles deserialize correctly. It does not guarantee that every external file referenced by every runtime operation is correct. For new projections, always run a real sample after validation.

## Adding a New Profile That Uses a New Projection

This is the most common advanced customization path.

Use this sequence:

1. Create or obtain the projection reference NetCDF file.
2. Place it under `resources/projections/` or a configured external resource path.
3. Copy the nearest existing profile.
4. Update the top-level matching fields.
5. Add or update the `reproject` operation with the correct `reference_dataset_path`.
6. Adjust variable names if your input or target dataset does not use the defaults.
7. Run `post-process validate`.
8. Run `--summarize` against a representative input file.
9. Run the actual profile on a small sample.
10. Inspect outputs with `--peek` or external NetCDF inspection tools.

## Save, Output, and Naming Patterns

Most production profiles end in one or more `save` operations. A `save` operation commonly controls:

- destination directory
- filename pattern
- optional identifier extraction from filenames
- whether new paths should be forwarded to later operations

Typical example:

```json
{
  "operation": "save",
  "directory": "{output_path}/RFC/{RFC}/",
  "filename_pattern": "{file_name}",
  "identifier_pattern": "(?P<rfc>[A-Za-z]{2}[Rr][Ff][Cc])"
}
```

### Design Advice for Save Patterns

- Use metadata placeholders that actually exist at that point in the pipeline.
- Prefer `{file_name}` when you want to preserve an already-constructed filename.
- Use identifier patterns only when you need additional metadata derived from filenames.
- Keep output naming conventions stable across related profiles.

## Debugging and Validation Workflow

When building or changing profiles, use this order.

### 1. Validate the Profile Directory

```shell
post-process validate
```

This catches unreadable JSON and schema problems.

### 2. Print Resolved Settings

```shell
post-process settings
```

Use this when path-related templates are not resolving the way you expect.

### 3. Summarize the Matching Profile

```shell
post-process /path/to/input/file.nc /path/to/output --summarize
```

Use this before first execution of any new profile.

### 4. Add `peek` Operations While Developing

Insert a `peek` operation into the profile when you need to inspect:

- current files
- available metadata
- dataset shape and attributes
- the sequence of operations completed so far

### 5. Use `--peek` on Outputs

```shell
post-process /path/to/input/file.nc /path/to/output --peek
```

This logs a preview of produced output files after the run completes.

## Common Problems and How to Fix Them

### Problem: No profile is found

Check all of the following:

- the input filename is correctly formatted
- `configuration` matches exactly
- `output_type` matches exactly
- `region` matches exactly
- `member` matches exactly, including `null` versus numeric member values
- `PP_PROFILE_PATH` points to the profile directory you expect

### Problem: `validate` reports unreadable profiles

Usually this means one of:

- invalid JSON syntax
- missing required keys
- misspelled operation names
- missing required fields for a given operation
- unsupported extra structure in an operation definition

Fix the profile JSON first before attempting runtime debugging.

### Problem: Runtime fails inside `reproject`

Check:

- the reference projection file exists
- the input file has the expected CRS variable
- the reference file has the expected CRS variable
- the expected CRS string attribute exists
- x and y variable names are correct on both source and target
- the target grid dataset actually represents the intended grid

### Problem: Template rendering fails

This means a placeholder such as `{RFC}` or `{reference_time__date}` was used before it became available. Either remove the placeholder, move the operation later, or add the logic that creates that metadata earlier in the pipeline.

### Problem: Intermediate files disappear before inspection

On successful runs, the generated working directory is cleaned up automatically. If you need deeper inspection, temporarily add `peek` operations or induce a controlled failure after the stage you want to inspect.

### Problem: Outputs were produced but look misaligned

This is often a projection-definition problem, not a profile-matching problem. Re-check the reference dataset's coordinate values, CRS metadata, extent, and resolution.

## Guidance for Maintaining a Clean Profile Library

As the number of profiles grows, consistency matters.

Recommended practices:

- copy the nearest existing profile instead of inventing a new layout from scratch
- keep filename conventions predictable
- use comments for non-obvious logic
- use settings-based templates for paths
- keep branch names meaningful, such as `WMS` or `RFC`
- avoid dead configuration and disabled operations in production profiles
- validate after every structural change
- test new projections with a real sample before considering them complete

## Suggested Workflow for Contributors

When adding a new product or adapting an existing one:

1. Identify the closest existing profile.
2. Copy it and adjust only the parts that are genuinely different.
3. Add or reference the needed projection file, if reprojection is required.
4. Validate all profiles.
5. Summarize the profile behavior.
6. Run on one representative cycle.
7. Inspect outputs.
8. Only then broaden testing to additional cycles or regions.

## Testing

To run the unit tests from the repository root:

```shell
python -m unittest discover -s test -p "test_*.py"
```

This is useful for catching regressions in the Python code, although profile correctness still needs direct validation and representative runtime tests.

## Final Checklist for New Profiles and Projections

Before you consider a new profile or new projection ready, verify all of the following:

- the input filename pattern matches what the parser expects
- the profile top-level metadata matches the intended product exactly
- every operation has the required keys
- every path template resolves correctly in your environment
- referenced masks, routelinks, thresholds, and projection files exist
- `post-process validate` succeeds
- `--summarize` output matches your intent
- a real sample run completes successfully
- the generated outputs have the expected names, variables, coordinates, and CRS metadata

If you follow that sequence, most integration problems show up early and in a form that is straightforward to diagnose.