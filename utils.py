from pathlib import Path
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Config:
	discord_token: str
	debug_mode: str
	spreadsheet_id: str
	pools_tab_id: str


def get_config(path: Path = Path("config.yaml")) -> Config:
	with open(path) as file:
		config_dict = yaml.load(file, Loader=yaml.FullLoader)
	config = Config(**config_dict)
	return config

