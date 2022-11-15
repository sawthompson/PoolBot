import argparse
import discord
from utils import get_config
from pathlib import Path
from PoolBot import PoolBot

def main():
	parser = argparse.ArgumentParser(
		prog="poolbot",
		description=(
			"A Discord bot to help out with the Arena Gauntlet League."
		),
	)
	parser.add_argument(
		"--config",
		help="config file path (default: ./config.yaml)",
		default="config.yaml",
	)
	args = parser.parse_args()
	config = get_config(Path(args.config))
	intents = discord.Intents.all()
	intents.members = True
	bot = PoolBot(config, intents)
	bot.run(config.discord_token)

if __name__ == "__main__":
	main()
