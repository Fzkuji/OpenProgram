from pathlib import Path
from openprogram.providers._migrate_catalog import verify_equivalence

_ROOT = Path(__file__).resolve().parents[2] / "openprogram" / "providers"


def test_new_catalog_equivalent_to_old():
    # After migration is run, every _catalog key must reproduce byte-identical Model.
    mismatched = verify_equivalence(_ROOT / "_catalog", _ROOT)
    assert mismatched == [], f"{len(mismatched)} keys differ: {mismatched[:10]}"
