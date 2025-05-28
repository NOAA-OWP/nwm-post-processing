import unittest
import typing

from post_processing.schema import profile

class ProfileTest(unittest.TestCase):
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








if __name__ == '__main__':
    unittest.main()
