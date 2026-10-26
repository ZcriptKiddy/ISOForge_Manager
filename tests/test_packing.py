"""Pruebas rápidas del algoritmo y sus validaciones principales."""

from pathlib import Path
import unittest

from file_utils import SourceItem
from packing import FIXED_ISO_OVERHEAD, PackingError, create_packing_plan


def item(name: str, size: int) -> SourceItem:
    return SourceItem(
        path=Path("C:/fuentes") / name,
        kind="file",
        size_bytes=size,
        estimated_bytes=size,
        file_count=1,
        directory_count=0,
    )


class PackingTests(unittest.TestCase):
    def test_ffd_uses_two_bins(self) -> None:
        capacity = FIXED_ISO_OVERHEAD + 10
        plan = create_packing_plan([], [item("a", 6), item("b", 5), item("c", 4)], capacity)
        self.assertEqual(plan.iso_count, 2)
        self.assertEqual([entry.size_bytes for entry in plan.bins[0].items], [6, 4])

    def test_permanent_only_creates_one_iso(self) -> None:
        plan = create_packing_plan(
            [item("fijo", 10)], [], FIXED_ISO_OVERHEAD + 20
        )
        self.assertEqual(plan.iso_count, 1)
        self.assertEqual(plan.bins[0].filename, "ISO01.iso")

    def test_permanent_content_over_capacity_is_rejected(self) -> None:
        with self.assertRaises(PackingError):
            create_packing_plan(
                [item("fijo", 11)], [], FIXED_ISO_OVERHEAD + 10
            )

    def test_large_variable_item_is_rejected(self) -> None:
        with self.assertRaises(PackingError):
            create_packing_plan(
                [item("fijo", 3)],
                [item("enorme", 8)],
                FIXED_ISO_OVERHEAD + 10,
            )

    def test_duplicate_paths_are_rejected(self) -> None:
        duplicate = item("mismo", 1)
        with self.assertRaises(PackingError):
            create_packing_plan(
                [duplicate], [duplicate], FIXED_ISO_OVERHEAD + 10
            )


if __name__ == "__main__":
    unittest.main()
