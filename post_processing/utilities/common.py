"""
Common functions
"""
import typing

T = typing.TypeVar("T")
"""A generic type"""

RT = typing.TypeVar("RT")
"""A generic return type"""

ArgsAndKwargs = typing.Union[
    typing.Sequence[typing.Any],
    typing.Mapping[str, typing.Any],
    typing.Tuple[typing.Sequence[typing.Any], typing.Mapping[str, typing.Any]]
]
"""
Either a series of positional arguments, a dictionary of keyword arguments, 
or a tuple of the first item being positional arguments and the second being keyword arguments
"""

def starmap(
    function: typing.Callable[[typing.Any], RT],
    args: typing.Iterable[ArgsAndKwargs]
) -> typing.Sequence[RT]:
    """
    Call the given function with each of squence of positional arguments

    :param function: The function to call
    :param args: Each set of arguments to pass
    :returns: The result of each function call
    """
    results: typing.List[RT] = []

    if not isinstance(args, typing.Iterable) or isinstance(args, (str, bytes)):
        raise TypeError(f"Arguments for starmap must be an iterable collection. Received '{args}' (type={type(args)})")

    for arg in args:
        if isinstance(arg, typing.Mapping):
            result: RT = function(**arg)
        elif isinstance(arg, typing.Sequence) and len(arg) == 2 and isinstance(args[0], typing.Sequence) and isinstance(args[1], typing.Mapping):
            result: RT = function(*arg[0], **arg[1])
        elif isinstance(arg, typing.Sequence) and not isinstance(arg, str):
            result: RT = function(*arg)
        else:
            result: RT = function(arg)
            
        results.append(result)

    return results