# bot.py
import os

import discord
import re
from dotenv import load_dotenv
from typing import Optional, Sequence, Union
from datetime import datetime

import aiohttp
import utils

load_dotenv()

SEALEDDECK_URL = "https://sealeddeck.tech/api/pools"

def arena_to_json(arena_list: str) -> Sequence[dict]:
	"""Convert a list of cards in arena format to a list of json cards"""
	json_list = []
	for line in arena_list.rstrip("\n ").split("\n"):
		count, card = line.split(" ", 1)
		card_name = card.split(" (")[0]
		json_list.append({"name": f"{card_name}", "count": int(count)})
	return json_list

async def pool_to_sealeddeck(
	punishment_cards: Sequence[dict], pool_sealeddeck_id: Optional[str] = None
) -> str:
	"""Adds punishment cards to a sealeddeck.tech pool and returns the id"""
	deck: dict[str, Union[Sequence[dict], str]] = {"sideboard": punishment_cards}
	if pool_sealeddeck_id:
		deck["poolId"] = pool_sealeddeck_id

	async with aiohttp.ClientSession() as session:
		async with session.post(SEALEDDECK_URL, json=deck) as resp:
			resp.raise_for_status()
			resp_json = await resp.json()

	return resp_json["poolId"]

async def update_message(message, new_content):
	"""Updates the text contents of a sent bot message"""
	return await message.edit(content=new_content)

class PoolBot(discord.Client):
	def __init__(self, config: utils.Config, *args, **kwargs):
		self.config = config
		# TODO(sawyer): Allow this to be set with a command by Arena Sealed League admins
		self.league_start = datetime.fromisoformat('2022-01-04')
		super().__init__(*args, **kwargs)

	async def on_ready(self):
		print(f'{self.user} has connected to Discord!')
		self.pool_channel = None
		self.packs_channel = None
		# Safe to assume that there's only one guild, because this is a very specific bot.
		for channel in self.guilds[0].channels:
			if (channel.name == 'starting-pools'):
				self.pool_channel = channel
			if (channel.name == 'pack-generation'):
				self.packs_channel = channel
		if (self.pool_channel == None):
			print('Could not find starting-pools channel')
		if (self.packs_channel == None):
			print('Could not find pack-generation channel')

	async def on_message(self, message):
		# Remove the prefix '!' and split the string on spaces
		argv = message.content.split()
		assert len(argv)
		command = argv[0].lower()
		if len(message.mentions):
			member = message.mentions[0]
		else:
			member = message.author

		if (message.channel.name == '#bot-lab'):
			return
		if command == '!viewpool':
			m = await message.channel.send(
						f"{message.author.mention}\n"
						f":hourglass: Searching for user's pool..."
					)
			pool = await self.find_pool(member.id)

			await update_message(m,
						f"{message.author.mention}\n"
						f":hourglass: Pool found. Searching for punishment packs..."
					)

			packs = await self.find_packs(member.id)

			if (len(packs) == 0):
				await update_message(m,
							f"{message.author.mention}\n"
							f"No punishment packs could be found.\n"
							f"Starting pool link: https://sealeddeck.tech/{pool}"
						)
				return
			await update_message(m,
						f"{message.author.mention}\n"
						f":hourglass: Found punishment pack(s). Adding to pool..."
					)
			try:
				if (len(packs) < 8):
					pack_json = arena_to_json('\n'.join(packs))
					new_id = await pool_to_sealeddeck(pack_json, pool)
				else:
					# Sealeddeck seems to be unable to handle adding more than 8 packs at a time.
					# For large pools, split the pack-adding into two separate requests.
					first_half_pack_json = arena_to_json('\n'.join(packs[:6]))
					second_half_pack_json = arena_to_json('\n'.join(packs[6:]))
					first_half_new_id = await pool_to_sealeddeck(first_half_pack_json, pool)
					new_id = await pool_to_sealeddeck(second_half_pack_json, first_half_new_id)
			except aiohttp.ClientResponseError as e:
				print(e)
				content = (
					f"{message.author.mention}\n"
					f"The packs could not be added to sealeddeck.tech."
				)

			else:
				content = (
					f"{message.author.mention}\n"
					f"Found {len(packs)} pack(s) and added them to the user's pool.\n\n"
					f"**Generated sealeddeck.tech pool**\n"
					f"link: https://sealeddeck.tech/{new_id}\n"
					f"ID: `{new_id}`\n"
					f"Note: This is still an experimental bot, and generated pools may\n"
					f"not be accurate. Please contact Sawyer T with any questions or if\n"
					f"you encounter any issues."
				)
			await update_message(m, content)
		if message.content.startswith('!setLeagueStartTime'):
			await message.channel.send('Command received but not yet implemented.')

	async def find_pool(self, user_id):
		async for message in self.pool_channel.history(limit = 200, after = self.league_start).filter(lambda message : message.author.name == 'Booster Tutor'):
			for mentionedUser in message.mentions:
				if (mentionedUser.id == user_id):
					# Use a regex to pull the sealeddeck code out of the message
					# TODO(sawyer): Make sure this is robust
					link = re.search("(?P<url>https?://[^\s]+)", message.content).group("url").split('sealeddeck.tech/')[1]
					return link

	async def find_packs(self, user_id):
		packs = []
		async for message in self.packs_channel.history(limit = 5000, after = self.league_start).filter(lambda message : message.author.name == 'Booster Tutor'):
			for mentionedUser in message.mentions:
				# Exclude non-pack Booster Tutor messages, e.g. responses to !addpack
				if (mentionedUser.id == user_id and "```" in message.content):
					pack_content = message.content.split("```")[1].strip()
					packs.append(pack_content)
		print(packs)
		return packs