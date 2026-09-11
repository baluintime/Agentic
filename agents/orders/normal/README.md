# Normal Order Agent

Places a market (or limit) order and reports the fill: status, average price,
filled quantity, broker order id and timestamp.

* **Live only.** Refuses unless the pipeline is armed LIVE.
* **Freeze quantity.** Quantity above the instrument's freeze limit is split into
  legal slices; each slice is placed and polled separately and the event reports
  the weighted average price.
* **Partial fills** are reported as `PARTIAL`, a fill of nothing as `REJECTED`.
* **No blind retries.** A slow confirmation is polled through the order-details
  endpoint, never re-placed.
* The correlation id is written into the order tag so a restart can re-attach the
  position to its pipeline.
