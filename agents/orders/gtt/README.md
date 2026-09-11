# GTT Order Agent

Market entry, then broker-side target and stop-loss legs.

1. Enter at market (sliced above the freeze quantity) and poll for the fill.
2. Read the **actual** fill price; compute target = fill + T and SL = fill − S
   (inverted for a short). Percent-mode targets are applied to the fill price.
3. Place the GTT legs and keep the id.
4. On an order update, report `TARGET_HIT`, `SL_HIT` or `CANCELLED` to the
   originating strategy on the entry's correlation id, and cancel the sibling leg
   if the broker has not already done so.
5. `system.squareoff.cancel_gtt` and `system.kill` cancel every open GTT first.

**Live only** — refuses unless the pipeline is armed LIVE. Verify the supported
leg combinations against the current Upstox GTT documentation before trading;
`gtt_payload()` is the single place to adjust.
