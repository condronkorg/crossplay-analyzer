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
quackle.js    1b9f62da89666b75b551506c4743bca5411ad56db3c9860a7163acf93024746e
quackle.wasm  b4e676f8d49ab5d5c8a4f82532aeb51b71d93d0f4ac53a4862206098ddc8f621
quackle.data  0f7b4e8cdcf4cb743c5423d6befe23ea77691c588f7c7dfb603bf475c1fbf8f3
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
stopping the worker. Existing `simulateKibitz` callers retain their prior sample
behavior. Run `python3 -m unittest -v test_review_simulation` for real browser/WASM
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
from insufficient bag tiles instead of reporting a dictionary error. The data
and JavaScript loader files remain byte-identical to the prior build.
