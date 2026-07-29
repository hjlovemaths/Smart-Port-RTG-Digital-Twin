"""Generate a deterministic random whole-yard inventory snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from yard_inventory import (
    DEFAULT_SIM_INVENTORY_PATH,
    YardInventory,
    generate_random_inventory,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_SIM_INVENTORY_PATH)
    parser.add_argument(
        "--mode",
        choices=("random", "empty"),
        default="random",
        help="Generate a random simulation snapshot or an empty live snapshot.",
    )
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--occupancy-ratio", type=float, default=0.42)
    parser.add_argument("--forty-ft-ratio", type=float, default=0.38)
    parser.add_argument("--max-tiers", type=int, default=5)
    args = parser.parse_args()
    if args.mode == "empty":
        inventory = YardInventory.empty()
        inventory.save(args.output)
    else:
        inventory = generate_random_inventory(
            args.output,
            seed=args.seed,
            occupancy_ratio=args.occupancy_ratio,
            forty_ft_ratio=args.forty_ft_ratio,
            max_tiers=args.max_tiers,
        )
    stats = inventory.to_dict()["stats"]
    seed_text = str(args.seed) if args.mode == "random" else "n/a"
    print(
        f"Generated {args.output}: mode={args.mode} seed={seed_text} "
        f"anchors={stats['anchorCount']} stacks={stats['occupiedStackCount']} "
        f"containers={stats['containerCount']}"
    )


if __name__ == "__main__":
    main()
