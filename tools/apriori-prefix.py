#!/usr/bin/env python3
"""Which scan prefix produces which a-priori batch, and what each one implies.

Why this exists: the recorder prints `P2 WALK t=0 seen=<n> iter=<n+1>` for the
DRIVER pass and `P2 SEQ` with one character per entry the promotion loop
*matched*, so `len(P2 SEQ)` is `mP2Apriori`. The panel's SEQ is 46 characters,
so the batch was 46 - and on this volume more than one `seen` produces 46,
because the two orders are independent: the walk fills `mDiscoveredList` in
**physical** order and the promotion loop reads the Apriori array in **Apriori**
order (`Dispatcher.c:2104-2125` is an outer loop over the array and an inner
loop over the discovered list). So the batch is a function of the scan prefix
and of nothing else, and this tool tabulates that function instead of arguing
about it.

The number that matters is not 46 by itself but *which* 46: a scan that reached
80 DRIVER files promotes a contiguous run of the array, while a scan cut at 48
or 49 promotes a scattered set that includes the four console drivers - whose
files sit low in the volume and late in the array. The two batches share 42
entries and differ in eight, so `P2 DIAG`'s GUID list separates them outright,
and this tool prints both lists for comparison against a photograph.

The letters (`s`, `L`) cannot separate them and no reading of them ever will.
Each character is the load result of the driver in that slot, and the slots are
the hypothesis under test - so a string of the right length is consistent with
*every* batch of that length, and any test that indexes the observed string by
a slot map has assumed the answer. That is worth printing because
`tools/fv-census.py`'s closing block does exactly that and reports the only two
candidates REFUTED for it (see `--letters` below, which reproduces the
comparison and says what it is worth).

Usage:
    tools/apriori-prefix.py <img>                  # every prefix that gives 46
    tools/apriori-prefix.py <img> --seen 80        # one prefix, explicitly
    tools/apriori-prefix.py <img> --target 46 --letters
    tools/apriori-prefix.py work/out/p2-variants/Mu-gauguin-silicon-gzip.img
"""

import argparse
import contextlib
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What the panel's `P2 SEQ` shows, verbatim from the one photograph of it: 46
# characters, 18 `s`, three `L`, one `s`, 24 `L`. The one copy of this literal
# is tools/fv-census.py's; it is repeated here rather than imported because that
# script runs its whole census at import time. `--letters` prints what the
# comparison against a batch is worth, which is the point of having it at all.
SEQ = "s" * 18 + "L" * 3 + "s" + "L" * 24

TYPE_DRIVER = 0x07


def load(mod, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", mod))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def volume(path, fvi):
    """(files, inner, offsets) - the FVMAIN inside a payload or a bare FV.

    `files` entries are 5-tuples whose first element is already a GUID *string*
    (`fvmain_of_fd`/`unpack` run it through `guid_str`; only `fv_files` hands back
    raw bytes), and `offsets` are byte offsets into `inner`. Both descents print
    an FD header of their own, so the caller's stdout is not the place for it.
    """
    d = open(path, "rb").read()
    with contextlib.redirect_stdout(io.StringIO()):
        if d[:8] == b"ANDROID!":
            files, _fv_len, offsets, inner = fvi.unpack(path)
        else:
            files, _fv_len, offsets, inner = fvi.fvmain_of_fd(d, verbose=False)
    if inner is None:
        sys.exit(f"{path}: no FVMAIN inside it")
    return files, inner, offsets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--target", type=int, default=len(SEQ),
                    help="the promotion count to tabulate (default: the length "
                         f"of the recorded P2 SEQ, {len(SEQ)})")
    ap.add_argument("--seen", type=int, action="append", default=[],
                    help="a t=0 seen= value to tabulate, whatever it promotes; "
                         "repeatable. Without it, --target's prefixes are printed")
    ap.add_argument("--letters", action="store_true",
                    help="compare each batch against the recorded SEQ and print "
                         "what that comparison can and cannot decide")
    args = ap.parse_args()

    fvi = load("fv-inventory.py", "fv_inventory")
    apo = load("apriori-order.py", "apriori_order")

    files, inner, offsets = volume(args.image, fvi)
    with contextlib.redirect_stdout(io.StringIO()):
        apriori, _nfiles = apo.apriori_array(fvi, args.image)

    # The walk's order is the volume's file order (FvCheck builds the FFS list by
    # walking the volume; FvGetNextFile hands them back in it), and it is
    # type-filtered, so a DRIVER's *rank* is its position among the DRIVER files.
    rank, order = {}, []
    for i, (g, t, *_r) in enumerate(files):
        if t == TYPE_DRIVER:
            rank[g] = len(order)
            order.append((i, g))
    n07 = len(order)

    phys = {g: i for i, (g, *_r) in enumerate(files)}
    name = {g: (n or "?") for g, _t, _s, n, _st in files}

    def nm(g):
        return name.get(g, "?")

    absent = [i for i, g in enumerate(apriori) if g not in rank]
    named = len(apriori) - len(absent)

    print(f"{os.path.basename(args.image)}: {len(files)} files, "
          f"{n07} DRIVER(0x07), Apriori {len(apriori)} entries")
    print(f"  Apriori entries with no DRIVER file of that GUID: {absent}"
          + (f"  ({[nm(apriori[i]) for i in absent]})" if absent else ""))
    print(f"  Apriori entries with one: {named}, so a complete scan promotes"
          f" {named} of {len(apriori)}"
          + (" - index 0 is the DXE core, which the walk's DXE_CORE branch never"
             " hands to CoreAddToDriverList\n  (it fills gDxeCoreLoadedImage->"
             "FilePath instead), so it has no DRIVER file to match"
             if 0 in absent else ""))
    print()

    def promoted(seen):
        """The Apriori entries matched by a DRIVER scan that reached `seen` files."""
        return [i for i, g in enumerate(apriori) if g in rank and rank[g] < seen]

    def unhit(seen):
        return [i for i, g in enumerate(apriori) if g not in rank or rank[g] >= seen]

    def miss(seen):
        u = [i for i in unhit(seen) if i > 0]
        return u[0] if u else None

    # The prefix function: `promoted` is a step function of `seen`, and the step
    # boundaries are what a panel `seen=` value has to be read against, because
    # most `seen` values inside a band are indistinguishable from each other.
    print("=== the prefix function: seen -> batch size ===")
    bands, prev, start = [], None, 0
    for seen in range(0, n07 + 1):
        k = len(promoted(seen))
        if prev is None:
            prev, start = k, seen
        elif k != prev:
            bands.append((start, seen - 1, prev))
            prev, start = k, seen
    bands.append((start, n07, prev))
    for lo, hi, k in bands:
        last = nm(order[hi - 1][1]) if hi >= 1 else "-"
        wanted_band = any(lo <= s <= hi for s in args.seen) if args.seen \
            else (k == args.target)
        if wanted_band:
            mark = "  <-- " + (f"target {args.target}" if k == args.target else "")
            print(f"  seen {lo:>3}..{hi:<3} -> {k:>3} promoted, "
                  f"last DRIVER seen {last}{mark}")
    hits = [hi for lo, hi, k in bands if k == args.target and hi]
    print(f"  -> {len(bands)} bands; batch sizes reachable: 0..{max(k for _l,_h,k in bands)}"
          f" (every size, one step at a time)")
    print(f"  -> seen values giving exactly {args.target} promotions: "
          f"{sorted(s for s in range(0, n07 + 1) if len(promoted(s)) == args.target)}")
    print()

    wanted = args.seen or sorted(s for s in range(0, n07 + 1)
                                 if len(promoted(s)) == args.target)
    if not wanted:
        sys.exit(f"no seen value promotes exactly {args.target} entries")

    for seen in wanted:
        batch = promoted(seen)
        unh = unhit(seen)
        print(f"=== seen={seen}: {len(batch)} promoted, {len(unh)} unhit "
              f"(including index 0), miss="
              f"{miss(seen) if miss(seen) is not None else 'none'} ===")
        lastdrv = nm(order[seen - 1][1]) if seen >= 1 else "-"
        print(f"  last DRIVER the walk was handed: {lastdrv} "
              f"(rank {seen - 1}, volume file {order[seen - 1][0]})")
        print(f"  unhit: ap{unh}")
        print(f"  {'slot':>4} {'ap':>3} {'phys':>5}  name")
        for slot, a in enumerate(batch):
            print(f"  {slot:>4} {a:>3} {phys.get(apriori[a], -1):>5}  {nm(apriori[a])}")
        print()

        if args.letters:
            if len(batch) != len(SEQ):
                print(f"  letters: the recorded SEQ is {len(SEQ)} characters and"
                      f" this batch is {len(batch)} slots,\n           so the string"
                      f" cannot be laid against it slot for slot at all - which is"
                      f" why the\n           census's `cands` list has no"
                      f" complete-sweep candidate in it.")
                print()
                continue
            # The comparison the census makes, and what it is worth. `pred[i]` is
            # the observed character at slot `batch[i] - 1`, i.e. the letter the
            # complete-walk reading assigns to the same driver - so a difference
            # here says the two batches disagree about which driver each character
            # belongs to, which is what the batches are. It is a restatement of
            # the hypothesis, not a test of it: `pred[i] != obs[i]` exactly when
            # the recorded string does not happen to be flat across the gaps in
            # `batch`, so every string with a transition near a gap is "refuted".
            obs = SEQ
            pred = "".join(obs[a - 1] if a - 1 < len(obs) else "?" for a in batch)
            diffs = [i for i, (p, o) in enumerate(zip(pred, obs))
                     if p != o and p != "?"]
            g = [batch[i] - 1 - i for i in range(len(batch))]
            print(f"  letters: recorded {obs}")
            print(f"           census   {pred}   "
                  f"diffs {diffs}  (shift per slot: {sorted(set(g))})")
            for i in diffs:
                print(f"           slot {i}: this batch holds {nm(apriori[batch[i]])}"
                      f" (ap{batch[i]}, phys {phys.get(apriori[batch[i]], -1)});"
                      f" the census's letter for it is\n             "
                      f"'{obs[batch[i] - 1]}' because the complete batch puts it at"
                      f" slot {batch[i] - 1}, where the panel shows another driver.")
            print("           -> the diff is the batch difference restated; the"
                  " recorded string has no\n              slot map of its own, so it"
                  " cannot choose between two batches of its own length.")
            # The census's headline result - "5 failed while 74 succeeded, so no
            # physical cutoff exists" - is computed the same way, off the map, so it
            # is worth re-running it under each batch: this is a conclusion that can
            # survive the map it was derived from or not, and it does survive.
            fail = [(phys[apriori[batch[i]]], nm(apriori[batch[i]]))
                    for i in range(len(batch))
                    if obs[i] == "L" and apriori[batch[i]] in phys]
            ok = [(phys[apriori[batch[i]]], nm(apriori[batch[i]]))
                  for i in range(len(batch))
                  if obs[i] != "L" and apriori[batch[i]] in phys]
            lo = min(fail) if fail else None
            hi = max(ok) if ok else None
            print(f"  physical extent under THIS batch: lowest failed file "
                  f"{lo[0] if lo else '-'} {lo[1] if lo else ''}, highest loaded "
                  f"file {hi[0] if hi else '-'} {hi[1] if hi else ''} -> a physical "
                  f"cutoff is "
                  f"{'IMPOSSIBLE' if lo and hi and lo[0] < hi[0] else 'not refuted here'}")
            print()


if __name__ == "__main__":
    main()
