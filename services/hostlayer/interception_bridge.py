"""Hand the AULA's keys to the Remote Desktop session.

Over RDP, Windows routes a locally plugged keyboard to the console session, so
the host layer (running in the RDP session) never sees the knob or the F-row.
The Interception driver sees every physical key first. This bridge re-sends
AULA keys into its own session with SendInput, tells engine.ahk the key came
from the AULA (so knob actions run), and passes every other keyboard's keys on
untouched. If it stops, the driver drops its filter and keys flow as before.

Needs the Interception driver installed (reboot after install) and its x64
interception.dll; set INTERCEPTION_DLL or keep the default path. Run with
pythonw.exe in the RDP session (the same session as engine.ahk).
"""
import ctypes
import os
import sys
from ctypes import wintypes

AULA_IDS = ("VID_0C45&PID_800A", "VID_05AC&PID_024F")
NOTICE = 0x84A1  # WM_APP + 0x4A1: engine.ahk counts the next knob key as the AULA's
KEY_UP, KEY_E0 = 0x01, 0x02
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x8
DLL = os.environ.get("INTERCEPTION_DLL", os.path.expanduser(r"~\interception\Interception\library\x64\interception.dll"))
LOG = os.environ.get("AULA_BRIDGE_LOG")  # set to a file path to log every stroke (debugging)


def log(msg):
    if LOG:
        with open(LOG, "a") as f:
            f.write(msg + "\n")


def is_aula(hwid):
    up = hwid.upper()
    return any(i in up for i in AULA_IDS)


def input_fields(code, state, to_vk):
    """(vk, scan, flags) for one Interception key stroke. Volume and media keys
    are E0 scancodes; sending their VK is what makes Windows act on them."""
    ext = bool(state & KEY_E0)
    vk = to_vk(code | (0xE000 if ext else 0))
    flags = (KEYEVENTF_EXTENDEDKEY if ext else 0) | (KEYEVENTF_KEYUP if state & KEY_UP else 0)
    if not vk:
        flags |= KEYEVENTF_SCANCODE
    return vk, code, flags


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class Stroke(ctypes.Structure):  # InterceptionKeyStroke, padded to the size of a stroke union
    _fields_ = [("code", ctypes.c_ushort), ("state", ctypes.c_ushort), ("information", ctypes.c_uint), ("pad", ctypes.c_byte * 24)]


def engine_window(user32):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        if buf.value.lower().replace("/", "\\").find("services\\hostlayer\\engine.ahk") >= 0:
            found.append(h)
            return False
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


CONSUMER_VK = {0xE9: 0xAF, 0xEA: 0xAE, 0xE2: 0xAD}  # volume up / down / mute


def consumer_vk(report):
    """VK for one consumer-control input report ([report id, usage lo, usage hi]); 0 = release."""
    return CONSUMER_VK.get(report[1] | report[2] << 8, 0) if len(report) >= 3 else None


def relay_knob(user32, notify):
    """The knob's volume and mute are consumer-control reports, which Interception
    never sees and Windows delivers to the console session. Read them from the
    AULA's consumer collection and re-send them here as volume keys."""
    import hid
    import threading
    import time

    def run():
        while True:
            paths = [d["path"] for d in hid.enumerate(0x0C45, 0x800A) if d["usage_page"] == 0x0C and d["usage"] == 1]
            if not paths:
                time.sleep(5)
                continue
            dev, held = hid.device(), 0
            try:
                dev.open_path(paths[0])
                while True:
                    vk = consumer_vk(dev.read(64, 1000))
                    if vk is None or vk == held:
                        continue
                    for v, up in ((held, True), (vk, False)):
                        if v:
                            if not up:
                                notify()
                            inp = INPUT(type=1)
                            inp.ki.wVk, inp.ki.dwFlags = v, KEYEVENTF_EXTENDEDKEY | (KEYEVENTF_KEYUP if up else 0)
                            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
                    log(f"knob consumer vk {vk:#x}")
                    held = vk
            except OSError as e:  # unplugged: retry
                log(f"knob relay: {e}")
                time.sleep(2)
            finally:
                dev.close()

    threading.Thread(target=run, daemon=True).start()


def main():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ic = ctypes.CDLL(DLL)
    ic.interception_create_context.restype = ctypes.c_void_p
    ic.interception_set_filter.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort]
    ic.interception_wait.argtypes = [ctypes.c_void_p]
    ic.interception_receive.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    ic.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    ic.interception_get_hardware_id.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    to_vk = lambda sc: user32.MapVirtualKeyW(sc, 3)  # MAPVK_VSC_TO_VK_EX

    ctx = ic.interception_create_context()
    if not ctx:
        sys.exit("Interception is not running (install it and reboot)")
    ic.interception_set_filter(ctx, ctypes.cast(ic.interception_is_keyboard, ctypes.c_void_p), 0xFFFF)
    aula, hwid, engine = {}, ctypes.create_unicode_buffer(500), [None]
    s, inp = Stroke(), INPUT(type=1)

    def notify():
        if not (engine[0] and user32.PostMessageW(engine[0], NOTICE, 0, 0)):
            engine[0] = engine_window(user32)
            if engine[0]:
                user32.PostMessageW(engine[0], NOTICE, 0, 0)

    relay_knob(user32, notify)
    while True:
        dev = ic.interception_wait(ctx)
        if ic.interception_receive(ctx, dev, ctypes.byref(s), 1) <= 0:
            continue
        if dev not in aula:
            n = ic.interception_get_hardware_id(ctx, dev, hwid, ctypes.sizeof(hwid))
            aula[dev] = n > 0 and is_aula(hwid.value)
            log(f"device {dev} {hwid.value if n > 0 else '?'} aula={aula[dev]}")
        if not aula[dev]:
            ic.interception_send(ctx, dev, ctypes.byref(s), 1)
            continue
        notify()
        inp.ki.wVk, inp.ki.wScan, inp.ki.dwFlags = input_fields(s.code, s.state, to_vk)
        sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        log(f"dev {dev} code {s.code:#x} state {s.state:#x} -> vk {inp.ki.wVk:#x} flags {inp.ki.dwFlags:#x} engine {engine[0]} sent {sent} err {ctypes.get_last_error()}")
        if not sent:
            ic.interception_send(ctx, dev, ctypes.byref(s), 1)  # this session can't take input (locked or disconnected): give it back


if __name__ == "__main__":
    main()
