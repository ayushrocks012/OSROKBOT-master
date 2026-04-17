from types import SimpleNamespace

from ai_fallback import AIFallback


class _FakeResponses:
    def __init__(self, output_text):
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


def test_ai_fallback_request_json_returns_none_when_disabled():
    fallback = AIFallback()
    fallback.enabled = False
    fallback.client = None

    assert fallback._request_json("instructions", [], "schema", {}) is None


def test_analyze_failure_stores_structured_result_in_context(monkeypatch, tmp_path):
    screenshot_path = tmp_path / "screen.png"
    screenshot_path.write_bytes(b"png")
    responses = _FakeResponses(
        (
            '{"state_guess":"CITY","visible_targets":["confirm"],'
            '"suggested_recovery":"Click confirm.",'
            '"target_hints":[{"label":"confirm","x":0.25,"y":0.75,"confidence":0.91}]}'
        )
    )
    fallback = AIFallback()
    fallback.enabled = True
    fallback.client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(fallback, "_image_data_url", lambda _path: "data:image/png;base64,AAA")
    context = SimpleNamespace(window_title="Test Window", extracted={})

    result = fallback.analyze_failure(context, screenshot_path, [{"state": "recover"}])

    assert result["state_guess"] == "CITY"
    assert context.extracted["ai_recovery"] == result
    assert responses.calls[0]["text"]["format"]["name"] == "osrokbot_failure_analysis"


def test_answer_lyceum_returns_strict_json_response():
    responses = _FakeResponses('{"answer":"B","confidence":0.88,"reason":"Historical fact."}')
    fallback = AIFallback()
    fallback.enabled = True
    fallback.client = SimpleNamespace(responses=responses)

    result = fallback.answer_lyceum("Question?", ["A1", "B1", "C1", "D1"])

    assert result == {"answer": "B", "confidence": 0.88, "reason": "Historical fact."}
    assert responses.calls[0]["text"]["format"]["name"] == "lyceum_answer"
