# Notes

## What I added

**`POST /orders/{order_id}/reserve`**

"Reserve" decrements `quantity_available` for every line on the order at the
chosen warehouse and moves the order to `status = "reserved"`. The existing
schema has a single `quantity_available` per (tenant, warehouse, product) and no
reserved-quantity column, so I kept that model rather than introducing a new one.

Design decisions:

- **Atomic.** The whole operation runs in one transaction. If any line cannot be
  satisfied, the transaction rolls back, so an order is never partially reserved.
- **Idempotent on retry.** The fulfilment agent retries on timeout, and the
  original request may already have committed. A reserve on an already-reserved
  order is a no-op that returns the order unchanged instead of decrementing stock
  a second time.
- **Overselling-safe under concurrency.** I avoided a plain read-check-write,
  because two concurrent requests can both read the same availability and both
  pass the check. Instead I take SQLite's write lock up front with
  `BEGIN IMMEDIATE`, and each decrement is a conditional `UPDATE ... WHERE
  quantity_available >= qty` whose row count I verify. A 30s busy timeout lets
  contenders wait for the lock rather than failing.
- **Status codes.** `404` for a missing/other-tenant order or warehouse; `409`
  when the request is valid but the current inventory state can't satisfy it
  (state conflict, not a client error).

## What I found and fixed in the existing code

- **Cross-tenant read (IDOR).** `GET /orders/{id}` filtered only by order id, not
  tenant id, so one tenant could read another tenant's order by guessing the id.
  Added tenant scoping to the lookup.
- **Slow order list (N+1).** The list endpoint loaded orders, then lazy-loaded
  customer, lines and each line's product during serialization. Switched to
  `selectinload` so those relationships load in batches, and added tenant-id
  indexes.
- **Race in `adjust_stock`.** It read the row, checked, then wrote — two
  concurrent calls could both pass the check. Replaced with a single conditional
  UPDATE guarded by `quantity_available + delta >= 0`.

## What I deliberately did not do

- No separate reservation table, inventory ledger, Redis, or distributed lock.
  For a one-hour task and a SQLite-backed service, that's beyond scope. I kept
  the existing data model and made the critical reservation path transactional
  and idempotent instead.
- Left `adjust_stock`'s `400` response as-is to preserve existing behavior.

## Tradeoff worth calling out

`BEGIN IMMEDIATE` serializes all reservations on SQLite's single writer lock.
That's acceptable here because SQLite allows only one writer anyway; per-product
concurrency would require a different backend.

## How I verified it

Ran the service and tested: successful reservation, insufficient-stock rollback,
multi-line atomicity, duplicate/retry reservation (no double decrement), tenant
isolation on orders and warehouses, invalid order/warehouse ids, and concurrent
reservations for the same scarce stock (many simultaneous requests never
oversold — only the available units were reserved).
