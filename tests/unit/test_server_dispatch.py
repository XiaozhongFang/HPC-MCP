import pytest

from hpc_mcp.errors import PolicyDenied
from hpc_mcp.tools.registry import ToolDef, validate_tool_args


async def _handler(_args):
    return None


def _tool():
    return ToolDef(
        name="test", description="test",
        schema={"type": "object", "properties": {"name": {"type": "string"}},
                "required": ["name"], "additionalProperties": False},
        handler=_handler,
    )


def test_unknown_and_missing_arguments_are_rejected():
    with pytest.raises(PolicyDenied):
        validate_tool_args(_tool(), {})
    with pytest.raises(PolicyDenied):
        validate_tool_args(_tool(), {"name": "x", "extra": 1})


def test_valid_argument_object_is_returned_unchanged():
    args = {"name": "x"}
    assert validate_tool_args(_tool(), args) is args
