"""Constants shared by the synthetic-data tooling."""

#: Value written to the ``synthetic_marker`` column of every generated record and to the
#: output manifest. Downstream layers (Bronze/Silver/Gold) must carry this through so that
#: synthetic data can never be mistaken for, or mixed with, real student records.
SYNTHETIC_MARKER = "SYNTHETIC_TEST_DATA"

#: Version of the *shape* of the generated source extracts (file names, columns). Bump when a
#: column is added, removed or renamed so adapters/dbt sources can pin to a contract.
SCHEMA_VERSION = "1.0.0"

#: Name of the manifest written alongside every generated dataset.
MANIFEST_FILENAME = "manifest.json"

#: Plain-text banner dropped next to the data so a human opening the folder is not misled.
BANNER_FILENAME = "README_SYNTHETIC_TEST_DATA.txt"

#: Reserved, non-routable e-mail domain (RFC 6761). The generator emits no e-mail addresses at
#: all; the validator treats any other e-mail-shaped string as a potential PII leak.
SAFE_EMAIL_DOMAIN = "example.invalid"

#: Placeholder geography that cannot correspond to a real place.
FAKE_STATE_CODE = "ZZ"
FAKE_POSTAL_CODE = "00000"
