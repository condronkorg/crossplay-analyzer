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
quackle.js    ac16b1ca6c448ab3a1cb88ed45885ffb121f8229f66d7a0fd60593a464b0b425
quackle.wasm  3641b36a04ba3413a49f8227e86b45ac5e12b496c4d0b97c9b6d0cbafd6afbf0
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

The patch enables C++ exception handling so invalid moves return an error without
stopping the worker. Existing `simulateKibitz` callers retain their prior sample
behavior. Run `python3 -m unittest -v test_review_simulation` for real browser/WASM
checks of candidate inclusion, determinism, blanks, exchanges, and final turns.
