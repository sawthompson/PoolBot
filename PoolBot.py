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

COMPLEATION_FLAVOR_MESSAGES = [
    'With the White Sun cresting above you a sense of hope and peace and oil washes over you. The ichor flows inside, washing away the doubt. If only all could know the embrace of the Mother.',
    '“The Work must continue. All shall be One.“',
    '“A Wise Choice” -Jin Gitaxias',
    '“Welcome to the Family” -Urabrask',
    '“Now you shall be strong enough to survive” -Vorinclex',
    '“Fall to your knees and welcome our embrace.” - Qal-Sha, Priest of Norn',
    '"Behold blessed perfection" - Sheoldred, Whispering One',
    '“May you rejoice in the magnificence of Norn. May your flesh serve perfection.”',
    '"From void evolved Phyrexia. Great Yawgmoth, Father of Machines, saw its perfection. Thus The Grand Evolution began."',
    '“Beg me for life, and I will fill you with the glory of Phyrexian perfection”',
]


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
        await member.send(
            "Greetings, current or former Arena Gauntlet League player! We're happy to announce that we're back for our Phyrexia: All Will Be One edition of the league.\n\nThis new league brings with it a new mechanic, in which players can succumb to temptation and become 'compleat' by trading in their pool for an equivalent number of random ONE packs. Will you succumb to the glistening oil, or attempt to preserve your humanity?\n\nSign up here: https://forms.gle/3mTgRyjtN5bZUuG8A. Registration closes Wednesday February 15th.\n\nWe hope to see you there!")
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
        self.pending_lfm_user_mention = None
        self.active_lfm_message = None
        self.num_boosters_awaiting = 0
        self.awaiting_boosters_for_user = None
        self.spreadsheet_id = self.config.spreadsheet_id
        for user in self.users:
            if user.name == 'Booster Tutor':
                self.booster_tutor = user

    async def on_message_edit(self, before, after):
        # Booster tutor adds sealeddeck.tech links as part of an edit operation
        if before.author == self.booster_tutor:
            if before.channel == self.pool_channel and "Sealeddeck.tech link" not in before.content and\
                    "Sealeddeck.tech link" in after.content:
                # Edit adds a sealeddeck link
                await self.track_starting_pool(after)
                return

    async def on_message(self, message):
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

        if command == '!investigate' and message.channel == self.packs_channel:
            await self.investigate(message)
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
        elif command == '!help':
            await message.channel.send(
                f"You can give me one of the following commands:\n"
                f"> `!challenge`: Challenges the current player in the LFM queue\n"
                f"> `!randint A B`: Generates a random integer n, where A <= n <= B. If only one input is given, "
                f"uses that value as B and defaults A to 1. \n "
                f"> `!help`: shows this message\n"
            )

    async def investigate(self, message):
        # Get sealeddeck link and loss count from spreadsheet
        spreadsheet_values = await self.get_spreadsheet_values('Pools!B7:Q200')
        curr_row = 6
        for row in spreadsheet_values:
            curr_row += 1
            if len(row) < 5:
                continue
            if row[0].lower() != '' and row[0].lower() in message.author.display_name.lower():
                if row[15] == 'No':
                    await message.reply(f'By my records, you cannot currently investigate. If this is in error, '
                                  f'please post in {self.league_committee_channel.mention}')
                    return

                # Set "investigating" to true and "can investigate" to false
                self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                           range=f'Pools!Q{curr_row}:R{curr_row}', valueInputOption='USER_ENTERED',
                                           body={'values': [['No', 'Yes']]}).execute()

                # Roll a new pack
                await self.packs_channel.send(f'!cube SIRLeague {message.author.mention} searches for answers')
                return
        await message.reply(f'Hmm, I can\'t find you in the league spreadsheet. '
                      f'Please post in {self.league_committee_channel.mention}')

    async def track_starting_pool(self, message):
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

                return
        # TODO do something if the value could not be found
        return

    async def track_pack(self, message):

        # Get sealeddeck link and loss count from spreadsheet
        spreadsheet_values = await self.get_spreadsheet_values('Pools!B7:S200')
        curr_row = 6
        current_pool = 'Not found'
        loss_count = 0
        investigating = False
        prev_pool = 'Not found'
        for row in spreadsheet_values:
            curr_row += 1
            if len(row) < 5:
                continue
            if row[0].lower() != '' and row[0].lower() in message.mentions[len(message.mentions) - 1].display_name.lower():
                current_pool = row[3]
                loss_count = int(row[2])
                investigating = row[16] == 'Yes'
                prev_pool = row[17]
                break
        if current_pool == 'Not found':
            # This should only happen during debugging / spreadsheet setup
            print("rut row")
            return

        # SIR-SPECIFIC
        if investigating:
            # Set "investigating" and "can investigate" to 'No'
            self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                       range=f'Pools!Q{curr_row}:R{curr_row}', valueInputOption='USER_ENTERED',
                                       body={'values': [['No', 'No']]}).execute()

            current_pool = prev_pool
        else:
            # If this is a non-rerolled pack, store the pool without it to more easily support rerolling
            self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                       range=f'Pools!S{curr_row}:S{curr_row}', valueInputOption='USER_ENTERED',
                                       body={'values': [[current_pool]]}).execute()

            # This is a new pack, so it can be investigated
            self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                       range=f'Pools!Q{curr_row}:Q{curr_row}', valueInputOption='USER_ENTERED',
                                       body={'values': [['Yes']]}).execute()
        # END SIR-SPECIFIC (also clean up investigate references)

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

        try:
            # Add pack to pool link
            updated_pool_id = await pool_to_sealeddeck(
                pack_json, current_pool.split('.tech/')[1]
            )
        except:
            print("sealeddeck issue — updating pool")
            # If something goes wrong with sealeddeck, highlight the pack cell red
            await self.set_cell_to_red(curr_row, chr(ord('F') + loss_count))
            return

        # Write updated pool to spreadsheet
        pool_body = {
            'values': [
                [f'https://sealeddeck.tech/{updated_pool_id}'],
            ],
        }
        self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                   range=f'Pools!E{curr_row}:E{curr_row}', valueInputOption='USER_ENTERED',
                                   body=pool_body).execute()

        return

    async def write_pack(self, new_pack_id, loss_count, curr_row):
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

    async def set_cell_to_red(self, row, col):
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

    async def print_members_not_in_league(self, league_name):
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
            if member.display_name in 'Andrew H // hopnertz#98617 Oskar T // ossytubbi#87966 Lukas M // Lonti#43464 Devin L // Dalopi#45146 Gabe_C // iamgabe#35305 Timothy S // Microbe#19739 Jeff U / / LargeMidget#22940 Brent.S//ZeroXD520#67942 Nate D // cptdeas#47820 Raffles C // Pachacuti#55372 Oscar F. // zahori#94244 Nick T // Peppenzo#98079 Gabriel L // gabeisgunk#67806 Vincent T // tvincent#37802 Dan K // Fizishy#82483 Mobin M // Mobin#15527 Nicolas D./Bloodyskillian#49800 Christopher S // Stemz#96067 Jake R // TankAlpha#18823 Tobias S // Schnto1390#63870 Christopher G//oompaloompa#65002 slade // Slademarini#60738 Michael M // Atombomb11991#07305 James T // RevisedIsland#19780 Steven Yean // Isos#92135 Trevor H // zheader#50741 Spencer F / dogtrout#70447 Joel L // Instinctive#21903 Cameron K // Camzillanator#66654 Greg M//grapereaper#84747 August W//HoxhaBallTactics63709 Trek B // tebwolf359#48559 MarleyL BeautyAndTheGitrog#98732 Anthony B // MonteBeurre#56664 Martin B // Daarken#71434 Sebastien C//Skylord#91002 Patrick G // ItsTehPats#93279 Nick Mc//finedontcare#64581 Chris S // LordBooBoo#20683 Doug c//Dougisacat#58521 CarlC/TechnicolourArborist#38665 Jesse R // Zephaer#38941 James_M//Minister_Of_Offence#976 Kyle C // AFKyle#29319 David S. // SirLossore#88647 Besmir H // GrumpyPants182#48111 Javier I // jfinumerable#09865 Frances L // kawaiiru#25405 Adrian P // apetresc#26642 Daniel J // Insequent#82225 Ryan P // f4wx#21512 Trevor T. // Sirtydanchez#87149 Keith S // Wittet#06465 Kyle B // Carbet#13857 Tony G // YnotRock#76764 Derek G // Czark#07914 Brendan Y // Earthmover#56028 Ken M//Fungusaur#15825 Matt // MopBeeble #38894 Olivier F // NephilimOli#21523 Jaidin M // MnemonicDevice#38774 Jake C // the_man_mulcahey#74753 David R // pipedream#19575 Mos I // SmoiL#85859 Michael C // Kentari#73068 deven_w //deven#51146 Jason M//experientialist#23057 Ade A // Blizz#50660 Jonas S // Napman#45504 Jared S // kingtriceratops#64516 Eva B // uNbaluNbabLe#82465 Corey S // C-Dog#37775 Karen W // Karen#26097 Georges S // Anandunaiss#77294 Luca P // LukeCloud#68182 Nick M // Bernardhibou#01354 Alex G//Villag3Idiat#59360 EvanC // PeasOfCrab#00133 0zkar T// microphone#67345 Hugo D // HDila#61011 Michael_L // JimmyPlays#62350 Damien F // DamienF16#36049 Brice C // AlBerTice#65077 Theo M // theopolist#91798 toxicfrog Walter H // DenzelKittyBoi#22057 Bal K // bskt#97187 Pengwin12 gordon c // Chocobro#30910 Louis-Frederic M //Louif#07092 Daniel G // FriskyHobbit#43185 Kowen S. //Zumas#77769 benson p // sick beak#62405 Sylvain T // Iquitos#15484 Shuo L // Tamamo#30654 Trenner W // Ittakesawile#02569 Ben DP // bdp18#67423 Rowan C // rcb#09642 Ryan D // TheSlickness#10857 Jules P // tridle201#23047 Randy F // Ezrem#08922 Nick L // ironick#50169 David N // NovaAsterix#12880 Mahmoud K // PyreDream#02622 Raphael T//bograts#18620 Mads B // mbb#24081 Jake S // jakespencer#33425 Robby P // Locke_009#22200 Andy P // TheWonderfulAndy#61401 Martin G//Murkeltoni#15463 Rick N // DeadlyErioc#94140 Audi J// They // Squeakz#38671 Matt T // Malikari#85656 Alex M // bikedog#84690 Thomas H // Fromcero#98384 Zeke W // smarmyplatapus#30066 Fabian R // fab47#18975 Rob R // RobRage#98978 Côme S // C_SM#79564 Mike B./Xenomorph#62228 John K / xx exar xx #07465 Willem_V//Fat-1#52752 Michael GM // Draculated#87727 Kelsey D // Samsquatch28#55922 Derek S // muffinman#81770 Ben A //JankInTheTank#50714 WarM:LinkThePlaneswalker33#61905 Zack H // HackZolt#31077 Austin C // theCortine#41513 Bryan_J//Brin87#32451 Touko V // Sienionelain#15574 Calvin Z // extrAme#05753 John B // vexingcarton#71473 Jose H // Hidalgo#34529 Martin F // iamausername#24158 Josh R//Batreyu#91093 Noel S // Stopgap#13873 David_R / Roooster#49289 Ryland B // Sabre#12079 Ericsson L // Dimmi#45280 Jakub K //Terrence McKenna#95766 Graham S // gram#97938 JP G // jaypiex#97612 Gregoire T // Emulink#08857 Mats K // MakroGames#12872 Dante R // dantesper#59172 Eric M // Mudge#85188 Jake M // Avobenzone#87339 Jonas K // Zakisan#66913 Andrew S//boats_and_sales#40966 Wanqi Z // iqiq#38801 Majo // fortem#78609 Lueder L // Abraxas#73462 Tyler K // SoulbondMagic#64278 Chris F // cferejohn#08269 Grey H // grensley#29260 Chris G//MachineKingV#13646 Nicholas L // PlayOnBirds#63476 Dante S // MTGGTL#05871 Cat D //catherine_d#11199 Spencer B // ThePensive#34256 Marshall W // Scribbles#92472 Tim H // hanshotf1rst#51070 Geoff E // Snorlaxguy#38043 Joseph W // ny5661#87523 Eric S // ejseltzer#94931 Bryan B // Bryan Buck#47229 Sam R // BUTTAmuffien#93800 Erika P // CharmToy#24980 Amir H // ShAZam#02792 Alex L // Alex Lim#00676 Helge B //turd_burglar#45671 Sawyer T (he/him)//soyboy#35469 Diego C // Wors3#38771 Peter C // Lens Blair#41153 Milan_G // NoTurnUnstoned#56094 Rafael F // Prudent#24319 Kevin O//Symtix#62780 Richard K. // preacher26#59445 Glenn D // he/him // coiol#34945 Eric C (he/him) // ecope#39827 Jack P//atticwolf#85756 John D //Pubby#44102 Aaron W // awind#52719 Noel P // ShelvedAvocado#25198 Sheldon D // downie#26773 Pim T // pact83#00681 Russell S // rstad#89052 Francesco V/He/Fra_LeChuck#63132 Clayton H //GallopingPanda#97361 Harry B // Midnight_0il#97781 Sean Sp // Talrand#06333 Joe U // Stepfield#97627 Eli P //thebigelixir#53686 Alex S // qwertfluff#33508 Mike K // mtklein#91858 Bill D // Cato Phoenix#70805 Gerardo L//SeductiveTurtle#91515 Jordan P // chunkieluver53#92811 Joel R //Leo T. Osborne Jr#98202 Tim W // Splinter#62088 Joseph G // JoeGumby#10687 Edgar A // PlayfulTerror#04926 Stephen W // Biceps_Inc#28236 Hank A. // hankypanky#85872 Ben S // Judal#13657 Tia T // Suicu#49869 Jigby // Jigby#61345 Breckin B // thekonfuzed#68018 Anmol D // Manmol#83915 Justin S // Justin#39129 Marco M // Vilverin#70937 Pierre-AntoineB//PymBragon#97733 Rowan H-K // Rowannn#77643 Craig V//CiViK#97068 Lorenz K // LHeHo#26216 Josune P // Trunks_Alvein#01450 Josh F // Frazati#21066 James MI // ImJolt#28410 Evan L // spectre#14747 Brian H // Brinbrin#59708 Shawn R // chuckygr#53420 Robert W // Ciago#90430 Joseph H // JoCool#84350 Martin E. // Meep#87492 Shamya_D//Hare__Krishna#31039 Dave D // SizzleDizzle#55788 Yaron B//Personification#18218 Joe T // elasticity#07466 Jean-Rene B // Nemesian#06455 Mike P // UnwieldyBiscuit#26471 Ben W // Shanksadoola#65676 Khris M // khrismauricio#39476 Paul S // YungChunk#45977 Humberto S // Miguilim#03209 Nick P//CrustyCheeseburger#09888 Sterling R /// silversire#93731 Nicholas P // Ami3vil?#84889 Javier B//Baimason#88379 Ryan W // MuttBunchies#61653 EvanS//TheyThem//Owenman21#35548 Charles B // child roland#86953 Zack Z// z_squared#65528 Kamil C // Camel#45385 Daniel M/HPWizard#40332 Juan Q // JuanCu#95616 Brian R // admiral_ace#37121 Ash T // TreeGoblin#16976 John L // fightstrife#66069 Adrian C/ CarrRadio#73846 Jeff H // Cletus Van Dam#26978 Miles B // bassnps#38867 Aus Z/ImperialPlaneswalker#40420 Kosta F // Alterus#06205 Craig H // Itsmecraig#89337 Christian W // Senpaiman#86951 James B / /GoJimbo#66932 Victor M / VictorMafort#53835 Zach C // ZachCarr#31881 Nguyen L /TheFancyPenguin#82137 Duran V // SuperD#94636 Adam S // wizma#29010 Ben P // HuevosLocos#10246 Krzys H - FaceBeardMcBeard#24731 Michaelangelo B // Soulnog#35870 Matthew S // Eel007#19082 Mathew S // VGCake#60410 Bryce B // Tr33man#95218 Grey J // GeeWhizz#25104 Fletcher Y // Raskall#91453 Noah N // newtownkid 33852 Amir A // HowDoesOneChoose#47480 Jonathan R//Jraps#79124 Jeb B // Gray_Bob19#67329 Jacob C//Breadlord#83962 gh0stboy vheissu JUJUBOF Garuuk':
                print('trying to DM: ' + member.display_name)
                # if 'Sawyer T' in member.display_name:
                await message_member(member)
                print('DMed ' + member.display_name)

    async def compleat_player(self, message):
        spreadsheet_values = await self.get_spreadsheet_values('Standings!C6:H120')
        if not spreadsheet_values:
            return await message.reply(f'Sorry, but I cannot access the spreadsheet. '
                                       f'Please post in {self.league_committee_channel.mention}')

        loss_count = 'not found'
        compleat = False
        curr_row = 6
        for row in spreadsheet_values:
            if len(row) < 6:
                continue
            if row[0].lower() != '' and row[0].lower() in message.author.display_name.lower():
                loss_count = int(row[5])
                if row[2].lower() == 'compleated':
                    compleat = True
                break
            curr_row += 1

        if loss_count == 'not found':
            return await message.reply(f'Unable to find your account in the league spreadsheet.'
                                       f'Please post in {self.league_committee_channel.mention}')
        if loss_count < 3:
            return await message.reply(
                'The machine orthodoxy has evaluated you and found you wanting, but fear not. The glory of compleation will be yours in time.')
        if compleat == True:
            return await message.reply(
                "Phyrexia approves of your enthusiasm, but you have already been reshaped by Norn's will. How could you ever hope to improve upon her perfection?")

        await message.reply(
            f'!one {loss_count + 6} {message.author.mention}\n\n{random.choice(COMPLEATION_FLAVOR_MESSAGES)}')

        # Update the proper cell in the spreadsheet        
        body = {
            'values': [
                ['Compleated'],
            ],
        }
        self.sheet.values().update(spreadsheetId=self.spreadsheet_id,
                                   range=f'Standings!E{curr_row}:E{curr_row}', valueInputOption='USER_ENTERED',
                                   body=body).execute()

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
            self.sheet = service.spreadsheets()
            result = self.sheet.values().get(spreadsheetId=self.spreadsheet_id,
                                             range=range).execute()
            return result.get('values', [])
        except HttpError as err:
            print(err)
        return []
