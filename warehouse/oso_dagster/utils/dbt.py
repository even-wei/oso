import os
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Tuple, Dict
import pathlib

from dagster_dbt import DbtCliResource

import yaml


@dataclass(kw_only=True)
class BQTargetConfigTemplate:
    job_execution_time_seconds: int = 3600
    job_retries: int = 3
    location: str = "US"
    method: str = "oauth"
    keyfile: str = ""
    impersonate_service_account: str = ""
    threads: int = 32

    def as_dict(self) -> dict:
        output = asdict(self)
        output["type"] = "bigquery"
        if self.method == "oauth":
            del output["keyfile"]
            if self.impersonate_service_account == "":
                del output["impersonate_service_account"]
        else:
            del output["impersonate_service_account"]
        return output


def get_profiles_dir():
    return os.environ.get("DBT_PROFILES_DIR", os.path.expanduser("~/.dbt"))


def default_profiles_path():
    return os.path.join(get_profiles_dir(), "profiles.yml")


def generate_dbt_profile(
    project_id: str,
    profile_name: str,
    targets: List[Tuple[str, str]],
    template: BQTargetConfigTemplate,
):
    targets_dict = dict()
    for target_name, dataset_name in targets:
        target_dict = template.as_dict()
        target_dict["project"] = project_id
        target_dict["dataset"] = dataset_name
        targets_dict[target_name] = target_dict
    yaml_dict = {profile_name: {"outputs": targets_dict}}
    return yaml.dump(yaml_dict)


def write_dbt_profile(
    project_id: str,
    profile_name: str,
    targets: List[Tuple[str, str]],
    template: BQTargetConfigTemplate,
    profile_path: str = "",
):
    if not profile_path:
        profile_path = default_profiles_path()

    Path(os.path.dirname(profile_path)).mkdir(parents=True, exist_ok=True)

    generated_profiles_yml = generate_dbt_profile(
        project_id, profile_name, targets, template
    )

    with open(profile_path, "w") as f:
        f.write(generated_profiles_yml)


def load_dbt_manifests(
    dbt_target_base_dir: str,
    dbt_project_dir: str,
    project_id: str,
    profile_name: str,
    targets: List[Tuple[str, str]],
    template: BQTargetConfigTemplate,
    parse_projects: bool = False,
    force_auth: bool = False,
) -> Dict[str, str]:
    manifests: Dict[str, str] = dict()
    print(f"checking for profile {default_profiles_path()}")

    if not os.path.isfile(default_profiles_path()) or force_auth:
        print("Writing dbt profile")
        write_dbt_profile(
            project_id,
            profile_name,
            targets,
            template,
        )

    if parse_projects:
        for target, _ in targets:
            target_path = Path(dbt_target_base_dir, target)
            # Ensure the dbt_target_base_dir exists
            pathlib.Path(dbt_target_base_dir).mkdir(parents=True, exist_ok=True)

            dbt = DbtCliResource(project_dir=os.fspath(dbt_project_dir), target=target)
            manifests[target] = (
                dbt.cli(
                    ["--quiet", "parse"],
                    target_path=target_path,
                )
                .wait()
                .target_path.joinpath("manifest.json")
            )
    else:
        for target, _ in targets:
            manifests[target] = os.path.join(
                dbt_target_base_dir, target, "manifest.json"
            )
    return manifests
