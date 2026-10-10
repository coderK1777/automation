"""A private fixed Decimal context keeps caller settings out of domain math."""

from decimal import Context, ROUND_HALF_EVEN, localcontext
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def fixed_decimal(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def calculate(*args: P.args, **kwargs: P.kwargs) -> T:
        with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
            return function(*args, **kwargs)

    return calculate
