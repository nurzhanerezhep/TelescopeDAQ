"""Compare raw buffer assembly, without hardware or disk I/O."""

from __future__ import annotations

import argparse
import logging
import timeit

import awkward as ak
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--waveforms", type=int, default=512)
    parser.add_argument("--samples", type=int, default=1024)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if min(args.waveforms, args.samples, args.repeat) < 1:
        parser.error("All arguments must be positive")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    samples = [
        np.arange(args.samples, dtype=np.uint16) % 4096 for _ in range(args.waveforms)
    ]
    offsets = ak.index.Index64(
        np.arange(args.waveforms + 1, dtype=np.int64) * args.samples
    )

    def old():
        return ak.Array(samples)

    def new():
        return ak.Array(
            ak.contents.ListOffsetArray(
                offsets, ak.contents.NumpyArray(np.concatenate(samples))
            )
        )

    assert ak.all(old() == new()), "Sample values changed"
    before = min(timeit.repeat(old, number=1, repeat=args.repeat))
    after = min(timeit.repeat(new, number=1, repeat=args.repeat))
    logging.info(
        "%d waveform x %d samples: old %.3f ms, new %.3f ms; equal values",
        args.waveforms,
        args.samples,
        before * 1000,
        after * 1000,
    )
    logging.info("Assembly only, precomputed offsets; not CAEN or ROOT disk throughput")


if __name__ == "__main__":
    main()
