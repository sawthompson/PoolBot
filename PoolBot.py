from __future__ import print_function
# bot.py
import os

import discord
import re
import random
import time
from dotenv import load_dotenv
from typing import Optional, Sequence, Union
from datetime import datetime

import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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


async def message_member(member):
    try:
        await member.send("Greetings, Arena Gauntlet League player!")
        time.sleep(0.25)
    except discord.errors.Forbidden as e:
        print(e)


class PoolBot(discord.Client):
    def __init__(self, config: utils.Config, intents: discord.Intents, *args, **kwargs):
        self.booster_tutor = None
        self.spreadsheet_id = None
        self.awaiting_boosters_for_user = None
        self.num_boosters_awaiting = None
        self.active_lfm_message = None
        self.league_committee_channel = None
        self.bot_bunker_channel = None
        self.lfm_channel = None
        self.packs_channel = None
        self.pool_channel = None
        self.dev_mode = None
        self.pending_lfm_user_mention = None
        self.config = config
        self.league_start = datetime.fromisoformat('2022-06-22')
        super().__init__(intents=intents, *args, **kwargs)

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        await self.user.edit(username='AGL Bot')
        # If this is true, posts will be limited to #bot-lab and #bot-bunker, and LFM DMs will be ignored.
        self.dev_mode = self.config.debug_mode == "active"
        self.pool_channel = self.get_channel(719933932690472970) if not self.dev_mode else self.get_channel(
            1065100936445448232)
        self.packs_channel = self.get_channel(798002275452846111) if not self.dev_mode else self.get_channel(
            1065101003168436295)
        self.lfm_channel = self.get_channel(720338190300348559) if not self.dev_mode else self.get_channel(
            1065101040770363442)
        self.bot_bunker_channel = self.get_channel(1000465465572864141) if not self.dev_mode else self.get_channel(
            1065101076002508800)
        self.league_committee_channel = self.get_channel(756195275743166655) if not self.dev_mode else self.get_channel(
            1065101182525259866)
        self.pending_lfm_user_mention = None
        self.active_lfm_message = None
        self.num_boosters_awaiting = 0
        self.awaiting_boosters_for_user = None
        self.spreadsheet_id = self.config.spreadsheet_id
        for user in self.users:
            if user.name == 'Booster Tutor':
                self.booster_tutor = user

    async def on_message(self, message):
        # As part of the !playerchoice flow, repost Booster Tutor packs in pack-generation with instructions for
        # the appropriate user to select their pack.
        if (message.channel == self.bot_bunker_channel and message.author == self.booster_tutor
                and message.mentions[0] == self.user):
            await self.handle_booster_tutor_response(message)
            return

        # Split the string on the first space
        argv = message.content.split(None, 1)
        assert len(argv)
        command = argv[0].lower()
        argument = ''
        if '"' in message.content:
            # Support arguments passed in quotes
            argument = message.content.split('"')[1]
        elif ' ' in message.content:
            argument = argv[1]

        if not message.guild:
            if message.author == self.user:
                return
            await self.on_dm(message, command, argument)
            return

        if command == '!becomecompleat' and message.channel == self.pool_channel:
            await self.compleat_player(message)
            return

        if command == '!playerchoice' and message.channel == self.packs_channel:
            await self.prompt_user_pick(message)
            return

        if command == '!addpack' and message.reference:
            await self.add_pack(message, argument)
            return

        if command == '!randint':
            args = argv[1].split(None)
            if len(args) == 1:
                await message.channel.send(
                    f"{random.randint(1, int(args[0]))}"
                )
            else:
                await message.channel.send(
                    f"{random.randint(int(args[0]), int(args[1]))}"
                )
            return

        if message.channel == self.lfm_channel and command == '!challenge':
            await self.issue_challenge(message)

        if command == '!viewpool':
            # Support viewing the pool of a user by referencing their name instead of mentioning them
            member = self.guilds[0].get_member_named(argument)
            if member is None:
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
            if pool == 'nopool':
                await update_message(m,
                                     f"{message.author.mention}\n"
                                     f"Unable to find pool for user. Are you sure they are in the "
                                     f"current league?"
                                     )
                return
            if pool == 'error':
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
                f"> `!challenge`: Challenges the current player in the LFM queue\n"
                f"> `!randint A B`: Generates a random integer n, where A <= n <= B. If only one input is given, "
                f"uses that value as B and defaults A to 1. \n "
                f"> `!help`: shows this message\n"
            )

    async def prompt_user_pick(self, message):
        # # Ensure the user doesn't already have a pending pick to make
        # pendingPickMessage = await self.packs_channel.history().find(
        # 	lambda m : m.author.name == 'AGL Bot'
        # 	and m.mentions
        # 	and m.mentions[0] == message.mentions[0]
        # 	and f'Pack Option' in m.content
        # 	)
        # if (pendingPickMessage):
        # 	await self.packs_channel.send(
        # 		f'{message.mentions[0].mention} You still have a pending pack selection to make! Please select your '
        # 		f'previous pack, and then post in #league-committee so someone can can manually generate your new packs.'
        # 	)
        # 	return

        # Messages from Booster Tutor aren't tied to a user, so only one pair can be resolved at a time.
        while self.awaiting_boosters_for_user is not None:
            time.sleep(3)

        booster_one_type = message.content.split(None)[1]
        booster_two_type = message.content.split(None)[2]
        self.num_boosters_awaiting = 2
        self.awaiting_boosters_for_user = message.mentions[0]

        # Generate two packs of the specified types
        await self.bot_bunker_channel.send(booster_one_type)
        await self.bot_bunker_channel.send(booster_two_type)

    async def handle_booster_tutor_response(self, message):
        assert self.num_boosters_awaiting > 0
        if self.num_boosters_awaiting == 2:
            self.num_boosters_awaiting -= 1
            await self.packs_channel.send(
                f'Pack Option A (Urza) for {self.awaiting_boosters_for_user.mention}. To select this pack, DM me '
                f'`!chooseUrza`\n '
                f'```{message.content.split("```")[1].strip()}```')
        else:
            self.num_boosters_awaiting -= 1
            await self.packs_channel.send(
                f'Pack Option B (Mishra) for {self.awaiting_boosters_for_user.mention}. To select this pack, DM me '
                f'`!chooseMishra`\n '
                f'```{message.content.split("```")[1].strip()}```')
        if self.num_boosters_awaiting == 0:
            self.awaiting_boosters_for_user = None

    async def issue_challenge(self, message):
        if not self.pending_lfm_user_mention:
            await self.lfm_channel.send(
                "Sorry, but no one is looking for a match right now. You can send out an anonymous LFM by DMing me "
                "`!lfm`. "
            )
            return

        await self.lfm_channel.send(
            f"{self.pending_lfm_user_mention}, your anonymous LFM has been accepted by {message.author.mention}.")

        await update_message(
            self.active_lfm_message,
            f'~~{self.active_lfm_message.content}~~\n'
            f'A match was found between {self.pending_lfm_user_mention} and {message.author.mention}.'
        )

        self.pending_lfm_user_mention = None
        self.active_lfm_message = None

    async def choose_pack(self, user, chosen_option):
        if chosen_option == 'A':
            not_chosen_option = 'B'
            split = '!chooseUrza`'
            not_chosen_split = '!chooseMishra`'
        else:
            not_chosen_option = 'A'
            split = '!chooseMishra`'
            not_chosen_split = '!chooseUrza`'
        chosen_message = None
        async for message in self.packs_channel.history(limit=500):
            if (message.author.name == 'AGL Bot' and message.mentions and message.mentions[0] == user
                    and f'Pack Option {chosen_option}' in message.content):
                chosen_message = message
                break

        not_chosen_message = None
        async for message in self.packs_channel.history(limit=500):
            if (message.author.name == 'AGL Bot' and message.mentions and message.mentions[0] == user
                    and f'Pack Option {not_chosen_option}' in message.content):
                not_chosen_message = message
                break

        if not chosen_message or not not_chosen_message:
            await user.send(
                f"Sorry, but I couldn't find any pending packs for you. Please post in "
                f"{self.league_committee_channel.mention} if you think this is an error.")
            return

        chosen_message_text = f'Pack chosen by {user.mention}.{chosen_message.content.split(split)[1]}'

        await update_message(chosen_message, chosen_message_text)

        await update_message(not_chosen_message,
                             f'Pack not chosen by {user.mention}.'
                             f'~~{not_chosen_message.content.split(not_chosen_split)[1]}~~')

        await user.send("Understood. Your selection has been noted.")

        # selected_pack = "\n" + chosen_message.content.split("```")[1]
        # result =
        # await self.update_pool(chosen_message.mentions[0], selected_pack, chosen_message, chosen_message_text)
        # if not result:
        #     await update_message(chosen_message,
        #                          chosen_message_text + "\n" + f"Unable to update pool. Please message Russell S")

        return

    async def on_dm(self, message, command, argument):
        if command == '!choosepacka' or command == '!chooseurza':
            await self.choose_pack(message.author, 'A')
            return

        if command == '!choosepackb' or command == '!choosemishra':
            await self.choose_pack(message.author, 'B')
            return

        if command == '!lfm':
            if self.pending_lfm_user_mention:
                await message.author.send(
                    "Someone is already looking for a match. You can play them by posting !challenge in the "
                    "looking-for-matches channel of the league discord. "
                )
                return
            if not argument:
                self.active_lfm_message = await self.lfm_channel.send(
                    "An anonymous player is looking for a match. Post `!challenge` to reveal their identity and "
                    "initiate a match. "
                )
            else:
                self.active_lfm_message = await self.lfm_channel.send(
                    f"An anonymous player is looking for a match. Post `!challenge` to reveal their identity and "
                    f"initiate a match.\n "
                    f"Message from the player:\n"
                    f"> {argument}"
                )
            await message.author.send(
                f"I've created a post for you. You'll receive a mention when an opponent is found.\n"
                f"If you want to cancel this, send me a message with the text `!retractLfm`."
            )
            self.pending_lfm_user_mention = message.author.mention
            return

        if command == '!retractlfm':
            if message.author.mention == self.pending_lfm_user_mention:
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
            f"> `!choosePackA`: responds to a pending pack selection option."
            f"> `!choosePackB`: responds to a pending pack selection option."
        )

    async def add_pack(self, message, argument):
        if message.channel != self.packs_channel:
            return

        ref = await message.channel.fetch_message(
            message.reference.message_id
        )
        if ref.author == self.booster_tutor:
            return
        if ref.author != self.user:
            await message.channel.send(
                f"{message.author.mention}\n"
                "The message you are replying to does not contain packs I have generated"
            )

        pack_content = ref.content.split("```")[1].strip()
        sealeddeck_id = argument.strip()
        pack_json = arena_to_json(pack_content)
        m = await message.channel.send(
            f"{message.author.mention}\n"
            f":hourglass: Adding pack to pool..."
        )
        try:
            new_id = await pool_to_sealeddeck(
                pack_json, sealeddeck_id
            )
        except aiohttp.ClientResponseError as e:
            print(f"Sealeddeck error: {e}")
            content = (
                f"{message.author.mention}\n"
                f"The packs could not be added to sealeddeck.tech "
                f"pool with ID `{sealeddeck_id}`. Please, verify "
                f"the ID.\n"
                f"If the ID is correct, sealeddeck.tech might be "
                f"having some issues right now, try again later."
            )

        else:
            content = (
                f"{message.author.mention}\n"
                f"The packs have been added to the pool.\n\n"
                f"**Updated sealeddeck.tech pool**\n"
                f"link: https://sealeddeck.tech/{new_id}\n"
                f"ID: `{new_id}`"
            )
        await m.edit(content=content)

    async def find_pool(self, user_id):
        async for message in self.pool_channel.history(limit=1000, after=self.league_start).filter(
                lambda m: m.author.name == 'Booster Tutor'):
            for mentionedUser in message.mentions:
                if mentionedUser.id == user_id:
                    # Handle cases where Booster Tutor fails to generate a sealeddeck.tech link
                    if '**Sealeddeck.tech:** Error' in message.content:
                        return 'error'
                    # Use a regex to pull the sealeddeck id out of the message
                    link = \
                        re.search("(?P<url>https?://[^\s]+)", message.content).group("url").split('sealeddeck.tech/')[1]
                    return link
        return 'nopool'

    async def find_packs(self, user_id):
        packs = []
        async for message in self.packs_channel.history(limit=5000, after=self.league_start).filter(
                lambda m: m.author.name == 'Booster Tutor'):
            for mentionedUser in message.mentions:
                # Exclude non-pack Booster Tutor messages, e.g. responses to !addpack
                if mentionedUser.id == user_id and "```" in message.content:
                    pack_content = message.content.split("```")[1].strip()
                    packs.append(pack_content)
        return packs

    async def get_members_not_in_league(self, league_name):
        for member in self.guilds[0].members:
            if member.display_name in '':
                print('trying to DM: ' + member.display_name)
                # if 'Sawyer T' in member.display_name:
                await message_member(member)
                print('DMed ' + member.display_name)
        # found = False
        # for role in member.roles:
        # 	if league_name in role.name:
        # 		found = True
        # if not found:
        # 	print(member.display_name)

    async def compleat_player(self, message):
        # TODO: update sheet values, and add reading/writing compleat state
        spreadsheet_values = await self.get_spreadsheet_values('Standings!C6:H120')
        if not spreadsheet_values:
            return await message.reply(f'Sorry, but I cannot access the spreadsheet. '
                                       f'Please post in {self.league_committee_channel.mention}')

        loss_count = 'not found'
        for row in spreadsheet_values:
            if len(row) < 6:
                continue
            if row[0].lower() != '' and row[0].lower() in message.author.display_name.lower():
                loss_count = int(row[5])
                break
        if loss_count == 'not found':
            return await message.reply(f'Unable to find your account in the league spreadsheet.'
                                       f'Please post in {self.league_committee_channel.mention}')
        if loss_count < 3:
            return await message.reply('Sorry, but you cannot become compleat until you have at least 3 losses')
        return await message.reply(f'!bro {loss_count + 6} {message.author.mention}')

    async def get_spreadsheet_values(self, range):
        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json',
                                                          ['https://www.googleapis.com/auth/spreadsheets'])
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', ['https://www.googleapis.com/auth/spreadsheets'])
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

        try:
            service = build('sheets', 'v4', credentials=creds)

            # Call the Sheets API
            sheet = service.spreadsheets()
            result = sheet.values().get(spreadsheetId=self.spreadsheet_id,
                                        range=range).execute()
            return result.get('values', [])
        except HttpError as err:
            print(err)
        return []

    # async def update_pool(self, user, pack_content, message, new_message_content):
    #     values = await self.get_spreadsheet_values()
    #     if not values:
    #         print('No data found.')
    #         return
    #
    #     curr_row = 7
    #     for row in values:
    #         if row[1].lower() in user.display_name.lower():
    #             sealeddeck_id = row[4].split("sealeddeck.tech/")[1]
    #             pack_json = arena_to_json(pack_content.split("\n", 1)[1])
    #             try:
    #                 new_id = await pool_to_sealeddeck(
    #                     pack_json, sealeddeck_id
    #                 )
    #             except aiohttp.ClientResponseError as e:
    #                 print(f"Sealeddeck error: {e}")
    #             else:
    #                 body = {
    #                     'values': [
    #                         [f"https://sealeddeck.tech/{new_id}"],
    #                     ],
    #                 }
    #                 sheet.values().update(spreadsheetId=self.spreadsheet_id,
    #                                       range=f'Pools!E{curr_row}:E{curr_row}', valueInputOption='USER_ENTERED',
    #                                       body=body).execute()
    #                 await update_message(message,
    #                                      new_message_content + f"\nUpdated Pool: https://sealeddeck.tech/{new_id}")
    #                 return True
    #         curr_row += 1


