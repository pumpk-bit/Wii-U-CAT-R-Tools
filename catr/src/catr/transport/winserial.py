"""Small synchronous Win32 serial adapter; no third-party packages required."""
import ctypes as C
from ctypes import wintypes as W


def available_ports():
    """The USB serial ports Windows has now, e.g. ['COM3', 'COM5'].

    Bluetooth ports (BthModemN) and the mainboard's own (SerialN) are left out: the CAT-R's cable
    is neither.
    """
    import re
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DEVICEMAP\SERIALCOMM')
    except OSError:             # no serial port at all
        return []
    with key:
        entries = [winreg.EnumValue(key, i)[:2] for i in range(winreg.QueryInfoKey(key)[1])]
    ports = {port for device, port in entries if not re.fullmatch(r'\\Device\\(BthModem|Serial)\d+', device)}
    return sorted(ports, key=lambda port: (len(port), port))


class DCB(C.Structure):
    _fields_ = [('length', W.DWORD), ('baud', W.DWORD), ('flags', W.DWORD),
                ('reserved', W.WORD), ('xonlim', W.WORD), ('xofflim', W.WORD),
                ('bytesize', C.c_ubyte), ('parity', C.c_ubyte), ('stopbits', C.c_ubyte),
                ('xon', C.c_char), ('xoff', C.c_char), ('error', C.c_char),
                ('eof', C.c_char), ('event', C.c_char), ('reserved1', W.WORD)]


class Port:
    def __init__(self, port, baud=57600):
        self.api = C.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.api.CreateFileW.restype = W.HANDLE
        for name in ('GetCommState', 'SetCommState', 'SetCommTimeouts'):
            getattr(self.api, name).argtypes = [W.HANDLE, C.c_void_p]
        for name in ('ReadFile', 'WriteFile'):
            getattr(self.api, name).argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        self.api.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.api.CreateFileW('\\\\.\\' + port, 0xC0000000, 0, None, 3, 0, None)
        if self.handle == W.HANDLE(-1).value:
            self.handle = None
            raise C.WinError(C.get_last_error())
        try:
            dcb = DCB()
            dcb.length = C.sizeof(dcb)
            self._check(self.api.GetCommState(self.handle, C.byref(dcb)))
            dcb.baud, dcb.flags = baud, 1 | (1 << 4) | (1 << 12)
            dcb.bytesize, dcb.parity, dcb.stopbits = 8, 0, 0
            self._check(self.api.SetCommState(self.handle, C.byref(dcb)))
            # Return immediately with available bytes. The daemon sleeps between reads.
            timeouts = (W.DWORD * 5)(0xffffffff, 0, 0, 0, 2000)
            self._check(self.api.SetCommTimeouts(self.handle, timeouts))
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if not result:
            raise C.WinError(C.get_last_error())

    def read(self):
        data, count = C.create_string_buffer(8192), W.DWORD()
        self._check(self.api.ReadFile(self.handle, data, len(data), C.byref(count), None))
        return data.raw[:count.value]

    def write(self, data):
        count = W.DWORD()
        self._check(self.api.WriteFile(self.handle, data, len(data), C.byref(count), None))
        if count.value != len(data):
            raise OSError('Incomplete serial command write; outcome uncertain')

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None
