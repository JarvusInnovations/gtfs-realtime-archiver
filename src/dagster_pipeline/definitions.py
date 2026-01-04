from pathlib import Path

from dagster import Definitions, definitions, load_from_defs_folder


@definitions
def defs() -> Definitions:
    return load_from_defs_folder(path_within_project=Path(__file__).parent)
