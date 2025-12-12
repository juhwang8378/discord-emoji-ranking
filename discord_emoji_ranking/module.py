import datetime
import logging
import os
from enum import Enum
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands
from discord_ext_commands_coghelper import CogHelper
from discord_ext_commands_coghelper.utils import (
    Constant,
    to_utc_naive,
    get_list,
    get_bool,
    get_before_after_fmts,
    get_corrected_before_after_str,
)

logger = logging.getLogger(__name__)


class _SortOrder(Enum):
    ASCENDING = 1
    DESCENDING = 2

    @staticmethod
    def parse(value: str):
        return _SortOrder.ASCENDING if value == "ascending" else _SortOrder.DESCENDING

    @staticmethod
    def reverse(value) -> bool:
        return True if value == _SortOrder.DESCENDING else False


class _EmojiCountType(Enum):
    MESSAGE_CONTENT = 1
    MESSAGE_REACTION = 2


class _EmojiCounter:
    def __init__(self, emoji: discord.Emoji):
        self._emoji = emoji
        self._rank = 0
        self._counts = {t: 0 for t in _EmojiCountType}

    def increment(self, count_type: _EmojiCountType):
        self._counts[count_type] += 1

    @property
    def emoji(self) -> discord.Emoji:
        return self._emoji

    @property
    def rank(self) -> int:
        return self._rank

    @rank.setter
    def rank(self, value):
        self._rank = value

    @property
    def content_count(self) -> int:
        return self._counts[_EmojiCountType.MESSAGE_CONTENT]

    @property
    def reaction_count(self) -> int:
        return self._counts[_EmojiCountType.MESSAGE_REACTION]

    @property
    def total_count(self) -> int:
        return sum(self._counts.values())


class _Constant(Constant):
    TIMEZONE_OFFSET = int(os.environ.get("DISCORD_EMOJI_RANKING_TIMEZONE_OFFSET", 0))
    TZ = datetime.timezone(datetime.timedelta(hours=TIMEZONE_OFFSET))
    DATE_FORMAT_SLASH = "%Y/%m/%d"
    DATE_FORMAT_HYPHEN = "%Y-%m-%d"
    DATE_FORMATS = [DATE_FORMAT_SLASH, DATE_FORMAT_HYPHEN]
    DEFAULT_RANK: int = 10


def _get_times_str(count: int) -> str:
    if count == 1:
        return "1 time"
    return f"{count} times"


def _get_rank_str(rank: int) -> str:
    if rank == 1:
        return "1st"
    if rank == 2:
        return "2nd"
    if rank == 3:
        return "3rd"
    return f"{rank}th"


class EmojiRanking(commands.Cog, CogHelper):
    def __init__(self, bot: commands.Bot):
        CogHelper.__init__(self, bot)
        self._channel_ids: List[int]
        self._before: Optional[datetime.datetime] = None
        self._after: Optional[datetime.datetime] = None
        self._order = _SortOrder.DESCENDING
        self._rank = _Constant.DEFAULT_RANK
        self._contains_bot = False
        self._user_ids: List[int] = []

    @app_commands.command(
        name="emoji_ranking",
        description="Show usage ranking of custom emojis.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        channel="Channel IDs or mentions separated by ','",
        before="Count messages before this date (YYYY/MM/DD or YYYY-MM-DD)",
        after="Count messages after this date",
        order="Sort order (ascending or descending)",
        rank="Number of rankings to display (1-25)",
        bot="Include bot messages and reactions",
        user="User IDs or mentions separated by ','",
    )
    @app_commands.choices(
        order=[
            app_commands.Choice(name="Descending", value="descending"),
            app_commands.Choice(name="Ascending", value="ascending"),
        ]
    )
    async def emoji_ranking(
        self,
        interaction: discord.Interaction,
        channel: Optional[str] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        order: Optional[str] = None,
        rank: Optional[int] = None,
        bot: Optional[bool] = None,
        user: Optional[str] = None,
    ):
        ctx = await commands.Context.from_interaction(interaction)

        if ctx.message and ctx.prefix:
            raw_content = ctx.message.content[len(ctx.prefix) :].lstrip()
            invoked = ctx.invoked_with or ""
            remaining = raw_content[len(invoked) :].strip()
            if remaining:
                args = self._parse_legacy_args(tuple(remaining.split()))
                await self._run_ranking(ctx, args)
                return

        args: Dict[str, str] = {}

        def set_arg(key: str, value):
            if value is None or value == "":
                return
            args[key] = str(value)

        set_arg("channel", channel)
        set_arg("before", before)
        set_arg("after", after)
        set_arg("order", order)
        if rank is not None:
            set_arg("rank", rank)
        if bot is not None:
            set_arg("bot", bot)
        set_arg("user", user)

        await self._run_ranking(ctx, args)

    def _parse_legacy_args(self, raw_args: tuple) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        for arg in raw_args:
            if "=" not in arg:
                continue
            key, _, value = arg.partition("=")
            if key:
                parsed[key] = value
        return parsed

    async def _run_ranking(self, ctx: commands.Context, args: Dict[str, str]):
        self._parse_args(ctx, args)
        await self._execute(ctx)

    def _parse_args(self, ctx: commands.Context, args: Dict[str, str]):
        self._channel_ids = get_list(args, "channel", ",", lambda value: int(value), [])
        self._before, self._after = get_before_after_fmts(
            ctx, args, *_Constant.DATE_FORMATS, tz=_Constant.TZ
        )
        self._order = _SortOrder.parse(args.get("order", ""))
        self._rank = int(args.get("rank", _Constant.DEFAULT_RANK))
        self._contains_bot = get_bool(args, "bot", False)
        self._user_ids = get_list(
            args, "user", ",", lambda value: int(value.strip("<@!>")), []
        )

    async def _execute(self, ctx: commands.Context):
        if hasattr(ctx, "interaction") and ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer()

        before = to_utc_naive(self._before)
        after = to_utc_naive(self._after)

        counters = [_EmojiCounter(emoji) for emoji in ctx.guild.emojis]

        channels = (
            [ctx.guild.get_channel(channel_id) for channel_id in self._channel_ids]
            if len(self._channel_ids) > 0
            else ctx.guild.channels
        )
        channels = [
            channel
            for channel in channels
            if channel is not None and isinstance(channel, discord.TextChannel)
        ]
        for channel in channels:
            logger.debug(f"count emoji in {channel.name} channel.")
            try:
                messages = [
                    message
                    async for message in channel.history(
                        limit=None, before=before, after=after
                    )
                ]
            except discord.Forbidden as e:
                # BOTに権限がないケースはログを出力して続行
                logger.warning(f"exception={e}, channel={channel}")
            else:
                counters = await self.count_emojis(counters, messages)

        rank = max(1, min(self._rank, len(ctx.guild.emojis)))
        sorted_counters = self.sort_ranking(counters, rank)

        # Embed生成
        if self._order == _SortOrder.DESCENDING:
            title = f"Emoji Usage Ranking Top {rank}"
        else:
            title = f"Emoji Usage Ranking Top {rank} Worst"
        before_str, after_str = get_corrected_before_after_str(
            self._before, self._after, ctx.guild, _Constant.TZ, *_Constant.DATE_FORMATS
        )
        description_lines = [f"{after_str} ~ {before_str}"]
        if self._user_ids:
            user_names = []
            for user_id in self._user_ids:
                member = ctx.guild.get_member(user_id)
                user_names.append(member.display_name if member else str(user_id))
            description_lines.append(f"User: {', '.join(user_names)}")
        description = "\n".join(description_lines)
        embed = discord.Embed(title=title, description=description)
        for counter in sorted_counters:
            name = f"{_get_rank_str(counter.rank)} {counter.emoji} Total: {_get_times_str(counter.total_count)}"
            value = f"In Messages: {counter.content_count} Reactions: {counter.reaction_count}"
            embed.add_field(name=name, value=value, inline=False)

        # 集計結果を送信
        logger.debug("send result")
        await ctx.send(embed=embed)

    async def count_emojis(
        self, counters: List[_EmojiCounter], messages: List[discord.Message]
    ) -> List[_EmojiCounter]:
        user_ids = set(self._user_ids)
        for message in messages:
            author_matches = not user_ids or message.author.id in user_ids
            for counter in counters:
                # メッセージ内に使われているかのカウント
                if author_matches and counter.emoji.name in message.content:
                    # BOTを弾く
                    if self._contains_bot or not message.author.bot:
                        counter.increment(_EmojiCountType.MESSAGE_CONTENT)
                # リアクションに使われているかのカウント
                for reaction in message.reactions:
                    if not isinstance(reaction.emoji, discord.Emoji):
                        continue
                    if reaction.emoji.id != counter.emoji.id:
                        continue
                    users = [user async for user in reaction.users()]
                    if user_ids:
                        users = [user for user in users if user.id in user_ids]
                    if not users:
                        continue
                    # BOTを弾く
                    if not self._contains_bot and all(user.bot for user in users):
                        continue
                    counter.increment(_EmojiCountType.MESSAGE_REACTION)
        return counters

    def sort_ranking(
        self, counters: List[_EmojiCounter], slice_num: int
    ) -> List[_EmojiCounter]:
        # ソートした上で要求された順位までの要素数に切り取る
        reverse = _SortOrder.reverse(self._order)
        sorted_counters = sorted(
            counters, key=lambda c: c.total_count, reverse=reverse
        )[0:slice_num]

        # 同順位を考慮した順位付け
        for index, counter in enumerate(sorted_counters):
            rank = index + 1
            if index > 0:
                prev = sorted_counters[index - 1]
                if prev.total_count == counter.total_count:
                    rank = prev.rank

            counter.rank = rank

        return sorted_counters


def setup(bot: commands.Bot):
    return bot.add_cog(EmojiRanking(bot))
