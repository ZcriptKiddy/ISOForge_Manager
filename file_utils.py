"""Utilidades de archivos, validación y creación de manifiestos ISO."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import os
from typing import Iterable, Literal, Sequence


BLOCK_SIZE = 2048
FILE_METADATA_RESERVE = 1024
DIRECTORY_METADATA_RESERVE = 4096

SourceKind = Literal["file", "directory"]


class SourceError(ValueError):
    """Error comprensible para el usuario relacionado con una fuente."""


@dataclass(frozen=True, slots=True)
class SourceItem:
    """Fuente seleccionada o archivo individual usado por el empaquetador."""

    path: Path
    kind: SourceKind
    size_bytes: int
    estimated_bytes: int
    file_count: int
    directory_count: int
    iso_target: PurePosixPath | None = None

    @property
    def is_directory(self) -> bool:
        return self.kind == "directory"

    @property
    def display_name(self) -> str:
        return self.iso_target.as_posix() if self.iso_target is not None else source_root_name(self.path)


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """Archivo local, destino y metadatos de tiempo."""

    source: Path
    target: PurePosixPath
    size_bytes: int
    creation_time_ns: int
    modification_time_ns: int
    access_time_ns: int


@dataclass(frozen=True, slots=True)
class IsoManifest:
    """Directorios y archivos listos para enviarse al constructor ISO."""

    directories: tuple[PurePosixPath, ...]
    files: tuple[ManifestFile, ...]
    directory_sources: dict[PurePosixPath, Path]


def align_to_block(value: int, block_size: int = BLOCK_SIZE) -> int:
    """Redondea bytes al siguiente sector óptico."""

    if value <= 0:
        return 0
    return ((value + block_size - 1) // block_size) * block_size


def source_root_name(path: Path) -> str:
    """Nombre raíz estable, incluso cuando se selecciona la raíz de una unidad."""

    if path.name:
        return path.name
    drive = path.drive.rstrip(":\\/")
    return f"{drive}_Drive" if drive else "Contenido"


def normalized_path_key(path: Path) -> str:
    """Clave canónica con semántica insensible a mayúsculas de Windows."""

    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError:
        resolved = path.expanduser().absolute()
    return os.path.normcase(str(resolved)).casefold()


def _raise_walk_error(error: OSError) -> None:
    raise SourceError(f"No se puede leer la ruta: {error.filename or error}") from error


def create_source_item(path: str | Path, kind: SourceKind) -> SourceItem:
    """Valida y mide una fuente sin seguir enlaces de carpetas."""

    raw_path = Path(path).expanduser()
    try:
        resolved = raw_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceError(f"La ruta no existe o no es válida: {raw_path}") from exc

    if kind == "file" and not resolved.is_file():
        raise SourceError(f"No es un archivo válido: {resolved}")
    if kind == "directory" and not resolved.is_dir():
        raise SourceError(f"No es una carpeta válida: {resolved}")

    if kind == "file":
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise SourceError(f"No se puede leer el archivo: {resolved}") from exc
        return SourceItem(
            path=resolved,
            kind=kind,
            size_bytes=size,
            estimated_bytes=align_to_block(size) + FILE_METADATA_RESERVE,
            file_count=1,
            directory_count=0,
        )

    logical_size = 0
    estimated_size = 0
    file_count = 0
    directory_count = 0

    for root, directory_names, file_names in os.walk(
        resolved, topdown=True, followlinks=False, onerror=_raise_walk_error
    ):
        root_path = Path(root)
        directory_count += 1
        directory_names.sort(key=str.casefold)
        file_names.sort(key=str.casefold)

        for directory_name in directory_names:
            child = root_path / directory_name
            if child.is_symlink():
                raise SourceError(
                    f"No se admiten enlaces simbólicos a carpetas: {child}"
                )

        for file_name in file_names:
            child = root_path / file_name
            try:
                size = child.stat().st_size
            except OSError as exc:
                raise SourceError(f"No se puede leer el archivo: {child}") from exc
            logical_size += size
            estimated_size += align_to_block(size) + FILE_METADATA_RESERVE
            file_count += 1

    estimated_size += directory_count * DIRECTORY_METADATA_RESERVE
    return SourceItem(
        path=resolved,
        kind=kind,
        size_bytes=logical_size,
        estimated_bytes=estimated_size,
        file_count=file_count,
        directory_count=directory_count,
    )



def expand_variable_sources(items: Iterable[SourceItem]) -> list[SourceItem]:
    """Convierte carpetas variables en archivos independientes con su ruta relativa."""

    expanded: list[SourceItem] = []
    for item in items:
        if not item.is_directory:
            expanded.append(SourceItem(
                path=item.path, kind="file", size_bytes=item.size_bytes,
                estimated_bytes=item.estimated_bytes, file_count=1,
                directory_count=0,
                iso_target=PurePosixPath(source_root_name(item.path)),
            ))
            continue

        if not item.path.is_dir():
            raise SourceError(f"La ruta ya no es una carpeta: {item.path}")
        for root, directory_names, file_names in os.walk(
            item.path, topdown=True, followlinks=False, onerror=_raise_walk_error
        ):
            root_path = Path(root)
            directory_names.sort(key=str.casefold)
            file_names.sort(key=str.casefold)
            for name in directory_names:
                child = root_path / name
                if child.is_symlink():
                    raise SourceError(f"No se admiten enlaces simbólicos a carpetas: {child}")
            relative = root_path.relative_to(item.path)
            target_dir = PurePosixPath(source_root_name(item.path), *relative.parts)
            if not directory_names and not file_names:
                expanded.append(SourceItem(
                    path=root_path, kind="directory", size_bytes=0,
                    estimated_bytes=0, file_count=0, directory_count=1,
                    iso_target=target_dir,
                ))
            for name in file_names:
                path = root_path / name
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    raise SourceError(f"No se puede leer el archivo: {path}") from exc
                expanded.append(SourceItem(
                    path=path, kind="file", size_bytes=size,
                    estimated_bytes=align_to_block(size) + FILE_METADATA_RESERVE,
                    file_count=1, directory_count=0,
                    iso_target=target_dir / name,
                ))
    return expanded

def refresh_source_items(items: Iterable[SourceItem]) -> list[SourceItem]:
    """Vuelve a validar y medir fuentes antes de calcular o generar."""

    return [create_source_item(item.path, item.kind) for item in items]


def validate_unique_sources(items: Sequence[SourceItem]) -> None:
    """Detecta rutas repetidas y colisiones en la raíz del ISO."""

    seen_paths: dict[str, Path] = {}
    seen_targets: dict[str, Path] = {}
    for item in items:
        path_key = normalized_path_key(item.path)
        if path_key in seen_paths:
            raise SourceError(f"Ruta duplicada: {item.path}")
        seen_paths[path_key] = item.path

        target = item.iso_target or PurePosixPath(source_root_name(item.path))
        target_key = target.as_posix().casefold()
        if target_key in seen_targets:
            raise SourceError(
                "Dos fuentes producirían el mismo nombre en el ISO: "
                f"{seen_targets[target_key]} y {item.path}"
            )
        seen_targets[target_key] = item.path


def _validate_joliet_component(component: str, source: Path) -> None:
    if not component or "/" in component or "\x00" in component:
        raise SourceError(f"Nombre no valido para ISO/Joliet: {source}")
    # PyCdlib 1.16 valida el componente en UTF-8 antes de convertirlo a UTF-16.
    if len(component.encode("utf-8")) > 64:
        raise SourceError(f"El nombre supera el limite de 64 bytes de Joliet: {source}")


def joliet_safe_component(component: str) -> str:
    """Conserva nombres compatibles y crea alias estables para nombres largos."""
    encoded = component.encode("utf-8")
    if len(encoded) <= 64:
        return component
    stem, dot, suffix = component.rpartition(".")
    extension = f".{suffix}" if dot and len(suffix.encode("utf-8")) <= 12 else ""
    marker = "~" + hashlib.sha256(encoded).hexdigest()[:10]
    budget = 64 - len(marker) - len(extension.encode("utf-8"))
    prefix = ""
    for character in stem if extension else component:
        if len((prefix + character).encode("utf-8")) > budget:
            break
        prefix += character
    return f"{prefix}{marker}{extension}"


def joliet_safe_path(path: PurePosixPath) -> PurePosixPath:
    return PurePosixPath(*(joliet_safe_component(part) for part in path.parts))


def _creation_time_ns(info: os.stat_result) -> int:
    return getattr(info, "st_birthtime_ns", info.st_ctime_ns)


def _manifest_file(path: Path, target: PurePosixPath) -> ManifestFile:
    info = path.stat()
    return ManifestFile(path, target, info.st_size, _creation_time_ns(info),
                        info.st_mtime_ns, info.st_atime_ns)


def build_manifest(items: Sequence[SourceItem]) -> IsoManifest:
    """Expande fuentes conservando carpetas y nombres mediante Joliet."""

    validate_unique_sources(items)
    directories: set[PurePosixPath] = set()
    directory_sources: dict[PurePosixPath, Path] = {}
    files: list[ManifestFile] = []
    occupied: dict[str, str] = {}

    def register(target: PurePosixPath, kind: str, source: Path) -> None:
        for component in target.parts:
            if not component or "/" in component or "\x00" in component:
                raise SourceError(f"Nombre no valido para ISO/Joliet: {source}")
            _validate_joliet_component(joliet_safe_component(component), source)
        key = target.as_posix().casefold()
        previous = occupied.get(key)
        if previous is not None and previous != kind:
            raise SourceError(f"Colisión de archivo/carpeta dentro del ISO: {target}")
        if previous == "file":
            raise SourceError(f"Archivo duplicado dentro del ISO: {target}")
        occupied[key] = kind

    for item in items:
        path = item.path
        if not path.exists():
            raise SourceError(f"La ruta dejó de existir: {path}")

        root_name = source_root_name(path)
        if item.kind == "file":
            if not path.is_file():
                raise SourceError(f"La ruta ya no es un archivo: {path}")
            target = item.iso_target or PurePosixPath(root_name)
            register(target, "file", path)
            files.append(_manifest_file(path, target))
            for depth in range(1, len(target.parts)):
                parent_target = PurePosixPath(*target.parts[:depth])
                source_parent = path.parents[len(target.parts) - 1 - depth]
                register(parent_target, "directory", source_parent)
                directories.add(parent_target)
                directory_sources[parent_target] = source_parent
            continue

        if not path.is_dir():
            raise SourceError(f"La ruta ya no es una carpeta: {path}")

        if item.iso_target is not None:
            target_dir = item.iso_target
            register(target_dir, "directory", path)
            directories.add(target_dir)
            for depth in range(1, len(target_dir.parts)):
                parent_target = PurePosixPath(*target_dir.parts[:depth])
                source_parent = path.parents[len(target_dir.parts) - depth]
                register(parent_target, "directory", source_parent)
                directories.add(parent_target)
                directory_sources[parent_target] = source_parent
            directory_sources[target_dir] = path
            continue

        for root, directory_names, file_names in os.walk(
            path, topdown=True, followlinks=False, onerror=_raise_walk_error
        ):
            root_path = Path(root)
            relative = root_path.relative_to(path)
            target_dir = PurePosixPath(root_name, *relative.parts)
            register(target_dir, "directory", root_path)
            directories.add(target_dir)
            directory_sources[target_dir] = root_path

            directory_names.sort(key=str.casefold)
            file_names.sort(key=str.casefold)
            for directory_name in directory_names:
                child = root_path / directory_name
                if child.is_symlink():
                    raise SourceError(
                        f"No se admiten enlaces simbólicos a carpetas: {child}"
                    )
            for file_name in file_names:
                child = root_path / file_name
                target = target_dir / file_name
                register(target, "file", child)
                try:
                    size = child.stat().st_size
                except OSError as exc:
                    raise SourceError(f"No se puede leer el archivo: {child}") from exc
                files.append(_manifest_file(child, target))

    sorted_directories = tuple(
        sorted(directories, key=lambda value: (len(value.parts), value.as_posix().casefold()))
    )
    sorted_files = tuple(sorted(files, key=lambda value: value.target.as_posix().casefold()))
    return IsoManifest(sorted_directories, sorted_files, directory_sources)


def format_size(value: int) -> str:
    """Formato legible en unidades binarias, sin perder claridad para cero."""

    value = max(0, int(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{value} B"


def gb_to_bytes(value: float) -> int:
    """Convierte GB decimales, como las capacidades comerciales de discos."""

    return int(value * 1_000_000_000)
