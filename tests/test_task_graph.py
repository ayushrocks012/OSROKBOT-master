from types import SimpleNamespace

from task_graph import TaskGraph


class _FakeResponse:
    def __init__(self, output_text):
        self.output_text = output_text


class _FakeTransport:
    def __init__(self, request):
        self._request = request
        self.payloads = []

    def request(self, request_payload, should_cancel):
        self.payloads.append(request_payload)
        return self._request(request_payload, should_cancel)

    def close(self):
        return None


def setup_function(_function=None):
    pass


def test_task_graph_decompose_uses_shared_transport():
    timings = []
    response = _FakeResponse(
        """{
        "sub_goals": [
            {
                "step": 1,
                "description": "Open the world map",
                "expected_labels": ["map"],
                "expected_ocr_keywords": [],
                "completion_hint": "The world map is visible."
            },
            {
                "step": 2,
                "description": "Search for a wood node",
                "expected_labels": ["searchaction"],
                "expected_ocr_keywords": ["wood"],
                "completion_hint": "The search panel is ready."
            }
        ]
    }"""
    )
    transport = _FakeTransport(lambda _payload, _should_cancel: response)
    context = SimpleNamespace(
        record_runtime_timing=lambda stage, duration_ms, detail="": timings.append((stage, detail, duration_ms))
    )

    graph = TaskGraph()
    sub_goals = graph.decompose(
        "Gather wood safely.",
        transport=transport,
        model="gpt-5.4-mini",
        context=context,
    )

    assert [goal.description for goal in sub_goals] == ["Open the world map", "Search for a wood node"]
    assert transport.payloads[0]["text"]["format"]["strict"] is True
    assert timings[-1][0] == "task_graph_decompose"
    assert timings[-1][1] == "sub_goals=2"
    assert timings[-1][2] >= 0.0


def test_task_graph_decompose_falls_back_to_single_goal_on_transport_error():
    transport = _FakeTransport(lambda _payload, _should_cancel: (_ for _ in ()).throw(RuntimeError("boom")))

    graph = TaskGraph()
    sub_goals = graph.decompose(
        "Gather wood safely.",
        transport=transport,
        model="gpt-5.4-mini",
    )

    assert len(sub_goals) == 1
    assert sub_goals[0].description == "Gather wood safely."
