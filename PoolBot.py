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

    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(SEALEDDECK_URL, json=deck) as resp:
                    resp.raise_for_status()
                    resp_json = await resp.json()
        except:
            continue
        else:
            break

    return resp_json["poolId"]


async def update_message(message: discord.Message, new_content: str):
    """Updates the text contents of a sent bot message"""
    return await message.edit(content=new_content)


async def message_member(member: Union[discord.Member, discord.User], message: str):
    try:
        await member.send(message)
        # await member.send(
        #     "Greetings, current or former Arena Gauntlet League player! This is your last chance to join us for the Wilds of Eldraine league before registration closes on Wednesday, September 6th at 5pm EST.\n\nSign up here: https://docs.google.com/forms/d/e/1FAIpQLSe44aHmif2QsplYoxdyKDmrpj6hRhywdPLQD4SYhOvhvjfsGA/viewform.\n\nWe hope to see you there!")
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
        self.side_quest_pools_channel = None
        self.dev_mode = None
        self.pools_tab_id = None
        self.pending_lfm_user_mention = None
        self.config = config
        self.league_start = datetime.fromisoformat('2022-06-22')
        super().__init__(intents=intents, *args, **kwargs)

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        await self.user.edit(username='AGL Bot')
        # If this is true, posts will be limited to #bot-lab and #bot-bunker, and LFM DMs will be ignored.
        self.dev_mode = self.config.debug_mode == "active"
        self.pools_tab_id = self.config.pools_tab_id
        self.pool_channel = self.get_channel(719933932690472970) if not self.dev_mode else self.get_channel(
            1065100936445448232)
        self.packs_channel = self.get_channel(798002275452846111) if not self.dev_mode else self.get_channel(
            1065101003168436295)
        self.lfm_channel = self.get_channel(720338190300348559) if not self.dev_mode else self.get_channel(
            1065101040770363442)
        self.bot_bunker_channel = self.get_channel(1000465465572864141) if not self.dev_mode else self.get_channel(
            1065101076002508800)

        self.league_committee_channel = self.get_channel(1052324453188632696) if not self.dev_mode else self.get_channel(
            1065101182525259866)
        self.side_quest_pools_channel = self.get_channel(1055515435073806387)
        self.pending_lfm_user_mention = None
        self.active_lfm_message = None
        self.num_boosters_awaiting = 0
        self.awaiting_boosters_for_user = None
        self.spreadsheet_id = self.config.spreadsheet_id
        for user in self.users:
            if user.name == 'Booster Tutor':
                self.booster_tutor = user
        #
        # for member in self.guilds[0].members:
        #     if member.bot:
        #         continue
        #     for role in member.roles:
        #         if 'Lord of the Rings' in role.name:
        #             # print(member.display_name)
        #             await self.packs_channel.send(f'!cube Fellowship {member.mention}')
        #             time.sleep(0.5)
        # await self.message_members_not_in_league("Wilds")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Booster tutor adds sealeddeck.tech links as part of an edit operation
        if before.author == self.booster_tutor:
            if before.channel == self.pool_channel and "Sealeddeck.tech link" not in before.content and\
                    "Sealeddeck.tech link" in after.content:
                # Edit adds a sealeddeck link
                await self.track_starting_pool(after)
                return

    async def on_message(self, message: discord.Message):
        # As part of the !playerchoice flow, repost Booster Tutor packs in pack-generation with instructions for
        # the appropriate user to select their pack.
        if (message.channel == self.bot_bunker_channel and message.author == self.booster_tutor
                and message.mentions[0] == self.user):
            await self.handle_booster_tutor_response(message)
            return

        if message.author == self.booster_tutor:
            if message.channel == self.packs_channel and "```" in message.content:
                # Message is a generated pack
                await self.track_pack(message)
                return

        # Split the string on the first space
        argv = message.content.split(None, 1)
        if len(argv) == 0:
            return
        command = argv[0].lower()
        argument = ''
        if '"' in message.content:
            # Support arguments passed in quotes
            argument = message.content.split('"')[1]
        elif ' ' in message.content:
            argument = argv[1]

        if not message.guild:
            # For now, only allow Sawyer to send broadcasts
            if command == '!messagetest' and 346124470940991488 == message.author.id:
                await self.message_members_not_in_league(message.content.split(' ')[1], argument, message.author, True)
                return

            if command == '!realmessageiambeingverycareful' and 346124470940991488 == message.author.id:
                await self.message_members_not_in_league(message.content.split(' ')[1], argument, message.author)
                return

            if message.author == self.user:
                return
            await self.on_dm(message, command, argument)
            return

        if command == '!playerchoice' and message.channel == self.packs_channel:
            await self.prompt_user_pick(message)
            return

        if command == '!addpack' and message.reference:
            await self.add_pack(message, argument)
            return

        if command == '!explore' and message.channel == self.packs_channel:
            await self.explore(message)
            return

        if command == '!collect' and message.channel == self.packs_channel:
            await self.collect_evidence(message, argument)

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
        elif command == '!help':
            await message.channel.send(
                f"You can give me one of the following commands:\n"
                f"> `!challenge`: Challenges the current player in the LFM queue\n"
                f"> `!randint A B`: Generates a random integer n, where A <= n <= B. If only one input is given, "
                f"uses that value as B and defaults A to 1. \n "
                f"> `!help`: shows this message\n"
            )

    async def collect(self, message: discord.Message, argument: str):
        allowed_sets = ["mkm", "lci", "woe", "mom", "one", "bro"]
        try:
            args = argument.split(' ')
            clues_to_spend = int(args[0])
            sets = args[1:]
        except ValueError:
            await message.reply("Hmm, I don't understand that. Try `!collect 2`, `!collect 4 SET`, `!collect 6 SET SET`, or `!collect 10 SET SET`. (SET can be mkm, lci, woe, mom, one, or bro. You can choose the same set twice.)")
            return
        if clues_to_spend not in [2,4,6,10]:
            await message.reply("You can only use 2, 4, 6, or 10 clues when collecting evidence.")
            return
        if clues_to_spend == 2 and (len(sets) != 1 or sets[0].lower() not in allowed_sets):
            await message.reply("Hmm, I don't understand that. Try `!collect 2`, `!collect 4 SET`, `!collect 6 SET SET`, or `!collect 10 SET SET`. (SET can be mkm, lci, woe, mom, one, or bro. You can choose the same set twice.)")
            return
        if clues_to_spend in [4, 6, 10] and (len(sets) != 2 or sets[0].lower() not in allowed_sets or sets[1].lower() not in allowed_sets):
            await message.reply("Hmm, I don't understand that. Try `!collect 2`, `!collect 4 SET`, `!collect 6 SET SET`, or `!collect 10 SET SET`. (SET can be mkm, lci, woe, mom, one, or bro. You can choose the same set twice.)")
            return

        sets = [s.lower() if s.lower() != "mkm" else "a-mkm" for s in sets]

        last_6 = "!from a-mkm|lci|woe|mom|one|bro"

        # Get sealeddeck link and loss count from spreadsheet
        spreadsheet_values = await self.get_spreadsheet_values('Pools!B7:AB200')
        curr_row = 6
        for row in spreadsheet_values:
            curr_row += 1
            if len(row) < 5:
                continue
            if row[0].lower() != '' and row[0].lower() in message.author.display_name.lower():
                clues = clues_available(row)
                if clues_available(row) < clues_to_spend:
                    await message.reply(f'By my records, you do not have enough clues. If this is in error, '
                                        f'please post in {self.league_committee_channel.mention}')
                    return

                # Mark the clues as used
                self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                           range=f'Pools!Q{curr_row}:Q{curr_row}', valueInputOption='USER_ENTERED',
                                           body={'values': [[int(row[15]) + clues_to_spend]]}).execute()

                if clues_to_spend == 2:
                    await self.packs_channel.send(f"{last_6} {message.author.mention}")
                    # TODO MKM replace pack???
                elif clues_to_spend == 4:
                    await self.packs_channel.send(f"!from {'|'.join(sets)} {message.author.mention}")
                    # TODO MKM replace pack???
                elif clues_to_spend == 6:
                    # ripped from prompt_user_pick
                    while self.awaiting_boosters_for_user is not None:
                        time.sleep(3)

                    booster_one_type = f"!{sets[0]}" # TODO MKM is this right?
                    booster_two_type = f"!{sets[1]}" # TODO MKM is this right?
                    self.num_boosters_awaiting = 2
                    self.awaiting_boosters_for_user = message.mentions[0]

                    # Generate two packs of the specified types
                    await self.bot_bunker_channel.send(booster_one_type)
                    await self.bot_bunker_channel.send(booster_two_type)
                    # TODO MKM replace pack??? (in choose_pack)
                elif clues_to_spend == 10:
                    await self.packs_channel.send(f"!{sets[0]} {message.author.mention}")
                    await self.packs_channel.send(f"!{sets[1]} {message.author.mention}")
                    # TODO MKM replace pack with both???
                return
        await message.reply(f'Hmm, I can\'t find you in the league spreadsheet. '
                            f'Please post in {self.league_committee_channel.mention}')

    async def explore(self, message: discord.Message):
        possible_sets = [
            "SIR",
            "AKR",
            "KLR",
            "WOE",
            "MOM",
            "ONE",
            "BRO",
            "DMU",
            "SNC",
            "NEO",
            "VOW",
            "MID",
            "AFR",
            "STX",
            "KHM",
            "ZNR",
            "M21",
            "IKO",
            "THB",
            "ELD",
            "M20",
            "WAR",
            "RNA",
            "GRN",
            "M19",
            "DOM",
            "RIX",
            "XLN",
        ]
        set_to_generate = random.choice(possible_sets)
        # Get sealeddeck link and loss count from spreadsheet
        spreadsheet_values = await self.get_spreadsheet_values('Pools!B7:R200')
        curr_row = 6
        for row in spreadsheet_values:
            curr_row += 1
            if len(row) < 5:
                continue
            if row[0].lower() != '' and row[0].lower() in message.author.display_name.lower():
                if int(row[16]) <= 0:
                    await message.reply(f'By my records, you do not have any unused maps. If this is in error, '
                                        f'please post in {self.league_committee_channel.mention}')
                    return

                # Mark the map as used
                self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                           range=f'Pools!Q{curr_row}:Q{curr_row}', valueInputOption='USER_ENTERED',
                                           body={'values': [[int(row[15]) + 1]]}).execute()

                # Roll a new pack
                await self.packs_channel.send(
                    f'!{set_to_generate} {message.author.mention} follows a map to uncharted territory')
                return
        await message.reply(f'Hmm, I can\'t find you in the league spreadsheet. '
                            f'Please post in {self.league_committee_channel.mention}')

    async def track_starting_pool(self, message: discord.Message):
        # Handle cases where Booster Tutor fails to generate a sealeddeck.tech link
        if '**Sealeddeck.tech:** Error' in message.content:
            # TODO: highlight the pool cell red and DM someone if this happens
            return

        # Use a regex to pull the sealeddeck id out of the message
        sealed_deck_id = \
            re.search("(?P<url>https?://[^\s]+)", message.content).group("url").split('sealeddeck.tech/')[1]
        sealed_deck_link = f'https://sealeddeck.tech/{sealed_deck_id}'

        spreadsheet_values = await self.get_spreadsheet_values('Pools!B7:F200')

        curr_row = 6
        for row in spreadsheet_values:
            curr_row += 1
            if len(row) < 1:
                continue
            if row[0].lower() != '' and row[0].lower() in message.mentions[0].display_name.lower():
                # Update the proper cell in the spreadsheet
                body = {
                    'values': [
                        # [f'=HYPERLINK("{sealed_deck_link}", "Link")', f'=HYPERLINK("{sealed_deck_link}", "Link")'],
                        [sealed_deck_link, sealed_deck_link],
                    ],
                }
                self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                           range=f'Pools!E{curr_row}:F{curr_row}', valueInputOption='USER_ENTERED',
                                           body=body).execute()
                self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                           range=f'Pools!S{curr_row}:S{curr_row}', valueInputOption='USER_ENTERED',
                                           body={'values': [[sealed_deck_link]]}).execute()
                # Add to Current Pack Only column
                self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                           range=f'Pools!AA{curr_row}:AA{curr_row}', valueInputOption='USER_ENTERED',
                                           body={'values': [[sealed_deck_link]]}).execute()

                return
        # TODO do something if the value could not be found
        return

    async def track_pack(self, message: discord.Message):

        # Get sealeddeck link and loss count from spreadsheet
        spreadsheet_values = await self.get_spreadsheet_values('Pools!B7:AB200')
        curr_row = 6
        current_pool = 'Not found'
        current_pack_only_pool = None
        extra_cards = []
        loss_count = 0
        for row in spreadsheet_values:
            curr_row += 1
            if len(row) < 5:
                continue
            if row[0].lower() != '' and row[0].lower() in message.mentions[len(message.mentions) - 1].display_name.lower():
                current_pool = row[3]
                current_pack_only_pool = row[ord('Z') - ord('B') + 1]
                extra_cards = [{"name": card, "count": 1} for card in row[(ord('T') - ord('B')):(ord('Z')-ord('B'))] if card != '']
                loss_count = int(row[2])
                break
        if current_pool == 'Not found':
            # This should only happen during debugging / spreadsheet setup
            print("rut row")
            return

        # For LOTR league, there's a special column for fellowship packs
        if "Fellowship" in message.content:
            loss_count = 11

        pack_content = message.content.split("```")[1].strip()
        pack_json = arena_to_json(pack_content)
        try:
            new_pack_id = await pool_to_sealeddeck(pack_json)
        except:
            print("sealeddeck issue — generating pack")
            # If something goes wrong with sealeddeck, highlight the pack cell red
            await self.set_cell_to_red(curr_row, chr(ord('F') + loss_count))
            return

        await self.write_pack(new_pack_id, loss_count, curr_row)

        if current_pool == '':
            await self.set_cell_to_red(curr_row, chr(ord('F') + loss_count))
            return

        if current_pack_only_pool == '':
            current_pack_only_pool = current_pool

        try:
            # Add pack to pool link
            updated_pool_id = await pool_to_sealeddeck(
                pack_json, current_pack_only_pool.split('.tech/')[1]
            )
        except:
            print("sealeddeck issue — updating pool")
            # If something goes wrong with sealeddeck, highlight the pack cell red
            await self.set_cell_to_red(curr_row, chr(ord('F') + loss_count))
            return

        # Move current pack-only pool to previous, to allow rebuilding on reroll
        previous_pool_body = {
            'values': [
                [current_pack_only_pool]
            ]
        }
        self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                   range=f'Pools!Z{curr_row}:Z{curr_row}, valueInputOption='USER_ENTERED',
                                   body=previous_pool_body).execute()

        # Write updated pack-only pool to spreadsheet
        pool_body = {
            'values': [
                [f'https://sealeddeck.tech/{updated_pool_id}'],
            ],
        }
        self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                   range=f'Pools!AA{curr_row}:AA{curr_row}', valueInputOption='USER_ENTERED',
                                   body=pool_body).execute()

        if len(extra_cards) > 0:
            try:
                # Add extra cards
                updated_pool_id = await pool_to_sealeddeck(
                    extra_cards, updated_pool_id
                )
            except:
                print("sealeddeck issue — updating pool")
                # If something goes wrong with sealeddeck, highlight the pack cell red
                await self.set_cell_to_red(curr_row, chr(ord('F') + loss_count))
                return

        # Write updated extra-card-included pool to spreadsheet
        pool_body = {
            'values': [
                [f'https://sealeddeck.tech/{updated_pool_id}'],
            ],
        }
        self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                   range=f'Pools!E{curr_row}:E{curr_row}', valueInputOption='USER_ENTERED',
                                   body=pool_body).execute()

        return

    async def write_pack(self, new_pack_id: str, loss_count: int, curr_row: int):
        pack_body = {
            'values': [
                [f'=HYPERLINK("https://sealeddeck.tech/{new_pack_id}", "Link")'],
            ],
        }
        # Find the proper column ID
        col = chr(ord('F') + loss_count)
        self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                   range=f'Pools!{col}{curr_row}:{col}{curr_row}', valueInputOption='USER_ENTERED',
                                   body=pack_body).execute()

    async def set_cell_to_red(self, row: int, col: str):
        # Note that this request (annoyingly) uses indices instead of the regular cell format.
        color_body = {
            'requests': [{
                'updateCells': {
                    'rows': [{
                        'values': [{
                            'userEnteredFormat': {
                                'backgroundColorStyle': {
                                    'rgbColor': {
                                        "red": 1,
                                        "green": 0,
                                        "blue": 0,
                                        "alpha": 1,
                                    }
                                }
                            }
                        }]
                    }],
                    'fields': 'userEnteredFormat',
                    'range': {
                        'sheetId': self.pools_tab_id,
                        'startRowIndex': row - 1,
                        'endRowIndex': row,
                        'startColumnIndex': ord(col) - ord('A'),
                        'endColumnIndex': ord(col) - ord('A') + 1,
                    },
                },
            }],
        }
        self.sheet.batchUpdate(spreadsheetId=self.spreadsheet_id,
                               body=color_body).execute()

    async def prompt_user_pick(self, message: discord.Message):
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

    async def handle_booster_tutor_response(self, message: discord.Message):
        assert self.num_boosters_awaiting > 0
        if self.num_boosters_awaiting == 2:
            self.num_boosters_awaiting -= 1
            await self.packs_channel.send(
                f'Pack Option A for {self.awaiting_boosters_for_user.mention}. To select this pack, DM me '
                f'`!choosePackA`\n '
                f'```{message.content.split("```")[1].strip()}```')
        else:
            self.num_boosters_awaiting -= 1
            await self.packs_channel.send(
                f'Pack Option B for {self.awaiting_boosters_for_user.mention}. To select this pack, DM me '
                f'`!choosePackB`\n '
                f'```{message.content.split("```")[1].strip()}```')
        if self.num_boosters_awaiting == 0:
            self.awaiting_boosters_for_user = None

    async def issue_challenge(self, message: discord.Message):
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

    async def choose_pack(self, user: Union[discord.Member, discord.User], chosen_option: str):
        if chosen_option == 'A':
            not_chosen_option = 'B'
            split = '!choosePackA`'
            not_chosen_split = '!choosePackB`'
        else:
            not_chosen_option = 'A'
            split = '!choosePackB`'
            not_chosen_split = '!choosePackA`'
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

    async def on_dm(self, message: discord.Message, command: str, argument: str):
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
                    "A mysterious creature is looking for a match. Post `!challenge` to reveal their identity and "
                    "initiate a match. "
                )
            else:
                self.active_lfm_message = await self.lfm_channel.send(
                    f"A mysterious creature is looking for a match. Post `!challenge` to reveal their identity and "
                    f"initiate a match.\n "
                    f"Message from the player:\n"
                    f"> {argument}"
                )
            await message.author.send(
                f"I've created a post for you. You'll receive a mention when an opponent is found.\n"
                f"If you want to cancel this, send me a message with the text `!nvm`."
            )
            self.pending_lfm_user_mention = message.author.mention
            return

        if command == '!retractlfm' or command == '!nvm':
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
            f"> `!nvm`: removes an anonymous LFM that you've sent out."
            f"> `!choosePackA`: responds to a pending pack selection option."
            f"> `!choosePackB`: responds to a pending pack selection option."
        )

    async def add_pack(self, message: discord.Message, argument: str):
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

    async def print_members_not_in_league(self, league_name: str):
        for member in self.guilds[0].members:
            found = False
            if member.bot:
                continue
            for role in member.roles:
                if league_name in role.name:
                    found = True
            if not found:
                print(member.display_name)

    async def message_members(self):
        for member in self.guilds[0].members:
            if member.display_name in 'put names here':
                print('trying to DM: ' + member.display_name)
                # if 'Sawyer T' in member.display_name:
                await message_member(member)
                print('DMed ' + member.display_name)

    async def message_members_not_in_league(self, league_name: str, content: str, sender: Union[discord.Member, discord.User], test_mode=False):
        count = 0
        if test_mode:
            await message_member(sender, content)
            count += 1
        else:
            for member in self.guilds[0].members:
                found = False
                if member.bot:
                    continue
                for role in member.roles:
                    if league_name in role.name:
                        found = True
                if not found:
                    print('trying to DM: ' + member.display_name)
                    await message_member(member, content)
                    print('DMed ' + member.display_name)
                    count += 1
        await sender.send(f'Successfully DMed {count} user(s).')

    async def get_spreadsheet_values(self, range: str):
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
            self.sheet = service.spreadsheets()
            result = self.sheet.values().get(spreadsheetId=self.spreadsheet_id,
                                             range=range).execute()
            return result.get('values', [])
        except HttpError as err:
            print(err)
        return []

def clues_available(row: Sequence[str]):
    # TODO MKM interpret from row (probably also need entire other sheet)
    return 0
