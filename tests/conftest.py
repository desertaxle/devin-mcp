import collections.abc
import sys
import typing
from pathlib import Path
from unittest.mock import patch

# Workaround for beartype incompatibility with Python 3.14 —
# `collections.abc.ByteString` was removed in Python 3.14.
if not hasattr(collections.abc, "ByteString"):
    collections.abc.ByteString = bytes  # type: ignore[attr-defined]

# Workaround for pydantic 2.12.x incompatibility with Python 3.14 —
# pydantic passes `prefer_fwd_module=True` to `typing._eval_type()`,
# but this keyword was removed in CPython 3.14.
_orig_eval_type = typing._eval_type  # type: ignore[attr-defined]


def _patched_eval_type(*args, **kwargs):  # type: ignore[no-untyped-def]
    kwargs.pop("prefer_fwd_module", None)
    return _orig_eval_type(*args, **kwargs)


patch.object(typing, "_eval_type", _patched_eval_type).start()

# Add project root to path so we can import main
sys.path.insert(0, str(Path(__file__).parent.parent))
