from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, NamedTuple, Optional

import aiohttp
import discord
import pytz
from red_commons.logging import getLogger
from redbot.core.utils.chat_formatting import humanize_number, pagify
from tabulate import tabulate

from .helpers import HEADERS, IMAGE_URL
from .xp import ELITE_XP, XP_TABLE

log = getLogger("red.trusty-cogs.runescape")

LVL_RE = re.compile(r"Levelled Up (\w+)", flags=re.I)
XP_RE = re.compile(r"(?P<xp>\d+)XP IN (.+)", flags=re.I)
KILLED_RE = re.compile(r"(?:I )?(?:killed|defeated) (?:\d+ |the )?([a-z \-,]+)", flags=re.I)
FOUND_RE = re.compile(r"I found (?:a pair of|some|a|an) (.+)", flags=re.I)


class APIError(Exception):
    pass


class PrivateProfileError(APIError):
    pass


class PlayerID(NamedTuple):
    id: int
    name: str


@dataclass
class HiScorePlayer:
    id: int
    bossId: int
    size: int
    rank: int
    enrage: int
    killTimeSeconds: float
    timeOfKill: int
    members: List[PlayerID]

    @classmethod
    def from_json(cls, data: dict) -> HiScorePlayer:
        members = [PlayerID(**i) for i in data.pop("members", [])]
        return cls(members=members, **data)


@dataclass
class ZamorakHiScores:
    content: List[HiScorePlayer]
    totalElements: int
    totalPages: int
    first: bool
    last: bool
    numberOfElements: int
    number: int
    size: int
    empty: bool
    url: str = "https://secure.runescape.com/m=group_hiscores/v1//groups?groupSize={groupSize}&size={size}&bossId=1&page={page}"

    @classmethod
    def from_json(cls, data: dict) -> ZamorakHiScores:
        content = [HiScorePlayer.from_json(i) for i in data.pop("content", [])]
        return cls(content=content, **data)

    @classmethod
    async def get(
        cls,
        groupSize: int,
        size: int,
        page: int,
        *,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> ZamorakHiScores:
        if session is None:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(
                    cls.url.format(groupSize=groupSize, size=size, page=page)
                ) as resp:
                    if resp.status != 200:
                        raise APIError
                    data = await resp.json()
        else:
            async with session.get(
                cls.url.format(groupSize=groupSize, size=size, page=page)
            ) as resp:
                if resp.status != 200:
                    raise APIError
                data = await resp.json()
        return cls.from_json(data)


class Skills(Enum):
    Attack = 0
    Defence = 1
    Strength = 2
    Constitution = 3
    Ranged = 4
    Prayer = 5
    Magic = 6
    Cooking = 7
    Woodcutting = 8
    Fletching = 9
    Fishing = 10
    Firemaking = 11
    Crafting = 12
    Smithing = 13
    Mining = 14
    Herblore = 15
    Agility = 16
    Thieving = 17
    Slayer = 18
    Farming = 19
    Runecrafting = 20
    Hunter = 21
    Construction = 22
    Summoning = 23
    Dungeoneering = 24
    Divination = 25
    Invention = 26
    Archaeology = 27
    Necromancy = 28

    @property
    def is_elite(self):
        return self is Skills.Invention

    @property
    def is_120(self):
        return self in (
            Skills.Herblore,
            Skills.Slayer,
            Skills.Farming,
            Skills.Dungeoneering,
            Skills.Invention,
            Skills.Archaeology,
            Skills.Necromancy,
        )


class Item:
    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id")
        self.icon = kwargs.get("icon")
        self.icon_large = kwargs.get("icon_large")
        self.type = kwargs.get("type")
        self.type_icon = kwargs.get("type_icon")
        self.name = kwargs.get("name")
        self.description = kwargs.get("description")
        self.members = kwargs.get("members")
        self.trend = kwargs.get("trend")
        self.price = kwargs.get("price")
        self.change = kwargs.get("change")
        self.current = kwargs.get("current")
        self.today = kwargs.get("today")
        self.day30 = kwargs.get("day30")
        self.day90 = kwargs.get("day90")
        self.day180 = kwargs.get("day180")


@dataclass
class Activity:
    date: datetime
    details: str
    text: str
    id: str

    @classmethod
    def from_json(cls, data: dict):
        tz = pytz.timezone("Europe/London")
        date_info = data.get("date")
        if date_info is not None:
            date = datetime.strptime(date_info, "%d-%b-%Y %H:%M")
        else:
            date = datetime.now()
        date = tz.localize(date)
        text = data.get("text")
        activity_id = f"{int(date.timestamp())}-{text}"
        return cls(
            date=date,
            details=data.get("details"),
            text=text,
            id=activity_id,
        )

    def _get_image_details_text(self):
        text = self.text
        details = self.details
        image_url = None
        page = None
        if match := KILLED_RE.search(self.text):
            page = match.group(1).strip()
            if page.endswith("s"):
                page = page[:-1]
            page = page.replace(" ", "_")
            if "-" in page:
                page = page.title()
            else:
                page = page.capitalize()
            image_url = IMAGE_URL + page + ".png"
        if match := XP_RE.search(self.text):
            page = match.group(2).strip()
            if xp := match.group("xp"):
                number = humanize_number(int(xp))
                text = self.text.replace(xp, number)
                details = self.details.replace(xp, number)
            image_url = IMAGE_URL + page.replace(" ", "_").capitalize() + ".png"
        if match := LVL_RE.search(self.text):
            page = match.group(1).strip()
            image_url = IMAGE_URL + page.replace(" ", "_").capitalize() + ".png"
        if match := FOUND_RE.search(self.text):
            page = match.group(1).strip() + " detail"
            image_url = IMAGE_URL + page.replace(" ", "_").capitalize() + ".png"
        return text, details, image_url

    def format_text(self, profile: Profile) -> str:
        text, details, image_url = self._get_image_details_text()
        ts = discord.utils.format_dt(self.date)
        return f"{profile.name}: {text}\n{details}\n\n{ts}"

    def embed(self, profile: Profile) -> discord.Embed:
        # url = f"https://apps.runescape.com/runemetrics/app/overview/player/{profile.name}"
        # msg = f"{profile.name}: {activity.text}\n{activity.details}\n\n"
        ts = discord.utils.format_dt(self.date)
        text, details, image_url = self._get_image_details_text()
        embed = discord.Embed(title=text, description=f"{details}\n\n{ts}")
        embed.set_author(name=profile.name, url=profile.metrics, icon_url=profile.avatar)
        if image_url is not None:
            embed.set_thumbnail(url=image_url)
        return embed


class Activities:
    def __init__(self, *args, **kwargs):
        self.channels = kwargs.get("channels")
        self.last_id: str = kwargs.get("last_id")
        self.last_timestamp = kwargs.get("last_timestamp")
        self.username: str = kwargs.get("username")
        self.posted_activities: deque = deque(kwargs.get("posted_activities", []), 40)

    def to_json(self) -> dict:
        return {
            "channels": self.channels,
            "last_id": self.last_id,
            "last_timestamp": self.last_timestamp,
            "username": self.username,
            "posted_activities": list(self.posted_activities),
        }


@dataclass
class Skill:
    level: int
    xp: int
    rank: int
    id: int
    name: str
    # sort of a pseudo cached property
    _virtual_level: Optional[int] = None

    def virtual_level(self) -> Optional[int]:
        if self._virtual_level is not None:
            return self._virtual_level
        table = XP_TABLE
        max_level = 120
        if self.skill.is_elite:
            table = ELITE_XP
            max_level = 150
        # since this is for virtual levels we can reduce iterations
        # by just slicing the list to only values after the start of virtual
        # level calculations. This should reduce our iterations from
        # 120(150 for elite) per skill to only 22 (52 for elite) per skill
        split = 99
        for level, xp in enumerate(table[split - 1 :], start=split):
            if self.xp >= xp:
                self._virtual_level = min(level, max_level)
        return self._virtual_level

    @property
    def skill(self):
        return Skills(self.id)

    @classmethod
    def from_json(cls, data: dict):
        return cls(
            level=data.get("level", 1),
            xp=int(data.get("xp", 1) * 0.1),
            rank=data.get("rank", 0),
            id=data.get("id"),
            name=Skills(int(data.get("id"))).name,
        )


@dataclass
class PlayerDetails:
    isSuffix: bool
    name: str
    title: str
    recruiting: Optional[bool] = None
    clan: Optional[str] = None
    online: Optional[bool] = False
    world: Optional[str] = None

    @classmethod
    def from_str(cls, data_str: str) -> PlayerDetails:
        try:
            data = json.loads(
                data_str.replace("jQuery000000000000000_0000000000([", "").replace("]);", "")
            )
        except Exception:
            log.exception("Error pulling player details")
            raise APIError
        return cls(**data)

    @classmethod
    async def get(
        cls, runescape_name: str, *, session: Optional[aiohttp.ClientSession] = None
    ) -> PlayerDetails:
        url = f"https://secure.runescape.com/m=website-data/playerDetails.ws?names=%5B%22{runescape_name}%22%5D&callback=jQuery000000000000000_0000000000&_=0.format"
        close_session = session is None
        if session is None:
            session = aiohttp.ClientSession(headers=HEADERS)
        async with session.get(url) as resp:
            if resp.status != 200:
                raise APIError
            data = await resp.text()
        if close_session:
            await session.close()
        return cls.from_str(data)


@dataclass
class Profile:
    name: str
    combatlevel: int
    logged_in: bool
    rank: int
    melee_total: int
    magic_total: int
    ranged_total: int
    totalskill: int
    totalxp: int
    questsstarted: int
    questscomplete: int
    questsnotstarted: int
    activities: List[Activity]
    attack: Skill
    defence: Skill
    strength: Skill
    constitution: Skill
    ranged: Skill
    prayer: Skill
    magic: Skill
    cooking: Skill
    woodcutting: Skill
    fletching: Skill
    fishing: Skill
    firemaking: Skill
    crafting: Skill
    smithing: Skill
    mining: Skill
    herblore: Skill
    agility: Skill
    thieving: Skill
    slayer: Skill
    farming: Skill
    runecrafting: Skill
    hunter: Skill
    construction: Skill
    summoning: Skill
    dungeoneering: Skill
    divination: Skill
    invention: Skill
    archaeology: Skill
    necromancy: Skill

    def __str__(self):
        skills_list = [["Overall", self.totalskill, "{:,}".format(self.totalxp), self.rank]]
        for skill_name in Skills:
            skill = getattr(self, skill_name.name.lower(), None)
            if not skill:
                level = 1
                xp = 0
                rank = "Unranked"
                skills_list.append([skill_name.name, level, xp, rank])
                continue
            level = skill.level
            virtual = skill.virtual_level()
            if virtual is not None and virtual != level:
                level = f"{skill.level} ({virtual})"
            xp = skill.xp
            rank = "Unranked"
            if skill.rank:
                rank = humanize_number(skill.rank)
            skills_list.append([skill.name, level, humanize_number(xp), rank])
        return tabulate(skills_list, headers=["Skill", "Level", "Experience", "Rank"])

    @property
    def avatar(self):
        return "http://secure.runescape.com/m=avatar-rs/{}/chat.png".format(
            self.name.replace(" ", "%20")
        )

    @property
    def metrics(self):
        return f"https://apps.runescape.com/runemetrics/app/overview/player/{self.name}"

    def stats_table(self) -> str:
        table = str(self)
        top_row_len = len(table.split("\n")[1])
        top_row = top_row_len * "-"
        title = f"RS3 Stats for {self.name}".center(top_row_len - 2)
        skills = ("```css\n{top_row}\n|{title}|\n{top_row}\n{skills}\n{top_row}```").format(
            top_row=top_row, title=title, skills=table
        )
        return skills

    async def embed(self, details: Optional[PlayerDetails] = None) -> discord.Embed:
        em = discord.Embed()
        if details and details.isSuffix:
            em.set_author(name="{} {}".format(self.name, details.title))
        elif details:
            em.set_author(name="{} {}".format(details.title, self.name))
        else:
            em.set_author(name=self.name)
        activities = ""
        for activity in self.activities:
            activities += f"[<t:{int(activity.date.timestamp())}>] {activity.details}\n"

        # em.colour = int(teams[_teams.name]["home"].replace("#", ""), 16)
        em.set_thumbnail(url=self.avatar)
        em.add_field(name="Combat Level", value=self.combatlevel)
        em.add_field(name="Total Level", value=humanize_number(self.totalskill))
        em.add_field(name="Total XP", value=humanize_number(self.totalxp))
        quests = tabulate(
            [[self.questsstarted, self.questsnotstarted, self.questscomplete]],
            headers=["Started", "Not Started", "Complete"],
        )
        combat_xp = tabulate(
            [
                [
                    humanize_number(self.magic_total),
                    humanize_number(self.melee_total),
                    humanize_number(self.ranged_total),
                ]
            ],
            headers=["Magic", "Melee", "Ranged"],
        )
        em.add_field(
            name="Combat XP Totals",
            value=f"```\n{combat_xp}\n```",
            inline=False,
        )
        em.add_field(
            name="Quests",
            value=f"```\n{quests}\n```",
            inline=False,
        )
        if activities:
            activities = list(pagify(activities, page_length=1024))[0]
            em.add_field(name="Activities", value=activities)
        if self.logged_in:
            em.set_footer(
                text="\N{LARGE GREEN CIRCLE} Online",
            )
        else:
            em.set_footer(
                text="\N{LARGE RED CIRCLE} Offline",
            )
        return em

    @classmethod
    def from_json(cls, data: dict):
        logged_in = True if data["loggedIn"] == "true" else False
        skills = {skill.value: 0 for skill in Skills}
        if "skillvalues" in data:
            for skill in data["skillvalues"]:
                skills[skill["id"]] = Skill.from_json(skill)

        return cls(
            name=data["name"],
            combatlevel=data["combatlevel"],
            logged_in=logged_in,
            rank=data["rank"],
            melee_total=data["melee"],
            magic_total=data["magic"],
            ranged_total=data["ranged"],
            totalskill=data["totalskill"],
            totalxp=data["totalxp"],
            questsstarted=data["questsstarted"],
            questscomplete=data["questscomplete"],
            questsnotstarted=data["questsnotstarted"],
            activities=[Activity.from_json(i) for i in data["activities"]],
            attack=skills[0],
            defence=skills[1],
            strength=skills[2],
            constitution=skills[3],
            ranged=skills[4],
            prayer=skills[5],
            magic=skills[6],
            cooking=skills[7],
            woodcutting=skills[8],
            fletching=skills[9],
            fishing=skills[10],
            firemaking=skills[11],
            crafting=skills[12],
            smithing=skills[13],
            mining=skills[14],
            herblore=skills[15],
            agility=skills[16],
            thieving=skills[17],
            slayer=skills[18],
            farming=skills[19],
            runecrafting=skills[20],
            hunter=skills[21],
            construction=skills[22],
            summoning=skills[23],
            dungeoneering=skills[24],
            divination=skills[25],
            invention=skills[26],
            archaeology=skills[27],
            necromancy=skills[28],
        )

    @classmethod
    async def get(
        cls,
        runescape_name: str,
        number_of_activities: int = 5,
        *,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Profile:
        url = "https://apps.runescape.com/runemetrics/profile/profile"
        close_session = session is None
        params = {"user": runescape_name, "activities": number_of_activities}
        if session is None:
            session = aiohttp.ClientSession(headers=HEADERS)
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise APIError
            data = await resp.json()
        if close_session:
            await session.close()
        if "error" in data:
            if data["error"] == "PROFILE_PRIVATE":
                raise PrivateProfileError(data["error"])
            else:
                raise APIError(data["error"])
        return cls.from_json(data)


class OSRSSkill(NamedTuple):
    id: int
    name: str
    rank: int
    level: int
    xp: int

    def __str__(self):
        return self.name


class OSRSActivity(NamedTuple):
    id: int
    name: str
    rank: int
    score: int

    def __str__(self):
        return self.name


@dataclass
class OSRSProfile:
    def __init__(
        self,
        name: str,
        skills: Dict[int, OSRSSkill],
        activities: Dict[int, OSRSActivity],
    ):
        self.skills: Dict[int, OSRSSkill] = skills
        self.activities: Dict[int, OSRSActivity] = activities
        self.name: str = name

    def get_skill(self, skill_name: str) -> Optional[OSRSSkill]:
        for skill in self.skills.values():
            if skill.name == skill_name:
                return skill
        return None

    def get_activity(self, activity_name: str) -> Optional[OSRSActivity]:
        for activity in self.activities.values():
            if activity.name == activity_name:
                return activity
        return None

    @classmethod
    async def get(
        cls, runescape_name: str, *, session: Optional[aiohttp.ClientSession] = None
    ) -> OSRSProfile:
        url = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.json"
        close_session = session is None
        params = {"player": runescape_name}
        if session is None:
            session = aiohttp.ClientSession(headers=HEADERS)
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise APIError
            data = await resp.json()
        if close_session:
            await session.close()
        skills = {}
        activities = {}
        for skill in data.get("skills", []):
            skills[skill["id"]] = OSRSSkill(**skill)
        for activity in data.get("activities", []):
            activities[activity["id"]] = OSRSActivity(**activity)
        return cls(runescape_name, skills, activities)

    async def get_stats_table(self) -> str:
        skills_list = []
        for skill in self.skills.values():
            skills_list.append(
                [
                    skill.name,
                    skill.level,
                    humanize_number(skill.xp),
                    humanize_number(skill.rank),
                ]
            )
        table = tabulate(skills_list, headers=["Skill", "Level", "Experience", "Rank"])
        top_row_len = len(table.split("\n")[1])
        top_row = top_row_len * "-"
        title = f"OSRS Stats For {self.name}".center(top_row_len - 2)
        skills = ("{top_row}\n|{title}|\n{top_row}\n{skills}\n{top_row}").format(
            top_row=top_row, title=title, skills=table
        )
        return skills

    async def get_profile_table(self) -> str:
        activities_list = []
        for activity in self.activities.values():
            activities_list.append([activity.name, humanize_number(activity.score)])
        table = tabulate(activities_list, headers=["Activity", "Completed"])
        top_row_len = len(table.split("\n")[1])
        top_row = top_row_len * "-"
        title = f"OSRS Activities For {self.name}".center(top_row_len - 2)
        skills = ("{top_row}\n|{title}|\n{top_row}\n{skills}\n{top_row}").format(
            top_row=top_row, title=title, skills=table
        )
        return skills
