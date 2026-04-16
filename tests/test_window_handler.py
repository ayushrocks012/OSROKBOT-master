from types import SimpleNamespace

import pytest
import window_handler


def test_window_dc_releases_on_inner_error(monkeypatch):
    release_calls = []
    fake_win32gui = SimpleNamespace(
        GetWindowDC=lambda hwnd: f"dc-{hwnd}",
        ReleaseDC=lambda hwnd, dc: release_calls.append((hwnd, dc)),
    )
    monkeypatch.setattr(window_handler, "win32gui", fake_win32gui)

    with pytest.raises(RuntimeError):
        with window_handler._window_dc(123) as window_dc:
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

    with pytest.raises(RuntimeError):
        with window_handler._compatible_bitmap("source-dc", 10, 10):
            pass

    assert delete_calls == ["bitmap-handle"]
