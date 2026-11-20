

def test_windows_title_bar_is_dark_in_our_colours():
    """Windows: dark mode flag first, then Windows 11's caption / text / border colours as COLORREF (0x00BBGGRR)."""
    import ctypes

    from vedit.editor import window

    class FakeDwm:
        def __init__(self, refuse=()):
            self.calls, self.refuse = [], set(refuse)

        def DwmSetWindowAttribute(self, hwnd, attr, ref, size):  # noqa: N802 - the Win32 name
            self.calls.append((attr, ctypes.cast(ref, ctypes.POINTER(ctypes.c_uint)).contents.value))
            return 1 if attr in self.refuse else 0

    dwm = FakeDwm()
    assert window._dark_title_bar(1234, dwm)
    got = dict(dwm.calls)
    assert got[20] == 1 and 19 not in got  # 20 took it: no need for the pre-20H1 number
    assert got[35] == 0x0B0D09  # caption = ui.css --bg #090d0b
    assert got[36] == 0xEBF0E9 and got[34] == 0x22281D
    old = FakeDwm(refuse={20})  # Windows 10 before 20H1
    assert window._dark_title_bar(1234, old) and dict(old.calls)[19] == 1


def test_no_grey_menu_strip_on_windows():
    from vedit.editor import window

    assert window._menu_for("win32", lambda: None) is None
    assert window._menu_for("darwin", lambda: None)
