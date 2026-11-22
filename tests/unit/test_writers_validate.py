import json
import shutil

import pytest

from ingestion.synthetic import validate_dataset, write_dataset
from ingestion.synthetic.constants import BANNER_FILENAME, MANIFEST_FILENAME, SYNTHETIC_MARKER
from ingestion.synthetic.spec import ENTITIES

pytestmark = pytest.mark.unit


@pytest.fixture
def dataset_copy(clean_dataset_dir, tmp_path):
    target = tmp_path / "ds"
    shutil.copytree(clean_dataset_dir, target)
    return target


def test_clean_dataset_validates(clean_dataset_dir):
    assert validate_dataset(clean_dataset_dir) == []


def test_all_files_manifest_and_banner_are_written(clean_dataset_dir):
    for spec in ENTITIES:
        assert (clean_dataset_dir / spec.relative_path).is_file()
    manifest = json.loads((clean_dataset_dir / MANIFEST_FILENAME).read_text())
    assert manifest["synthetic_marker"] == SYNTHETIC_MARKER
    assert manifest["total_rows"] == sum(f["rows"] for f in manifest["files"])
    assert SYNTHETIC_MARKER in (clean_dataset_dir / BANNER_FILENAME).read_text()


def test_identical_config_yields_identical_checksums(clean_data, clean_dataset_dir, tmp_path):
    second = write_dataset(clean_data, tmp_path / "again")
    first = json.loads((clean_dataset_dir / MANIFEST_FILENAME).read_text())
    assert second == first


def test_missing_manifest_is_reported(tmp_path):
    assert validate_dataset(tmp_path)[0].startswith("missing manifest.json")


def test_tampering_is_detected_by_checksum(dataset_copy):
    path = dataset_copy / "sis" / "schools.csv"
    path.write_text(path.read_text() + "\n")
    assert any("checksum mismatch: sis/schools.csv" in e for e in validate_dataset(dataset_copy))


def test_missing_file_is_detected(dataset_copy):
    (dataset_copy / "lms" / "lms_activity.jsonl").unlink()
    assert any("missing file: lms/lms_activity.jsonl" in e for e in validate_dataset(dataset_copy))


def test_unmarked_rows_are_detected(dataset_copy):
    """Strip the marker from one row, then fix the manifest checksum so only the marker fails."""
    from ingestion.synthetic.writers import sha256_of

    path = dataset_copy / "grading" / "grades.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[0])
    row["synthetic_marker"] = "REAL"
    lines[0] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")
    manifest_path = dataset_copy / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == "grading/grades.jsonl":
            entry["sha256"] = sha256_of(path)
    manifest_path.write_text(json.dumps(manifest))
    assert any("1 rows missing synthetic_marker" in e for e in validate_dataset(dataset_copy))


@pytest.mark.parametrize(
    ("leak", "label"),
    [
        ("123-45-6789", "ssn-like"),
        ("(555) 123-4567", "phone-like"),
        ("kid@gmail.com", "email-like"),
    ],
)
def test_pii_like_strings_are_flagged(dataset_copy, leak, label):
    from ingestion.synthetic.writers import sha256_of

    path = dataset_copy / "sis" / "students.csv"
    path.write_text(path.read_text() + f"# {leak}\n")
    manifest_path = dataset_copy / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == "sis/students.csv":
            entry["sha256"] = sha256_of(path)
    manifest_path.write_text(json.dumps(manifest))
    assert any(label in e for e in validate_dataset(dataset_copy))


def test_reserved_example_domain_is_allowed(dataset_copy):
    from ingestion.synthetic.writers import sha256_of

    path = dataset_copy / "sis" / "students.csv"
    path.write_text(path.read_text() + "# someone@example.invalid\n")
    manifest_path = dataset_copy / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == "sis/students.csv":
            entry["sha256"] = sha256_of(path)
    manifest_path.write_text(json.dumps(manifest))
    assert not any("email-like" in e for e in validate_dataset(dataset_copy))


def test_orphan_rows_are_detected(dataset_copy):
    from ingestion.synthetic.writers import sha256_of

    path = dataset_copy / "attendance" / "attendance_daily.csv"
    header, first, *rest = path.read_text().splitlines()
    first = first.replace(first.split(",")[2], "SYN-T99-ST99999", 1)
    path.write_text("\n".join([header, first, *rest]) + "\n")
    manifest_path = dataset_copy / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == "attendance/attendance_daily.csv":
            entry["sha256"] = sha256_of(path)
    manifest_path.write_text(json.dumps(manifest))
    assert any("reference an unknown student" in e for e in validate_dataset(dataset_copy))


def test_writer_rejects_rows_that_do_not_match_spec(clean_data, tmp_path):
    import copy

    broken = copy.deepcopy(clean_data)
    broken.tables["sis/schools"][0]["extra_column"] = "x"
    with pytest.raises(ValueError, match="do not match spec"):
        write_dataset(broken, tmp_path / "broken")
