"""
Base classes for data models
"""
from __future__ import annotations
import typing
import dataclasses
import pathlib

T = typing.TypeVar("T")

MEMBER_FIELD_KEY: typing.Final[str] = "__IS_MEMBER__"


@dataclasses.dataclass
class BaseModel:
    """
    A base class for post-processing model objects
    """
    def __post_init__(self):
        self._validate()

    @classmethod
    def from_dict(cls: typing.Type[ModelType], **kwargs: typing.Any) -> ModelType:
        """
        Load the model from a dictionary.

        :param kwargs: Keyword arguments
        :return: A newly constructed model
        """
        type_hints: typing.Dict[str, typing.Any] = typing.get_type_hints(cls)
        initial_values: typing.Dict[str, typing.Any] = {}

        for field in dataclasses.fields(cls):  # type: dataclasses.Field
            if field.name in kwargs:
                value: typing.Union[typing.Dict, typing.Any] = kwargs[field.name]
                expected_type: typing.Optional[typing.Type] = type_hints.get(field.name)

                if dataclasses.is_dataclass(expected_type) and isinstance(value, dict):
                    initial_values[field.name] = expected_type(**value)
                elif expected_type is None:
                    initial_values[field.name] = value
                else:
                    try:
                        initial_values[field.name] = expected_type(value)
                    except (TypeError, ValueError):
                        initial_values[field.name] = value
            elif field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING:
                # The constructor of the dataclass will handle this
                continue
            else:
                raise ValueError(f"Missing a value for '{field.name}' - cannot construct a {cls.__qualname__}")

        instance: ModelType = cls(**initial_values)
        return instance

    @classmethod
    def from_json(
        cls: typing.Type[ModelType],
        path_or_buffer: typing.Union[pathlib.Path, str, typing.IO]
    ) -> ModelType:
        """
        Load model data from a JSON file

        :param path_or_buffer: Path or buffer or file-like object
        :return: A newly constructed model
        """
        import json
        if isinstance(path_or_buffer, (pathlib.Path, str)):
            with open(path_or_buffer, "r") as json_file:
                data: typing.Dict = json.load(json_file)
        elif isinstance(path_or_buffer, typing.IO):
            data = json.load(path_or_buffer)

        deserialized_model: ModelType = cls.from_dict(**data)
        return deserialized_model

    def _validate(self):
        """
        Validate and/or transform values on the model
        """
        pass

ModelType = typing.TypeVar("ModelType", bound=BaseModel)


def member(
    *,
    default: T = dataclasses.MISSING,
    default_factory: typing.Callable[[], T] = dataclasses.MISSING,
    metadata: typing.Dict = None
) -> dataclasses.Field:
    """
    Create a member specific field
    """
    if metadata is None:
        metadata = {}

    if dataclasses.MISSING not in (default, default_factory):
        raise ValueError(
            f"Cannot create a field - both a default value and a default factory cannot be specified - "
            f"choose one or the other"
        )
    elif default == dataclasses.MISSING and default_factory == dataclasses.MISSING:
        raise ValueError(f"An initial value must be given if a member variable is to be added")

    if default_factory != dataclasses.MISSING:
        if not callable(default_factory):
            raise TypeError(
                f"Cannot use '{default_factory}' (type={type(default_factory)} as the default factory as it is not callable"
            )
        field = dataclasses.field(
            default_factory=default_factory,
            metadata=metadata,
            init=False,
            compare=False,
            repr=False
        )
    else:
        field = dataclasses.field(
            default=default,
            metadata=metadata,
            init=False,
            compare=False,
            repr=False
        )

    return field


def get_fields(class_or_instance) -> typing.Sequence[dataclasses.Field]:
    """
    Get fields from a class that are meant to be internal runtime state, not recognizable, serializable values

    :param class_or_instance: Either a dataclass class or instance
    :return: A list of fields, or an empty list
    """
    fields = [
        field
        for field in dataclasses.fields(class_or_instance=class_or_instance)
        if not field.metadata or not field.metadata.get(MEMBER_FIELD_KEY, None)
    ]

    return fields

def to_dict(obj: typing.Any) -> typing.Union[typing.Dict[str, typing.Any], typing.Any]:
    """
    Convert a dataclass into a dictionary while excluding member fields

    :param obj: The dataclass to convert
    :return: The converted dictionary
    """
    if not dataclasses.is_dataclass(obj=obj):
        return obj

    dictionary: typing.Dict[str, typing.Any] = {
        field.name: getattr(obj, field.name)
        for field in get_fields(obj)
    }

    return dictionary

def to_json(obj: typing.Any) -> str:
    """
    Convert an object (preferably a data class) into a JSON string

    :param obj: The object to convert
    :return: A JSON string
    """
    import json
    return json.dumps(to_dict(obj), indent=4)
