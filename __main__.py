import argparse
from utils import get_config
from pathlib import Path
from PoolBot import PoolBot

def main():
	parser = argparse.ArgumentParser(
		prog="poolbot",
		description=(
			"A Discord bot to combine generated pools and packs in the Arena "
			"Sealed League."
		),
	)
	parser.add_argument(
		"--config",
		help="config file path (default: ./config.yaml)",
		default="config.yaml",
	)
	args = parser.parse_args()
	config = get_config(Path(args.config))
	bot = PoolBot(config)
	bot.run(config.discord_token)

if __name__ == "__main__":
	main()