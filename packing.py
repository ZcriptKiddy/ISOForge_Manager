"""Distribución First-Fit Decreasing (FFD) de contenidos entre ISOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from file_utils import (
    DIRECTORY_METADATA_RESERVE,
    SourceError,
    SourceItem,
    expand_variable_sources,
    validate_unique_sources,
)


# Reserva fija para descriptores, tablas de rutas y sectores de cierre.
FIXED_ISO_OVERHEAD = 2 * 1024 * 1024


class PackingError(ValueError):
    """El conjunto solicitado no puede distribuirse con esa capacidad."""


@dataclass(frozen=True, slots=True)
class IsoBin:
    """Contenido variable asignado a un ISO."""

    index: int
    items: tuple[SourceItem, ...]
    variable_size_bytes: int
    variable_estimated_bytes: int
    estimated_iso_bytes: int
    free_bytes: int

    @property
    def filename(self) -> str:
        return f"ISO{self.index:02d}.iso"


@dataclass(frozen=True, slots=True)
class PackingPlan:
    """Plan inmutable que la vista previa y el constructor comparten."""

    capacity_bytes: int
    permanent_items: tuple[SourceItem, ...]
    bins: tuple[IsoBin, ...]
    variable_sources: tuple[SourceItem, ...]
    permanent_size_bytes: int
    permanent_estimated_bytes: int
    variable_size_bytes: int

    @property
    def iso_count(self) -> int:
        return len(self.bins)

    @property
    def minimum_free_bytes(self) -> int:
        return min((bin_.free_bytes for bin_ in self.bins), default=0)


def create_packing_plan(
    permanent_items: Sequence[SourceItem],
    variable_items: Sequence[SourceItem],
    capacity_bytes: int,
) -> PackingPlan:
    """Aplica FFD distribuyendo archivos variables y conservando sus rutas."""

    if capacity_bytes <= FIXED_ISO_OVERHEAD:
        raise PackingError("La capacidad seleccionada es demasiado pequeña.")
    if not permanent_items and not variable_items:
        raise PackingError("Agrega al menos un archivo o una carpeta.")

    try:
        validate_unique_sources([*permanent_items, *variable_items])
        expanded_variable = expand_variable_sources(variable_items)
    except SourceError as exc:
        raise PackingError(str(exc)) from exc

    permanent_size = sum(item.size_bytes for item in permanent_items)
    permanent_estimated = sum(item.estimated_bytes for item in permanent_items)
    fixed_size = FIXED_ISO_OVERHEAD + permanent_estimated
    if fixed_size > capacity_bytes:
        raise PackingError(
            "Los contenidos permanentes superan la capacidad seleccionada "
            "al incluir los metadatos del ISO."
        )

    variable_limit = capacity_bytes - fixed_size

    def needed_directories(item: SourceItem) -> set[PurePosixPath]:
        target = item.iso_target or PurePosixPath(item.display_name)
        end = len(target.parts) if item.is_directory else len(target.parts) - 1
        return {
            PurePosixPath(*target.parts[:depth])
            for depth in range(1, end + 1)
        }

    ordered_items = sorted(
        expanded_variable,
        key=lambda item: (
            -item.estimated_bytes,
            -item.size_bytes,
            item.display_name.casefold(),
        ),
    )

    mutable_bins: list[list[SourceItem]] = []
    used_file_estimates: list[int] = []
    used_directories: list[set[PurePosixPath]] = []
    for item in ordered_items:
        item_directories = needed_directories(item)
        for index, used in enumerate(used_file_estimates):
            new_directories = item_directories - used_directories[index]
            added = item.estimated_bytes + len(new_directories) * DIRECTORY_METADATA_RESERVE
            if (used + len(used_directories[index]) * DIRECTORY_METADATA_RESERVE
                    + added <= variable_limit):
                mutable_bins[index].append(item)
                used_file_estimates[index] += item.estimated_bytes
                used_directories[index].update(item_directories)
                break
        else:
            initial = item.estimated_bytes + len(item_directories) * DIRECTORY_METADATA_RESERVE
            if initial > variable_limit:
                raise PackingError(
                    f"El elemento '{item.display_name}' es demasiado grande para un ISO "
                    "junto con los contenidos permanentes."
                )
            mutable_bins.append([item])
            used_file_estimates.append(item.estimated_bytes)
            used_directories.append(set(item_directories))

    if not mutable_bins:
        mutable_bins.append([])
        used_file_estimates.append(0)
        used_directories.append(set())

    bins: list[IsoBin] = []
    for number, (items, file_estimate, directories) in enumerate(
        zip(mutable_bins, used_file_estimates, used_directories, strict=True),
        start=1,
    ):
        variable_size = sum(item.size_bytes for item in items)
        variable_estimated = file_estimate + len(directories) * DIRECTORY_METADATA_RESERVE
        estimated_iso = fixed_size + variable_estimated
        bins.append(
            IsoBin(
                index=number,
                items=tuple(items),
                variable_size_bytes=variable_size,
                variable_estimated_bytes=variable_estimated,
                estimated_iso_bytes=estimated_iso,
                free_bytes=capacity_bytes - estimated_iso,
            )
        )

    return PackingPlan(
        capacity_bytes=capacity_bytes,
        permanent_items=tuple(permanent_items),
        bins=tuple(bins),
        variable_sources=tuple(variable_items),
        permanent_size_bytes=permanent_size,
        permanent_estimated_bytes=permanent_estimated,
        variable_size_bytes=sum(item.size_bytes for item in expanded_variable),
    )
