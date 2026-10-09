#!/usr/bin/env python3
import json
import logging
import pathlib
import tempfile
import unittest
import unittest.mock

from post_processing.schema.base import BaseModel
from post_processing.schema.base import postprocessing_model


@postprocessing_model
class PositiveModel(BaseModel):
    """A tiny model that rejects negative values, used to trigger construction errors"""
    value: int

    def _validate(self):
        if self.value < 0:
            raise ValueError(f"'value' must not be negative, but got {self.value}")


class BaseModelSourceTest(unittest.TestCase):
    def test_from_json_names_the_file_in_construction_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "negative.json"
            path.write_text(json.dumps({"value": -1}))

            with self.assertRaises(Exception) as context:
                PositiveModel.from_json(path)

        message = str(context.exception)
        self.assertIn(str(path), message)
        self.assertIn("Could not construct a PositiveModel", message)
        self.assertIn(f"(loaded from {path})", message)

    def test_from_json_still_loads_valid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "valid.json"
            path.write_text(json.dumps({"value": 3}))
            model = PositiveModel.from_json(path)

        self.assertEqual(model.value, 3)

    def test_from_dict_has_no_source_to_report(self):
        with self.assertRaises(RuntimeError) as context:
            PositiveModel.from_dict(value=-5)

        message = str(context.exception)
        self.assertIn("Could not construct a PositiveModel", message)
        self.assertNotIn("loaded from", message)

    def test_unserializable_inputs_are_logged_with_the_source(self):
        class Unserializable:
            pass

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.json"
            path.write_text(json.dumps({"value": -1}))

            with self.assertLogs("base", level=logging.ERROR) as logs:
                with unittest.mock.patch(
                    "post_processing.utilities.common.to_json", side_effect=TypeError("cannot serialize")
                ):
                    # An ordinary (non-group) error takes the branch that serializes the inputs
                    with self.assertRaises(Exception):
                        PositiveModel._build({"value": Unserializable()}, source=str(path))

        self.assertTrue(any(str(path) in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
