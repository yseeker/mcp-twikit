from fastmcp import FastMCP, Context
import twikit
import os
from pathlib import Path
import logging
from typing import Optional, List
import time
import random
import asyncio

# Create an MCP server
mcp = FastMCP("mcp-twikit")
logger = logging.getLogger(__name__)
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

USERNAME = os.getenv('TWITTER_USERNAME')
EMAIL = os.getenv('TWITTER_EMAIL')
PASSWORD = os.getenv('TWITTER_PASSWORD')
TOTP = os.getenv('TOTP')
USER_AGENT = os.getenv('USER_AGENT')
COOKIES_PATH = Path(os.getenv('COOKIES_FILE'))

# Rate limit tracking
RATE_LIMITS = {}
RATE_LIMIT_WINDOW = 15 * 60  # 15 minutes in seconds

async def get_twitter_client() -> twikit.Client:
    """Initialize and return an authenticated Twitter client."""
    client = twikit.Client('en-US', user_agent=USER_AGENT)
    
    sleep_duration = random.uniform(15, 40)
    await asyncio.sleep(sleep_duration)

    if COOKIES_PATH.exists():
        client.load_cookies(COOKIES_PATH)
    else:
        try:
            await client.login(
                auth_info_1=USERNAME,
                auth_info_2=EMAIL,
                password=PASSWORD,
                totp_secret=TOTP
            )
        except Exception as e:
            logger.error(f"Failed to login: {e}")
            raise
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        client.save_cookies(COOKIES_PATH)

    return client

def check_rate_limit(endpoint: str) -> bool:
    """Check if we're within rate limits for a given endpoint."""
    now = time.time()
    if endpoint not in RATE_LIMITS:
        RATE_LIMITS[endpoint] = []

    # Remove old timestamps
    RATE_LIMITS[endpoint] = [t for t in RATE_LIMITS[endpoint] if now - t < RATE_LIMIT_WINDOW]

    # Check limits based on endpoint
    if endpoint == 'tweet':
        return len(RATE_LIMITS[endpoint]) < 15  # 300 tweets per 15 minutes
    elif endpoint == 'dm':
        return len(RATE_LIMITS[endpoint]) < 10  # 1000 DMs per 15 minutes
    elif endpoint == 'follow_action':
        return len(RATE_LIMITS[endpoint]) < 10  # 1000 DMs per 15 minutes
    return True

# Existing search and read tools
@mcp.tool()
async def search_twitter(query: str, sort_by: str = 'Top', count: int = 10, ctx: Context = None) -> str:
    """Search twitter with a query. Sort by 'Top' or 'Latest'"""
    try:
        client = await get_twitter_client()
        tweets = await client.search_tweet(query, product=sort_by, count=count)
        return convert_tweets_to_markdown(tweets)
    except Exception as e:
        logger.error(f"Failed to search tweets: {e}")
        return f"Failed to search tweets: {e}"

@mcp.tool()
async def get_user_tweets(username: str, tweet_type: str = 'Tweets', count: int = 10, ctx: Context = None) -> str:
    """Get tweets from a specific user's timeline."""
    try:
        client = await get_twitter_client()
        username = username.lstrip('@')
        user = await client.get_user_by_screen_name(username)
        if not user:
            return f"Could not find user {username}"

        tweets = await client.get_user_tweets(
            user_id=user.id,
            tweet_type=tweet_type,
            count=count
        )
        return convert_tweets_to_markdown(tweets)
    except Exception as e:
        logger.error(f"Failed to get user tweets: {e}")
        return f"Failed to get user tweets: {e}"

@mcp.tool()
async def get_timeline(count: int = 20) -> str:
    """Get tweets from your home timeline (For You)."""
    try:
        client = await get_twitter_client()
        tweets = await client.get_timeline(count=count)
        return convert_tweets_to_markdown(tweets)
    except Exception as e:
        logger.error(f"Failed to get timeline: {e}")
        return f"Failed to get timeline: {e}"

@mcp.tool()
async def get_latest_timeline(count: int = 20) -> str:
    """Get tweets from your home timeline (Following)."""
    try:
        client = await get_twitter_client()
        tweets = await client.get_latest_timeline(count=count)
        return convert_tweets_to_markdown(tweets)
    except Exception as e:
        logger.error(f"Failed to get latest timeline: {e}")
        return f"Failed to get latest timeline: {e}"

# New write tools
@mcp.tool()
async def post_tweet(
    text: str,
    media_paths: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> str:
    """Post a tweet with optional media, reply, and tags."""
    try:
        if not check_rate_limit('tweet'):
            return "Rate limit exceeded for tweets. Please wait before posting again."

        client = await get_twitter_client()

        # Handle tags by converting to mentions
        if tags:
            mentions = ' '.join(f"@{tag.lstrip('@')}" for tag in tags)
            text = f"""{text}
{mentions}"""

        # Upload media if provided
        media_ids = []
        if media_paths:
            for path in media_paths:
                media_id = await client.upload_media(path, wait_for_completion=True)
                media_ids.append(media_id)

        # Create the tweet
        tweet = await client.create_tweet(
            text=text,
            media_ids=media_ids if media_ids else None,
            reply_to=reply_to
        )
        RATE_LIMITS.setdefault('tweet', []).append(time.time())
        return f"Successfully posted tweet: {tweet.id}"
    except Exception as e:
        logger.error(f"Failed to post tweet: {e}")
        return f"Failed to post tweet: {e}"

@mcp.tool()
async def delete_tweet(tweet_id: str) -> str:
    """Delete a tweet by its ID."""
    try:
        client = await get_twitter_client()
        await client.delete_tweet(tweet_id)
        return f"Successfully deleted tweet {tweet_id}"
    except Exception as e:
        logger.error(f"Failed to delete tweet: {e}")
        return f"Failed to delete tweet: {e}"


@mcp.tool()
async def follow_user(username: str, ctx: Context = None) -> str:
    """Follow user by username"""
    client = await get_twitter_client()
    username = username.lstrip('@')
    logger.info(f"フォロー試行: @{username}")
    try:
        user = await client.get_user_by_screen_name(username)
        if not user:
            return f"ユーザー @{username} が見つからずフォローできませんでした。"
        await user.follow()
        return f"ユーザー @{username} をフォローしました。"
    except Exception as e:
        err_str = str(e).lower()
        if "already follow" in err_str or "すでにフォロー" in err_str:
            logger.warning(f"既に @{username} をフォローしています。")
            return f"既に @{username} をフォローしています。"
        elif "blocked" in err_str or "ブロックされて" in err_str:
            logger.warning(f"@{username} にブロックされているためフォローできません。")
            return f"@{username} にブロックされているためフォローできません。"
        elif "suspended" in err_str or "凍結されて" in err_str:
                logger.warning(f"@{username} のアカウントが凍結されているためフォローできません。")
                return f"@{username} のアカウントが凍結されているためフォローできません。"
        else:
            logger.error(f"ユーザー @{username} のフォロー中にエラー: {e}")
            return f"ユーザー @{username} のフォロー中にエラーが発生しました: {e}"

        

def convert_tweets_to_markdown(tweets) -> str:
    """Convert a list of tweets to markdown format."""
    result = []
    for tweet in tweets:
        result.append(f"### @{tweet.user.screen_name}")
        result.append(f"**{tweet.created_at}**")
        result.append(tweet.text)
        if tweet.media:
            for media in tweet.media:
                result.append(f"![media]({media.url})")
        result.append("---")
    return "\n".join(result)