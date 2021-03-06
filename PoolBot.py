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
	def __init__(self, config: utils.Config, intents: discord.Intents, *args, **kwargs):
		self.config = config
		# TODO(sawyer): Allow this to be set with a command by Arena Sealed League admins
		self.league_start = datetime.fromisoformat('2022-01-04')
		super().__init__(intents=intents, *args, **kwargs)

	async def on_ready(self):
		print(f'{self.user} has connected to Discord!')
		await self.user.edit(username='AGL Bot')
		self.pool_channel = None
		self.packs_channel = None
		self.lfm_channel = None
		self.pending_lfm_user_mention = None
		self.active_lfm_message = None
		# Safe to assume that there's only one guild, because this is a very specific bot.
		for channel in self.guilds[0].channels:
			if (channel.name == 'starting-pools'):
				self.pool_channel = channel
			if (channel.name == 'pack-generation'):
				self.packs_channel = channel
			if (channel.name == 'looking-for-matches'):
				self.lfm_channel = channel
		if self.pool_channel == None:
			print('Could not find starting-pools channel')
		if self.packs_channel == None:
			print('Could not find pack-generation channel')
		if self.lfm_channel == None:
			print('Could not find looking-for-matches channel')

	async def on_message(self, message):
		# Remove the prefix '!' and split the string on spaces
		argv = message.content.split(None, 1)
		assert len(argv)
		command = argv[0].lower()
		argument = ''
		if '"' in message.content:
			# Support arguments passed in quotes
			argument = message.content.split('"')[1]
		elif ' ' in message.content:
			argument = argv[1]
		if len(message.mentions):
			member = message.mentions[0]

		if not message.guild:
			if message.author.bot:
				return
			await self.on_dm(message, command, argument)
			return

		if message.channel != self.lfm_channel:
			return

		if command == '!challenge':
			await self.issue_challenge(message)

		if command == '!viewpool':
			# Support viewing the pool of a user by referencing their name instead of mentioning them
			member = self.guilds[0].get_member_named(argument)
			if member == None:
				await message.channel.send(
						f"{message.author.mention}\n"
						f"No user could be found with that name. Make sure you're using "
						f"their discord identifying name, and not their guild nickname. "
						f"Names with spaces must be surrounded by quotes.\n"
					)
				return
		else:
			member = message.author

		if command == '!viewpool':
			m = await message.channel.send(
						f"{message.author.mention}\n"
						f":hourglass: Searching for user's pool..."
					)
			pool = await self.find_pool(member.id)
			if (pool == 'nopool'):
				await update_message(m,
							f"{message.author.mention}\n"
							f"Unable to find pool for user. Are you sure they are in the "
							f"current league?"
						)
				return
			if (pool == 'error'):
				await update_message(m,
							f"{message.author.mention}\n"
							f"Unable to find pool for user. This likely means that no "
							f"sealeddeck.tech link was generated for them with their pool. "
							f"You'll have to scout them manually. Sorry!"
						)
				return

			await update_message(m,
						f"{message.author.mention}\n"
						f":hourglass: Pool found. Searching for punishment packs..."
					)

			packs = await self.find_packs(member.id)

			if len(packs) == 0:
				await update_message(m,
							f"{message.author.mention}\n"
							f"No punishment packs could be found. "
							f"Starting pool link: https://sealeddeck.tech/{pool}"
						)
				return
			await update_message(m,
						f"{message.author.mention}\n"
						f":hourglass: Found punishment pack(s). Adding to pool..."
					)
			try:
				if len(packs) < 8:
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
					f"Note: This is still an experimental bot, and generated pools may "
					f"not be accurate. Please contact Sawyer T with any questions or if "
					f"you encounter any issues."
				)
			await update_message(m, content)
		elif command == '!setleaguestarttime':
			self.league_start = datetime.fromisoformat(argument)
			await message.channel.send(
				f"League start time updated to {argument}. Commands will now only look "
				f"for packs after that date."
			)
		elif command == '!help':
			await message.channel.send(
				f"You can give me one of the following commands:\n"
                f"> `!viewpool {{user}}`: finds and displays the pool, including "
                f"punishment packs, for a given user. Can reference a user either "
                f"through an @mention or by their discord name (displayed on their "
                f"below their nickname, e.g. 'sawyer#8108'. For users with spaces in "
                f"their names, use quotes, e.g. `!viewpool \"Soy Boy#1234\"`\n"
                f"> `!setLeagueStartTime`: updates the league start time used to "
                f"search for pools and packs. Takes in a date in the form YYYY-MM-DD\n"
                f"> `!help`: shows this message\n"
			)

	async def issue_challenge(self, message):
		if not self.pending_lfm_user_mention:
			await self.lfm_channel.send(
				"Sorry, but no one is looking for a match right now. You can send out an anonymous LFM by DMing me `!lfm`."
			)
			return
		
		await self.lfm_channel.send(
			f"{self.pending_lfm_user_mention}, your anonymous LFM has been accepted by {message.author.mention}.")

		await update_message(
			self.active_lfm_message,
			f'~~{self.active_lfm_message.content}~~\n'
			f'A match was found between {self.pending_lfm_user_mention} and {message.author.mention}.'
			)
		
		self.pending_lfm_user_mention = None;
		self.active_lfm_message = None;

	async def on_dm(self, message, command, argument):
		if (command == '!lfm'):
			if (self.pending_lfm_user_mention):
				await message.author.send(
					"Someone is already looking for a match. You can play them by posting !challenge in the looking-for-matches channel of the league discord."
				)
				return
			if (not argument):
				self.active_lfm_message = await self.lfm_channel.send(
					"An anonymous player is looking for a match. Post `!challenge` to reveal their identity and initiate a match."
				)
			else:
				self.active_lfm_message = await self.lfm_channel.send(
					f"An anonymous player is looking for a match. Post `!challenge` to reveal their identity and initiate a match.\n"
					f"Message from the player:\n"
					f"> {argument}"
				)
			await message.author.send(
				f"I've created a post for you. You'll receive a mention when an opponent is found.\n"
				f"If you want to cancel this, send me a message with the text `!retractLfm`."
				)
			self.pending_lfm_user_mention = message.author.mention
			return

		if (command == '!retractlfm'):
			if (message.author.mention == self.pending_lfm_user_mention):
				await self.active_lfm_message.delete()
				self.active_lfm_message = None
				await message.author.send(
					"Understood. The post made on your behalf has been deleted."
					)
				self.pending_lfm_user_mention = None
			else:
				await message.author.send(
					"You don't currently have an outgoing LFM."
					)
			return

		await message.author.send(
			f"I'm sorry, but I didn't understand that. Please send one of the following commands:\n"
			f"> `!lfm`: creates an anonymous post looking for a match.\n"
			f"> `!retractLfm`: removes an anonymous LFM that you've sent out."
		)

	async def find_pool(self, user_id):
		async for message in self.pool_channel.history(limit = 1000, after = self.league_start).filter(lambda message : message.author.name == 'Booster Tutor'):
			for mentionedUser in message.mentions:
				if (mentionedUser.id == user_id):
					# Handle cases where Booster Tutor fails to generate a sealeddeck.tech link
					if ('**Sealeddeck.tech:** Error' in message.content):
						return 'error'
					# Use a regex to pull the sealeddeck id out of the message
					link = re.search("(?P<url>https?://[^\s]+)", message.content).group("url").split('sealeddeck.tech/')[1]
					return link
		return 'nopool'

	async def find_packs(self, user_id):
		packs = []
		async for message in self.packs_channel.history(limit = 5000, after = self.league_start).filter(lambda message : message.author.name == 'Booster Tutor'):
			for mentionedUser in message.mentions:
				# Exclude non-pack Booster Tutor messages, e.g. responses to !addpack
				if (mentionedUser.id == user_id and "```" in message.content):
					pack_content = message.content.split("```")[1].strip()
					packs.append(pack_content)
		return packs