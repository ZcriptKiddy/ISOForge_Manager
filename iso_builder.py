"""Creación transaccional de imágenes ISO mediante pycdlib."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import threading
from typing import Callable
from uuid import uuid4

from file_utils import (
    IsoManifest,
    SourceError,
    build_manifest,
    joliet_safe_path,
)
from packing import PackingPlan
from source_preservation import PreservedReader, SourceReadPool, validate_output_safety


ProgressCallback = Callable[[int, str], None]


class IsoBuildError(RuntimeError):
    """Fallo al preparar o escribir una imagen ISO."""


class BuildCancelled(IsoBuildError):
    """Cancelación solicitada desde la interfaz."""


def _iso_file_path(parent: str, alias: str) -> str:
    return f"/{alias}" if parent == "/" else f"{parent}/{alias}"


def _joliet_path(path: PurePosixPath) -> str:
    return f"/{path.as_posix()}"


def _set_udf_times(record: object, creation_ns: int, modification_ns: int, access_ns: int) -> None:
    from pycdlib.udf import UDFTimestamp

    for field, timestamp_ns in (
        ("creation_time", creation_ns),
        ("mod_time", modification_ns),
        ("access_time", access_ns),
        ("attr_time", modification_ns),
    ):
        value = UDFTimestamp()
        value.new(timestamp_ns / 1_000_000_000)
        setattr(record, field, value)


def _set_iso_modification_time(iso: object, *, iso_path: str, joliet_path: str,
                               modification_ns: int) -> None:
    from pycdlib.dates import DirectoryRecordDate

    for record in (iso.get_record(iso_path=iso_path),
                   iso.get_record(joliet_path=joliet_path)):
        while record is not None:
            stamp = DirectoryRecordDate()
            stamp.new(modification_ns // 1_000_000_000)
            record.date = stamp
            record = record.data_continuation


def _populate_iso(iso: object, manifest: IsoManifest, readers: SourceReadPool) -> None:
    """Agrega alias ISO9660, Joliet compatible y rutas completas en UDF."""

    iso_directories: dict[PurePosixPath, str] = {PurePosixPath(): "/"}
    for index, directory in enumerate(manifest.directories, start=1):
        parent_iso = iso_directories[directory.parent]
        alias = f"D{index:07d}"
        iso_path = _iso_file_path(parent_iso, alias)
        joliet_path = _joliet_path(joliet_safe_path(directory))
        source = manifest.directory_sources[directory]
        info = source.stat()
        creation_ns = getattr(info, "st_birthtime_ns", info.st_ctime_ns)
        iso.add_directory(
            iso_path=iso_path,
            joliet_path=joliet_path,
            udf_path=_joliet_path(directory),
            creation_time=creation_ns / 1_000_000_000,
        )
        iso_directories[directory] = iso_path
        _set_iso_modification_time(
            iso, iso_path=iso_path, joliet_path=joliet_path,
            modification_ns=info.st_mtime_ns,
        )
        _set_udf_times(
            iso.get_record(udf_path=_joliet_path(directory)),
            creation_ns, info.st_mtime_ns, info.st_atime_ns,
        )

    for index, file_entry in enumerate(manifest.files, start=1):
        parent_iso = iso_directories.get(file_entry.target.parent, "/")
        alias = f"F{index:07d}.DAT;1"
        reader = PreservedReader(file_entry.source, readers, file_entry.size_bytes)
        iso_path = _iso_file_path(parent_iso, alias)
        joliet_path = _joliet_path(joliet_safe_path(file_entry.target))
        udf_path = _joliet_path(file_entry.target)
        iso.add_fp(
            reader,
            file_entry.size_bytes,
            iso_path=iso_path,
            joliet_path=joliet_path,
            udf_path=udf_path,
            creation_time=file_entry.creation_time_ns / 1_000_000_000,
        )
        _set_iso_modification_time(
            iso, iso_path=iso_path, joliet_path=joliet_path,
            modification_ns=file_entry.modification_time_ns,
        )
        _set_udf_times(
            iso.get_record(udf_path=udf_path),
            file_entry.creation_time_ns,
            file_entry.modification_time_ns,
            file_entry.access_time_ns,
        )


def build_iso_set(
    plan: PackingPlan,
    output_directory: str | Path,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Genera todos los ISO en temporales y los publica solo al terminar."""

    try:
        import pycdlib
    except ImportError as exc:
        raise IsoBuildError(
            "No se encontró pycdlib. Instala las dependencias con "
            "'pip install -r requirements.txt'."
        ) from exc

    output = Path(output_directory).expanduser()
    if not output.is_dir():
        raise IsoBuildError("Selecciona una carpeta de salida válida.")

    try:
        validate_output_safety(plan, output)
    except (ValueError, OSError) as exc:
        raise IsoBuildError(str(exc)) from exc

    cancel_event = cancel_event or threading.Event()
    callback = progress or (lambda _percent, _message: None)
    weights = [max(bin_.estimated_iso_bytes, 1) for bin_ in plan.bins]
    total_weight = max(sum(weights), 1)
    completed_weight = 0
    temporary_files: list[tuple[Path, Path]] = []

    try:
        for position, (bin_, weight) in enumerate(
            zip(plan.bins, weights, strict=True), start=1
        ):
            if cancel_event.is_set():
                raise BuildCancelled("Generación cancelada por el usuario.")

            name = bin_.filename
            final_path = output / name
            temp_path = output / f".{name}.{uuid4().hex}.tmp"
            temporary_files.append((temp_path, final_path))
            callback(
                int(completed_weight * 100 / total_weight),
                f"Preparando {name} ({position}/{plan.iso_count})…",
            )

            try:
                manifest = build_manifest([*plan.permanent_items, *bin_.items])
            except (OSError, SourceError) as exc:
                raise IsoBuildError(str(exc)) from exc

            iso = pycdlib.PyCdlib()
            readers = SourceReadPool()
            try:
                # El campo de etiqueta existe en ISO9660; se deja vacío y no es configurable.
                iso.new(
                    interchange_level=3,
                    joliet=3,
                    udf="2.60",
                    vol_ident="",
                    set_size=plan.iso_count,
                    seqnum=position,
                )
                _populate_iso(iso, manifest, readers)

                def pycdlib_progress(done: int, total: int, _opaque: object) -> None:
                    if cancel_event.is_set():
                        raise BuildCancelled("Generación cancelada por el usuario.")
                    ratio = done / total if total else 0.0
                    overall = int((completed_weight + ratio * weight) * 100 / total_weight)
                    callback(
                        min(overall, 99),
                        f"Escribiendo {name} ({position}/{plan.iso_count})…",
                    )

                iso.write(str(temp_path), progress_cb=pycdlib_progress)
            except BuildCancelled:
                raise
            except Exception as exc:
                raise IsoBuildError(f"No se pudo crear {name}: {exc}") from exc
            finally:
                readers.close()
                iso.close()

            actual_size = temp_path.stat().st_size
            if actual_size > plan.capacity_bytes:
                raise IsoBuildError(
                    f"{name} ocupa más que la capacidad seleccionada. "
                    "Reduce el contenido o aumenta la capacidad."
                )
            completed_weight += weight

        if cancel_event.is_set():
            raise BuildCancelled("Generación cancelada por el usuario.")

        published: list[Path] = []
        for temp_path, final_path in temporary_files:
            os.replace(temp_path, final_path)
            published.append(final_path)
        callback(100, f"Listo: {len(published)} ISO generado(s).")
        return published
    finally:
        for temp_path, _final_path in temporary_files:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
