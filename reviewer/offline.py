"""Per-run offline isolation; never globally monkeypatch a provider or subprocess."""

from contextvars import ContextVar

model_override = ContextVar("review_model_override", default=None)
offline_mode = ContextVar("review_offline_mode", default=False)
review_active = ContextVar("review_active", default=False)


class MockLLM:
    """Empty/uncertain responses for workflow testing, never an accuracy model."""

    metadata = {"reviewer_provider": "mock"}

    def bind_tools(self, _tools, *, tool_choice="auto"):
        class Response:
            async def ainvoke(self, _messages):
                from langchain_core.messages import AIMessage

                return AIMessage(content="Offline mock; no tools requested.")

        return Response()

    def with_structured_output(self, schema):
        class Response:
            async def ainvoke(self, _messages):
                if schema.__name__ == "FindingList":
                    return schema.model_validate({"findings": []})
                if schema.__name__ == "_SummaryResponse":
                    return schema.model_validate({"summary": "Offline mock; no live model called."})
                if schema.__name__ == "JudgeDecision":
                    return schema.model_validate(
                        {"status": "uncertain", "reason": "Offline mock cannot verify defects."}
                    )
                return schema.model_validate({})

        return Response()
