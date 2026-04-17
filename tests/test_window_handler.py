from types import SimpleNamespace

import pytest
import window_handler
from PIL import Image


def test_window_dc_releases_on_inner_error(monkeypatch):
    release_calls = []
    fake_win32gui = SimpleNamespace(
        GetWindowDC=lambda hwnd: f"dc-{hwnd}",
        ReleaseDC=lambda hwnd, dc: release_calls.append((hwnd, dc)),
    )
    monkeypatch.setattr(window_handler, "win32gui", fake_win32gui)

    with pytest.raises(RuntimeError), window_handler._window_dc(123) as window_dc:
        assert window_dc == "dc-123"
        raise RuntimeError("inner failure")

    assert release_calls == [(123, "dc-123")]


def test_compatible_bitmap_deletes_after_partial_create_failure(monkeypatch):
    delete_calls = []

    class FakeBitmap:
        def CreateCompatibleBitmap(self, _source_dc, _width, _height):
            raise RuntimeError("bitmap create failed")

        def GetHandle(self):
            return "bitmap-handle"

    fake_win32ui = SimpleNamespace(CreateBitmap=lambda: FakeBitmap())
    fake_win32gui = SimpleNamespace(DeleteObject=lambda handle: delete_calls.append(handle))
    monkeypatch.setattr(window_handler, "win32ui", fake_win32ui)
    monkeypatch.setattr(window_handler, "win32gui", fake_win32gui)

    with pytest.raises(RuntimeError), window_handler._compatible_bitmap("source-dc", 10, 10):
        pass

    assert delete_calls == ["bitmap-handle"]


@pytest.mark.integration
def test_screenshot_window_uses_injected_capture_backend(monkeypatch):
    class FakeBackend:
        name = "fake"

        def __init__(self):
            self.calls = []

        def capture_client_image(self, hwnd, client_rect):
            self.calls.append((hwnd, client_rect))
            return Image.new("RGBA", (client_rect.width, client_rect.height))

    backend = FakeBackend()
    handler = window_handler.WindowHandler(capture_backend=backend)
    win = SimpleNamespace(_hWnd=99)
    client_rect = window_handler.ClientRect(hwnd=99, left=10, top=20, width=100, height=50)
    monkeypatch.setattr(handler, "get_window", lambda _title: win)
    monkeypatch.setattr(handler, "_get_client_rect", lambda _win: client_rect)
    monkeypatch.setattr(window_handler.WindowHandler, "_restore_no_activate", staticmethod(lambda _hwnd: None))

    screenshot, rect = handler.screenshot_window("Rise of Kingdoms")

    assert rect == client_rect
    assert screenshot is not None
    assert screenshot.mode == "RGB"
    assert backend.calls == [(99, client_rect)]


@pytest.mark.integration
def test_get_client_window_rect_returns_none_on_client_rect_failure(monkeypatch):
    handler = window_handler.WindowHandler()
    monkeypatch.setattr(handler, "get_window", lambda _title: SimpleNamespace(_hWnd=99))
    monkeypatch.setattr(window_handler.WindowHandler, "_restore_no_activate", staticmethod(lambda _hwnd: None))
    monkeypatch.setattr(handler, "_get_client_rect", lambda _win: (_ for _ in ()).throw(OSError("bad rect")))

    assert handler.get_client_window_rect("Rise of Kingdoms") is None
