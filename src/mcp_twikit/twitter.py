from fastmcp import FastMCP, Context
import twikit
import os
from pathlib import Path
import logging
from typing import Optional, List
import time
import json
from typing import Optional, List, Dict, Literal # DictとLiteralを追加

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
    time.sleep(15)

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
        return len(RATE_LIMITS[endpoint]) < 300  # 300 tweets per 15 minutes
    elif endpoint == 'dm':
        return len(RATE_LIMITS[endpoint]) < 1000  # 1000 DMs per 15 minutes
    return True


# --- ヘルパー関数 (既存の convert_tweets_to_markdown の下に追加) ---

def convert_users_to_markdown(users) -> str:
    """Convert a list of user objects to markdown format."""
    result = []
    if not users:
        return "No users found."
    for user in users:
        result.append(f"### @{user.screen_name} (ID: {user.id})")
        result.append(f"**Name:** {user.name}")
        result.append(f"**Description:** {user.description}")
        result.append(f"**Followers:** {getattr(user, 'followers_count', 'N/A')}")
        result.append(f"**Following:** {getattr(user, 'friends_count', 'N/A')}")
        result.append(f"**Verified:** {getattr(user, 'verified', 'N/A')}")
        result.append("---")
    return "\n".join(result)

def convert_lists_to_markdown(lists) -> str:
    """Convert a list of List objects to markdown format."""
    result = []
    if not lists:
        return "No lists found."
    for list_obj in lists:
        result.append(f"### {list_obj.name} (ID: {list_obj.id})")
        result.append(f"**Owner:** @{getattr(list_obj.user, 'screen_name', 'N/A')}")
        result.append(f"**Description:** {list_obj.description}")
        result.append(f"**Members:** {list_obj.member_count}")
        result.append(f"**Subscribers:** {list_obj.subscriber_count}")
        result.append(f"**Private:** {list_obj.is_private}")
        result.append("---")
    return "\n".join(result)

def convert_to_json_string(data, indent=2) -> str:
    """Convert Python object to a JSON string, handling non-serializable types."""
    def default_serializer(obj):
        if hasattr(obj, '__dict__'):
            # Try converting custom objects to their dictionary representation
            try:
                # Filter out non-serializable items like functions or complex objects if needed
                return {k: v for k, v in obj.__dict__.items() if not k.startswith('_') and not callable(v)}
            except Exception:
                return f"<<Non-serializable: {type(obj).__name__}>>"
        try:
            # Let json module handle basic types
            return json.JSONEncoder.default(None, obj)
        except TypeError:
            return f"<<Non-serializable: {type(obj).__name__}>>"
        except Exception as e:
             return f"<<Serialization Error: {e}>>"


    if data is None:
        return "null"
    if isinstance(data, (str, int, float, bool)):
         return json.dumps(data)
    if isinstance(data, list):
         # Process list items individually
         serializable_list = [json.loads(convert_to_json_string(item, indent)) for item in data]
         return json.dumps(serializable_list, indent=indent, ensure_ascii=False)

    # Handle Result objects - extract the data list
    if hasattr(data, 'data') and isinstance(getattr(data, 'data', None), list):
         items = data.data
         # Recursively serialize list items
         serializable_items = [json.loads(convert_to_json_string(item, indent)) for item in items]
         output = {'data': serializable_items}
         if hasattr(data, 'next_cursor'):
              output['next_cursor'] = data.next_cursor
         if hasattr(data, 'previous_cursor'):
             output['previous_cursor'] = data.previous_cursor
         return json.dumps(output, indent=indent, ensure_ascii=False, default=default_serializer)

    # Handle single objects with __dict__ or other complex types
    try:
         # Attempt direct serialization with default handler
        return json.dumps(data, indent=indent, ensure_ascii=False, default=default_serializer)
    except Exception as e:
        logger.error(f"Failed to serialize data: {e}")
        return f'{{"error": "Failed to serialize object", "type": "{type(data).__name__}"}}'

# -----------------------------------------------------------------

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
async def send_dm(user_id: str, message: str, media_path: Optional[str] = None) -> str:
    """Send a direct message to a user."""
    try:
        if not check_rate_limit('dm'):
            return "Rate limit exceeded for DMs. Please wait before sending again."

        client = await get_twitter_client()

        media_id = None
        if media_path:
            media_id = await client.upload_media(media_path, wait_for_completion=True)

        await client.send_dm(
            user_id=user_id,
            text=message,
            media_id=media_id
        )
        RATE_LIMITS.setdefault('dm', []).append(time.time())
        return f"Successfully sent DM to user {user_id}"
    except Exception as e:
        logger.error(f"Failed to send DM: {e}")
        return f"Failed to send DM: {e}"

@mcp.tool()
async def delete_dm(message_id: str) -> str:
    """Delete a direct message by its ID."""
    try:
        client = await get_twitter_client()
        await client.delete_dm(message_id)
        return f"Successfully deleted DM {message_id}"
    except Exception as e:
        logger.error(f"Failed to delete DM: {e}")
        return f"Failed to delete DM: {e}"

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


# --- 検索・取得 (ツイート・ユーザー・場所など) ---

@mcp.tool()
async def search_user(query: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Search for users based on a query."""
    try:
        client = await get_twitter_client()
        users_result = await client.search_user(query, count=count, cursor=cursor)
        # Consider using convert_users_to_markdown or convert_to_json_string
        return convert_to_json_string(users_result)
    except Exception as e:
        logger.error(f"Failed to search users: {e}")
        return f"Failed to search users: {e}"

@mcp.tool()
async def get_similar_tweets(tweet_id: str) -> str:
    """Retrieves tweets similar to the specified tweet (Twitter premium only)."""
    try:
        client = await get_twitter_client()
        tweets = await client.get_similar_tweets(tweet_id)
        return convert_tweets_to_markdown(tweets)
    except Exception as e:
        logger.error(f"Failed to get similar tweets: {e}")
        return f"Failed to get similar tweets: {e}"

@mcp.tool()
async def get_user_highlights_tweets(user_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves highlighted tweets from a user’s timeline."""
    try:
        client = await get_twitter_client()
        tweets_result = await client.get_user_highlights_tweets(user_id, count=count, cursor=cursor)
        return convert_to_json_string(tweets_result) # Using JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get user highlights tweets: {e}")
        return f"Failed to get user highlights tweets: {e}"

@mcp.tool()
async def get_user_by_id(user_id: str) -> str:
    """Fetches a user by ID."""
    try:
        client = await get_twitter_client()
        user = await client.get_user_by_id(user_id)
        return convert_to_json_string(user) # Return user data as JSON
    except Exception as e:
        logger.error(f"Failed to get user by ID {user_id}: {e}")
        return f"Failed to get user by ID {user_id}: {e}"

@mcp.tool()
async def reverse_geocode(lat: float, long: float, accuracy: Optional[str] = None, granularity: Optional[str] = None, max_results: Optional[int] = None) -> str:
    """Given a latitude and longitude, searches for nearby places."""
    try:
        client = await get_twitter_client()
        places = await client.reverse_geocode(lat, long, accuracy=accuracy, granularity=granularity, max_results=max_results)
        return convert_to_json_string(places) # Return places data as JSON
    except Exception as e:
        logger.error(f"Failed to reverse geocode: {e}")
        return f"Failed to reverse geocode: {e}"

@mcp.tool()
async def search_geo(lat: Optional[float] = None, long: Optional[float] = None, query: Optional[str] = None, ip: Optional[str] = None, granularity: Optional[str] = None, max_results: Optional[int] = None) -> str:
    """Search for places that can be attached to a Tweet."""
    try:
        client = await get_twitter_client()
        places = await client.search_geo(lat=lat, long=long, query=query, ip=ip, granularity=granularity, max_results=max_results)
        return convert_to_json_string(places) # Return places data as JSON
    except Exception as e:
        logger.error(f"Failed to search geo: {e}")
        return f"Failed to search geo: {e}"

@mcp.tool()
async def get_place(place_id: str) -> str:
    """Get information about a specific place by its ID."""
    try:
        client = await get_twitter_client()
        place = await client.get_place(place_id)
        return convert_to_json_string(place) # Return place data as JSON
    except Exception as e:
        logger.error(f"Failed to get place {place_id}: {e}")
        return f"Failed to get place {place_id}: {e}"

@mcp.tool()
async def get_tweet_by_id(tweet_id: str) -> str:
    """Fetches a single tweet by its ID."""
    try:
        client = await get_twitter_client()
        tweet = await client.get_tweet_by_id(tweet_id)
        # Use existing markdown converter for single tweet
        return convert_tweets_to_markdown([tweet]) if tweet else "Tweet not found."
    except Exception as e:
        logger.error(f"Failed to get tweet by ID {tweet_id}: {e}")
        return f"Failed to get tweet by ID {tweet_id}: {e}"

@mcp.tool()
async def get_tweets_by_ids(tweet_ids: List[str]) -> str:
    """Retrieve multiple tweets by a list of IDs."""
    try:
        client = await get_twitter_client()
        tweets = await client.get_tweets_by_ids(tweet_ids)
        return convert_tweets_to_markdown(tweets)
    except Exception as e:
        logger.error(f"Failed to get tweets by IDs: {e}")
        return f"Failed to get tweets by IDs: {e}"

@mcp.tool()
async def get_scheduled_tweets() -> str:
    """Retrieves scheduled tweets for the authenticated user."""
    try:
        client = await get_twitter_client()
        scheduled_tweets = await client.get_scheduled_tweets()
        return convert_to_json_string(scheduled_tweets) # Return scheduled tweets data as JSON
    except Exception as e:
        logger.error(f"Failed to get scheduled tweets: {e}")
        return f"Failed to get scheduled tweets: {e}"

@mcp.tool()
async def get_retweeters(tweet_id: str, count: int = 40, cursor: Optional[str] = None) -> str:
    """Retrieve users who retweeted a specific tweet."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_retweeters(tweet_id, count=count, cursor=cursor)
        # Consider using convert_users_to_markdown or convert_to_json_string
        return convert_to_json_string(users_result)
    except Exception as e:
        logger.error(f"Failed to get retweeters for tweet {tweet_id}: {e}")
        return f"Failed to get retweeters for tweet {tweet_id}: {e}"

@mcp.tool()
async def get_favoriters(tweet_id: str, count: int = 40, cursor: Optional[str] = None) -> str:
    """Retrieve users who favorited (liked) a specific tweet."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_favoriters(tweet_id, count=count, cursor=cursor)
         # Consider using convert_users_to_markdown or convert_to_json_string
        return convert_to_json_string(users_result)
    except Exception as e:
        logger.error(f"Failed to get favoriters for tweet {tweet_id}: {e}")
        return f"Failed to get favoriters for tweet {tweet_id}: {e}"

@mcp.tool()
async def get_community_note(note_id: str) -> str:
    """Fetches a community note by its ID."""
    try:
        client = await get_twitter_client()
        note = await client.get_community_note(note_id)
        return convert_to_json_string(note) # Return note data as JSON
    except Exception as e:
        logger.error(f"Failed to get community note {note_id}: {e}")
        return f"Failed to get community note {note_id}: {e}"

@mcp.tool()
async def get_trends(category: Literal['trending', 'for-you', 'news', 'sports', 'entertainment'] = 'trending', count: int = 20) -> str:
    """Retrieves trending topics on Twitter for a specific category."""
    try:
        client = await get_twitter_client()
        trends = await client.get_trends(category=category, count=count)
        return convert_to_json_string(trends) # Return trends data as JSON
    except Exception as e:
        logger.error(f"Failed to get trends for category {category}: {e}")
        return f"Failed to get trends for category {category}: {e}"

@mcp.tool()
async def get_available_locations() -> str:
    """Retrieves locations where trends can be retrieved."""
    try:
        client = await get_twitter_client()
        locations = await client.get_available_locations()
        return convert_to_json_string(locations) # Return locations data as JSON
    except Exception as e:
        logger.error(f"Failed to get available locations: {e}")
        return f"Failed to get available locations: {e}"

@mcp.tool()
async def get_place_trends(woeid: int) -> str:
    """Retrieves the top 50 trending topics for a specific WOEID (Where On Earth ID)."""
    try:
        client = await get_twitter_client()
        place_trends = await client.get_place_trends(woeid)
        return convert_to_json_string(place_trends) # Return place trends data as JSON
    except Exception as e:
        logger.error(f"Failed to get place trends for WOEID {woeid}: {e}")
        return f"Failed to get place trends for WOEID {woeid}: {e}"

# --- ツイート操作 (スケジュール含む) ---

@mcp.tool()
async def create_scheduled_tweet(scheduled_at: int, text: str = '', media_paths: Optional[List[str]] = None) -> str:
    """Schedules a tweet to be posted at a specified timestamp (Unix time). Media can be attached via paths."""
    try:
        # Add rate limit check if needed for scheduling
        # if not check_rate_limit('schedule_tweet'):
        #     return "Rate limit exceeded for scheduling tweets."

        client = await get_twitter_client()

        media_ids = []
        if media_paths:
            for path in media_paths:
                # Assume media upload requires waiting for completion for scheduling
                media_id = await client.upload_media(path, wait_for_completion=True)
                media_ids.append(media_id)

        scheduled_tweet_id = await client.create_scheduled_tweet(
            scheduled_at=scheduled_at,
            text=text,
            media_ids=media_ids if media_ids else None
        )
        # Add timestamp to rate limit tracking if needed
        # RATE_LIMITS.setdefault('schedule_tweet', []).append(time.time())
        return f"Successfully scheduled tweet. Scheduled ID: {scheduled_tweet_id}"
    except Exception as e:
        logger.error(f"Failed to schedule tweet: {e}")
        return f"Failed to schedule tweet: {e}"

@mcp.tool()
async def delete_scheduled_tweet(scheduled_tweet_id: str) -> str:
    """Deletes a previously scheduled tweet by its scheduled ID."""
    try:
        client = await get_twitter_client()
        await client.delete_scheduled_tweet(scheduled_tweet_id)
        return f"Successfully deleted scheduled tweet {scheduled_tweet_id}"
    except Exception as e:
        logger.error(f"Failed to delete scheduled tweet {scheduled_tweet_id}: {e}")
        return f"Failed to delete scheduled tweet {scheduled_tweet_id}: {e}"

@mcp.tool()
async def favorite_tweet(tweet_id: str) -> str:
    """Favorites (likes) a tweet by its ID."""
    try:
        # Add rate limit check for liking tweets
        if not check_rate_limit('favorite'):
             return "Rate limit exceeded for favoriting tweets."

        client = await get_twitter_client()
        await client.favorite_tweet(tweet_id)
        RATE_LIMITS.setdefault('favorite', []).append(time.time())
        return f"Successfully favorited tweet {tweet_id}"
    except Exception as e:
        # Handle potential errors like already favorited, tweet not found, etc.
        logger.error(f"Failed to favorite tweet {tweet_id}: {e}")
        return f"Failed to favorite tweet {tweet_id}: {e}"

@mcp.tool()
async def unfavorite_tweet(tweet_id: str) -> str:
    """Unfavorites (unlikes) a tweet by its ID."""
    try:
        # Add rate limit check if needed (might share limit with favorite)
        # if not check_rate_limit('favorite'):
        #     return "Rate limit exceeded for unfavoriting tweets."

        client = await get_twitter_client()
        await client.unfavorite_tweet(tweet_id)
        # RATE_LIMITS.setdefault('favorite', []).append(time.time()) # Or use a separate limit
        return f"Successfully unfavorited tweet {tweet_id}"
    except Exception as e:
        logger.error(f"Failed to unfavorite tweet {tweet_id}: {e}")
        return f"Failed to unfavorite tweet {tweet_id}: {e}"

@mcp.tool()
async def retweet(tweet_id: str) -> str:
    """Retweets a tweet by its ID."""
    try:
        # Add rate limit check for retweeting
        if not check_rate_limit('retweet'):
             return "Rate limit exceeded for retweeting."

        client = await get_twitter_client()
        await client.retweet(tweet_id)
        RATE_LIMITS.setdefault('retweet', []).append(time.time())
        return f"Successfully retweeted tweet {tweet_id}"
    except Exception as e:
         # Handle potential errors like already retweeted, protected tweet, etc.
        logger.error(f"Failed to retweet tweet {tweet_id}: {e}")
        return f"Failed to retweet tweet {tweet_id}: {e}"

@mcp.tool()
async def delete_retweet(tweet_id: str) -> str:
    """Deletes the retweet of a specific tweet ID (unretweets)."""
    try:
        # Add rate limit check if needed (might share limit with retweet)
        # if not check_rate_limit('retweet'):
        #     return "Rate limit exceeded for deleting retweets."

        client = await get_twitter_client()
        await client.delete_retweet(tweet_id)
        # RATE_LIMITS.setdefault('retweet', []).append(time.time()) # Or use a separate limit
        return f"Successfully deleted retweet of tweet {tweet_id}"
    except Exception as e:
        logger.error(f"Failed to delete retweet of tweet {tweet_id}: {e}")
        return f"Failed to delete retweet of tweet {tweet_id}: {e}"

# --- ユーザー操作 (フォロー・ブロックなど) ---

@mcp.tool()
async def follow_user(user_id: str) -> str:
    """Follows a user by their ID."""
    try:
        # Add rate limit check for following users
        if not check_rate_limit('follow'):
             return "Rate limit exceeded for following users."

        client = await get_twitter_client()
        followed_user = await client.follow_user(user_id)
        RATE_LIMITS.setdefault('follow', []).append(time.time())
        return f"Successfully followed user {followed_user.screen_name} (ID: {user_id})"
    except Exception as e:
        logger.error(f"Failed to follow user {user_id}: {e}")
        return f"Failed to follow user {user_id}: {e}"

@mcp.tool()
async def unfollow_user(user_id: str) -> str:
    """Unfollows a user by their ID."""
    try:
        # Add rate limit check if needed (might share limit with follow)
        # if not check_rate_limit('follow'):
        #     return "Rate limit exceeded for unfollowing users."

        client = await get_twitter_client()
        unfollowed_user = await client.unfollow_user(user_id)
        # RATE_LIMITS.setdefault('follow', []).append(time.time()) # Or use a separate limit
        return f"Successfully unfollowed user {unfollowed_user.screen_name} (ID: {user_id})"
    except Exception as e:
        logger.error(f"Failed to unfollow user {user_id}: {e}")
        return f"Failed to unfollow user {user_id}: {e}"

@mcp.tool()
async def block_user(user_id: str) -> str:
    """Blocks a user by their ID."""
    try:
        # Add rate limit check for blocking users
        if not check_rate_limit('block'):
             return "Rate limit exceeded for blocking users."

        client = await get_twitter_client()
        blocked_user = await client.block_user(user_id)
        RATE_LIMITS.setdefault('block', []).append(time.time())
        return f"Successfully blocked user {blocked_user.screen_name} (ID: {user_id})"
    except Exception as e:
        logger.error(f"Failed to block user {user_id}: {e}")
        return f"Failed to block user {user_id}: {e}"

@mcp.tool()
async def unblock_user(user_id: str) -> str:
    """Unblocks a user by their ID."""
    try:
        # Add rate limit check if needed (might share limit with block)
        # if not check_rate_limit('block'):
        #     return "Rate limit exceeded for unblocking users."

        client = await get_twitter_client()
        unblocked_user = await client.unblock_user(user_id)
        # RATE_LIMITS.setdefault('block', []).append(time.time()) # Or use a separate limit
        return f"Successfully unblocked user {unblocked_user.screen_name} (ID: {user_id})"
    except Exception as e:
        logger.error(f"Failed to unblock user {user_id}: {e}")
        return f"Failed to unblock user {user_id}: {e}"

@mcp.tool()
async def mute_user(user_id: str) -> str:
    """Mutes a user by their ID."""
    try:
        # Add rate limit check for muting users
        if not check_rate_limit('mute'):
             return "Rate limit exceeded for muting users."

        client = await get_twitter_client()
        muted_user = await client.mute_user(user_id)
        RATE_LIMITS.setdefault('mute', []).append(time.time())
        return f"Successfully muted user {muted_user.screen_name} (ID: {user_id})"
    except Exception as e:
        logger.error(f"Failed to mute user {user_id}: {e}")
        return f"Failed to mute user {user_id}: {e}"

@mcp.tool()
async def unmute_user(user_id: str) -> str:
    """Unmutes a user by their ID."""
    try:
        # Add rate limit check if needed (might share limit with mute)
        # if not check_rate_limit('mute'):
        #     return "Rate limit exceeded for unmuting users."

        client = await get_twitter_client()
        unmuted_user = await client.unmute_user(user_id)
        # RATE_LIMITS.setdefault('mute', []).append(time.time()) # Or use a separate limit
        return f"Successfully unmuted user {unmuted_user.screen_name} (ID: {user_id})"
    except Exception as e:
        logger.error(f"Failed to unmute user {user_id}: {e}")
        return f"Failed to unmute user {user_id}: {e}"

@mcp.tool()
async def get_user_followers(user_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves a list of followers for a given user ID."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_user_followers(user_id, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get followers for user {user_id}: {e}")
        return f"Failed to get followers for user {user_id}: {e}"

@mcp.tool()
async def get_latest_followers(user_id: Optional[str] = None, screen_name: Optional[str] = None, count: int = 200, cursor: Optional[str] = None) -> str:
    """Retrieves the latest followers (up to 200) for a user ID or screen name."""
    try:
        if not user_id and not screen_name:
             return "Error: Please provide either user_id or screen_name."
        client = await get_twitter_client()
        # Note: twikit docs mention user_id OR screen_name, but signature shows both optional. Clarify usage if needed.
        users_result = await client.get_latest_followers(user_id=user_id, screen_name=screen_name, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get latest followers for user {user_id or screen_name}: {e}")
        return f"Failed to get latest followers for user {user_id or screen_name}: {e}"

@mcp.tool()
async def get_latest_friends(user_id: Optional[str] = None, screen_name: Optional[str] = None, count: int = 200, cursor: Optional[str] = None) -> str:
    """Retrieves the latest friends (following users, up to 200) for a user ID or screen name."""
    try:
        if not user_id and not screen_name:
             return "Error: Please provide either user_id or screen_name."
        client = await get_twitter_client()
        users_result = await client.get_latest_friends(user_id=user_id, screen_name=screen_name, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get latest friends for user {user_id or screen_name}: {e}")
        return f"Failed to get latest friends for user {user_id or screen_name}: {e}"

@mcp.tool()
async def get_user_verified_followers(user_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves a list of verified followers for a given user ID."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_user_verified_followers(user_id, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get verified followers for user {user_id}: {e}")
        return f"Failed to get verified followers for user {user_id}: {e}"

@mcp.tool()
async def get_user_followers_you_know(user_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves a list of common followers between the authenticated user and the target user."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_user_followers_you_know(user_id, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get followers you know for user {user_id}: {e}")
        return f"Failed to get followers you know for user {user_id}: {e}"

@mcp.tool()
async def get_user_following(user_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves a list of users whom the given user ID is following."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_user_following(user_id, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get following for user {user_id}: {e}")
        return f"Failed to get following for user {user_id}: {e}"

@mcp.tool()
async def get_user_subscriptions(user_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves a list of users to which the specified user ID is subscribed."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_user_subscriptions(user_id, count=count, cursor=cursor)
        return convert_to_json_string(users_result) # JSON for Result object
    except Exception as e:
        logger.error(f"Failed to get subscriptions for user {user_id}: {e}")
        return f"Failed to get subscriptions for user {user_id}: {e}"

@mcp.tool()
async def get_followers_ids(user_id: Optional[str] = None, screen_name: Optional[str] = None, count: int = 5000, cursor: Optional[str] = None) -> str:
    """Fetches the IDs (up to 5000) of the followers of a specified user ID or screen name."""
    try:
        if not user_id and not screen_name:
             return "Error: Please provide either user_id or screen_name."
        client = await get_twitter_client()
        ids_result = await client.get_followers_ids(user_id=user_id, screen_name=screen_name, count=count, cursor=cursor)
        # Result object contains list of integers (IDs)
        return convert_to_json_string(ids_result) # JSON for Result object containing IDs
    except Exception as e:
        logger.error(f"Failed to get follower IDs for user {user_id or screen_name}: {e}")
        return f"Failed to get follower IDs for user {user_id or screen_name}: {e}"

@mcp.tool()
async def get_friends_ids(user_id: Optional[str] = None, screen_name: Optional[str] = None, count: int = 5000, cursor: Optional[str] = None) -> str:
    """Fetches the IDs (up to 5000) of the friends (following) of a specified user ID or screen name."""
    try:
        if not user_id and not screen_name:
             return "Error: Please provide either user_id or screen_name."
        client = await get_twitter_client()
        ids_result = await client.get_friends_ids(user_id=user_id, screen_name=screen_name, count=count, cursor=cursor)
        # Result object contains list of integers (IDs)
        return convert_to_json_string(ids_result) # JSON for Result object containing IDs
    except Exception as e:
        logger.error(f"Failed to get friend IDs for user {user_id or screen_name}: {e}")
        return f"Failed to get friend IDs for user {user_id or screen_name}: {e}"


# --- リスト関連 ---

@mcp.tool()
async def create_list(name: str, description: str = '', is_private: bool = False) -> str:
    """Creates a new Twitter list."""
    try:
        # Add rate limit check for creating lists
        if not check_rate_limit('list_create'):
             return "Rate limit exceeded for creating lists."

        client = await get_twitter_client()
        created_list = await client.create_list(name=name, description=description, is_private=is_private)
        RATE_LIMITS.setdefault('list_create', []).append(time.time())
        return f"Successfully created list '{created_list.name}' (ID: {created_list.id})"
    except Exception as e:
        logger.error(f"Failed to create list: {e}")
        return f"Failed to create list: {e}"

# edit_list_banner requires uploading media first to get media_id
@mcp.tool()
async def edit_list_banner(list_id: str, media_path: str) -> str:
    """Sets or updates the banner image for a list using a local media file path."""
    try:
        # Add rate limit check if needed
        client = await get_twitter_client()
        # Upload the media first
        logger.info(f"Uploading banner media from {media_path} for list {list_id}")
        media_id = await client.upload_media(media_path, wait_for_completion=True)
        logger.info(f"Media uploaded with ID: {media_id}. Setting as banner for list {list_id}")
        # Set the banner using the obtained media_id
        await client.edit_list_banner(list_id=list_id, media_id=media_id)
        return f"Successfully updated banner for list {list_id} using media {media_id}"
    except Exception as e:
        logger.error(f"Failed to edit list banner for {list_id}: {e}")
        return f"Failed to edit list banner for {list_id}: {e}"

@mcp.tool()
async def delete_list_banner(list_id: str) -> str:
    """Deletes the banner image from a list."""
    try:
        # Add rate limit check if needed
        client = await get_twitter_client()
        await client.delete_list_banner(list_id=list_id)
        return f"Successfully deleted banner for list {list_id}"
    except Exception as e:
        logger.error(f"Failed to delete list banner for {list_id}: {e}")
        return f"Failed to delete list banner for {list_id}: {e}"

@mcp.tool()
async def edit_list(list_id: str, name: Optional[str] = None, description: Optional[str] = None, is_private: Optional[bool] = None) -> str:
    """Edits the details (name, description, privacy) of an existing list."""
    try:
        # Add rate limit check if needed
        if not check_rate_limit('list_edit'):
             return "Rate limit exceeded for editing lists."

        client = await get_twitter_client()
        updated_list = await client.edit_list(list_id=list_id, name=name, description=description, is_private=is_private)
        RATE_LIMITS.setdefault('list_edit', []).append(time.time())
        return f"Successfully updated list {list_id}. New name: '{updated_list.name}'"
    except Exception as e:
        logger.error(f"Failed to edit list {list_id}: {e}")
        return f"Failed to edit list {list_id}: {e}"

@mcp.tool()
async def add_list_member(list_id: str, user_id: str) -> str:
    """Adds a user (by ID) to a specified list."""
    try:
        # Add rate limit check for list modifications
        if not check_rate_limit('list_modify_member'):
             return "Rate limit exceeded for modifying list members."

        client = await get_twitter_client()
        await client.add_list_member(list_id=list_id, user_id=user_id)
        RATE_LIMITS.setdefault('list_modify_member', []).append(time.time())
        return f"Successfully added user {user_id} to list {list_id}"
    except Exception as e:
        logger.error(f"Failed to add member {user_id} to list {list_id}: {e}")
        return f"Failed to add member {user_id} to list {list_id}: {e}"

@mcp.tool()
async def remove_list_member(list_id: str, user_id: str) -> str:
    """Removes a user (by ID) from a specified list."""
    try:
         # Add rate limit check (might share limit with add_list_member)
        if not check_rate_limit('list_modify_member'):
             return "Rate limit exceeded for modifying list members."

        client = await get_twitter_client()
        await client.remove_list_member(list_id=list_id, user_id=user_id)
        RATE_LIMITS.setdefault('list_modify_member', []).append(time.time()) # Or use a separate limit
        return f"Successfully removed user {user_id} from list {list_id}"
    except Exception as e:
        logger.error(f"Failed to remove member {user_id} from list {list_id}: {e}")
        return f"Failed to remove member {user_id} from list {list_id}: {e}"

@mcp.tool()
async def get_lists(count: int = 100, cursor: Optional[str] = None) -> str:
    """Retrieves lists owned or followed by the authenticated user."""
    try:
        client = await get_twitter_client()
        lists_result = await client.get_lists(count=count, cursor=cursor)
        # Use custom markdown or JSON for Result object
        return convert_to_json_string(lists_result)
    except Exception as e:
        logger.error(f"Failed to get lists: {e}")
        return f"Failed to get lists: {e}"

@mcp.tool()
async def get_list(list_id: str) -> str:
    """Retrieve details of a specific list by its ID."""
    try:
        client = await get_twitter_client()
        list_obj = await client.get_list(list_id)
        # Use custom markdown or JSON
        return convert_to_json_string(list_obj)
    except Exception as e:
        logger.error(f"Failed to get list {list_id}: {e}")
        return f"Failed to get list {list_id}: {e}"

@mcp.tool()
async def get_list_tweets(list_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves tweets from the timeline of a specific list."""
    try:
        client = await get_twitter_client()
        tweets_result = await client.get_list_tweets(list_id, count=count, cursor=cursor)
        # Use existing tweet converter for Result object
        return convert_to_json_string(tweets_result)
    except Exception as e:
        logger.error(f"Failed to get tweets for list {list_id}: {e}")
        return f"Failed to get tweets for list {list_id}: {e}"

@mcp.tool()
async def get_list_members(list_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves members of a specific list."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_list_members(list_id, count=count, cursor=cursor)
        # Use custom user converter or JSON for Result object
        return convert_to_json_string(users_result)
    except Exception as e:
        logger.error(f"Failed to get members for list {list_id}: {e}")
        return f"Failed to get members for list {list_id}: {e}"

@mcp.tool()
async def get_list_subscribers(list_id: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Retrieves subscribers of a specific list."""
    try:
        client = await get_twitter_client()
        users_result = await client.get_list_subscribers(list_id, count=count, cursor=cursor)
         # Use custom user converter or JSON for Result object
        return convert_to_json_string(users_result)
    except Exception as e:
        logger.error(f"Failed to get subscribers for list {list_id}: {e}")
        return f"Failed to get subscribers for list {list_id}: {e}"

@mcp.tool()
async def search_list(query: str, count: int = 20, cursor: Optional[str] = None) -> str:
    """Search for public Twitter lists based on a query."""
    try:
        client = await get_twitter_client()
        lists_result = await client.search_list(query, count=count, cursor=cursor)
        # Use custom list converter or JSON for Result object
        return convert_to_json_string(lists_result)
    except Exception as e:
        logger.error(f"Failed to search lists with query '{query}': {e}")
        return f"Failed to search lists: {e}"

# --- END ---
