# Crossplay Board Analyzer

Review completed NYT Crossplay games, replay turns, and compare plays with Quackle. You can also parse screenshots locally in the browser or enter a board by hand.

## Run locally

Use Python 3.10 or newer:

```bash
python3 crossplay_server.py 8000
```

Open [http://localhost:8000](http://localhost:8000). On the game review page, paste the `NYT-S` cookie from a signed-in NYTimes browser session. The local server validates it with NYT and keeps it in memory for that browser's session. Use **Forget cookie** to remove it. After restarting the server, connect again.

The recent games list comes from NYT's Crossplay service and only completed games can be reviewed. NYT may change its service endpoints or session requirements.

Each game review shows the result, scores, points per turn, and a score timeline. Background analysis covers every saved completed game for the signed-in account while the page is open. The visible turn and custom moves have priority over the other turns in the open game and other saved games. Each analysis page uses one shared pool of up to three Quackle workers. Phones and low-memory devices use up to two. Background turn jobs can use all available workers. Foreground requests run first when a worker is free. You can replay moves while estimates finish. The game list shows progress and a retry button for failed imports, calculations, or saves. Personal summaries exclude opponent moves and unrated turns.

Choose **Start walkthrough** to review the game from the first turn. Step forward or backward, jump to the first or last move, or use the arrow keys and Home/End while focused in the replay. After analyzing move quality, use **Previous flagged move** / **Next flagged move** or **Only your flagged moves** to revisit your inaccuracies, mistakes, and blunders in game order. Opponent moves remain available in the full turn list.

Each valid placement, pass, or exchange is simulated from the board, rack, score, bag count, and final-turn state before that move. An **Early estimate** compares the top 5 static candidates plus the recorded move with 64 samples each. The queue gives each pending turn in the open game an early estimate before full analysis. The **Full estimate** compares the top 15 static candidates plus the recorded move, even when that move falls outside the shortlist, with 277 samples each outside the late game and with an empty bag. With 1–7 bag tiles, full estimates use 192 samples on all devices. With 8–21 bag tiles, they use 192 samples on devices with two or three workers and 96 on one-worker devices. Smaller sample counts have more sampling uncertainty. With one through seven bag tiles, it combines the original top 15 with the top 15 under a Crossplay-specific endgame equity estimate, plus the recorded move. This is a shortlist, not a search of every bag-emptying placement. It processes these candidates in small batches and completes only after every required candidate has the full sample count for its bag-size profile. Best means best among analyzed moves. Both passes use the same seed and common initial hidden racks and bag orders. Above 21 tiles in the bag, Quackle uses two-ply play and its win model. At 21 or fewer, it plays to the Crossplay finish. Future choices follow an approximate policy; every sample must reach the official finish. Ties count as half a win. Invalid positions, missing exchange tiles, and words absent from the engine dictionary remain unrated with a reason. When an exchange omits its swapped tiles, position alternatives are still shown and the rest of the review continues; no win estimate is assigned to the unknown exchange.

Early estimates can change in both value and grade. They reduce the wait for a first result; the extra pass adds work before full analysis is complete. In two synthetic early-game positions, the early pass took 0.84–1.44 seconds, versus 8.93–13.44 seconds for full analysis. These are local Chromium timings, not a guarantee for all devices or positions. Both passes retain the stated sample counts.

Move quality measures the gap between the highest sampled win estimate and the recorded move, in percentage points (pp). For example, 60% versus 45% is a loss of 15 pp. The provisional bands are Best <2 pp, Inaccuracy 2–<5, Mistake 5–<10, and Blunder ≥10. These estimates have sampling noise and are not calibrated predictions of human play. The grade, both move estimates, and sample count appear in the review. **Preview best move** places the alternative on the board; **Return to played move** restores the replay. Static equity remains separate for Luck & performance, which uses full results only.

**Win chance through the game** shows a full-game chart beside the white/black bar. The chart starts with a gray triangle for the opening racks and follows your chance through the recorded moves to the known final result, with a fixed 0–100% scale and a 50% reference line. Select a point with the mouse or keyboard to review its turn. Passes and exchanges keep their own turns. Early estimates have separate markers, and missing estimates leave gaps. The bar follows the selected turn; white shows your chance and black shows your opponent's. **Preview best move** switches the bar to that alternative's win estimate; **Return to played move** restores the recorded move's estimate. The chart keeps showing recorded-move estimates during previews. The actual final board shows the known game result before analysis finishes; a draw is shown as 50%. Previewing an alternative on the last turn still shows its estimate. Move estimates use the recorded move. The separate gray triangle shows the estimated balance after both opening racks, before Turn 1; select it to inspect the racks and each player’s opening draw WPA.

Select any move in the Quackle analysis table to preview its tiles and win estimate on the board. Rows support mouse and keyboard selection, including passes and exchanges. **Return to played move** restores the recorded position.

From a preview, choose **Explore continuations** to play out a hypothetical line. Each selected reply updates the board, racks, bag, and scores and generates fresh replies for the other player. **Play best reply** selects the highest simulated-win reply; **Undo reply** lets you try a different branch. The starting opponent rack comes from their next recorded turn when available; otherwise it is sampled. Future draws use one reproducible sample of the remaining physical Crossplay tiles. This is one possible line, not a prediction of future draws or optimal play. Each reply shows the current mover’s simulated win percentage, ranked highest first, alongside static equity. Estimates compare up to 15 static candidates plus pass using the same paired-sample profile as game review (96, 192, or 277 samples), with two-ply play above 21 bag tiles and play to the Crossplay finish at 21 or fewer. Ties count as half a win. These estimates sample hidden opponent racks and future draws rather than fixing them to the displayed hypothetical line. Undo reuses that position’s completed estimates; a different move gets fresh simulations. Play continues through the two final turns after the bag empties. Continuations stay local to the open preview.

Opening racks count as one draw event per player in **Rack Draws WPA**, in addition to subsequent refills. The opening calculation keeps the recorded first mover fixed. It compares random initial racks, each recorded rack on its own, and both recorded racks together, then averages the two reveal orders. This keeps the normal first-move advantage out of rack luck. The engine selects the opening with its static-best policy, without using the other player's rack to choose that move, then plays two future moves and uses its win model. Early results use 64 paired samples; full results use 277. These are model estimates with sampling noise.

Both original seven-tile racks must be available and physically possible. Missing opening data remains unavailable; a later rack is not used as a substitute. The joint opening estimate knows both racks, while move/refill estimates use the mover's information. Changes in the chart therefore do not form an exact decomposition of gameplay and draw WPA. Opening results use a separate versioned cache, so existing move and refill analyses are retained.

Saved per-turn results are reused across navigation and later visits. Partial runs resume missing results. Old equity-based grades are rebuilt automatically, and never shown as win-probability loss. Saved history is read in pages, so processing is not limited to the first database page. A saved game with no imported review needs a connected NYT session; the app reports the missing review and can retry after connection. Shared visitors can analyze only the shared game and cannot save results or list an owner's other games. Signing out or changing accounts cancels account work.

**Luck & performance** estimates how favorable your racks were compared with
2,048 reproducible samples of the same size from Crossplay tiles not on the
board. The tile-flexibility heuristic rewards blanks, S, balanced vowels and
consonants, and flexible letters, while penalizing duplicates and awkward
combinations. A score of 50 is typical. This is rack ease, not pure draw luck:
retained tiles reflect previous choices, and repeated racks are not independent.
The estimate does not model board openings or opponent decisions.

**Analyze move quality** also produces execution and adjusted performance scores
from 0–100. Move quality uses Quackle's deterministic position evaluation rather
than sampled draws. Execution converts each rated turn's Quackle equity loss with
`100 / (1 + loss / 10)`. Adjusted performance discounts less-common candidate
words using the bundled wordfreq list, then gives tougher racks up to twice the
weight. Unknown words receive no discount. Coverage, a replayable turn breakdown,
frequency attribution, and the full provisional formulas appear in the panel.
These scores do not assign a percentage of the result to skill or luck. Existing
saved reviews still work; re-analyze moves to add word-opportunity data. New data
is saved with move-quality results and is available on shared reviews.

Opening a finished game puts its shareable URL in the address bar. Send that URL to a friend, or expand **Share this game** and use **Copy game link**. Anyone with the link can replay that game and view player names, scores, racks, and saved analysis without signing in or connecting an NYT account. Links use a random token and grant access only to that game. Guests can run Quackle in their own browser; their analysis does not change the owner's saved results.

Hosted links persist in Supabase. Apply all migrations in `supabase/migrations/` before deploying this feature. Local links last until the owner's local session expires, its cookie is forgotten or replaced, or the server restarts; recipients must be able to reach the server's address. For synthetic data without real credentials, use `npm run frontend` as described under Tests.

The screenshot parser is at `/screenshot.html` and the manual board page is at `/manual.html`. Screenshot parsing and the bundled Quackle engine run locally in the browser.

Use **Try a custom move** on any analysis page to evaluate a word outside
Quackle’s suggested moves. Enter the word, its start square (for example, `G8`),
and Across or Down. Include existing board letters or use `.` for them; uppercase
letters use regular rack tiles and lowercase letters use blanks. Game reviews
evaluate against the board and rack **before** the selected turn.

The result compares score, rack leave, and static equity with the best legal
move. **Estimate win% (slower)** uses the same sample profile as game review and compares
simulated equity and win chances. The comparison includes the custom move and
the leading static candidates. With one through seven bag tiles, it combines the original shortlist with
the leading moves under the endgame equity estimate. The manual and screenshot
pages use the same search for generated moves and use seed 123456789 for paired
terminal samples. You can cancel a custom evaluation while other analysis
continues. Custom results stay in the current page and do not alter the
recorded game or saved move-quality review.

Game imports refresh automatically when the signed-in account has not refreshed successfully in 24 hours. The page checks on startup, hourly while open, and when returning to the tab; manual Refresh always imports immediately. Saved history stays visible during background refreshes and after a refresh failure. Each import reports the number of games NYT returned, new and changed games, the newest returned game date, and the check time. An incomplete NYT response is an error and does not mark an import successful. An explicit empty games list is valid. Successful refresh times are stored per Supabase user, including accounts with no games. Local sessions refresh daily while open.

Automatic processing runs while the page is open and catches up on the next visit. Analysis details list the game, turn, and reason for each failure. Unrated moves have no win estimate; only retryable analysis, load, or save failures need a retry. Retry background analysis preserves successful moves and retries failed saves without running those moves again. Import results are separate from analysis results. New engine results are saved in batches every few seconds, when a game finishes, when you switch games, and when the tab is hidden; results that were restored from a save are not sent again. There is no unattended server job or daily Quackle rerun: finished-game results are reused until their position or engine parameters change. A server queue and compute worker would be needed to analyze games with every browser closed. Apply `supabase/migrations/20261027000000_background_analysis.sql` before deploying: it adds per-turn results, an atomic result-merge function, and per-user refresh times. The browser never stores NYT credentials in local storage.

## Datadog

All integrations target the US5 site, `us5.datadoghq.com`, with service
`crossplay-analyzer`. Browser monitoring uses environment `production`; tests
use `ci`.

### Browser RUM and Error Tracking

1. Create a **Browser** RUM application in Datadog. In the shared
   `datadog-rum.js` file, replace both public placeholder values:
   - `REPLACE_WITH_DATADOG_RUM_APPLICATION_ID`: the RUM application ID.
   - `REPLACE_WITH_DATADOG_RUM_CLIENT_TOKEN`: the RUM client token.
2. Redeploy. `index.html`, `screenshot.html`, and `manual.html` load that shared
   file, which loads the US5 Browser RUM v6 SDK asynchronously from Datadog's
   CDN. Until **both** placeholders are filled, it loads no SDK and sends no
   telemetry. Allow `www.datadoghq-browser-agent.com` and Datadog US5 RUM intake
   in any deployment content security policy or network allow list.
3. Verify page views, resources, and browser errors in RUM / Error Tracking.

The application ID and client token are public browser configuration. **Never
put `DD_API_KEY` or `DD_APP_KEY` in frontend files.** Session Replay is disabled
(`sessionReplaySampleRate: 0`), `defaultPrivacyLevel: 'mask'` masks all content
and inputs, and the NYT-S form has `data-dd-privacy="hidden"` as an additional
protection. Do not add cookies, request headers/bodies, or authentication data
to RUM context or error messages. Screenshot processing and Quackle remain
local; configured RUM sends browser diagnostics to Datadog.

RUM injects Datadog and W3C trace context only into same-origin
`/api/crossplay` and `/api/crossplay/...` requests. Other origins, including
NYTimes and Supabase, do not receive these tracing headers. To see linked
backend spans, the deployed API must also have Datadog APM ingestion configured
and extract this incoming trace context; the browser configuration alone does
not create backend traces. Backend APM deployment setup is separate from this
repository's browser and CI instrumentation.

### GitHub Actions: pipelines, Test Optimization, and Code Security

Add these **repository Actions secrets** from the same Datadog organization:

| Secret | Required for |
| --- | --- |
| `DD_API_KEY` | Agentless Python test reporting, pipeline custom spans, SAST, and SCA |
| `DD_APP_KEY` | SAST and SCA, with the Code Security access required by the Datadog actions |

In Datadog, connect this repository through the **GitHub integration**, enable
**CI Visibility / Pipeline Visibility** for GitHub Actions, and enable **Test
Optimization** and **Code Security** for the repository. The GitHub integration
supplies full workflow/job/step events. The API key and `datadog-ci trace`
custom span in `test.yml` do not replace that integration.

The existing Test workflow runs `npm test`, covering the API, sharing, game
review, browser pipeline, grid-detection, score-OCR, and RUM checks. The Python
suites run through pytest, with adapters for the standalone OCR/grid scripts.
When `DD_API_KEY` is present, `PYTEST_ADDOPTS=--ddtrace` sends individual Python
test results directly to Datadog, and `datadog-ci trace` adds a regression step
span to the pipeline. The adapters preserve each script's exit status
and execute inside pytest so coverage can be associated with each test;
they do not change OCR or grid checks. RUM privacy and trace-routing checks
run with `npm run test:rum`.

The Datadog Code Security workflow runs the official SAST and SCA actions on
pushes, manual dispatch, and weekly. Datadog Code Security rejects
`pull_request` events, so branch updates use only `push` to avoid duplicate
scans. The Test workflow continues to run on pull requests. SAST uses the Python
and JavaScript security rules in `static-analysis.datadog.yml`; SCA scans dependency
manifests. The test tracer has an exact version in `requirements-test.txt`
because SCA omits ranged versions. SCA still runs if SAST fails. Without the
repository secrets (including fork pull requests), Python tests run without Datadog and security
scans are explicitly skipped. Neither workflow uses `pull_request_target`,
and both only request read access to repository contents.

For local validation:

```bash
npm run frontend                 # mock API/dev server on port 8000
npm run test:rum                 # no npm dependencies or Datadog credentials
npm test                         # full Python and RUM presubmit suite
```

The Python suites need `requirements-test.txt` and a Playwright Chromium
installation, as described below. Local tests do not upload to Datadog unless
explicitly instrumented and given credentials.

## Deploy to Vercel

Vercel serves the HTML and analyzer assets as static files and runs the game-review API as a Python Function. [`vercel.json`](vercel.json) configures the same-origin API route, function region, duration, and bundle exclusions. The function uses `cryptography` from `requirements.txt` to encrypt remembered NYT cookies. Vercel's Python runtime is [generally available](https://vercel.com/changelog/python-on-vercel-is-now-generally-available).

1. Connect a GitHub repository in Vercel, or install the Vercel CLI and sign in:

   ```bash
   npm install --global vercel
   vercel login
   vercel link
   ```

   You can deploy this local checkout with the CLI without forking Kyle's repository. If you want GitHub to trigger deployments automatically, connect a repository your GitHub account can access; fork it if you don't have access to the original.

2. Keep the project root as the repository root and the framework preset as **Other**. Leave the build command and output directory empty; Vercel serves the root files directly.
3. Deploy a preview with `vercel`, then deploy production with `vercel --prod`. With GitHub connected, pushes can deploy automatically.
4. Configure Supabase and Google sign-in before using the production deployment:

   - Apply the checked-in migration with the Supabase CLI from the repository root:

     ```bash
     supabase link --project-ref xxqskwsnnfnpghatcbds
     supabase db push
     ```

     The linked project ref is the one in `supabase-config.js`. Supabase CLI state is local and ignored by Git.
   - In Supabase **Authentication → Providers → Google**, enable Google and enter a Google OAuth client ID and secret. In Google Cloud, set the authorized redirect URI to the callback URL shown in that Supabase provider page. Keep the Google client secret in Supabase, not in Vercel or this repository.
   - In Supabase **Authentication → URL Configuration**, set the production Site URL to your Vercel app URL and add it to the redirect allow list. Supabase allows wildcard patterns for Vercel preview URLs; only add previews if you plan to use them.
   - In Vercel **Project → Settings → Environment Variables**, add `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, and `NYT_COOKIE_ENCRYPTION_KEY` for Production (and Preview if desired). `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` are the project URL and public key in `supabase-config.js`; `SUPABASE_SECRET_KEY` must be a new server-only `sb_secret_...` key from Supabase **Settings → API Keys**. Generate a separate 32-byte encryption key locally with `python3 -c 'import secrets; print(secrets.token_hex(32))'` and save that output directly as `NYT_COOKIE_ENCRYPTION_KEY` in Vercel. Do not commit or paste that encryption key into chat. Changing it makes previously remembered NYT cookies unreadable, so users would need to reconnect.
   - Redeploy after adding the variables.

5. Open the HTTPS deployment URL, choose **Sign in with Google**, then paste the `NYT-S` cookie from a signed-in NYTimes browser session. Imported games and completed analyses are saved to the signed-in Supabase account. The cookie is sent over HTTPS and is encrypted before it is stored when **Remember this NYT cookie encrypted on my account** is checked; uncheck it to keep the cookie only in page memory. **Forget cookie** deletes the saved copy. Refresh checks the saved connection before asking for another cookie. If NYT rejects the token, use the retrieval steps below the connection field to replace it.

The local server keeps game analyses with its in-memory browser session and does not use Supabase persistence; they expire with that session or a server restart. The screenshot parser and manual board page remain local browser tools. Vercel ignores local fixtures, developer notes, and test tooling through `.vercelignore`.

Vercel Hobby is free for personal, non-commercial use. The [Hobby plan](https://vercel.com/docs/plans/hobby) allows Functions up to 60 seconds, which is configured here.

Do not put NYT cookies in deployment variables, browser storage, query strings, logs, HAR files, or committed fixtures. If you add a custom Vercel domain, set `APP_ALLOWED_HOSTS` to its hostname in the Vercel project environment variables.

## Tests

### Develop and test with fake NYT data

Start the real frontend and local API with a synthetic NYT transport:

```bash
npm run frontend
# Open http://127.0.0.1:8000
```

Enter `mock-nyt-cookie-for-local-testing` in the **NYT-S cookie** field and click
**Connect**. No real NYT cookie, Supabase account, or network access is needed.
Two finished games are available to replay and analyze with the bundled Quackle
engine; an active game is hidden. Fixtures include existing letters, an assigned
blank, a swap, passes, and a game fetched through the detail endpoint.

The transport replaces only upstream NYT JSON requests. The frontend, HTTP API,
cookie validation, browser sessions, game summaries, and replay use the normal
application code. All players, identifiers, and abbreviated games in
`tools/fixtures/nyt.json` are invented, not captured account data. This exercises
the local API; Vercel's Google sign-in and Supabase persistence are not emulated.

Other fake cookie values exercise error and empty states:

| Cookie value | Scenario |
| --- | --- |
| `mock-empty-cookie-for-local-testing` | Connected account with no recent games |
| `mock-expired-cookie-for-local-testing` | NYT rejects the cookie (401) |
| `mock-outage-cookie-for-local-testing` | Connect succeeds; loading games fails (502) |

`npm run frontend -- 8001` selects another port; `PORT` and `HOST` are also
supported. The default bind address is loopback. Mock mode requires the explicit
`--mock-nyt` flag and is rejected when `VERCEL` is set. Running
`python3 crossplay_server.py 8000` without the flag still uses the live NYT API.

### Run presubmit checks

Use Node.js 22+ and Python 3.10+. Install the test dependencies and Chromium:

```bash
python3 -m pip install -r requirements-test.txt
python3 -m playwright install chromium
```

Bits Code already provides system Chrome, which the browser tests use if
Playwright's bundled Chromium is unavailable. There are no npm dependencies;
`npm ci --ignore-scripts` verifies the package lock without downloading packages.

Run the same suite used for GitHub pull requests:

```bash
npm test
```

Individual suites are available as `npm run test:api` (standard-library HTTP API
and upstream transport tests), `npm run test:sharing` (local and persisted sharing
authorization checks), `npm run test:review` (Playwright game review and
real Quackle analysis), and `npm run test:ocr` (existing grid, score OCR, screenshot
upload, manual board, and engine regressions). API and review tests start and stop
isolated mock servers on free ports; each browser test uses a fresh context and
blocks external requests. No service credentials or GitHub secrets are needed.

To run review checks against the interactive dev server instead:

```bash
# Keep npm run frontend running in another terminal.
CROSSPLAY_TEST_BASE_URL=http://127.0.0.1:8000 npm run test:review
```

For agent-driven exploration, use the `playwright-cli` skill with that server,
then navigate and connect through the visible form:

```bash
playwright-cli open http://127.0.0.1:8000 --browser=chrome
playwright-cli snapshot
# Click the NYT-S textbox ref from the snapshot, then:
playwright-cli type "mock-nyt-cookie-for-local-testing"
# Click Connect, then a finished game; use snapshot to inspect current refs.
playwright-cli close
```

Keep local `.env` files, HAR captures, and account game data out of commits. Those files are ignored by Git.

## License

Licensed under the [GNU Affero General Public License v3.0](LICENSE). If you run a modified version of this project as a network service, you must offer its source to the users of that service.

The bundled Quackle engine (`quackle.js`, `quackle.wasm`, `quackle.data`) is built from [Quackle](https://github.com/quackle/quackle), which is licensed under the GPL. Check the dictionary data bundled with it for its own terms. "NYT Crossplay" is a trademark of its owner, and this project is not affiliated with or endorsed by The New York Times.
