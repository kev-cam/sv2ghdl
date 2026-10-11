import io
import struct
from typing import BinaryIO


class BinaryReader:

    def __init__(self, file: BinaryIO):
        self.buffered = io.BufferedReader(file)
        self._seekable = file.seekable()
        self._pos = 0  # Manual position tracking for non-seekable streams

    def is_seekable(self) -> bool:
        return self._seekable

    def tell(self) -> int:
        if self.is_seekable():
            return self.buffered.tell()
        return self._pos

    def seek(self, offset: int, whence: int = 0):
        self.buffered.seek(offset, whence)
        self._pos = self.buffered.tell()

    # === Core Read ===
    def read_bytes(self, n: int) -> bytes:
        data = self.buffered.read(n)
        if len(data) != n:
            raise EOFError(f'Expected {n} bytes, got {len(data)}.')
        self._pos += len(data)
        return data

    def _read_struct(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.read_bytes(size))

    def read_uint8(self): return self._read_struct('!B')[0]
    def read_uint16(self): return self._read_struct('!H')[0]
    def read_uint32(self): return self._read_struct('!I')[0]
    def read_float64(self): return self._read_struct('!d')[0]

    def read_string(self, encoding='utf-8') -> str:
        length = self.read_uint32()
        data = self.read_bytes(length)
        padding = (4 - (length % 4)) % 4
        if padding:
            self.read_bytes(padding)
        return data.decode(encoding)

    # === Peek Support ===
    def peek_bytes(self, n: int) -> bytes:
        data = self.buffered.peek(n)[:n]
        if len(data) < n:
            raise EOFError(f'Could only peek {len(data)} bytes (expected {n})')
        return data

    def peek_uint32(self) -> int:
        data = self.peek_bytes(4)
        return struct.unpack('!I', data)[0]

    def skip(self, n: int):
        if self.is_seekable():
            self.seek(n, 1)
        else:
            _ = self.read_bytes(n)

    def close(self):
        self.buffered.close()
