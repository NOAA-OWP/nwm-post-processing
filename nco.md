# NCO Tutorial for Editing and Transforming NetCDF Files

This tutorial explains how to use NCO (NetCDF Operators) tools like `ncks`, `ncatted`, `ncap2`, `ncrename`, and `ncrcat` to manipulate NetCDF files, especially for hydrological model output like NWM.

---

## 1. **Dropping Variables with `ncks`**
To drop variables (e.g., `nudge`, `velocity`) and write to a new file:

```bash
ncks -O -C -x -v nudge,velocity input.nc output.nc
```

## 📄 Command Breakdown

| Part                | Meaning                                                                    |
| ------------------- | -------------------------------------------------------------------------- |
| `ncks`              | NetCDF Kitchen Sink — tool to extract, slice, or edit NetCDF files         |
| `-O`                | **Overwrite** the output file if it exists                                 |
| `-C`                | **Coordinate output** — include coordinate variables even if not requested |
| `-x`                | **Exclude mode** — remove specified variables instead of extracting them   |
| `-v nudge,velocity` | Specifies **variables** to operate on (`nudge`, `velocity`)                |
| `input.nc`          | Input NetCDF file                                                          |
| `output.nc`         | Output NetCDF file (will be written or overwritten)                        |

---

## 🧠 What This Does

In Python/xarray terms, this command:

```python
import xarray
ds = xarray.open_dataset("input.nc")
ds = ds.drop_vars(["nudge", "velocity"])
ds.to_netcdf("output.nc")
```

* It opens the `input.nc` NetCDF file.
* It **removes** the variables `nudge` and `velocity`.
* It writes the remaining dataset to `output.nc`, overwriting it if it exists.
* It **keeps all coordinate variables**, even though none were explicitly selected.

---

## ✅ Why Use This?

If your NetCDF file contains many variables and you want to:

* Remove problematic or unused variables
* Shrink file size
* Clean up before merging or analysis

It is a fast, CLI-based alternative to using Python/xarray.

---

## 2. **Adding or Modifying Attributes with `ncatted`**

### 🧰 Add a new attribute to a variable:
```bash
ncatted -O -a units,streamflow,o,c,"cubic meters per second" input.nc
```

### 📄 Breakdown

| Part                          | Meaning                                                                        |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `ncatted`                     | NCO Attribute Editor — modifies variable or global attributes in a NetCDF file |
| `-O`                          | **Overwrite** the file directly (in-place edit)                                |
| `-a units,streamflow,o,c,...` | Edit the `units` attribute of the `streamflow` variable                        |
| `o`                           | **Operation**: overwrite (`o` = overwrite existing value)                      |
| `c`                           | **Type**: character string (NetCDF type)                                       |
| `"cubic meters per second"`   | New value to assign                                                            |
| `input.nc`                    | Target NetCDF file                                                             |

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_dataset("input.nc")
ds["streamflow"].attrs["units"] = "cubic meters per second"
ds.to_netcdf("input.nc")  # overwrites original
```

### ✅ Purpose:

* Updates the **units** attribute for the `streamflow` variable
* Useful for standardizing metadata (e.g., for CF compliance)

---

## 🧰 Modify global attribute:

```bash
ncatted -O -a title,global,o,c,"Processed NWM Output" input.nc
```

---

### 📄 Breakdown

| Part                          | Meaning                                  |
|-------------------------------| ---------------------------------------- |
| `-a title,global,o,c,<value>` | Edit the **global** attribute `title`    |
| `global`                      | Target the global scope (not a variable) |
| `"Processed NWM Output"`      | New string value for the attribute       |

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_dataset("input.nc")
ds.attrs["title"] = "Processed NWM Output"
ds.to_netcdf("input.nc")
```

---

### ✅ Purpose:

* Sets or updates the global **title** attribute
* Useful for tagging datasets with metadata, provenance, or processing history

---

## 🧑‍💻 Why Use `ncatted`?

It's:

* Faster than opening/saving with Python for minor metadata changes
* Precise and scriptable for batch workflows
* Friendly to CF-compliance workflows
---

## 3. **Transform Variable Values with `ncap2`**

### 🧰 Multiply all valid values in a variable:

```bash
ncap2 -O -s "streamflow=(streamflow!=-999900)*streamflow*1.2 + (streamflow==-999900)*streamflow" input.nc output.nc
```

---

### 📄 Breakdown

| Component                            | Description                                                               |
| ------------------------------------ | ------------------------------------------------------------------------- |
| `ncap2`                              | NCO Arithmetic Processor — evaluates math expressions on NetCDF variables |
| `-O`                                 | Overwrites the output file                                                |
| `-s`                                 | Script string — defines the expression to execute                         |
| `streamflow=...`                     | Overwrites the `streamflow` variable in-place                             |
| `(streamflow!=-999900)`              | Boolean mask: `True` (1) where data is not missing                        |
| `*streamflow*1.2`                    | Scales valid values by 1.2                                                |
| `+ (streamflow==-999900)*streamflow` | Preserves the fill values (leaves `-999900` untouched)                    |
| `input.nc output.nc`                 | Input and output NetCDF files                                             |

---

### 🧠 Python Equivalent (xarray-style):

```python
import xarray
ds = xarray.open_dataset("input.nc")
sf = ds["streamflow"]
ds["streamflow"] = xarray.where(sf != -999900, sf * 1.2, sf)
ds.to_netcdf("output.nc")
```

---

### ✅ Purpose:

* This scales **valid `streamflow` values** by 20% (multiply by 1.2)
* Missing values (`-999900`) remain **unchanged**, not modified or scaled
* This is a **safe way to do arithmetic while respecting fill/missing values**

---

## 📐 How Dimensionality Affects the Outcome

The command works correctly **regardless of dimensionality**, because:

* NCO applies operations **element-wise** (like NumPy broadcasting)
* Whether `streamflow` is 1D (`feature_id`) or 2D (`time, feature_id`), the boolean masks and math apply per-element

### 📌 If `streamflow(feature_id)`:

* Acts on a 1D array of 1000+ values
* The mask checks each index independently

### 📌 If `streamflow(time, feature_id)`:

* Acts on a 2D matrix
* Each element in the 2D array is checked and scaled if not missing

---

### ⚠️ Important Notes:

* The output type and shape will match the input unless you explicitly reshape or cast
* This does **not** modify `_FillValue` metadata — only raw values

---

### 🧪 Validation:

Afterward, you can verify the result:

```bash
ncdump -v streamflow output.nc
```

Or:

```python
import xarray
print(xarray.open_dataset("output.nc")["streamflow"])
```
---

## 4. **Renaming Coordinates and Dimensions with `ncrename`**

## 🧰 Rename a **variable**:

```bash
ncrename -O -v feature_id,reach_id input.nc
```

---

### 📄 Explanation

| Flag                     | Meaning                                            |
| ------------------------ | -------------------------------------------------- |
| `ncrename`               | NCO Rename Tool — renames variables and dimensions |
| `-O`                     | Overwrites the original file                       |
| `-v feature_id,reach_id` | Rename **variable** `feature_id` → `reach_id`      |
| `input.nc`               | Input file (modified in place)                     |

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_dataset("input.nc")
ds = ds.rename_vars({"feature_id": "reach_id"})
ds.to_netcdf("input.nc")
```

---

## 🧰 Rename a **dimension**:

```bash
ncrename -O -d feature_id,reach_id input.nc
```

---

### 📄 Explanation

| Flag                     | Meaning                                        |
| ------------------------ | ---------------------------------------------- |
| `-d feature_id,reach_id` | Rename **dimension** `feature_id` → `reach_id` |

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_dataset("input.nc")
ds = ds.rename_dims({"feature_id": "reach_id"})
ds.to_netcdf("input.nc")
```

---

## 🧰 Rename both **variable** and **dimension**:

```bash
ncrename -O -v feature_id,reach_id -d feature_id,reach_id input.nc
```

---

### 📄 Explanation

| Combined Flags        | Meaning                                            |
| --------------------- | -------------------------------------------------- |
| `-v ...` and `-d ...` | Rename both the **variable** and the **dimension** |

This is often needed when a variable and its dimension **share the same name**, e.g.:

```text
int feature_id(feature_id)
```

Renaming both ensures internal consistency:

```text
int reach_id(reach_id)
```

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_dataset("input.nc")
ds = ds.rename({"feature_id": "reach_id"})  # handles vars & dims
ds.to_netcdf("input.nc")
```

---

## ✅ When to use which?

| Scenario                                        | Use Command                          |
| ----------------------------------------------- | ------------------------------------ |
| Rename a variable only                          | `-v feature_id,reach_id`             |
| Rename a dimension only                         | `-d feature_id,reach_id`             |
| Rename both (dimension and associated variable) | `-v ... -d ...` or Python `rename()` |

---

## ⚠️ Caution:

* If the dimension name is used elsewhere (e.g. coordinate variables), renaming just the variable **without the dimension** can lead to inconsistent or broken metadata.

---
## 5. **Combining Files with `ncrcat` and `ncecat`**


### 🧰  Concatenate multiple NetCDF files along the **record dimension**

```bash
ncrcat nwm.t00z.*.nc output_concat.nc
```

---

### 📄 Explanation

| Flag or Part       | Meaning                                            |
| ------------------ | -------------------------------------------------- |
| `ncrcat`           | NCO Concatenate along Record (unlimited) Dimension |
| `nwm.t00z.*.nc`    | Input files to concatenate (glob pattern)          |
| `output_concat.nc` | Output NetCDF file (concatenated)                  |

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_mfdataset("nwm.t00z.*.nc", concat_dim="time")
ds.to_netcdf("output_concat.nc")
```

---

### ✅ Purpose:

* Combine multiple NetCDF files into a **single file** along the **unlimited dimension**, typically `time`.

---

### ⚠️ Gotchas & Considerations:

| Gotcha                                        | Explanation                                                                                                        |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| ✅ **Requires a record (unlimited) dimension** | One of the files must have a dimension marked as `UNLIMITED` (e.g., `time`) — check with `ncdump -h`.              |
| ✅ **Variables must match**                    | Variables across files must have the same name, shape, and metadata (e.g., `streamflow(feature_id)` in all files). |
| ❌ **Silent dimension mismatch errors**        | If one file lacks a matching structure, `ncrcat` may skip it or corrupt output. Always validate with `ncdump`.     |
| 🧠 **Glob expands in shell, not NCO**         | Be sure globbing is done by the shell (`*`) — avoid using it in quotes.                                            |
| 💡 **Use `ncks --mk_rec_dmn time` if needed** | Preprocess files to promote `time` to record dimension if missing.                                                 |

---

## 🧰 Concatenate specific **variable(s)** only

```bash
ncrcat -v streamflow file1.nc file2.nc file3.nc combined.nc
```

---

### 📄 Explanation

| Flag or Part            | Meaning                                          |
| ----------------------- | ------------------------------------------------ |
| `-v streamflow`         | Include only the `streamflow` variable in output |
| `file1.nc file2.nc ...` | Files to concatenate                             |
| `combined.nc`           | Output file                                      |

---

### 🧠 Python Equivalent:

```python
import xarray
ds = xarray.open_mfdataset(["file1.nc", "file2.nc", "file3.nc"], concat_dim="time")
ds = ds[["streamflow"]]  # keep only this var
ds.to_netcdf("combined.nc")
```

---

### ✅ Purpose:

* Concatenates only a subset of variables (e.g., `streamflow`) from multiple files — useful for trimming down file size and focusing on what matters.

---

### ⚠️ Gotchas & Considerations:

| Gotcha                                           | Explanation                                                                                                    |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| ✅ **Must match in structure**                    | All `streamflow` variables must have the same dimension structure (e.g., `time, feature_id`) across all files. |
| ✅ **Coordinate vars are included automatically** | Even if not requested, dimensions and coords used by `streamflow` are kept.                                    |
| ❌ **Metadata conflicts**                         | If `streamflow` has different attributes across files, `ncrcat` may fail or issue warnings.                    |
| 🧠 **Efficient for post-processed output**       | This is a great way to extract and merge a single variable from many model runs or forecasts.                  |

---

## ✅ When to Use Which:

| Use Case                                            | Use Command                                 |
| --------------------------------------------------- | ------------------------------------------- |
| Combine full files along `time`                     | `ncrcat nwm.t00z.*.nc output_concat.nc`     |
| Combine only a single variable (e.g., `streamflow`) | `ncrcat -v streamflow file*.nc combined.nc` |

---

## 🔍 How to Validate Output:

```bash
ncdump -h output_concat.nc
```

Look for:

```text
time = UNLIMITED ; // (N records)
double streamflow(time, feature_id)
```

Or inspect in Python:

```python
import xarray
xarray.open_dataset("combined.nc")
```

## `ncrcat` vs `ncecat`

---

## 🧩 Core Difference: Dimension Strategy

| Tool     | Purpose                                            | Behavior                                                        |
| -------- | -------------------------------------------------- | --------------------------------------------------------------- |
| `ncrcat` | Concatenate along an **existing record dimension** | Requires an existing unlimited (record) dimension (e.g. `time`) |
| `ncecat` | Concatenate by adding a **new record dimension**   | Adds a new outermost dimension called `record`                  |

---

## 🔀 Analogy in Python (xarray)

```python
import xarray
ds1 = xarray.load_dataset("/path/to/one.nc")
ds2 = xarray.load_dataset("/path/to/two.nc")
ds3 = xarray.load_dataset("/path/to/three.nc")
xarray.concat([ds1, ds2, ds3], dim="time")   # ← ncrcat
xarray.concat([ds1, ds2, ds3], dim="record") # ← ncecat
```

---

## 🧰 `ncrcat`: **Concatenate over time**

### ✅ Use when:

* Files already have a **record (unlimited) dimension**, such as `time`
* You want to combine them into a longer time-series

### 🔄 Command:

```bash
ncrcat file1.nc file2.nc file3.nc output.nc
```

* Input: `streamflow(time, feature_id)` in each file
* Output: `streamflow(time=3, feature_id)`

---

## 🧰 `ncecat`: **Force stack with new record dim**

### ✅ Use when:

* Input files lack a record dimension (e.g., just `feature_id`)
* You want to simulate stacking over a new axis (like `time`, `run`, `realization`)

### 🔄 Command:

```bash
ncecat file1.nc file2.nc file3.nc output.nc
```

* Input: `streamflow(feature_id)`
* Output: `streamflow(record=3, feature_id)`

---

## 🧠 Why This Matters

### Example use cases:

| Situation                          | Use `ncrcat`                 | Use `ncecat`            |
| ---------------------------------- | ---------------------------- | ----------------------- |
| Files have `time` dimension        | ✅ Yes                        | ❌ No                    |
| Files lack any record dim          | ❌ No                         | ✅ Yes                   |
| You want to simulate an ensemble   | ❌ No                         | ✅ Yes                   |
| You want to merge time series data | ✅ Yes                        | ❌ No                    |
| You’ll run `ncrcat` later          | ✅ Yes (after `--mk_rec_dmn`) | ✅ (pre-processing step) |

---

## 🧠 Technical Notes

| Feature                   | `ncrcat`                               | `ncecat`                         |
| ------------------------- | -------------------------------------- | -------------------------------- |
| Record dim req?           | ✅ Yes (e.g., `time` must be unlimited) | ❌ No (adds new `record` dim)     |
| Output dim name           | Original (e.g., `time`)                | Always `record` (unless renamed) |
| Compatible with `ncrcat`? | ✅ Concatenated again over `time`       | ✅ If you rename `record → time`  |

---

## 🔧 Common `ncecat` pipeline:

```bash
# Step 1: Add record dim
ncecat *.nc temp.nc

# Step 2: Rename 'record' to 'time'
ncrename -d record,time temp.nc

# Step 3: Reorder dimensions (optional)
ncpdq -a time,feature_id temp.nc final.nc
```

---

## 📌 Summary Table

| Tool     | Requires record dim | Adds new dim | Best for                              |
| -------- | ------------------- | ------------ | ------------------------------------- |
| `ncrcat` | ✅ Yes               | ❌ No         | Time-series or unlimited-dim stacking |
| `ncecat` | ❌ No                | ✅ Yes        | Ensemble/replica stacking             |

## 🔍 Situation:

Your NetCDF file looks like this (via `ncdump -h`):

```text
dimensions:
  time = UNLIMITED ; // (1 currently)
  feature_id = 1000 ;

variables:
  int streamflow(feature_id) ;
  int64 time(time) ;
```

So:

* ✅ `time` exists and is a **record/unlimited** dimension
* ❌ `streamflow` does **not** use `time` — it’s only defined over `feature_id`

---

## 🧰 `ncrcat`: Will **NOT** work as intended

```bash
ncrcat file1.nc file2.nc file3.nc output.nc
```

### ❌ What happens:

* `ncrcat` only stacks variables that **use the record dimension** (e.g., `streamflow(time, feature_id)`)
* Since `streamflow` is just `streamflow(feature_id)`, it **will not be stacked**
* Result: `streamflow` is simply taken from the **first file only** (no time series created!)

### 🧠 Python analogy:

```python
import xarray
ds1 = xarray.load_dataset("/path/to/one.nc")
ds2 = xarray.load_dataset("/path/to/two.nc")
ds3 = xarray.load_dataset("/path/to/three.nc")
xarray.concat([ds1, ds2, ds3], dim="time")  # but streamflow lacks time → not stacked
```

---

## 🧰 `ncecat`: Will **work as expected**

```bash
ncecat file1.nc file2.nc file3.nc output.nc
```

### ✅ What happens:

* Adds a **new dimension `record`** and **forces all variables** to use it
* `streamflow(feature_id)` → becomes → `streamflow(record, feature_id)`
* Each input file becomes a new slice along the `record` axis

### 🧠 Python analogy:

```python
import xarray
ds1 = xarray.load_dataset("/path/to/one.nc")
ds2 = xarray.load_dataset("/path/to/two.nc")
ds3 = xarray.load_dataset("/path/to/three.nc")
xarray.concat([ds1, ds2, ds3], dim="record")  # even if streamflow lacks time, it's stacked
```

---

## ✅ Real-World Fix for `ncrcat` to work:

You must **promote `time` as a dimension of `streamflow`**, e.g. make:

```text
streamflow(time, feature_id)
```

Instead of:

```text
streamflow(feature_id)
```

You can do this with `ncap2`:

```bash
ncap2 -s "streamflow_new[time,feature_id]=streamflow" file.nc temp.nc
ncrename -v streamflow_new,streamflow temp.nc
```

Then `ncrcat` will concatenate as expected.

---

## 📌 Summary: Which Works?

| Structure                | `ncrcat`              | `ncecat`              |
| ------------------------ | --------------------- | --------------------- |
| `streamflow(time, ...)`  | ✅ works               | ✅ works               |
| `streamflow(feature_id)` | ❌ skips or duplicates | ✅ stacks across files |

---

## 6. **Extra: Extract a list of specific feature_ids**

## 🧰 Dimension slicing by index

```bash
ncks -O -d feature_id,10,50 input.nc subset.nc
```

---

### 📄 Explanation

| Flag                  | Meaning                                              |
| --------------------- | ---------------------------------------------------- |
| `ncks`                | NetCDF Kitchen Sink — extracts slices from datasets  |
| `-O`                  | Overwrite the output file                            |
| `-d feature_id,10,50` | Slice the `feature_id` dimension from index 10 to 50 |
| `input.nc subset.nc`  | Read from `input.nc`, write to `subset.nc`           |

---

### 🧠 Python Equivalent (xarray):

```python
import xarray
ds = xarray.open_dataset("input.nc")
subset = ds.isel(feature_id=slice(10, 51))  # 10 to 50 inclusive
subset.to_netcdf("subset.nc")
```

---

### ✅ Use Case:

* You want to subset a **contiguous range of indices** for a given dimension (`feature_id`)
* **Index-based**: This is positional, not value-based

---

### 🔄 Before → After:

If `feature_id = 0..999`, `streamflow(feature_id)` shape = `(1000,)`

After subsetting 10–50:

* `feature_id` → `(41,)`
* `streamflow` → `(41,)` (values from index 10 to 50)

---

## 🧰 Subsetting by external ID match (`--cmp`)

```bash
ncks -O -X -d feature_id,, --cmp ids_subset.nc input.nc output.nc
```

---

### 📄 Explanation

| Flag                  | Meaning                                                          |
| --------------------- | ---------------------------------------------------------------- |
| `-X`                  | Activate **external file-based subsetting**                      |
| `-d feature_id,,`     | Subset on `feature_id`, but use external values instead of range |
| `--cmp ids_subset.nc` | Use coordinate values from this file to define what to keep      |
| `input.nc output.nc`  | Input and output NetCDF files                                    |

---

### 🧠 What it does:

* Extracts the values of `feature_id` from `ids_subset.nc`
* Then matches those values to the `feature_id` dimension in `input.nc`
* Only **keeps the entries that match**

---

### 🧠 Python Equivalent:

```python
import xarray
subset_ids = xarray.open_dataset("ids_subset.nc")["feature_id"].values
ds = xarray.open_dataset("input.nc")
filtered = ds.sel(feature_id=subset_ids)
filtered.to_netcdf("output.nc")
```

---

### ✅ Use Case:

* You have a **non-contiguous list** of IDs to keep (e.g., `[101, 205, 999]`)
* You want **semantic filtering** instead of index slicing
* Useful for spatial subsetting based on external masks or station lists

---

### 🔄 Before → After:

If:

```text
input.nc has: feature_id = [0, 1, 2, ..., 999]
ids_subset.nc has: feature_id = [2, 9, 88, 550]
```

Then:

* `output.nc` will keep only entries where `feature_id` ∈ `[2, 9, 88, 550]`
* `streamflow` → shape = `(4,)`

---

## 📌 Summary of Differences

| Feature                | `-d feature_id,10,50` | `-X -d feature_id,, --cmp file.nc` |
| ---------------------- | --------------------- | ---------------------------------- |
| Based on               | **Index** (slicing)   | **Value match** (content-driven)   |
| Input                  | One file              | Two files (reference + target)     |
| Output shape           | Fixed-size slice      | Based on matched values            |
| Handles non-contiguous | ❌ No                  | ✅ Yes                              |
| Python analog          | `isel()`              | `sel()` with `np.isin()`           |

---

## 🧠 Gotchas:

| Gotcha                                         | Description                                                             |
| ---------------------------------------------- | ----------------------------------------------------------------------- |
| ❗ `--cmp` requires matching coordinate names   | The variable in both files must be called the same (e.g., `feature_id`) |
| ❗ `-d feature_id,,` is not a typo              | That means “use all values, but subset by matching from `--cmp`”        |
| ✅ `ncks -X` is faster than scripting in Python | Especially for large files or workflows run on clusters                 |
---

## 🧩 Reminder: What is a coordinate variable?

In NetCDF (and `xarray`), a **coordinate variable**:

* Has the **same name** as a dimension (`feature_id`)
* Contains **coordinate values** used to label and align data
* Used to subset or align across datasets

Example:

```text
dimensions:
  feature_id = 1000 ;

variables:
  int feature_id(feature_id) ;  // coordinate variable
  float streamflow(feature_id) ;  // data variable
```

---

## 🧰 With `ncks -d feature_id,10,50 input.nc subset.nc`

### 📌 Effect:

* `feature_id` dimension is sliced by **index** (from 10 to 50)
* All variables **that depend on `feature_id`** will be subset **in the same slice**
* This includes:

  * `streamflow(feature_id)`
  * `velocity(feature_id)`
  * any variable defined over `feature_id`

### ✅ Result:

* All relevant data variables are **reduced in size**
* `feature_id` coordinate variable is sliced to match

---

### 📊 Before:

```text
feature_id = 1000 ;
streamflow(feature_id) → shape (1000,)
```

### 📊 After:

```text
feature_id = 41 ;  // (50 - 10 + 1)
streamflow(feature_id) → shape (41,)
```

---

## 🧰 With `ncks -X -d feature_id,, --cmp ids_subset.nc input.nc output.nc`

### 📌 Effect:

* `feature_id` coordinate values from `ids_subset.nc` are used as a **filter**
* All data variables that **use `feature_id`** are subset to only include **matching rows**
* Result: only data for those `feature_id` values are retained

### ✅ This works *semantically*, not by position

* So if `ids_subset.nc` has `feature_id = [101, 205, 999]`
* Then `streamflow(feature_id)` will be filtered to include only entries where `feature_id == 101`, `205`, or `999`

---

### 📊 Before:

```text
feature_id = 1000 ;
streamflow(feature_id) → shape (1000,)
```

### 📊 After:

```text
feature_id = 3 ;  // if 3 IDs matched
streamflow(feature_id) → shape (3,)
```

---

## 🔬 Key Observations for Data Variables:

| Condition                                 | What Happens to Data Variables              |
| ----------------------------------------- | ------------------------------------------- |
| Data var uses `feature_id` as a dimension | ✅ Will be subset accordingly                |
| Data var **does not use** `feature_id`    | ❌ Will be retained in full (unchanged)      |
| `feature_id` is a coordinate variable     | ✅ It will be sliced/matched in sync         |
| Matching fails (no overlaps)              | 🔥 Output variable will be empty or missing |

---

## ✅ Real-World Use Cases

| Use Case                              | Best Command                                  |
| ------------------------------------- | --------------------------------------------- |
| You want a block from index 10 to 50  | `ncks -d feature_id,10,50`                    |
| You want exact IDs from a region file | `ncks -X -d feature_id,, --cmp ids_subset.nc` |

---

## Summary of NCO Tools Used:
| Tool     | Purpose |
|----------|---------|
| `ncks`   | Extract or exclude variables, dimensions |
| `ncatted`| Add, modify, or delete attributes |
| `ncap2`  | Arithmetic operations on variables |
| `ncrename`| Rename variables and dimensions |
| `ncrcat` | Concatenate along record (time) dimension |
| `ncpdq`  | Reorder dimensions (transpose) |
| `ncecat` | Add a new record dimension |

