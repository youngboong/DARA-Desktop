"""Default DARA settings. This approach was inspired by the atomate2 package."""

import warnings
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dara.hardware import detect_cpu_count

_DEFAULT_CONFIG_FILE_PATH = "~/.dara.yaml"


class DaraSettings(BaseSettings):
    """
    Settings for DARA.

    To edit these, please modify the config YAML file. By default, this is located at
    ~/.dara.yaml, but one can specify the path by setting the environment variable:
    "DARA_CONFIG_FILE".
    """

    CONFIG_FILE: str = Field(_DEFAULT_CONFIG_FILE_PATH, description="File to load alternative defaults from.")

    PATH_TO_ICSD: Path = Field(Path("~/ICSD_2024/ICSD_2024_experimental_inorganic/experimental_inorganic").expanduser())
    PATH_TO_COD: Path = Field(Path("~/COD_2024").expanduser())

    BGMN_N_THREADS: int = Field(
        1,
        description="Number of internal threads BGMN uses per refinement. Kept at 1 by "
        "default so worker parallelism (RAY_NUM_CPUS) doesn't oversubscribe the machine's "
        "cores. Override with the DARA_BGMN_N_THREADS env var.",
    )
    RAY_NUM_CPUS: int = Field(
        default_factory=detect_cpu_count,
        description="Number of CPUs Ray is told it has available, passed explicitly to "
        "ray.init() so it doesn't auto-detect (which grabs the whole node under SLURM). "
        "Defaults to the runtime-detected usable core count. Override with the "
        "DARA_RAY_NUM_CPUS env var.",
    )

    RAY_OBJECT_STORE_MB: int = Field(
        1024,
        description="Object store size (MB) given to ray.init(). Ray sizes it from total RAM by "
        "default, which can exhaust the Windows paging file; the patterns and refinement results "
        "are small, so a fixed cap is enough. Override with the DARA_RAY_OBJECT_STORE_MB env var.",
    )

    model_config = SettingsConfigDict(env_prefix="dara_")  # prepend dara_ to env vars

    @model_validator(mode="before")
    @classmethod
    def load_default_settings(cls, values) -> dict:
        """
        Load settings from file or environment variables.

        Loads settings from a root file if available and uses that as defaults in
        place of built-in defaults.

        This allows setting of the config file path through environment variables.
        """
        from monty.serialization import loadfn

        config_file_path = values.get("CONFIG_FILE", _DEFAULT_CONFIG_FILE_PATH)
        config_file_path = Path(config_file_path).expanduser()

        new_values = {}
        if config_file_path.exists():
            if config_file_path.stat().st_size == 0:
                warnings.warn(
                    f"DARA config file is empty: {config_file_path}",
                    stacklevel=2,
                )
            else:
                try:
                    new_values.update(loadfn(config_file_path))
                except ValueError:
                    raise SyntaxError(f"DARA config file is unparsable:{config_file_path} ") from None

        return {**new_values, **values}


def ray_init_options() -> dict:
    """``num_cpus`` and ``object_store_memory`` for ``ray.init``.

    The worker count follows RAY_NUM_CPUS but is lowered to what the free memory holds, so a
    busy machine runs fewer parallel refinements instead of crashing the raylet. An explicit
    DARA_RAY_NUM_CPUS is used as given.
    """
    import os

    from dara.hardware import memory_capped_workers

    settings = DaraSettings()
    cpus = settings.RAY_NUM_CPUS
    if "DARA_RAY_NUM_CPUS" not in {key.upper() for key in os.environ}:
        cpus = memory_capped_workers(cpus, settings.RAY_OBJECT_STORE_MB / 1024)
    return {"num_cpus": cpus, "object_store_memory": settings.RAY_OBJECT_STORE_MB * 1024 * 1024}
