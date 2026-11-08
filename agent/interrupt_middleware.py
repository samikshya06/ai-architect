from langchain.agents.middleware import HumanInTheLoopMiddleware, wrap_model_call
from langchain_core.tools import tool


@tool
def ask_user(question: str) -> str:
    """Ask the user one clarification question and wait for their response."""
    raise RuntimeError("ask_user must be handled by ClarificationInterruptMiddleware")


class ClarificationInterruptMiddleware(HumanInTheLoopMiddleware):
    """Pause ask_user tool calls until the user provides a response."""

    def __init__(self) -> None:
        super().__init__(interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}})


@wrap_model_call
def one_tool_call_per_turn(request, handler):
    """Disable parallel tool calls so a question and the final structured spec can't
    share a turn (the agent would end with a spec built before the user's answer)."""
    return handler(
        request.override(
            model_settings={**request.model_settings, "parallel_tool_calls": False}
        )
    )
