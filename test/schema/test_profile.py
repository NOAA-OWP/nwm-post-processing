import unittest
import typing
import logging
import pathlib

import numpy
import xarray

from post_processing.configuration import settings
from post_processing.schema import profile
from post_processing.schema.manifest import InputManifest

from ..data_test import DataTest
from ..helpers import get_logger
from ..helpers import TestLogger

LOGGER: TestLogger = get_logger(__file__)

class ProfileTest(DataTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.profile_directory = settings.application_path / "test" / "specifications" / "profiles"

    def test_get_function_by_name(self):
        square_root = profile.get_function_by_name("math.sqrt")
        self.assertIsNotNone(square_root)
        self.assertTrue(callable(square_root))
        self.assertEqual(square_root(9), 3)

        import math
        import operator

        scope = {**globals()}

        scope["dummy_func"] = lambda x: x
        scope["non_callable"] = 123

        self.assertEqual(profile.get_function_by_name("dummy_func", scope), scope['dummy_func'])

        with self.assertRaises(ValueError):
            profile.get_function_by_name("")

        with self.assertRaises(KeyError):
            profile.get_function_by_name("unknown_function")

        with self.assertRaises(ValueError):
            profile.get_function_by_name("non_callable", scope)

        self.assertEqual(profile.get_function_by_name("math.sqrt", scope), math.sqrt)
        self.assertEqual(profile.get_function_by_name("operator.add", scope), operator.add)

        with self.assertRaises(ImportError):
            profile.get_function_by_name("nonexistent.module.function")

        with self.assertRaises(AttributeError):
            profile.get_function_by_name("math.nonexistent_function")

        with self.assertRaises(ValueError):
            profile.get_function_by_name("math.pi")  # pi is not callable

        del scope["dummy_func"]
        del scope["non_callable"]

        first_from_root = profile.get_function_by_name("post_processing.utilities.common.first", scope)

        self.assertEqual(first_from_root([1, 2, 3]), 1)

        from post_processing import utilities

        first_from_utilities = profile.get_function_by_name("utilities.common.first", {**scope, **locals()})

        self.assertEqual(utilities.common.first, first_from_utilities)

        self.assertEqual(first_from_utilities([1, 2, 3]), 1)

        from post_processing.utilities import common

        first_from_common = profile.get_function_by_name("common.first", {**scope, **locals()})

        self.assertEqual(common.first, first_from_common)

        self.assertEqual(first_from_common([1, 2, 3]), 1)

        from post_processing.utilities.common import first

        first_from_here = profile.get_function_by_name("first", {**scope, **locals()})

        self.assertEqual(first, first_from_here)

        self.assertEqual(first_from_here([1, 2, 3]), 1)

    def test_get_profile_operation_types(self):
        profile_operation_types: typing.Dict[profile.OperationType, typing.Type[profile.ProfileOperation]] = {}
        profile_operation_types.update(profile.get_profile_operation_types())
        self.assertTrue(len(profile_operation_types) > 0)

    def test_merge_files(self):
        """
        Test to ensure the `merge` operation correctly merges multiple files and maintains data and metadata integrity
        """
        merge_profile_path: pathlib.Path = self.profile_directory / "merge_operation.json"
        merge_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=merge_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, merge_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()

        self.assertEqual(len(merge_profile.operations), 2)
        self.assertTrue(isinstance(merge_profile.operations[0], profile.MergeOperation))
        self.assertTrue(isinstance(merge_profile.operations[1], profile.SaveOperation))
        manifest: InputManifest = InputManifest.from_files(self.get_input_files())
        merged_paths: typing.Sequence[pathlib.Path] = merge_profile(
            date=manifest.reference_time,
            cycle=manifest.cycle,
            files=manifest.files
        )
        self.assertGreater(len(merged_paths), 0)
        
        for merged_path in merged_paths:
            self.assertTrue(merged_path.exists())
            
    def test_extract_operation(self):
        extract_profile_path: pathlib.Path = self.profile_directory / "extract_operation.json"
        extract_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=extract_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, extract_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.ExtractOperation")
        
    def test_drop_operation(self):
        drop_profile_path: pathlib.Path = self.profile_directory / "drop_operation.json"
        drop_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=drop_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, drop_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.DropOperation")
        
    def test_rename_operation(self):
        rename_profile_path: pathlib.Path = self.profile_directory / "rename_operation.json"
        rename_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=rename_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, rename_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.RenameOperation")
        
    def test_attribute_operation(self):
        attribute_profile_path: pathlib.Path = self.profile_directory / "attribute_operation.json"
        attribute_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=attribute_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, attribute_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.AttributeOperation")
        
    def test_save_operation(self):
        # This may not need to be done - it is already tested in some of the above steps
        save_profile_path: pathlib.Path = self.profile_directory / "save_operation.json"
        save_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=save_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, save_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.SaveOperation")
        
    def test_branch_operation(self):
        branch_profile_path: pathlib.Path = self.profile_directory / "branch_operation.json"
        branch_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=branch_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, branch_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.BranchOperation")
        
    def test_load_operation(self):
        # This may need to be tested indirectly
        load_profile_path: pathlib.Path = self.profile_directory / "load_operation.json"
        load_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=load_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, load_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.LoadOperation")

    def test_intopython_operation(self):
        into_python_profile_path: pathlib.Path = self.profile_directory / "into_python.json"
        into_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=into_python_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, into_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.IntoPythonOperation")
        
    def test_topython_operation(self):
        to_python_profile_path: pathlib.Path = self.profile_directory / "to_python.json"
        to_python_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=to_python_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, to_python_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.ToPythonOperation")
        
    def test_out_of_python_operation(self):
        outofpython_profile_path: pathlib.Path = self.profile_directory / "out_of_python.json"
        out_of_python_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=outofpython_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, out_of_python_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()
        self.assertFalse(True, "Implement the test for profile.OutOfPythonOperation")
        
    def test_profile(self):
        """
        This tests a fully defined, semi-real world example of how to process an entire profile
        """
        full_profile_path: pathlib.Path = self.profile_directory / "test_profile.json"
        full_profile: profile.Profile = profile.Profile.from_json(path_or_buffer=full_profile_path)

        def is_save(profile_operation: profile.ProfileOperation) -> bool:
            return profile_operation.operation() == profile.OperationType.SAVE

        for operation in filter(is_save, full_profile.operations):  # type: profile.SaveOperation
            operation.directory = self.get_output_directory()






if __name__ == '__main__':
    unittest.main()
