# Quackle WebAssembly provenance

The checked-in Quackle artifacts were built from
`condronkyle/quackle@0facea429741d32de1f2bc854ba5b875e1bde2ff`, with
[the review simulation patch](tools/quackle-review-simulation.patch), using
Emscripten `6.0.13-git`. From a checkout at that commit:

```sh
git apply /path/to/crossplay-analyzer/tools/quackle-review-simulation.patch
./wasm/build.sh
```

The build used the optional `nwl23.gaddag` documented in the source
repository (SHA-256
`07d12c5142110f47564196f917b4324a995c0c6782d89ddc6793272d6b828cd4`).
Artifact SHA-256 values:

```text
quackle.js   ee8077ff20ab6b37ca22918ab74db8b39d69267d5c81825aff21c16cbbc9d9bd
quackle.wasm ebe1e2e52d825ddc8f06d06da04478e96092e1d9b4098a28fedbc9d3a3f9b132
quackle.data 0f7b4e8cdcf4cb743c5423d6befe23ea77691c588f7c7dfb603bf475c1fbf8f3
```

The worker passes the observed bag count and explicit empty-bag final-turn
state to the engine. The engine rejects positions that do not conserve the
100 physical Crossplay tiles.

`simulateReviewMove` accepts the recorded placement, pass, or exchange. It
includes that move beside at most 15 static candidates, then compares their win
estimates with the same initial hidden rack and bag order in each trial. The seed
is stable for a position and independent of the candidate count. Later draws can
differ because different plays consume different tiles and random numbers.

Review results include `samplingVersion: "paired-review-v1"`, the seed, actual
and best moves, static equity, and sample counts. Best means the greatest sampled
win estimate among the candidates. For bags of 21 tiles or fewer, samples play to
the Crossplay finish; otherwise they use two future moves and Quackle's Bogowin
model. Ties at the finish count as half a win. These are estimates under Quackle's
play model, not measured human win rates.

`simulateReviewDraw` accepts the same position, recorded move, settings, and seed,
plus the mover's recorded rack after the draw. It simulates only the recorded
move. `beforeWin` repeats that move's existing unconditional estimate at the same
horizon. `afterWin` uses the same move with its actual first draw fixed. The
engine removes those physical tiles from the unseen pool before it samples the
hidden opponent rack, then returns them to the bag and forces the first refill.
Later moves use the existing play model. Both estimates use the mover's
perspective; their difference in percentage points is the draw-luck estimate.

The engine checks retained tiles, physical blanks, the number of drawn tiles,
and tile availability. An exchange draws before the discarded tiles return to
the bag. Passes and empty-bag moves have no draw and return exactly equal before
and after estimates. Results include `samplingVersion: "rack-draw-v1"`, the seed,
`rackAfter`, `drawCount`, and the same horizon and sample-count metadata as review
results. These two sampled estimates can include sampling noise.

The patch enables C++ exception handling so invalid moves return an error without
stopping the worker. Existing `simulateKibitz` callers retain the same sample
counts and result contract. Run `python3 -m unittest -v test_review_simulation` for real browser/WASM
checks of candidate inclusion, determinism, blanks, exchanges, and final turns.
Run `python3 -m unittest -v test_rack_draw_simulation` for real WASM checks of
draw conditioning, retained tiles, partial bags, and exact zero-draw deltas.

## Low-bag swaps

Crossplay exchanges require one available bag tile for each tile returned. A
recorded game accepted a four-tile swap with six tiles left in the bag; the
previous seven-tile minimum came from Scrabble. Crossplay's minimum is now one,
and both move validation and move generation reject exchanges larger than the
current bag. Scrabble parameters retain their seven-tile minimum.

The patch includes these changes in `gameparameters.cpp`, `game.cpp`, and
`generator.cpp`. Recorded swap failures now distinguish unavailable rack tiles
from insufficient bag tiles instead of reporting a dictionary error. The data file remains byte-identical to the prior build. The loader and WASM
also include the opening-deal API below.

## Endgame policy

Low-bag results include `endgamePolicy: "crossplay-endgame-v3"`. The existing
64-sample previews and the full sample profiles and terminal-result metadata remain in use. A100% result
means every sampled continuation won; it is not a proof under hidden tiles.

When the root bag is empty, candidate selection uses an exact Crossplay win-utility search:
all legal first actions, including pass, followed by the opponent's maximum
score on the last turn. A proved winning action can stop the search. The solver first checks the static best move and the bound from an opponent
pass before it records all legal actions. It does not promise the largest
winning margin. Empty-bag root candidate lists
include that winning action even when it falls outside the static shortlist.
The existing two-final-turn countdown and no-rack-penalty rule are unchanged.

For an occupied Crossplay board with one through seven bag tiles, the main
`CatchallEvaluator::equity` returns
`effective score + 0.5 * rack-leave value + drain term`.
The drain term is10 for a placement that empties the bag and zero otherwise. Physical tiles placed are the
rack size minus the remaining physical rack, so board overlaps do not count
and assigned blanks remain physical tiles. This is the ordinary engine equity:
raw `kibitz`, previews, root shortlists, and future move selection all use it.
There is no separate candidate-equity formula or union of two rankings.
The coefficients are a heuristic, not an exact value of playing last.
Empty-board evaluation, bags above seven, non-Crossplay parameters, and the
empty-bag score-only rule retain their earlier behavior.

If a legal placement exists, the future policy uses a placement so the bag
count falls. Otherwise it uses ordinary pass/exchange evaluation. Recorded
root passes and exchanges remain legal. Selection uses only the acting
player's board, rack, and bag count; it does not inspect the sampled hidden
rack or consume the outer paired random stream.

In sampled future play, both empty-bag turns retain the exact solver described
above. The bounded first-final-turn experiment did not meet the latency goal
and is not used. Version v3 makes low-bag equity authoritative. A generator
caches the authoritative evaluator's rack adjustment directly by action and
remaining physical rack, then uses the same score-plus-adjustment grouping.
The cache is cleared at every generation and position change. It cannot retain
strategy values across separate generations. Its seven-tile input bounds the
number of rack/action keys. No second scoring formula is implemented there.
Ordinary native tie order is used throughout.

During play-to-end future moves with more than seven bag tiles, the generator
also caches the authoritative player-consideration component by the remaining
physical rack. The shared evaluator helper keeps the original arithmetic
order `(player + shared + effective score) + timing`; the cache does not
subtract a score or combine the timing term with the rack value. It clears on
every generation and position change, with at most 128 physical subsets for a
seven-tile rack. Both static helper caches require the exact CatchallEvaluator
type; custom evaluator subclasses keep ordinary virtual evaluation. The
play-to-end flag is captured before the negative-ply request becomes a bound
of 1,000 moves. Ordinary root generation and normal two-ply future calls do not
enable this new cache. Generic pass defaults (score 0 and equity -999) are unchanged.
There are no profiling timers, diagnostic exports, or model-policy changes.
Version v3 invalidates prior
terminal move, preview, and draw results through their cache
identities. The paired seed excludes policy suffixes, so the sampled physical
worlds remain comparable. All reported terminal candidates must still finish
all requested samples; partial batches cannot become full results.

Run `python3 -m unittest -v test_endgame_simulation` for the synthetic winning-block,
root shortlist, blank identity, and paired-sample cache regressions.

## Opening deal

`simulateInitialDeal(firstRack, secondRack, iterations, seed)` estimates the
opening deal before any recorded move. Both inputs must contain seven physical
uppercase letters or `?` blanks and fit the 100-tile Crossplay distribution.
The native API accepts 1 to 277 samples and an explicit unsigned 32-bit seed.
The worker request `simulateOpening` accepts the 64- and 277-sample profiles.

The method computes four means from the first mover's view: both racks random
(B), first rack fixed (A), second rack fixed (C), and both racks fixed (J).
Each trial starts with a full bag, removes all fixed rack tiles, and then draws
unknown racks. The result must contain seven tiles on each rack and 86 in the
bag. The opener uses the best static move with the opponent rack hidden. Each
trial then plays two future static moves and uses the same residual and Bogowin
calculation as `simulate(2)`. It never uses the recorded first move or selects
among noisy simulated candidates.

All four scenarios use the same trial seed sequence. The trial seed depends on
the input seed and trial index, so the first 64 trials of a 277-sample run match
the preview. The page uses seed 806877465 for every rack pair and both profiles.
B thus retains the model's first-move advantage. The method does not assume a
50 percent baseline. First-player WPA is `100*((A-B)+(J-C))/2`; second-player
WPA in that player's view is `-100*((C-B)+(J-A))/2`.

Results include canonical sorted racks, `samplingVersion: "initial-deal-v1"`,
`policy: "static-best-v1"`, the four means, both signed WPA values, the seed,
sample counts, and `plies: 2`, `playToEnd: false`, `simulationMode: "2-ply"`,
`winModel: "bogowin"`. These are short-horizon play-model estimates and can
contain sampling noise. They are not calibrated human win probabilities.

Run `python3 -m unittest -v test_initial_deal_simulation` for real browser/WASM
checks of determinism, rack order, conditional independence, physical limits,
WPA identities, the full 277-sample result, exact preview-prefix identity, and
worker dispatch. Run `node --test test_initial_deal_worker.js` for request and
raw-result rejection checks. The source patch preserves the earlier review,
draw, exception, low-bag exchange, and 1000-ply finish-bound changes.

## Bag-emptying candidate coverage

The worker pool uses the legacy-named `planBagEmptying` export for full
recorded/custom comparisons and continuation alternatives with one through
seven bag tiles. The plan is the ordinary authoritative-equity top15 plus the
supplied recorded action, with physical duplicates removed (at most16 moves).
Early64-sample previews use the ordinary top5 under the same evaluator.
Existing board letters do not count as newly placed tiles. Exchanges do not
empty the bag. This search does not assess every legal bag-emptying move.

The worker pool submits the plan through `simulateReviewBatch`. It retains
completed sums across transient failures, yields between requests, and
coalesces matching jobs. The 120-second worker deadline still applies.

Full results retain the top15 sampled alternatives, the recorded action, and
the highest static-equity action, with duplicates removed (at most16 moves).
`candidateCount` is the retained count; `searchedCandidateCount` is the number
of selected candidates assessed. `generatedCandidateCount` counts all generated
legal actions, and `totalBagEmptyingCount` counts all legal placements that
empty the bag. `requiredBagEmptyingCount` and `evaluatedBagEmptyingCount` count
the selected bag-emptying placements and must match before completion. These
counts do not claim exhaustive coverage. `totalPlayaheads` counts all selected
candidates times their completed samples.
`equityMoves` contains the current authoritative-equity shortlist plus the recorded action
for word-commonness and adjusted-performance calculations. These metrics do
not use the compact win-ranked display list. Results and affected cache identities carry `candidatePolicy: crossplay-low-bag-equity-v2`.
The cache policy does not change the paired seed. The legacy
`simulateCandidateBatch` export still accepts explicit candidates for bags
with one through seven tiles.

Run `node --test test_bag_emptying_analysis.js test_quackle_pool.js` for compact
results, coverage, coalescing and resume checks, and
`python3 -m unittest -v test_bag_emptying_simulation` for real browser/WASM
coverage and sample-offset checks. The data artifact is unchanged.

## Terminal review and draw batches

For a bag with zero through 21 tiles, `planReviewSimulation` returns the
ordinary authoritative-equity shortlist and includes the recorded action when supplied. At
zero tiles, the shortlist includes the exact final winner from the native
search. Both plan exports include the native tie order as an array of candidate
indices, starting with the highest-ranked candidate when win and equity tie.

`simulateReviewBatch` accepts one through eight explicit candidates, a
zero-based sample offset, and one through 16 samples. The offset plus the
requested count cannot exceed 277. The engine shifts the base seed by the
sample offset and uses the same paired sample schedule as the original review.
Each result includes raw win and residual sums, terminal counts, and per-level,
per-player score sums and sample counts. Combining these values preserves
equity across variable-length games.

`simulateDrawBatch` uses the same offset and sample limits for one recorded
action. A supplied win estimate in the range zero through one reuses a
matching completed review. Pass `-1` to compute the unconditional estimate.
The engine checks physical draws in both cases. It returns before and after
sums plus the draw metadata. The estimates are exactly equal for a move with
no draw.

Both batch APIs return the offset and the number of complete samples. They
check the eight-second budget between trials. The draw call completes both
estimates for the current sample before checking the budget. One trial can
exceed the budget. Legacy synchronous review and draw exports remain available.

Run `python3 -m unittest -v test_terminal_batches` to check split and monolithic
results, the native tie order, conditioned draws, batch limits, and the
cooperative budget in real browser WASM.

## Exact final-reply optimization

The final solver keeps a bounded maximum-score move cache keyed by the physical board, blank flags, and the acting rack. The cache is used only for empty-bag Crossplay positions, where static equity equals move score. Score-dependent two-turn choices keep their separate full-information key.

After the existing reply bound, an alternative cannot improve the current result if any legal final reply reaches `next.spread(actor) - bestSpread`. The generator can stop at that witness. If no witness exists, it completes the normal search and supplies the exact maximum-score move with the original tie order. Witnesses are never stored as exact maxima. These reply bounds preserve the score returned for a searched reply. Earlier future choices use the approximate policy described above.

Focused real-WASM checks include score changes, wins, ties, physical blank identity, and partitioned equity. A cold subset comparison preserved every result and sufficient statistic. Full user-workload timings are recorded in the work-item report.

The final, preempty, and maximum-score caches use bounded LRU eviction with the existing 32,768-entry limits. A shared utility owns keys once in stable list nodes and uses string views for lookup. This preserves a new working set after earlier game positions fill the cache; arbitrary unordered-map iteration is not an eviction policy. A native C++ regression compiles the exact header from the source patch and checks working-set retention, hit promotion, bounded capacity, and physical binary keys.

## Late-game sample profile

Full analysis selects a fixed paired-sample profile from the starting bag and
existing browser worker budget. With1–7 bag tiles, all devices use192
samples. With8–21 bag tiles, one-worker
devices use128 and two- or three-worker devices use192. Empty bags and bags above21
retain277. Previews retain64 on all devices. Every chosen move and its draw
comparison uses the actual profile count and must reach the official terminal
state in every terminal trial. A smaller full result is complete for its
profile, not an incomplete277-sample result. Its sampling uncertainty is larger.

Cache identities contain the actual count. Review-seed hashing normalizes
public full profiles for bags1–21 back to277 to preserve the prior sample-world
prefix. Explicit configured sample overrides and native diagnostic requests
remain available. Saved full results must use one of the supported counts for
their bag range. The server does not infer the user's device budget.

## Hindsight position values

`evaluateKnownStateBatch(stateJson, iterations, seed, offset, count)` evaluates
one canonical position under the existing continuation policy. Its
`informationModel` is `hindsight-state-v1`; its `policyVersion` is
`hindsight-state-v1/crossplay-endgame-v3`. It is separate from player-information
move grades and draw-luck estimates.

The JSON state contains `grid`, two owner-oriented `racks` and `scores`,
`toMove` (0 for owner or 1 for opponent), `bagCount`, and `finalTurnsRemaining`.
An optional `pendingDraw` has `player`, `count`, and physical `returnedTiles`.
The move is already applied: the pending refill consumes no new turn or score.
Exchange returns stay outside the bag until the draw completes. All physical
tiles, including board blanks and short final racks, must conserve the known
Crossplay distribution. Missing hands are errors, not sampled substitutes.

Each seeded trial keeps both current racks fixed and samples only future bag
order. The root uses the same actor-visible policy as future turns, without a
forced recorded move or a fake pass. Pending draws resolve before the horizon
starts. Settled bags of21 or fewer play to the official finish; larger bags use
the existing three-action horizon (policy root plus two future actions) and
Bogowin estimate. Reaching a final empty bag by refill sets two final turns.
Known-state advancement bypasses legacy history-based unknown-rack repairs;
those repairs must never redistribute these complete physical hands.

The result returns owner-oriented `winSum`, `win`, actual `samples`/`completed`,
`terminalSamples`, sample `offset`, `seed`, `iterationsRequested`, input
`bagCount`, resolved `settledBagCount`, and horizon/model metadata. Batches
contain1–16 samples, with offsets bounded by the requested1–277 sample total.
The existing private-information review/draw APIs retain their behavior.

Run `python3 -m pytest -q test_hindsight_simulation.py` for root-policy,
owner-orientation, empty/short-rack, pending-refill, exchange, split-offset,
physical-inventory, malformed-JSON and same-module private-analysis checks.
