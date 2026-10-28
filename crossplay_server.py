#!/usr/bin/env python3
"""Serve the analyzer and load your finished Crossplay games.

Usage:
    python3 crossplay_server.py [port]      # then open http://localhost:8000
    python3 crossplay_server.py --mock-nyt  # synthetic NYT data for local testing

The server serves the static app and a JSON API that fetches the current
browser user's games from NYT Crossplay. Locally, the NYT-S cookie is kept in
server memory for that browser's session. On Vercel, the page keeps it only in
memory and sends it to the same-origin API with each request.

    GET    /api/crossplay/status               is a cookie configured? (no NYT call)
    GET    /api/crossplay/me                   your NYT games user id and name
    PUT    /api/crossplay/token                save a cookie: {"nyt_s": "..."}
    DELETE /api/crossplay/token                forget the saved cookie
    GET    /api/crossplay/games                your recent games (summary rows)
    GET    /api/crossplay/games/<id>/review    a finished game, turn by turn
    GET    /api/crossplay/shared/<token>       a finished game via its share link

Reviews are only served for completed games, so the tool can't be used to
look at a game while it is still being played. By default the server binds to
127.0.0.1 for local use. The Vercel function accepts same-origin requests for
the deployment URL and configured custom domains. The local server never
serves dotfiles, Python source, or capture files.
"""

import argparse
import base64
import gzip
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
NYT_BASE = 'https://www.nytimes.com'
DEFAULT_UA = 'Crossplay/7955 CFNetwork Darwin'
COLS = 'ABCDEFGHIJKLMNO'
GAME_ID_RE = re.compile(r'^[a-z0-9]{6,32}$')
SHARE_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
# RFC 6265 cookie-octet characters; NYT-S values can include '^'.
COOKIE_RE = re.compile(r'^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{20,4096}$')
ACROSS_RE = re.compile(r'^(\d{1,2})([A-O])$')
DOWN_RE = re.compile(r'^([A-O])(\d{1,2})$')
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
SESSION_COOKIE = 'crossplay_session'
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60
MAX_SESSIONS = 256
LIST_CACHE_SECONDS = 15
ANALYSIS_VERSION = 'quackle-equity-v1'
DAILY_REFRESH_SECONDS = 24 * 60 * 60
TOTAL_TILES = 100
CENTER = (7, 7)


class CrossplayError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


# ── Move notation and game replay ────────────────────────────────────────

def parse_play(action):
    """Parse 'BR(E)AST D10 27' into (row, col, dr, dc, [(letter, existing)]).

    Returns None for moves that place no tiles (swaps and passes). Letters in
    parentheses are already on the board; lowercase letters are blanks.
    '8F' (row first) is across and 'F8' (column first) is down.
    """
    parts = action.split()
    if len(parts) != 3:
        return None
    word, pos = parts[0], parts[1]
    across = ACROSS_RE.match(pos)
    down = DOWN_RE.match(pos)
    if across:
        row, col, dr, dc = int(across.group(1)) - 1, COLS.index(across.group(2)), 0, 1
    elif down:
        row, col, dr, dc = int(down.group(2)) - 1, COLS.index(down.group(1)), 1, 0
    else:
        return None
    tiles, existing = [], False
    for ch in word:
        if ch == '(':
            existing = True
        elif ch == ')':
            existing = False
        elif ch.isalpha():
            tiles.append((ch, existing))
        else:
            raise ValueError(f'Unexpected character {ch!r} in play {action!r}')
    if not 0 <= row < 15 or not 0 <= col < 15:
        raise ValueError(f'Play {action!r} starts off the board')
    return row, col, dr, dc, tiles


def apply_play(board, placement):
    """Place a parsed play on board in place; False if it does not fit."""
    row, col, dr, dc, tiles = placement
    for i, (letter, existing) in enumerate(tiles):
        r, c = row + dr * i, col + dc * i
        if not (0 <= r < 15 and 0 <= c < 15):
            return False
        if existing:
            if board[r][c].upper() != letter.upper():
                return False
        elif board[r][c] != '.':
            return False
        else:
            board[r][c] = letter
    return True


def normalize_rack(tray):
    return ''.join('?' if ch in '?*_ ' or ch.islower() else ch for ch in (tray or '')).upper()


def play_score(action):
    last = action.split()[-1] if action.split() else ''
    return int(last) if last.lstrip('-').isdigit() else 0


def turn_order(game):
    """Interleave both players' plays in the order they happened.

    The API stores plays per player, so try each player as the opener and
    keep the order in which every play fits the board as it stood and the
    first placement covers the center square.
    """
    players = game['game_data']['players']
    for first in (0, 1):
        a, b = players[first], players[1 - first]
        pa, pb = a.get('plays', []), b.get('plays', [])
        if len(pa) - len(pb) not in (0, 1):
            continue
        order = []
        for i in range(len(pa)):
            order.append((a, pa[i]))
            if i < len(pb):
                order.append((b, pb[i]))
        board = [['.'] * 15 for _ in range(15)]
        placed_any, valid = False, True
        for _, play in order:
            placement = parse_play(play['action'])
            if not placement:
                continue
            if not placed_any:
                row, col, dr, dc, tiles = placement
                cells = {(row + dr * i, col + dc * i) for i in range(len(tiles))}
                if CENTER not in cells:
                    valid = False
                    break
            if not apply_play(board, placement):
                valid = False
                break
            placed_any = True
        if valid:
            return order
    raise ValueError('Could not determine the order of plays')


def game_timeline(game, me):
    """Every turn of a game with the exact position the mover faced.

    Each turn carries the board before the move, the mover's rack, both
    scores, the bag count and (with an empty bag) how many final turns
    remained, so it can be handed straight to the Quackle worker.
    """
    order = turn_order(game)
    final_trays = {p['id']: p.get('tray') or '' for p in game['game_data']['players']}

    def rack_at(player_id, index):
        """The rack player_id held at turn index (before any later play)."""
        for j in range(index, len(order)):
            if order[j][0]['id'] == player_id:
                return order[j][1].get('tray') or ''
        return final_trays[player_id]

    board = [['.'] * 15 for _ in range(15)]
    scores = {p['id']: 0 for p in game['game_data']['players']}
    turns = []
    for index, (player, play) in enumerate(order):
        pid = player['id']
        other = next(o for o in scores if o != pid)
        rack = normalize_rack(play.get('tray'))
        on_board = sum(ch != '.' for row in board for ch in row)
        bag = TOTAL_TILES - on_board - len(rack) - len(rack_at(other, index))
        if bag < 0:
            raise ValueError('Tile counts do not add up')
        turns.append({
            'index': index,
            'mine': pid == me,
            'action': play['action'],
            'swapped_tiles': play.get('swapped_tiles'),
            'score': play_score(play['action']),
            'rack': rack,
            'grid': [''.join(row) for row in board],
            'mover_score': scores[pid],
            'other_score': scores[other],
            'bag_count': bag,
            'final_turns_remaining': min(len(order) - index, 2) if bag == 0 else 0,
        })
        placement = parse_play(play['action'])
        if placement and not apply_play(board, placement):
            raise ValueError(f'Play {play["action"]!r} does not fit the board')
        turns[-1]['grid_after'] = [''.join(row) for row in board]
        scores[pid] += turns[-1]['score']
    return turns


def split_players(game, me):
    players = {p['id']: p for p in game.get('players', [])}
    if me not in players:
        raise CrossplayError('You are not a player in this game', 403)
    opponent_id = next(pid for pid in players if pid != me)
    return players[me], players[opponent_id]


def summarize_game(game, me):
    mine, theirs = split_players(game, me)
    return {
        'id': game['id'],
        'status': game.get('game_status'),
        'opponent': theirs.get('username') or 'Opponent',
        'my_score': mine.get('score'),
        'opponent_score': theirs.get('score'),
        'turn_count': game.get('turn_count'),
        'timestamp': game.get('timestamp'),
    }


def game_review(game, me):
    """A completed game's summary plus every turn, for the review page."""
    if game.get('game_status') != 'completed':
        raise CrossplayError('Only finished games can be reviewed', 403)
    mine, theirs = split_players(game, me)
    review = summarize_game(game, me)
    review.update({
        'my_name': mine.get('username') or 'You',
        'turns': game_timeline(game, me),
    })
    return review


# ── NYT client ────────────────────────────────────────────────────────────


class CrossplayClient:
    def __init__(self, nyt_s, user_agent=DEFAULT_UA):
        self.nyt_s = nyt_s
        self.user_agent = user_agent
        self._lock = threading.Lock()
        self._me = None
        self._games = (0.0, None)

    def get(self, path):
        request = urllib.request.Request(NYT_BASE + path, headers={
            'Cookie': f'NYT-S={self.nyt_s}',
            'User-Agent': self.user_agent,
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read()
                if response.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise CrossplayError('NYT rejected the NYT-S cookie; paste a fresh one', 401)
            if e.code == 404:
                raise CrossplayError('Game not found', 404)
            raise CrossplayError(f'NYT returned HTTP {e.code}')
        except urllib.error.URLError as e:
            raise CrossplayError(f'Could not reach NYT: {e.reason}')

    def me(self):
        with self._lock:
            if self._me is None:
                player = self.get('/svc/games/player').get('player') or {}
                if not player.get('user_id'):
                    raise CrossplayError('NYT did not recognize this cookie; is it from a logged-in session?', 401)
                self._me = {'user_id': player['user_id'], 'username': player.get('username')}
            return self._me

    def recent_games(self):
        with self._lock:
            fetched_at, games = self._games
            if games is None or time.time() - fetched_at > LIST_CACHE_SECONDS:
                data = self.get('/svc/games/mp/subscribe/fallback')
                games = [g for msg in data.get('msgs', [])
                         for g in msg.get('recent-games', {}).get('games', [])
                         if g.get('game') == 'crossplay']
                self._games = (time.time(), games)
            return games

    def game(self, game_id):
        # The recent-games response includes the full play history for games
        # it returns. Use that payload when possible; some NYT clients deliver
        # game state through the subscription response rather than a separate
        # GET request.
        for game in self.recent_games():
            game_data = game.get('game_data') or {}
            players = game_data.get('players') or []
            has_play_history = len(players) == 2 and all(
                isinstance(player.get('plays'), list) for player in players
            )
            if str(game.get('id', '')).lower() == game_id and has_play_history:
                return game
        return self.get(f'/svc/games/mp/game/{game_id}')


# ── HTTP server ───────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    client_factory = CrossplayClient
    sessions = {}
    sessions_lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, format, *args):
        # Keep request lines but never echo bodies or headers.
        sys.stderr.write('%s - %s\n' % (self.address_string(), format % args))

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api_route(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if os.environ.get('VERCEL') and path == '/api/crossplay':
            return parse_qs(parsed.query).get('route', [''])[0].strip('/')
        if not path.startswith('/api/crossplay/'):
            return None
        return path[len('/api/crossplay/'):].strip('/')

    @staticmethod
    def authority_host(authority):
        try:
            return (urlsplit('//' + authority).hostname or '').lower().rstrip('.')
        except ValueError:
            return ''

    def allowed_host(self, host_header):
        host = self.authority_host(host_header)
        configured_hosts = os.environ.get('APP_ALLOWED_HOSTS', '').split(',')
        vercel_hosts = [
            os.environ.get('VERCEL_URL', ''),
            os.environ.get('VERCEL_PROJECT_PRODUCTION_URL', ''),
        ]
        allowed_hosts = {
            self.authority_host(value.strip())
            for value in [*vercel_hosts, *configured_hosts]
            if value.strip()
        }
        return host in LOCAL_HOSTS or bool(host and host in allowed_hosts)

    def same_origin_request(self, method):
        """Accept only this app's host and reject cross-site API mutations."""
        host = (self.headers.get('Host') or '').strip().lower()
        if not self.allowed_host(host):
            return False
        origin = self.headers.get('Origin')
        if origin:
            try:
                parsed_origin = urlsplit(origin)
            except ValueError:
                return False
            if parsed_origin.scheme not in ('http', 'https') or parsed_origin.netloc.lower() != host:
                return False
        elif method in ('PUT', 'POST', 'DELETE'):
            return False
        return True

    @classmethod
    def purge_sessions(cls, now):
        expired = [
            session_id for session_id, session in cls.sessions.items()
            if now > session['expires_at']
        ]
        for session_id in expired:
            del cls.sessions[session_id]

    def session_id_from_cookie(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie') or '')
        except CookieError:
            return None
        morsel = cookies.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    @staticmethod
    def supabase_config():
        url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
        publishable_key = os.environ.get('SUPABASE_PUBLISHABLE_KEY') or ''
        secret_key = os.environ.get('SUPABASE_SECRET_KEY') or ''
        if not url or not publishable_key or not secret_key:
            raise CrossplayError('Supabase server configuration is incomplete; set SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, and SUPABASE_SECRET_KEY in Vercel.', 503)
        return url, publishable_key, secret_key

    def current_supabase_user(self, required=True):
        authorization = (self.headers.get('Authorization') or '').strip()
        if not authorization.startswith('Bearer '):
            if required:
                raise CrossplayError('Sign in with Google to access saved games.', 401)
            return None
        url, publishable_key, _ = self.supabase_config()
        access_token = authorization[7:].strip()
        if not access_token:
            if required:
                raise CrossplayError('Your sign-in session is missing. Sign in again.', 401)
            return None
        request = urllib.request.Request(url + '/auth/v1/user', headers={
            'apikey': publishable_key,
            'Authorization': 'Bearer ' + access_token,
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                user = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise CrossplayError('Your Google sign-in expired. Sign in again.', 401)
            raise CrossplayError('Could not verify your Supabase sign-in.', 502)
        except (urllib.error.URLError, ValueError) as error:
            raise CrossplayError('Could not verify your Supabase sign-in.', 502)
        user_id = str(user.get('id') or '')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', user_id):
            raise CrossplayError('Supabase returned an invalid account.', 502)
        return {'id': user_id, 'email': str(user.get('email') or '')}

    def supabase_data_request(self, method, table, params=None, payload=None, prefer=None):
        url, _, secret_key = self.supabase_config()
        endpoint = url + '/rest/v1/' + table
        if params:
            endpoint += '?' + urlencode(params)
        headers = {
            'apikey': secret_key,
            'Accept': 'application/json',
        }
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(',', ':')).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if prefer:
            headers['Prefer'] = prefer
        request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise CrossplayError('Supabase persistence tables are not ready. Apply the migration in supabase/migrations.', 503)
            # Do not echo remote response bodies, which could contain request data.
            raise CrossplayError('Supabase could not save or load this account’s data (HTTP ' + str(error.code) + ').', 502)
        except (urllib.error.URLError, ValueError):
            raise CrossplayError('Could not reach Supabase. Try again in a moment.', 502)

    @staticmethod
    def cookie_encryption_key():
        value = os.environ.get('NYT_COOKIE_ENCRYPTION_KEY') or ''
        try:
            key = bytes.fromhex(value)
        except ValueError:
            key = b''
        if len(key) != 32:
            raise CrossplayError('NYT_COOKIE_ENCRYPTION_KEY must be a 64-character hex value generated for this app.', 503)
        return key

    def encrypt_nyt_cookie(self, user_id, value):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise CrossplayError('Cookie encryption is unavailable; install the runtime requirements and redeploy.', 503)
        nonce = os.urandom(12)
        encrypted = AESGCM(self.cookie_encryption_key()).encrypt(nonce, value.encode('utf-8'), user_id.encode('ascii'))
        return {
            'user_id': user_id,
            'ciphertext': base64.urlsafe_b64encode(encrypted).decode('ascii').rstrip('='),
            'nonce': base64.urlsafe_b64encode(nonce).decode('ascii').rstrip('='),
            'key_version': 1,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

    def decrypt_nyt_cookie(self, record, user_id):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            ciphertext = base64.urlsafe_b64decode(record['ciphertext'] + '=' * (-len(record['ciphertext']) % 4))
            nonce = base64.urlsafe_b64decode(record['nonce'] + '=' * (-len(record['nonce']) % 4))
            return AESGCM(self.cookie_encryption_key()).decrypt(nonce, ciphertext, user_id.encode('ascii')).decode('utf-8')
        except CrossplayError:
            raise
        except Exception:
            raise CrossplayError('The saved NYT cookie could not be decrypted. Check the encryption key configuration.', 503)

    def saved_nyt_cookie(self, user_id):
        rows = self.supabase_data_request('GET', 'nyt_credentials', {
            'select': 'ciphertext,nonce,key_version',
            'user_id': 'eq.' + user_id,
            'limit': '1',
        })
        if not rows:
            return None
        return self.decrypt_nyt_cookie(rows[0], user_id)

    def has_saved_nyt_cookie(self, user_id):
        rows = self.supabase_data_request('GET', 'nyt_credentials', {
            'select': 'user_id',
            'user_id': 'eq.' + user_id,
            'limit': '1',
        })
        return bool(rows)

    def save_nyt_cookie(self, user_id, value):
        record = self.encrypt_nyt_cookie(user_id, value)
        self.supabase_data_request('POST', 'nyt_credentials', {'on_conflict': 'user_id'}, record,
                                  'resolution=merge-duplicates,return=minimal')

    def delete_nyt_cookie(self, user_id):
        self.supabase_data_request('DELETE', 'nyt_credentials', {'user_id': 'eq.' + user_id})

    def persistent_games(self, user_id):
        rows = self.supabase_data_request('GET', 'crossplay_games', {
            'select': 'summary',
            'user_id': 'eq.' + user_id,
            'order': 'updated_at.desc',
        })
        return [row['summary'] for row in rows if isinstance(row.get('summary'), dict)]

    def game_page_parameters(self):
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        if 'page_size' not in query and 'after' not in query:
            return None
        sizes = query.get('page_size', [])
        cursors = query.get('after', [])
        if (len(sizes) != 1 or not re.fullmatch(r'[0-9]{1,3}', sizes[0])
                or not 1 <= int(sizes[0]) <= 100 or len(cursors) > 1
                or (cursors and not GAME_ID_RE.fullmatch(cursors[0]))):
            raise CrossplayError('Use page_size from 1 to 100 and a valid after game ID.', 400)
        return int(sizes[0]), cursors[0] if cursors else None

    def persistent_games_page(self, user_id, page_size, after=None):
        # Use an immutable unique ID; background saves change updated_at.
        params = {'select': 'nyt_game_id,summary', 'user_id': 'eq.' + user_id,
                  'order': 'nyt_game_id.asc', 'limit': str(page_size + 1)}
        if after is not None:
            params['nyt_game_id'] = 'gt.' + after
        rows = self.supabase_data_request('GET', 'crossplay_games', params)
        page = rows[:page_size]
        return {'games': [row['summary'] for row in page if isinstance(row.get('summary'), dict)],
                'next_cursor': page[-1]['nyt_game_id'] if len(rows) > page_size else None}

    def upsert_game_summaries(self, user_id, summaries):
        if not summaries:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = [{
            'user_id': user_id,
            'nyt_game_id': str(summary['id']),
            'summary': summary,
            'updated_at': now,
        } for summary in summaries if summary.get('id')]
        if rows:
            self.supabase_data_request('POST', 'crossplay_games', {'on_conflict': 'user_id,nyt_game_id'}, rows,
                                      'resolution=merge-duplicates,return=minimal')

    def upsert_game_review(self, user_id, review):
        summary = {key: review.get(key) for key in ('id', 'status', 'opponent', 'my_score', 'opponent_score', 'turn_count', 'timestamp')}
        self.upsert_game_summaries(user_id, [summary])
        self.supabase_data_request('PATCH', 'crossplay_games', {
            'user_id': 'eq.' + user_id,
            'nyt_game_id': 'eq.' + str(review['id']),
        }, {'review': review, 'updated_at': datetime.now(timezone.utc).isoformat()}, 'return=minimal')

    def saved_game_review(self, user_id, game_id):
        rows = self.supabase_data_request('GET', 'crossplay_games', {
            'select': 'review,share_token',
            'user_id': 'eq.' + user_id,
            'nyt_game_id': 'eq.' + game_id,
            'limit': '1',
        })
        if not rows or not rows[0].get('review'):
            return None
        review = dict(rows[0]['review'])
        review['share_token'] = rows[0].get('share_token')
        return review

    def game_share_token(self, user_id, game_id):
        # Only fill an empty token so concurrent requests keep the same URL.
        params = {'user_id': 'eq.' + user_id, 'nyt_game_id': 'eq.' + game_id}
        rows = self.supabase_data_request('PATCH', 'crossplay_games', {
            **params, 'share_token': 'is.null', 'select': 'share_token',
        }, {'share_token': secrets.token_urlsafe(32)}, 'return=representation')
        if not rows:
            rows = self.supabase_data_request('GET', 'crossplay_games', {
                **params, 'select': 'share_token', 'limit': '1',
            })
        if not rows or not rows[0].get('share_token'):
            raise CrossplayError('Game not found', 404)
        return rows[0]['share_token']

    def local_game_share_token(self, session_id, review):
        with Handler.sessions_lock:
            Handler.purge_sessions(time.time())
            session = Handler.sessions.get(session_id)
            if not session:
                raise CrossplayError('Connect your NYT cookie again to share this game.', 409)
            shares = session.setdefault('shared_reviews', {})
            for token, saved in shares.items():
                if saved['id'] == review['id']:
                    return token
            if len(shares) >= 256:
                raise CrossplayError('This local session has reached its shared game limit.', 503)
            token = secrets.token_urlsafe(32)
            shares[token] = dict(review)
            return token

    def shared_game_review(self, token):
        if not SHARE_TOKEN_RE.fullmatch(token):
            raise CrossplayError('Shared game not found. Check the link with its sender.', 404)
        review = None
        if os.environ.get('VERCEL'):
            rows = self.supabase_data_request('GET', 'crossplay_games', {
                'select': 'user_id,nyt_game_id,review',
                'share_token': 'eq.' + token,
                'limit': '1',
            })
            if rows and isinstance(rows[0].get('review'), dict):
                review = dict(rows[0]['review'])
                if review.get('status') == 'completed':
                    review['stored_analysis'] = self.saved_game_analysis(rows[0]['user_id'], rows[0]['nyt_game_id'])
        else:
            with Handler.sessions_lock:
                Handler.purge_sessions(time.time())
                for session in Handler.sessions.values():
                    saved = session.get('shared_reviews', {}).get(token)
                    if saved:
                        review = dict(saved)
                        break
        if not review or review.get('status') != 'completed':
            raise CrossplayError('Shared game not found. Check the link with its sender.', 404)
        review['share_token'] = token
        return review

    def saved_game_analysis(self, user_id, game_id):
        rows = self.supabase_data_request('GET', 'game_analyses', {
            'select': 'win_trend,move_quality,turn_analyses,analysis_version',
            'user_id': 'eq.' + user_id,
            'nyt_game_id': 'eq.' + game_id,
            'analysis_version': 'eq.' + ANALYSIS_VERSION,
            'limit': '1',
        })
        if not rows:
            return None
        return {
            'version': rows[0].get('analysis_version'),
            'win_trend': rows[0].get('win_trend'),
            'move_quality': rows[0].get('move_quality'),
            'turn_analyses': rows[0].get('turn_analyses') or {},
        }

    @staticmethod
    def validate_game_analysis(body):
        if body.get('analysis_version') != ANALYSIS_VERSION:
            raise CrossplayError('Unsupported analysis version.', 400)
        for field in ('win_trend', 'move_quality'):
            value = body.get(field)
            if field in body and (not isinstance(value, dict) or value.get('state') != 'done' or not isinstance(value.get('points'), list)):
                raise CrossplayError('Only completed game analyses can be saved.', 400)
        quality = body.get('move_quality')
        if quality and quality.get('evaluation') == 'win-probability-v1':
            Handler.validate_win_quality(quality)
        entries = body.get('turn_analyses', {})
        if not isinstance(entries, dict) or len(entries) > 512:
            raise CrossplayError('Invalid turn analysis cache.', 400)
        for key, result in entries.items():
            if not isinstance(key, str) or len(key) > 2048 or not isinstance(result, dict) or result.get('error') or not isinstance(result.get('moves'), list):
                raise CrossplayError('Invalid turn analysis result.', 400)
            try:
                identity = json.loads(key)
            except ValueError:
                identity = None
            is_review = isinstance(identity, list) and len(identity) > 1 and identity[1] == 'review'
            if is_review or 'samplingVersion' in result:
                Handler.validate_review_result(identity, result)
        return {field: body[field] for field in ('win_trend', 'move_quality', 'turn_analyses') if field in body}

    @staticmethod
    def finite_number(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False

    @staticmethod
    def recorded_move_matches(grid, action, swapped_tiles, move):
        if move['action'] == 'pass':
            return re.fullmatch(r'PASS(?:\s+-?\d+)?', action.strip(), re.IGNORECASE) is not None
        if move['action'] == 'exchange':
            return (re.fullmatch(r'(?:SWAP|EXCHANGE)(?:\s+-?\d+)?', action.strip(), re.IGNORECASE) is not None
                    and bool(re.fullmatch(r'[A-Za-z?*_ ]{1,7}', swapped_tiles))
                    and sorted(normalize_rack(swapped_tiles)) == sorted(move['tiles']))
        try:
            recorded = parse_play(action)
        except ValueError:
            return False
        # Quackle uses '.' for an existing board tile, whereas NYT uses brackets.
        across, down = ACROSS_RE.fullmatch(move['position']), DOWN_RE.fullmatch(move['position'])
        if not recorded or (not across and not down) or not re.fullmatch(r'[A-Za-z.]{1,15}', move['tiles']):
            return False
        row = int(across[1] if across else down[2]) - 1
        col = COLS.index(across[2] if across else down[1])
        candidate = (row, col, 0 if across else 1, 1 if across else 0,
                     [(letter, letter == '.') for letter in move['tiles']])

        def placements(parsed):
            row, col, dr, dc, tiles = parsed
            placed = []
            for index, (letter, existing) in enumerate(tiles):
                r, c = row + dr * index, col + dc * index
                if not (0 <= r < 15 and 0 <= c < 15):
                    return None
                board_letter = grid[r][c]
                if board_letter == '.':
                    if existing or letter == '.':
                        return None
                    placed.append((r, c, letter))
                elif letter != '.' and letter.upper() != board_letter.upper():
                    return None
            return sorted(placed) or None

        actual = placements(recorded)
        return actual is not None and actual == placements(candidate)

    @staticmethod
    def validate_review_result(identity, result):
        def invalid():
            raise CrossplayError('Invalid win-probability analysis result.', 400)

        if (not isinstance(identity, list) or len(identity) != 15
                or identity[:2] != ['quackle-review-v1', 'review']
                or identity[8:12] != [15, 277, 2, 21]
                or identity[14] != 'paired-review-v1'
                or not all(isinstance(value, str) for value in identity[12:14])):
            invalid()
        grid, rack, score, other, bag, final = identity[2:8]
        if (not isinstance(grid, list) or len(grid) != 15
                or not all(isinstance(row, str) and re.fullmatch(r'[A-Za-z.]{15}', row) for row in grid)
                or not isinstance(rack, str) or not re.fullmatch(r'[A-Z?]{1,7}', rack)
                or not all(Handler.finite_number(value) for value in (score, other))
                or type(bag) is not int or not 0 <= bag <= 100 or type(final) is not int
                or (final not in (1, 2) if bag == 0 else final != 0)):
            invalid()
        play_to_end = bag <= 21
        moves = result.get('moves')
        integer_fields = ('bagCount', 'finalTurnsRemaining', 'plies', 'iterationsRequested',
                          'iterationsCompleted', 'candidateCount', 'totalPlayaheads')
        if (not isinstance(moves, list) or not 1 <= len(moves) <= 16
                or result.get('simulated') is not True
                or result.get('samplingVersion') != 'paired-review-v1'
                or type(result.get('seed')) is not int or not 0 <= result['seed'] < 2 ** 32
                or not all(type(result.get(field)) is int for field in integer_fields)
                or result.get('bagCount') != bag or result.get('finalTurnsRemaining') != final
                or result.get('playToEnd') is not play_to_end
                or result.get('simulationMode') != ('play-to-end' if play_to_end else '2-ply')
                or result.get('winModel') != ('terminal' if play_to_end else 'bogowin')
                or result.get('plies') != (-1 if play_to_end else 2)
                or result.get('iterationsRequested') != 277 or result.get('iterationsCompleted') != 277
                or result.get('candidateCount') != len(moves)
                or result.get('totalPlayaheads') != 277 * len(moves)):
            invalid()
        for move in moves:
            if (not isinstance(move, dict) or move.get('action') not in ('place', 'pass', 'exchange')
                    or not all(isinstance(move.get(field), str) for field in ('position', 'tiles'))
                    or not all(Handler.finite_number(move.get(field)) for field in ('win', 'equity', 'staticEquity'))
                    or not 0 <= move['win'] <= 1 or type(move.get('samples')) is not int or move['samples'] != 277
                    or (play_to_end and (type(move.get('terminalSamples')) is not int or move['terminalSamples'] != 277))):
                invalid()
        if (result.get('selected') not in moves or result.get('best') not in moves
                or result['best']['win'] != max(move['win'] for move in moves)):
            invalid()
        if not Handler.recorded_move_matches(grid, identity[12], identity[13], result['selected']):
            invalid()
        static_moves = result.get('staticMoves')
        if not isinstance(static_moves, list) or len(static_moves) != len(moves):
            invalid()
        for move in moves:
            matches = [candidate for candidate in static_moves if isinstance(candidate, dict)
                       and all(candidate.get(field) == move[field] for field in ('action', 'position', 'tiles'))]
            if (len(matches) != 1 or not Handler.finite_number(matches[0].get('equity'))
                    or matches[0]['equity'] != move['staticEquity']):
                invalid()
        if (not Handler.finite_number(result.get('bestStaticEquity'))
                or result['bestStaticEquity'] != max(move['equity'] for move in static_moves)):
            invalid()

    @staticmethod
    def validate_win_quality(quality):
        def invalid():
            raise CrossplayError('Invalid win-probability move quality.', 400)

        if len(quality['points']) > 512:
            invalid()
        for point in quality['points']:
            if not isinstance(point, dict):
                invalid()
            if point.get('category') == 'unrated':
                if (point.get('loss') is not None or not isinstance(point.get('reason'), str)
                        or not point['reason'] or point.get('playedWin') is not None or point.get('bestWin') is not None):
                    invalid()
                continue
            fields = ('playedWin', 'bestWin', 'loss', 'playedEquity', 'bestEquity')
            if (not all(Handler.finite_number(point.get(field)) for field in fields)
                    or not 0 <= point['playedWin'] <= point['bestWin'] <= 1):
                invalid()
            played_move = point.get('playedMove')
            action = played_move.get('action') if isinstance(played_move, dict) else None
            equity_loss = point.get('equityLoss')
            if equity_loss is None:
                if action not in ('pass', 'exchange'):
                    invalid()
            elif not Handler.finite_number(equity_loss) or equity_loss < 0:
                invalid()
            loss = max(0, point['bestWin'] - point['playedWin']) * 100
            category = 'best' if loss < 2 else 'inaccuracy' if loss < 5 else 'mistake' if loss < 10 else 'blunder'
            if abs(point['loss'] - loss) > 1e-8 or point.get('category') != category:
                invalid()

    def saved_review_status(self, user_id, game_id):
        # Saves happen during analysis, so read only the status rather than the whole review.
        rows = self.supabase_data_request('GET', 'crossplay_games', {
            'select': 'status:review->>status',
            'user_id': 'eq.' + user_id,
            'nyt_game_id': 'eq.' + game_id,
            'limit': '1',
        })
        return rows[0].get('status') if rows else None

    def save_game_analysis(self, user_id, game_id, body):
        snapshot = self.validate_game_analysis(body)
        if self.saved_review_status(user_id, game_id) != 'completed':
            raise CrossplayError('Import this finished game before saving analysis.', 404)
        self.supabase_data_request('POST', 'rpc/save_crossplay_analysis', payload={
            'account_id': user_id, 'game_id': game_id,
            'version': ANALYSIS_VERSION, 'snapshot': snapshot,
        })

    def refresh_persistent_games(self, user_id, if_stale=False):
        if if_stale:
            rows = self.supabase_data_request('GET', 'crossplay_refresh_state', {
                'select': 'refreshed_at', 'user_id': 'eq.' + user_id, 'limit': '1',
            })
            if rows:
                refreshed = datetime.fromisoformat(rows[0]['refreshed_at'].replace('Z', '+00:00'))
                if time.time() - refreshed.timestamp() < DAILY_REFRESH_SECONDS:
                    return self.persistent_games(user_id)
        _, client = self.current_session()
        if client is None:
            raise CrossplayError('Connect your NYT cookie before importing recent games.', 409)
        me = client.me()
        summaries = [summarize_game(game, me['user_id']) for game in client.recent_games()]
        self.upsert_game_summaries(user_id, summaries)
        self.supabase_data_request('POST', 'crossplay_refresh_state', {'on_conflict': 'user_id'}, {
            'user_id': user_id, 'refreshed_at': datetime.now(timezone.utc).isoformat(),
        }, 'resolution=merge-duplicates,return=minimal')
        return self.persistent_games(user_id)

    def current_session(self):
        if os.environ.get('VERCEL'):
            user = self.current_supabase_user(required=False)
            if not user:
                return None, None
            value = (self.headers.get('X-NYT-S') or '').strip()
            if not COOKIE_RE.fullmatch(value):
                value = self.saved_nyt_cookie(user['id']) or ''
            if not value:
                return user['id'], None
            return user['id'], self.client_factory(value, os.environ.get('CROSSPLAY_UA', DEFAULT_UA))
        session_id = self.session_id_from_cookie()
        if not session_id:
            return None, None
        now = time.time()
        with Handler.sessions_lock:
            Handler.purge_sessions(now)
            session = Handler.sessions.get(session_id)
            if not session:
                return None, None
            return session_id, session['client']

    def save_session(self, client, previous_session_id=None):
        now = time.time()
        with Handler.sessions_lock:
            Handler.purge_sessions(now)
            if previous_session_id in Handler.sessions:
                del Handler.sessions[previous_session_id]
            if len(Handler.sessions) >= MAX_SESSIONS:
                raise CrossplayError('The app is at its active session limit; try again later', 503)
            session_id = secrets.token_urlsafe(32)
            Handler.sessions[session_id] = {
                'client': client,
                'expires_at': now + SESSION_TTL_SECONDS,
            }
            return session_id

    @staticmethod
    def session_cookie(session_id, max_age):
        cookie = f'{SESSION_COOKIE}={session_id}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax'
        if os.environ.get('CROSSPLAY_SECURE_COOKIE', '').lower() in ('1', 'true', 'yes'):
            cookie += '; Secure'
        return cookie

    def do_GET(self):
        if self.path.split('?', 1)[0] == '/health':
            return self.send_json(200, {'ok': True})
        route = self.api_route()
        if route is not None:
            return self.handle_api('GET', route)
        # Decode before checking so a request such as /%2eenv cannot bypass
        # the dotfile and private-directory guard below.
        request_path = self.path.split('?', 1)[0]
        while True:
            decoded_path = unquote(request_path)
            if decoded_path == request_path:
                break
            request_path = decoded_path
        segments = [s for s in request_path.split('/') if s]
        private_files = {
            'AGENTS.md', 'Dockerfile', 'README.md', 'requirements-test.txt',
            'package.json', 'package-lock.json', '.dockerignore', '.gitignore',
        }
        if (any(s.startswith('.') for s in segments)
                or (segments and segments[0] in ('captures', 'tools'))
                or (segments and (segments[0] in private_files or segments[0].endswith('.py')))):
            return self.send_error(404)
        return super().do_GET()

    def do_PUT(self):
        return self.handle_api('PUT', self.api_route())

    def do_POST(self):
        return self.handle_api('POST', self.api_route())

    def do_DELETE(self):
        return self.handle_api('DELETE', self.api_route())

    def read_json_body(self, max_bytes=16384):
        if 'application/json' not in (self.headers.get('Content-Type') or ''):
            raise CrossplayError('Expected a JSON body', 415)
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise CrossplayError('Bad request body', 400)
        if not 0 < length <= max_bytes:
            raise CrossplayError('Bad request body', 400)
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            raise CrossplayError('Bad request body', 400)
        if not isinstance(body, dict):
            raise CrossplayError('Expected a JSON object', 400)
        return body

    def handle_api(self, method, route):
        if route is None:
            return self.send_error(405)
        if not self.same_origin_request(method):
            return self.send_json(403, {'error': 'The API only accepts same-origin requests'})
        try:
            match = re.fullmatch(r'shared(?:/([^/]*))?', route)
            if match:
                if method != 'GET':
                    return self.send_json(405, {'error': 'Shared games are read-only'})
                return self.send_json(200, self.shared_game_review(match.group(1) or ''))
            if route == 'status' and method == 'GET':
                if os.environ.get('VERCEL'):
                    user = self.current_supabase_user(required=False)
                    if not user:
                        return self.send_json(200, {'configured': False, 'auth_required': True, 'authenticated': False})
                    header_cookie = (self.headers.get('X-NYT-S') or '').strip()
                    saved_cookie = self.has_saved_nyt_cookie(user['id']) if not COOKIE_RE.fullmatch(header_cookie) else False
                    return self.send_json(200, {
                        'configured': bool(saved_cookie or COOKIE_RE.fullmatch(header_cookie)),
                        'auth_required': True,
                        'authenticated': True,
                        'email': user['email'],
                    })
                _, client = self.current_session()
                return self.send_json(200, {'configured': client is not None})
            if route == 'token':
                return self.handle_token(method)
            if os.environ.get('VERCEL'):
                return self.handle_persistent_api(method, route)
            match = re.fullmatch(r'games/([a-z0-9]{6,32})/analysis', route)
            if match and method == 'PUT':
                session_id, client = self.current_session()
                if client is None:
                    raise CrossplayError('Paste your NYT-S cookie first', 409)
                snapshot = self.validate_game_analysis(self.read_json_body(max_bytes=2097152))
                with Handler.sessions_lock:
                    session = Handler.sessions.get(session_id)
                    saved = next((review for review in session.get('shared_reviews', {}).values()
                                  if review['id'] == match.group(1)), None) if session else None
                    if saved is None:
                        raise CrossplayError('Import this finished game before saving analysis.', 404)
                    analysis = saved.get('stored_analysis') or {'version': ANALYSIS_VERSION}
                    saved['stored_analysis'] = {**analysis, **snapshot, 'turn_analyses': {
                        **analysis.get('turn_analyses', {}), **snapshot.get('turn_analyses', {}),
                    }}
                return self.send_json(200, {'saved': True, 'analysis_version': ANALYSIS_VERSION})
            if method != 'GET':
                return self.send_json(405, {'error': 'Method not allowed'})
            session_id, client = self.current_session()
            if client is None:
                return self.send_json(409, {'error': 'Paste your NYT-S cookie first'})
            me = client.me()
            if route == 'me':
                return self.send_json(200, me)
            if route == 'games':
                games = client.recent_games()
                summaries = [summarize_game(g, me['user_id']) for g in games]
                page = self.game_page_parameters()
                if page is None:
                    return self.send_json(200, {'games': summaries})
                page_size, after = page
                remaining = sorted((game for game in summaries if after is None or game['id'] > after),
                                   key=lambda game: game['id'])
                return self.send_json(200, {
                    'games': remaining[:page_size],
                    'next_cursor': remaining[page_size - 1]['id'] if len(remaining) > page_size else None,
                })
            match = re.match(r'^games/([^/]+)/review$', route)
            if match:
                game_id = match.group(1).lower()
                if not GAME_ID_RE.match(game_id):
                    return self.send_json(400, {'error': 'Invalid game ID'})
                review = game_review(client.game(game_id), me['user_id'])
                review['share_token'] = self.local_game_share_token(session_id, review)
                review['stored_analysis'] = self.shared_game_review(review['share_token']).get('stored_analysis')
                return self.send_json(200, review)
            return self.send_json(404, {'error': 'Unknown endpoint'})
        except CrossplayError as e:
            return self.send_json(e.status, {'error': str(e)})
        except (KeyError, ValueError, StopIteration, TypeError) as e:
            return self.send_json(502, {'error': f'Unexpected game data: {e}'})

    def handle_persistent_api(self, method, route):
        user = self.current_supabase_user(required=True)
        user_id = user['id']
        if route == 'me' and method == 'GET':
            _, client = self.current_session()
            if client is None:
                return self.send_json(409, {'error': 'Connect your NYT cookie before loading your account.'})
            return self.send_json(200, client.me())
        if route == 'games' and method == 'GET':
            page = self.game_page_parameters()
            if page is not None:
                return self.send_json(200, self.persistent_games_page(user_id, *page))
            return self.send_json(200, {'games': self.persistent_games(user_id)})
        if route == 'games/refresh' and method == 'POST':
            body = self.read_json_body() if int(self.headers.get('Content-Length') or 0) else {}
            return self.send_json(200, {'games': self.refresh_persistent_games(user_id, body.get('if_stale') is True)})
        match = re.match(r'^games/([^/]+)/review$', route)
        if match and method == 'GET':
            game_id = match.group(1).lower()
            if not GAME_ID_RE.match(game_id):
                return self.send_json(400, {'error': 'Invalid game ID'})
            review = self.saved_game_review(user_id, game_id)
            if review is None:
                _, client = self.current_session()
                if client is None:
                    return self.send_json(409, {'error': 'Connect your NYT cookie to import this game review.'})
                me = client.me()
                review = game_review(client.game(game_id), me['user_id'])
                self.upsert_game_review(user_id, review)
            if review.get('status') != 'completed':
                raise CrossplayError('Only finished games can be reviewed', 403)
            if not review.get('share_token'):
                review['share_token'] = self.game_share_token(user_id, game_id)
            review['stored_analysis'] = self.saved_game_analysis(user_id, game_id)
            return self.send_json(200, review)
        match = re.match(r'^games/([^/]+)/analysis$', route)
        if match and method == 'PUT':
            game_id = match.group(1).lower()
            if not GAME_ID_RE.match(game_id):
                return self.send_json(400, {'error': 'Invalid game ID'})
            body = self.read_json_body(max_bytes=2097152)
            self.save_game_analysis(user_id, game_id, body)
            return self.send_json(200, {'saved': True, 'analysis_version': ANALYSIS_VERSION})
        return self.send_json(404 if method in ('GET', 'POST', 'PUT', 'DELETE') else 405,
                              {'error': 'Unknown persistence endpoint'})

    def handle_token(self, method):
        if method == 'DELETE':
            if os.environ.get('VERCEL'):
                user = self.current_supabase_user(required=True)
                self.delete_nyt_cookie(user['id'])
                return self.send_json(200, {'configured': False, 'remembered': False})
            session_id = self.session_id_from_cookie()
            if session_id:
                with Handler.sessions_lock:
                    Handler.sessions.pop(session_id, None)
            return self.send_json(200, {'configured': False}, {
                'Set-Cookie': self.session_cookie('', 0),
            })
        if method != 'PUT':
            return self.send_json(405, {'error': 'Method not allowed'})
        user = self.current_supabase_user(required=True) if os.environ.get('VERCEL') else None
        body = self.read_json_body()
        value = str(body.get('nyt_s') or '').strip()
        value = re.sub(r'^NYT-S=', '', value).strip().strip('"')
        if not COOKIE_RE.match(value):
            return self.send_json(400, {
                'error': 'Paste only the NYT-S cookie value, not the full Cookie header'
            })
        client = self.client_factory(value, os.environ.get('CROSSPLAY_UA', DEFAULT_UA))
        me = client.me()  # validates the cookie before saving it
        if os.environ.get('VERCEL'):
            remember = body.get('remember', True) is True
            if remember:
                self.save_nyt_cookie(user['id'], value)
            else:
                self.delete_nyt_cookie(user['id'])
            return self.send_json(200, {'configured': True, 'remembered': remember, **me})
        previous_session_id, _ = self.current_session()
        session_id = self.save_session(client, previous_session_id)
        return self.send_json(200, {'configured': True, **me}, {
            'Set-Cookie': self.session_cookie(session_id, SESSION_TTL_SECONDS),
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', nargs='?', type=int, default=int(os.environ.get('PORT', '8000')))
    parser.add_argument('--mock-nyt', action='store_true',
                        help='Use synthetic NYT responses without credentials or network access')
    args = parser.parse_args()
    handler = Handler
    if args.mock_nyt:
        if os.environ.get('VERCEL'):
            parser.error('--mock-nyt is only available for local development')
        from tools.mock_nyt import MOCK_COOKIE, MockHandler
        handler = MockHandler
        print(f'MOCK NYT: synthetic data only. Connect with {MOCK_COOKIE}', flush=True)
    host = os.environ.get('HOST', '127.0.0.1')
    server = ThreadingHTTPServer((host, args.port), handler)
    print(f'Serving on {host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
