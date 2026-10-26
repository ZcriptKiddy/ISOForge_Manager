"""Lectura de fuentes sin escribir contenido ni restaurar/cambiar sus fechas."""
from __future__ import annotations
import io
import os
from pathlib import Path
from contextlib import contextmanager


def validate_output_safety(plan, output):
    """Evita publicar ISO sobre una fuente o dentro de una carpeta seleccionada."""
    output = Path(output).resolve()
    sources = [*plan.permanent_items, *plan.variable_sources]
    destinations = [output / disc.filename for disc in plan.bins]
    for item in sources:
        source = item.path.resolve()
        if item.is_directory and (output == source or source in output.parents):
            raise ValueError('La carpeta de salida no puede estar dentro del contenido original. Selecciona otra carpeta.')
        if not item.is_directory:
            for destination in destinations:
                if destination.resolve() == source or (destination.exists() and os.path.samefile(source, destination)):
                    raise ValueError(f'La salida sobrescribiría un archivo original: {source}. Selecciona otra carpeta.')


@contextmanager
def open_preserving_access(path):
    """Desactiva la actualización de acceso SOLO para este descriptor de lectura."""
    path = Path(path)
    if os.name != 'nt':
        # No fallback to ordinary reading: that could update access timestamps.
        if not hasattr(os, 'O_NOATIME'):
            raise OSError('Este sistema no permite proteger la fecha de acceso.')
        fd = os.open(path, os.O_RDONLY | os.O_NOATIME)
        with os.fdopen(fd, 'rb') as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    set_time = kernel.SetFileTime
    set_time.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    set_time.restype = wintypes.BOOL
    # GENERIC_READ | FILE_WRITE_ATTRIBUTES, sharing READ only while reading.
    # WRITE_ATTRIBUTES is required by the API to suppress timestamp updates;
    # no timestamp value, file bytes or attributes are written back.
    handle = create(str(path.resolve()), 0x80000000 | 0x100, 1, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(f'No se puede leer protegiendo la fecha de acceso: {path}. {ctypes.WinError(ctypes.get_last_error())}')
    try:
        suppress = wintypes.FILETIME(0xFFFFFFFF, 0xFFFFFFFF)
        if not set_time(handle, None, ctypes.byref(suppress), None):
            raise OSError(f'No se pudo proteger la fecha de acceso de {path}: {ctypes.WinError(ctypes.get_last_error())}')
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None  # Ownership transferred to the Python file descriptor.
        with os.fdopen(fd, 'rb') as stream:
            yield stream
    finally:
        if handle is not None:
            close(handle)


class SourceReadPool:
    """Un solo archivo abierto a la vez, incluso con miles de fuentes."""
    def __init__(self):
        self.active = None
        self.context = None
        self.stream = None

    def close(self):
        if self.context is not None:
            self.context.__exit__(None, None, None)
        self.active = self.context = self.stream = None

    def read(self, reader, size):
        if self.active is not reader:
            self.close()
            context = open_preserving_access(reader.path)
            stream = context.__enter__()
            try:
                info = os.fstat(stream.fileno())
                if info.st_size != reader.size or info.st_mtime_ns != reader.mtime_ns:
                    raise OSError(f'El archivo cambió desde el cálculo: {reader.path}')
            except BaseException:
                context.__exit__(None, None, None)
                raise
            self.active, self.context, self.stream = reader, context, stream
        self.stream.seek(reader.position)
        result = self.stream.read(size)
        reader.position += len(result)
        return result


class PreservedReader(io.RawIOBase):
    """Adaptador de solo lectura para la API pública add_fp de pycdlib."""
    def __init__(self, path, pool, size):
        super().__init__()
        self.path = Path(path)
        self.pool = pool
        info = self.path.stat()
        self.size = size
        self.mtime_ns = info.st_mtime_ns
        self.position = 0
        if info.st_size != size:
            raise OSError(f'El tamaño del archivo cambió: {path}')

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        position = offset if whence == io.SEEK_SET else self.position + offset if whence == io.SEEK_CUR else self.size + offset
        if position < 0:
            raise ValueError('Posición de lectura negativa')
        self.position = position
        return position

    def read(self, size=-1):
        if self.closed:
            raise ValueError('Archivo cerrado')
        return self.pool.read(self, size)

    def close(self):
        if self.pool.active is self:
            self.pool.close()
        super().close()
