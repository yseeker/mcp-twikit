### twikit-mcp (拡張・待機時間追加版) ###
import asyncio  # asyncio.sleep のために必要
import json
import logging
import os
import random  # random.uniform のために必要
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import twikit
from fastmcp import Context, FastMCP

# --- 初期設定 ---
# (変更なし)
# Create an MCP server
mcp = FastMCP("mcp-twikit")
logger = logging.getLogger(__name__)
# httpxのログレベルを調整して冗長な出力を抑制
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO) # 必要に応じてログレベルを設定

# 環境変数から認証情報を取得
USERNAME = os.getenv('TWITTER_USERNAME')
EMAIL = os.getenv('TWITTER_EMAIL')
PASSWORD = os.getenv('TWITTER_PASSWORD')
# USER_AGENT は twikit のデフォルトを使用するか、環境変数で設定
USER_AGENT = os.getenv('USER_AGENT') # None の場合は twikit がデフォルトを使用
COOKIES_PATH = Path.home() / '.mcp-twikit' / 'cookies.json'

# --- レートリミット管理 ---
# (変更なし)
RATE_LIMITS: Dict[str, List[float]] = {}
RATE_LIMIT_WINDOW = 15 * 60  # 15 minutes in seconds
ENDPOINT_LIMITS = {
    'search_tweet': 50,
    'user_tweets': 100,
    'home_timeline': 15,
    'tweet': 10,
    'delete_tweet': 10,
    'dm_write': 10,
    'dm_read': 50,
    'user_search': 100,
    'user_lookup': 100,
    'follow': 5,
    'unfollow': 3,
    'favorite': 10,
    'unfavorite': 10,
    'retweet': 10,
    'delete_retweet': 100,
    'trends': 50,
    'upload_media': 300,
    'create_poll': 50,
}

# --- 認証とクライアント初期化 ---
# (変更なし)
async def get_twitter_client() -> twikit.Client:
    """認証済みのTwitterクライアントを初期化または読み込みして返す。"""
    client = twikit.Client('en-US', user_agent=USER_AGENT)

    if COOKIES_PATH.exists():
        try:
            client.load_cookies(COOKIES_PATH)
            await client.get_self()
            logger.info("クッキーを読み込み、ログインを確認しました。")
        except Exception as e:
            logger.warning(f"クッキーの読み込みまたは検証に失敗しました: {e}。再ログインを試みます。")
            COOKIES_PATH.unlink(missing_ok=True)
            await login_and_save_cookies(client)
    else:
        await login_and_save_cookies(client)

    return client

async def login_and_save_cookies(client: twikit.Client):
    """認証情報を使用してログインし、クッキーを保存する。"""
    if not all([USERNAME, EMAIL, PASSWORD]):
        raise ValueError("Twitterの認証情報 (TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD) が不足しています。環境変数を設定してください。")
    try:
        logger.info("ユーザー名/メールアドレスとパスワードでログインを試みます...")
        await client.login(
            auth_info_1=USERNAME,
            auth_info_2=EMAIL,
            password=PASSWORD
        )
        logger.info("ログイン成功。")
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        client.save_cookies(COOKIES_PATH)
        logger.info(f"クッキーを {COOKIES_PATH} に保存しました。")
    except Exception as e:
        logger.error(f"ログインに失敗しました: {e}")
        raise

# --- レートリミットヘルパー (待機時間追加) ---

def check_rate_limit(endpoint: str) -> bool:
    """指定されたエンドポイントのレートリミット内かどうかを確認する。"""
    # (変更なし)
    now = time.time()
    if endpoint not in RATE_LIMITS:
        RATE_LIMITS[endpoint] = []

    RATE_LIMITS[endpoint] = [t for t in RATE_LIMITS[endpoint] if now - t < RATE_LIMIT_WINDOW]

    limit = ENDPOINT_LIMITS.get(endpoint)
    if limit is None:
        logger.warning(f"エンドポイント '{endpoint}' に定義されたレートリミットがありません。リクエストを許可します。")
        return True

    current_count = len(RATE_LIMITS[endpoint])
    logger.debug(f"'{endpoint}' のレートリミットチェック: {current_count}/{limit}")
    return current_count < limit

def record_rate_limit_usage(endpoint: str):
    """レートリミット追跡のためにAPI呼び出しを記録する。"""
    # (変更なし)
    RATE_LIMITS.setdefault(endpoint, []).append(time.time())

# ############################################################################ #
# ######################## execute_with_rate_limit の修正 ####################### #
# ############################################################################ #
async def execute_with_rate_limit(endpoint: str, coro) -> str:
    """レートリミットを確認し、コルーチンを実行。成功時に指定ルールで待機し、結果/エラーを返す。"""
    if not check_rate_limit(endpoint):
        try:
            wait_time = RATE_LIMIT_WINDOW - (time.time() - RATE_LIMITS[endpoint][0])
            wait_time = max(0, wait_time)
            logger.warning(f"エンドポイント '{endpoint}' のレートリミットを超過しました。約 {wait_time:.0f} 秒待機してください。")
            # レートリミット超過時は待機しない
            return f"レートリミット超過 ({endpoint})。約 {wait_time:.0f} 秒後に再試行してください。"
        except IndexError:
             logger.error(f"レートリミット超過の計算中にエラーが発生しました ({endpoint})。")
             # レートリミット超過時は待機しない
             return f"レートリミット超過 ({endpoint})。しばらく待ってから再試行してください。"

    result = None
    error_message = None

    try:
        # --- API呼び出し実行 ---
        result = await coro
        record_rate_limit_usage(endpoint)
        # --- API呼び出し成功時の待機処理 ---
        sleep_duration = 0
        # post_tweet (endpoint='tweet') と follow_user (endpoint='follow') の場合
        if endpoint in ['tweet', 'follow']:
            sleep_duration = random.uniform(30, 60)
            logger.info(f"'{endpoint}' 実行成功。人間的な操作に見せるため {sleep_duration:.2f} 秒待機します。")
        # それ以外のツールの場合
        else:
            sleep_duration = random.uniform(10, 20)
            logger.info(f"'{endpoint}' 実行成功。人間的な操作に見せるため {sleep_duration:.2f} 秒待機します。")

        await asyncio.sleep(sleep_duration)
        # -------------------------

    except twikit.errors.TwitterException as e:
        logger.error(f"'{endpoint}' でTwitter APIエラーが発生しました: {e}")
        if e.response and e.response.status_code == 429:
             now = time.time()
             limit = ENDPOINT_LIMITS.get(endpoint, 1)
             RATE_LIMITS[endpoint] = [now] * limit
             logger.warning(f"APIにより '{endpoint}' のレートリミット到達を確認しました。約15分待機してください。")
             error_message = f"APIレートリミット到達 ({endpoint})。約15分待機してください。"
        else:
            error_message = f"'{endpoint}' の実行に失敗しました (APIエラー): {e}"
        # エラー発生時は待機しない
    except Exception as e:
        logger.error(f"'{endpoint}' で予期せぬエラーが発生しました: {e}")
        error_message = f"'{endpoint}' の実行に失敗しました: {e}"
        # エラー発生時は待機しない

    # 成功時は結果、エラー時はエラーメッセージを返す
    return result if error_message is None else error_message
# ############################################################################ #
# ############################################################################ #
# ############################################################################ #


# --- フォーマット変換ヘルパー ---

def convert_tweet_to_markdown(tweet: twikit.Tweet) -> str:
    """単一のツイートオブジェクトをMarkdown形式に変換する。"""
    try:
        user_info = f"**@{tweet.user.screen_name}** ({tweet.user.name})" if tweet.user else "不明なユーザー"
        tweet_url = f"https://twitter.com/{tweet.user.screen_name}/status/{tweet.id}" if tweet.user and tweet.id else f"ツイートID: {tweet.id}"
        markdown = [
            f"### {user_info}",
            f"[{tweet.created_at}]({tweet_url}) | ID: `{tweet.id}`",
            "",
            tweet.text if tweet.text else " ", # 空のテキストの場合でも改行を保持
        ]
        # メディア
        if tweet.media:
            media_urls = [f"![media {idx+1}]({media.get('media_url_https') or media.get('url')})"
                          for idx, media in enumerate(tweet.media) if media.get('media_url_https') or media.get('url')]
            if media_urls:
                markdown.append("\n**メディア:**")
                markdown.extend(media_urls)
        # 引用ツイート
        if tweet.quoted_tweet:
            markdown.append("\n**引用元:**")
            # 引用ツイートの内容をインデントして追加
            quoted_md = convert_tweet_to_markdown(tweet.quoted_tweet)
            markdown.append("> " + quoted_md.replace("\n", "\n> "))
        # 投票
        if hasattr(tweet, 'poll') and tweet.poll:
            markdown.append("\n**投票:**")
            for option in tweet.poll.options:
                markdown.append(f"- {option.label} ({option.votes} 票)")
            status = getattr(tweet.poll, 'voting_status', '不明')
            end_time = getattr(tweet.poll, 'end_datetime', '不明')
            markdown.append(f"状態: {status} | 終了日時: {end_time}")
        # 統計情報
        stats = []
        if tweet.reply_count is not None: stats.append(f"返信: {tweet.reply_count}")
        if tweet.retweet_count is not None: stats.append(f"RT: {tweet.retweet_count}")
        if tweet.favorite_count is not None: stats.append(f"いいね: {tweet.favorite_count}")
        if tweet.bookmark_count is not None: stats.append(f"ブックマーク: {tweet.bookmark_count}")
        if tweet.views is not None: stats.append(f"表示: {tweet.views}")
        if stats:
            markdown.append(f"\n*統計: {' | '.join(stats)}*")

        return "\n".join(markdown)
    except Exception as e:
        logger.error(f"ツイート ({getattr(tweet, 'id', '不明')}) のMarkdown変換中にエラー: {e}")
        return f"ツイート情報の表示中にエラーが発生しました (ID: {getattr(tweet, 'id', '不明')})"


def convert_tweets_to_markdown(tweets: Optional[List[twikit.Tweet]]) -> str:
    """ツイートのリストをMarkdown形式に変換する。"""
    if not tweets:
        return "ツイートが見つかりませんでした。"
    result = []
    for tweet in tweets:
        result.append(convert_tweet_to_markdown(tweet))
        result.append("---")
    return "\n".join(result)


def convert_user_to_markdown(user: twikit.User) -> str:
    """単一のユーザーオブジェクトをMarkdown形式に変換する。"""
    try:
        profile_url = f"https://twitter.com/{user.screen_name}"
        markdown = [
            f"### {user.name} ([@{user.screen_name}]({profile_url}))",
            f"ID: `{user.id}`",
            f"自己紹介: {user.description}" if user.description else "",
            f"場所: {user.location}" if user.location else "",
            f"ウェブサイト: {user.url}" if user.url else "",
            f"登録日: {user.created_at}",
            f"ツイート数: {user.statuses_count} | フォロー中: {user.friends_count} | フォロワー: {user.followers_count}",
            f"認証済み: {'はい' if user.verified else 'いいえ'}",
            # f"プロフィール画像: ![]({user.profile_image_url_https})" # 必要なら追加
        ]
        return "\n".join(filter(None, markdown)) # 空行を除去
    except Exception as e:
        logger.error(f"ユーザー ({getattr(user, 'screen_name', '不明')}) のMarkdown変換中にエラー: {e}")
        return f"ユーザー情報の表示中にエラーが発生しました (ScreenName: {getattr(user, 'screen_name', '不明')})"

def convert_users_to_markdown(users: Optional[List[twikit.User]]) -> str:
    """ユーザーのリストをMarkdown形式に変換する。"""
    if not users:
        return "ユーザーが見つかりませんでした。"
    result = []
    for user in users:
        result.append(convert_user_to_markdown(user))
        result.append("---")
    return "\n".join(result)


def convert_dms_to_markdown(messages: Optional[List[twikit.DmMessage]], client_user_id: Optional[str] = None) -> str:
    """DMメッセージのリストをMarkdown形式に変換する。"""
    if not messages:
        return "ダイレクトメッセージが見つかりませんでした。"
    result = []
    for message in messages:
        try:
            direction = ""
            if client_user_id:
                direction = "送信済み" if str(message.sender_id) == str(client_user_id) else "受信"

            markdown = [
                f"**メッセージID:** `{message.id}`",
                f"時刻: {message.time}",
                f"送信者ID: `{message.sender_id}`" + (f" ({direction})" if direction else ""),
                # f"受信者ID: `{message.recipient_id}`", # 通常は不要か
                f"テキスト: {message.text}",
            ]
            if message.attachment:
                att_type = message.attachment.get('type')
                if att_type == 'media':
                    media = message.attachment.get('media', {})
                    media_url = media.get('media_url_https')
                    media_type = media.get('type') # 'photo', 'video' など
                    markdown.append(f"添付ファイル: [{media_type}]({media_url})")
                else:
                     markdown.append(f"添付ファイルタイプ: {att_type}") # 他のタイプも表示

            result.append("\n".join(markdown))
            result.append("---")
        except Exception as e:
             logger.error(f"DM ({getattr(message, 'id', '不明')}) のMarkdown変換中にエラー: {e}")
             result.append(f"DM情報の表示中にエラーが発生しました (ID: {getattr(message, 'id', '不明')})")
             result.append("---")
    return "\n".join(result)

def convert_trends_to_markdown(trends: Optional[List[twikit.Trend]]) -> str:
    """トレンドのリストをMarkdown形式に変換する。"""
    if not trends:
        return "トレンドが見つかりませんでした。"
    result = ["### 現在のトレンド"]
    for trend in trends:
        try:
            line = f"- **{trend.name}**"
            # ツイート数が 0 または None の場合は表示しない
            if trend.tweet_volume and trend.tweet_volume > 0:
                line += f" ({trend.tweet_volume:,} ツイート)"
            if trend.url:
                 line += f" - [リンク]({trend.url})"
            result.append(line)
            if trend.description:
                result.append(f"  - _{trend.description}_")
        except Exception as e:
            logger.error(f"トレンド ({getattr(trend, 'name', '不明')}) のMarkdown変換中にエラー: {e}")
            result.append(f"- トレンド情報の表示中にエラーが発生しました ({getattr(trend, 'name', '不明')})")

    return "\n".join(result)


# --- MCPツール ---
# (各ツールの内部ロジックは変更なし、execute_with_rate_limit の呼び出しはそのまま)

@mcp.tool()
async def search_twitter(query: str, sort_by: str = 'Latest', count: int = 20, ctx: Context = None) -> str:
    """指定されたクエリでTwitterを検索します。'Top' または 'Latest' でソート可能。デフォルトは20件。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        valid_products = ['Top', 'Latest', 'Media']
        product = sort_by if sort_by in valid_products else 'Latest'
        logger.info(f"ツイート検索実行: '{query}' (ソート: {product}, 件数: {count})")
        tweets = await client.search_tweet(query, product=product, count=count)
        if hasattr(tweets, '__aiter__'):
             tweets_list = [tweet async for tweet in tweets]
             return convert_tweets_to_markdown(tweets_list)
        return convert_tweets_to_markdown(tweets)
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('search_tweet', job())

@mcp.tool()
async def get_user_tweets(username: str, tweet_type: str = 'Tweets', count: int = 20, ctx: Context = None) -> str:
    """指定されたユーザーのタイムラインからツイートを取得します。tweet_type: 'Tweets', 'TweetsAndReplies', 'Media'。デフォルト20件。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        username = username.lstrip('@')
        logger.info(f"ユーザー @{username} のツイート取得実行 (タイプ: {tweet_type}, 件数: {count})")
        try:
            user = await client.get_user_by_screen_name(username)
            if not user:
                logger.warning(f"ユーザー @{username} が見つかりませんでした。")
                return f"ユーザー @{username} が見つかりませんでした。"
        except Exception as e:
             logger.error(f"ユーザー @{username} の取得中にエラー: {e}")
             return f"ユーザー @{username} の検索中にエラーが発生しました: {e}"
        tweets = await client.get_user_tweets(user_id=user.id, tweet_type=tweet_type, count=count)
        if hasattr(tweets, '__aiter__'):
            tweets_list = [tweet async for tweet in tweets]
            return convert_tweets_to_markdown(tweets_list)
        return convert_tweets_to_markdown(tweets)
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('user_tweets', job())

@mcp.tool()
async def get_timeline(count: int = 20) -> str:
    """あなたのホームタイムライン（「おすすめ」）からツイートを取得します。デフォルト20件。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"ホームタイムライン（おすすめ）取得実行 (件数: {count})")
        tweets = await client.get_timeline(count=count)
        if hasattr(tweets, '__aiter__'):
            tweets_list = [tweet async for tweet in tweets]
            return convert_tweets_to_markdown(tweets_list)
        return convert_tweets_to_markdown(tweets)
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('home_timeline', job())

@mcp.tool()
async def get_latest_timeline(count: int = 20) -> str:
    """あなたのホームタイムライン（「フォロー中」）からツイートを取得します。デフォルト20件。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"最新タイムライン（フォロー中）取得実行 (件数: {count})")
        tweets = await client.get_latest_timeline(count=count)
        if hasattr(tweets, '__aiter__'):
            tweets_list = [tweet async for tweet in tweets]
            return convert_tweets_to_markdown(tweets_list)
        return convert_tweets_to_markdown(tweets)
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('home_timeline', job())

@mcp.tool()
async def search_users(query: str, count: int = 10, ctx: Context = None) -> str:
    """指定されたクエリでユーザーを検索します。デフォルト10件。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"ユーザー検索実行: '{query}' (件数: {count})")
        users = await client.search_user(query, count=count)
        if hasattr(users, '__aiter__'):
             users_list = [user async for user in users]
             return convert_users_to_markdown(users_list)
        return convert_users_to_markdown(users)
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('user_search', job())

@mcp.tool()
async def get_user_info(username: str, ctx: Context = None) -> str:
    """指定されたユーザー名 (@は不要) の詳細情報を取得します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        username = username.lstrip('@')
        logger.info(f"ユーザー情報取得実行: @{username}")
        try:
            user = await client.get_user_by_screen_name(username)
            if not user:
                return f"ユーザー @{username} が見つかりませんでした。"
            return convert_user_to_markdown(user)
        except Exception as e:
            logger.error(f"ユーザー @{username} の情報取得中にエラー: {e}")
            return f"ユーザー @{username} の情報取得中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('user_lookup', job())

@mcp.tool()
async def follow_user(username: str, ctx: Context = None) -> str:
    """指定されたユーザー名 (@は不要) をフォローします。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
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
    # 変更後の execute_with_rate_limit が呼び出される (30-60秒待機)
    return await execute_with_rate_limit('follow', job())

@mcp.tool()
async def unfollow_user(username: str, ctx: Context = None) -> str:
    """指定されたユーザー名 (@は不要) のフォローを解除します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        username = username.lstrip('@')
        logger.info(f"アンフォロー試行: @{username}")
        try:
            user = await client.get_user_by_screen_name(username)
            if not user:
                return f"ユーザー @{username} が見つからずアンフォローできませんでした。"
            await user.unfollow()
            return f"ユーザー @{username} のフォローを解除しました。"
        except Exception as e:
            err_str = str(e).lower()
            if "not following" in err_str or "フォローしていません" in err_str:
                logger.warning(f"@{username} をフォローしていません。")
                return f"現在 @{username} をフォローしていません。"
            else:
                logger.error(f"ユーザー @{username} のアンフォロー中にエラー: {e}")
                return f"ユーザー @{username} のアンフォロー中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('unfollow', job())

@mcp.tool()
async def get_dm_history(username: str, count: int = 20, ctx: Context = None) -> str:
    """指定されたユーザー (@は不要) とのDM履歴を取得します。DM権限が必要です。デフォルト20件。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        username = username.lstrip('@')
        logger.info(f"DM履歴取得実行: @{username} (件数: {count})")
        try:
            user = await client.get_user_by_screen_name(username)
            if not user:
                return f"ユーザー @{username} が見つからずDM履歴を取得できませんでした。"
            messages_paginator = await user.get_dm_history()
            messages_list = []
            msg_count = 0
            async for message in messages_paginator:
                 messages_list.append(message)
                 msg_count += 1
                 if msg_count >= count:
                     break
            client_user_id = None
            try:
                self_user = await client.get_self()
                client_user_id = self_user.id if self_user else None
            except Exception as self_e:
                 logger.warning(f"自身のユーザーID取得に失敗: {self_e}")
            return convert_dms_to_markdown(messages_list, client_user_id)
        except Exception as e:
            logger.error(f"ユーザー @{username} とのDM履歴取得中にエラー: {e}")
            if "permission" in str(e).lower() or "権限" in str(e):
                return f"ユーザー @{username} とのDM履歴取得に必要な権限がない可能性があります。"
            return f"ユーザー @{username} とのDM履歴取得中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('dm_read', job())


@mcp.tool()
async def get_tweet_info(tweet_id: str, ctx: Context = None) -> str:
    """指定されたツイートIDの詳細情報を取得します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"ツイート情報取得実行: ID {tweet_id}")
        try:
            tweet = await client.get_tweet_by_id(tweet_id)
            if not tweet:
                return f"ツイートID {tweet_id} が見つかりませんでした。"
            return convert_tweet_to_markdown(tweet)
        except Exception as e:
            logger.error(f"ツイートID {tweet_id} の情報取得中にエラー: {e}")
            if "Not found" in str(e) or "見つかりません" in str(e):
                 return f"ツイートID {tweet_id} が見つかりませんでした。"
            return f"ツイートID {tweet_id} の情報取得中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('search_tweet', job()) # Assuming search limit is appropriate

@mcp.tool()
async def favorite_tweet(tweet_id: str, ctx: Context = None) -> str:
    """指定されたツイートIDをいいね（ふぁぼ）します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"いいね試行: ツイートID {tweet_id}")
        try:
            tweet = await client.get_tweet_by_id(tweet_id)
            if not tweet:
                 return f"いいねするツイートID {tweet_id} が見つかりませんでした。"
            await tweet.favorite()
            return f"ツイート {tweet_id} をいいねしました。"
        except Exception as e:
            err_str = str(e).lower()
            if "already favorited" in err_str or "すでにお気に入り" in err_str:
                 logger.warning(f"ツイート {tweet_id} は既にいいね済みです。")
                 return f"ツイート {tweet_id} は既にいいね済みです。"
            # ... other error handling ...
            else:
                logger.error(f"ツイート {tweet_id} のいいね中にエラー: {e}")
                return f"ツイート {tweet_id} のいいね中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('favorite', job())

@mcp.tool()
async def unfavorite_tweet(tweet_id: str, ctx: Context = None) -> str:
    """指定されたツイートIDのいいねを取り消します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"いいね取り消し試行: ツイートID {tweet_id}")
        try:
            tweet = await client.get_tweet_by_id(tweet_id)
            if not tweet:
                 return f"いいねを取り消すツイートID {tweet_id} が見つかりませんでした。"
            await tweet.unfavorite()
            return f"ツイート {tweet_id} のいいねを取り消しました。"
        except Exception as e:
            err_str = str(e).lower()
            if "not favorited" in err_str or "お気に入りに登録されていません" in err_str:
                logger.warning(f"ツイート {tweet_id} はいいねされていません。")
                return f"ツイート {tweet_id} はいいねされていません。"
            # ... other error handling ...
            else:
                logger.error(f"ツイート {tweet_id} のいいね取り消し中にエラー: {e}")
                return f"ツイート {tweet_id} のいいね取り消し中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('unfavorite', job())

@mcp.tool()
async def retweet_tweet(tweet_id: str, ctx: Context = None) -> str:
    """指定されたツイートIDをリツイートします。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"リツイート試行: ツイートID {tweet_id}")
        try:
            tweet = await client.get_tweet_by_id(tweet_id)
            if not tweet:
                 return f"リツイートするツイートID {tweet_id} が見つかりませんでした。"
            await tweet.retweet()
            return f"ツイート {tweet_id} をリツイートしました。"
        except Exception as e:
            err_str = str(e).lower()
            if "already retweeted" in err_str or "すでにリツイート" in err_str:
                logger.warning(f"ツイート {tweet_id} は既にリツイート済みです。")
                return f"ツイート {tweet_id} は既にリツイート済みです。"
            # ... other error handling ...
            else:
                logger.error(f"ツイート {tweet_id} のリツイート中にエラー: {e}")
                return f"ツイート {tweet_id} のリツイート中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('retweet', job())

@mcp.tool()
async def delete_retweet(tweet_id: str, ctx: Context = None) -> str:
    """指定されたツイートIDのリツイートを取り消します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"リツイート取り消し試行: ツイートID {tweet_id}")
        try:
            tweet = await client.get_tweet_by_id(tweet_id)
            if not tweet:
                 return f"リツイートを取り消すツイートID {tweet_id} が見つかりませんでした。"
            await tweet.delete_retweet() # Assuming method name
            return f"ツイート {tweet_id} のリツイートを取り消しました。"
        except Exception as e:
            err_str = str(e).lower()
            if "not retweeted" in err_str or "リツイートされていません" in err_str:
                 logger.warning(f"ツイート {tweet_id} はリツイートされていません。")
                 return f"ツイート {tweet_id} はリツイートしていません。"
            # ... other error handling ...
            else:
                logger.error(f"ツイート {tweet_id} のリツイート取り消し中にエラー: {e}")
                return f"ツイート {tweet_id} のリツイート取り消し中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('delete_retweet', job())

@mcp.tool()
async def post_poll_tweet(
    text: str,
    options: List[str],
    duration_minutes: int = 1440,
    reply_to: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> str:
    """投票付きのツイートを投稿します。選択肢(options)は2～4個、期間(duration_minutes)は5～10080分(7日)。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        if not (2 <= len(options) <= 4): return "投票の選択肢は2個以上4個以下である必要があります。"
        if not (5 <= duration_minutes <= 10080): return "投票期間は5分以上10080分（7日間）以下である必要があります。"
        final_text = text
        if tags:
            mentions = ' '.join(f"@{tag.lstrip('@')}" for tag in tags)
            final_text = f"{text}\n{mentions}"
        try:
            logger.info(f"投票作成: 選択肢={options}, 期間={duration_minutes}分")
            poll = await client.create_poll(options=options, duration_minutes=duration_minutes)
            poll_identifier = getattr(poll, 'uri', None) or getattr(poll, 'id', None)
            if not poll_identifier:
                 logger.error("作成された投票オブジェクトに必要な識別子 (uri or id) が見つかりません。")
                 return "投票の作成に失敗しました: 予期しない投票オブジェクト形式です。"
            logger.info(f"投票付きツイート投稿実行 (Reply to: {reply_to}, Poll: {poll_identifier})")
            tweet = await client.create_tweet(text=final_text, poll_uri=poll_identifier, reply_to=reply_to)
            return f"投票付きツイートを投稿しました: {tweet.id}"
        except Exception as e:
            logger.error(f"投票付きツイートの投稿中にエラー: {e}")
            if "duplicate" in str(e).lower() or "重複" in str(e): return "直近のツイートと内容が重複しているため投稿できませんでした。"
            return f"投票付きツイートの投稿中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (30-60秒待機、endpoint='tweet'のため)
    return await execute_with_rate_limit('tweet', job()) # Using 'tweet' endpoint for poll posts

@mcp.tool()
async def get_trends(trend_type: str = 'Top', ctx: Context = None) -> str:
    """指定されたタイプのトレンドを取得します。タイプ: 'Top', 'Latest', 'Hashtags', 'News' など。デフォルトは 'Top'。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        valid_trend_types = ['Top', 'Latest', 'Hashtags', 'News']
        selected_type = trend_type if trend_type in valid_trend_types else 'Top'
        logger.info(f"トレンド取得実行 (タイプ: {selected_type})")
        try:
            trends = await client.get_trends(trend_type=selected_type)
            return convert_trends_to_markdown(trends)
        except Exception as e:
            logger.error(f"トレンド取得 (タイプ: {selected_type}) 中にエラー: {e}")
            return f"トレンド取得 (タイプ: {selected_type}) 中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('trends', job())

@mcp.tool()
async def post_tweet(
    text: str,
    media_paths: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> str:
    """ツイートを投稿します。メディアファイルパス(media_paths)、返信先ツイートID(reply_to)、ユーザーメンション(tags)を任意で指定可能。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        final_text = text
        if tags:
            mentions = ' '.join(f"@{tag.lstrip('@')}" for tag in tags)
            final_text = f"{text}\n{mentions}"
        media_ids = []
        if media_paths:
            logger.info(f"メディアアップロード開始: {media_paths}")
            for i, path in enumerate(media_paths):
                 if not Path(path).is_file(): return f"メディアファイルが見つかりません: {path}"
                 try:
                    media_id = await client.upload_media(path, 0, wait_for_completion=True)
                    if not media_id: raise Exception(f"メディアIDが返されませんでした ({path})")
                    media_ids.append(media_id)
                    logger.info(f"メディアアップロード成功: {path} -> ID: {media_id}")
                 except Exception as upload_error:
                    logger.error(f"メディア '{path}' のアップロード中にエラー: {upload_error}")
                    return f"メディア '{path}' のアップロード中にエラーが発生しました: {upload_error}"
        try:
            logger.info(f"ツイート投稿実行 (Reply to: {reply_to}, Media IDs: {media_ids})")
            tweet = await client.create_tweet(text=final_text, media_ids=media_ids if media_ids else None, reply_to=reply_to)
            return f"ツイートを投稿しました: {tweet.id}"
        except Exception as post_error:
            logger.error(f"ツイート投稿中にエラー: {post_error}")
            if "duplicate" in str(post_error).lower() or "重複" in str(post_error): return "直近のツイートと内容が重複しているため投稿できませんでした。"
            return f"ツイート投稿中にエラーが発生しました: {post_error}"
    # 変更後の execute_with_rate_limit が呼び出される (30-60秒待機)
    return await execute_with_rate_limit('tweet', job())

@mcp.tool()
async def delete_tweet(tweet_id: str) -> str:
    """指定されたツイートIDのツイートを削除します。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"ツイート削除実行: ID {tweet_id}")
        try:
            await client.delete_tweet(tweet_id)
            return f"ツイート {tweet_id} を削除しました。"
        except Exception as e:
             logger.error(f"ツイート {tweet_id} の削除中にエラー: {e}")
             if "Not found" in str(e) or "見つかりません" in str(e): return f"削除するツイートID {tweet_id} が見つかりませんでした。"
             return f"ツイート {tweet_id} の削除中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('delete_tweet', job())

@mcp.tool()
async def send_dm(username: str, message: str, media_path: Optional[str] = None) -> str:
    """指定されたユーザー (@は不要) にダイレクトメッセージを送信します。任意でメディアファイルを添付可能。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        username = username.lstrip('@')
        user_id = None
        try:
             logger.info(f"DM送信のためユーザー検索: @{username}")
             user = await client.get_user_by_screen_name(username)
             if not user: return f"DM送信先のユーザー @{username} が見つかりませんでした。"
             user_id = user.id
        except Exception as e:
             logger.error(f"DM送信先のユーザー @{username} 検索中にエラー: {e}")
             return f"DM送信先のユーザー @{username} 検索中にエラーが発生しました: {e}"
        media_id = None
        if media_path:
            if not Path(media_path).is_file(): return f"DM添付メディアファイルが見つかりません: {media_path}"
            try:
                logger.info(f"DMメディアアップロード開始: {media_path}")
                media_id = await client.upload_media(media_path, 0, wait_for_completion=True)
                if not media_id: raise Exception(f"DMメディアIDが返されませんでした ({media_path})")
                logger.info(f"DMメディアアップロード成功: {media_path} -> ID: {media_id}")
            except Exception as upload_error:
                logger.error(f"DMメディア '{media_path}' のアップロード中にエラー: {upload_error}")
                return f"DMメディア '{media_path}' のアップロード中にエラーが発生しました: {upload_error}"
        try:
            logger.info(f"DM送信実行: To User ID {user_id} (Media ID: {media_id})")
            await client.send_dm(user_id=user_id, text=message, media_id=media_id)
            return f"ユーザー @{username} (ID: {user_id}) にDMを送信しました。"
        except Exception as dm_error:
             logger.error(f"DM送信 (@{username}) 中にエラー: {dm_error}")
             err_str = str(dm_error).lower()
             if "cannot send messages" in err_str or "フォローされていない" in err_str: return f"ユーザー @{username} はあなたをフォローしていないか、DM受信を許可していないため送信できません。"
             elif "blocked" in err_str or "ブロック" in err_str: return f"ユーザー @{username} にブロックされているためDMを送信できません。"
             return f"DM送信 (@{username}) 中にエラーが発生しました: {dm_error}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('dm_write', job())

@mcp.tool()
async def delete_dm(message_id: str) -> str:
    """指定されたIDのダイレクトメッセージを削除します（自分が送信したもののみ可能）。"""
    async def job():
        # ... (内部ロジックは変更なし) ...
        client = await get_twitter_client()
        logger.info(f"DM削除実行: ID {message_id}")
        try:
            await client.delete_dm(message_id)
            return f"DM {message_id} を削除しました。"
        except Exception as e:
             logger.error(f"DM {message_id} の削除中にエラー: {e}")
             if "Not found" in str(e) or "見つかりません" in str(e): return f"削除するDM ID {message_id} が見つからないか、削除権限がありません。"
             return f"DM {message_id} の削除中にエラーが発生しました: {e}"
    # 変更後の execute_with_rate_limit が呼び出される (10-20秒待機)
    return await execute_with_rate_limit('dm_write', job()) # Assuming dm_write limit is appropriate

# --- MCPサーバーの起動 ---
# (変更なし)
# 例: fastmcp serve your_script_name.py --port 8080