# Bug Report

## 1. Datetime offset normalization
- Files/lines: `app/timeutils.py:5-14`
- Bug: Offset-aware datetimes were stripped to naive values with `replace(tzinfo=None)` instead of being converted to UTC first.
- Impact: A request like `2026-07-09T10:00:00+02:00` was stored as `10:00 UTC` instead of `08:00 UTC`, breaking the booking window, overlap, availability, and reporting rules.
- Fix: Convert aware inputs with `astimezone(timezone.utc).replace(tzinfo=None)` so all stored/comparison datetimes are normalized to UTC.

## 2. Access token lifetime
- Files/lines: `app/auth.py:51-63`
- Bug: Access token lifetime was computed with `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)`, producing a 15-hour token instead of 900 seconds.
- Impact: Tokens remained valid far longer than the contract allows.
- Fix: Use `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`, yielding an exact 900-second lifetime.

## 3. Logout revocation and refresh-token reuse
- Files/lines: `app/auth.py:25-27`, `app/auth.py:88-112`, `app/routers/auth.py:85-98`
- Bug: Logout stored revoked token `jti`s, but request validation checked `sub`, so logged-out access tokens still worked. Refresh tokens were also reusable because no consumed-token state was tracked.
- Impact: Logout was ineffective and refresh tokens were not single-use, violating the auth rules.
- Fix: Check access-token revocation by `jti`, add a lock-protected consumed refresh-token set, and reject reused refresh tokens with `401`.

## 4. Duplicate registration and concurrent org creation
- Files/lines: `app/routers/auth.py:24-63`
- Bug: Registering an existing username returned the existing user payload instead of `409 USERNAME_TAKEN`. Concurrent creation of the same org name could also fail incorrectly without handling unique-constraint races.
- Impact: Registration broke the documented error contract and could mis-handle concurrent signups for a new org.
- Fix: Catch `IntegrityError` on user creation and return `409 USERNAME_TAKEN`; catch org-creation races so the first registrant becomes admin and concurrent followers join as members.

## 5. Booking window validation
- Files/lines: `app/routers/bookings.py:79-101`
- Bug: The route allowed a five-minute grace window for past starts, did not reject `end_time <= start_time` early, and only enforced the maximum duration without the minimum.
- Impact: Invalid booking windows could be accepted even though the business rules require strictly future starts, strictly increasing endpoints, and durations from 1 to 8 whole hours.
- Fix: Enforce `start_time > now` with no grace window, reject `end_time <= start_time`, require whole-hour durations, and require duration bounds of 1 through 8 hours inclusive.

## 6. Room-overlap logic
- Files/lines: `app/routers/bookings.py:43-53`
- Bug: Overlap detection used `<=` comparisons.
- Impact: Back-to-back bookings were treated as conflicting even though they should be allowed.
- Fix: Use the strict overlap rule `existing.start_time < new.end_time and new.start_time < existing.end_time`.

## 7. Booking creation concurrency, quota, and reference-code safety
- Files/lines: `app/routers/bookings.py:25`, `app/routers/bookings.py:96-129`, `app/services/ratelimit.py:20-29`, `app/services/reference.py:1-9`, `app/models.py:46-56`
- Bug: Booking creation performed rate-limit/quota/conflict/reference checks without synchronization. Rate-limit buckets were not thread-safe, reference codes came from a race-prone in-memory counter, and the database schema did not enforce uniqueness.
- Impact: Concurrent requests could bypass rate limiting, exceed quota, double-book a room, or generate duplicate reference codes.
- Fix: Serialize booking mutations with a process lock, make the rate limiter lock-protected, switch reference generation to random high-entropy codes, check for collisions before insert, and add a DB unique constraint on `Booking.reference_code`.

## 8. Booking list pagination and ordering
- Files/lines: `app/routers/bookings.py:136-156`
- Bug: Listing sorted bookings descending by start time, used `offset(page * limit)`, and always applied `.limit(10)` regardless of the requested `limit`.
- Impact: Pages skipped items, repeated items, ignored the caller’s requested page size, and violated the required ascending order.
- Fix: Sort by `start_time ASC, id ASC`, use `offset((page - 1) * limit)`, and apply the requested `limit`.

## 9. Booking detail visibility and timestamp serialization
- Files/lines: `app/routers/bookings.py:159-185`
- Bug: Members could read other members’ bookings within the same org, and the handler overwrote `start_time` in the response with `created_at`.
- Impact: The endpoint violated the booking-visibility rule and returned incorrect booking data.
- Fix: Return `404 BOOKING_NOT_FOUND` when a non-admin requests another member’s booking, and keep `start_time` sourced from the booking itself.

## 10. Cancellation refund policy and atomic refund logging
- Files/lines: `app/routers/bookings.py:75-76`, `app/routers/bookings.py:188-232`, `app/services/refunds.py:14-21`
- Bug: The refund tiers used `> 48` instead of `>= 48`, returned `50%` instead of `0%` for notice under 24 hours, and used float rounding that could disagree with stored refund amounts. Refund-log insertion also committed separately from the booking cancellation.
- Impact: Refund amounts and percentages were wrong at tier boundaries and for low-notice cancellations, and concurrent cancel requests could create inconsistent state.
- Fix: Apply the exact tier thresholds, compute refund cents with integer half-up rounding, and add the refund log within the same transaction as setting the booking to `cancelled`.

## 11. Concurrent double-cancel handling
- Files/lines: `app/routers/bookings.py:25`, `app/routers/bookings.py:194-223`
- Bug: Cancellation checks and updates were not synchronized across requests.
- Impact: Two concurrent cancel requests for the same booking could both pass the pre-checks and create multiple refund logs or inconsistent responses.
- Fix: Run the booking lookup, status check, refund calculation, refund-log insertion, and status update inside the shared booking-mutation lock so only one cancel succeeds.

## 12. Availability and usage-report staleness
- Files/lines: `app/routers/rooms.py:59-94`, `app/routers/admin.py:17-55`
- Bug: Availability and usage-report endpoints served in-memory cached results.
- Impact: Reads could lag behind the current booking state, violating the requirement that both endpoints reflect the current state immediately.
- Fix: Remove cache reads/writes from these endpoints and compute directly from the database on each request.

## 13. Room stats consistency
- Files/lines: `app/routers/rooms.py:97-116`
- Bug: Room stats came from an in-memory accumulator instead of the booking table.
- Impact: Stats could drift from the actual confirmed bookings, especially after bursts of concurrent activity or process restarts.
- Fix: Aggregate current confirmed booking count and revenue directly from the database.

## 14. Cross-org admin export leakage
- Files/lines: `app/services/export.py:21-50`
- Bug: `include_all=true` with `room_id` used an unscoped room query and could export bookings from another organization.
- Impact: Cross-tenant data could leak through the export endpoint instead of behaving like a non-existent room.
- Fix: Validate `room_id` against the caller’s org first, then fetch export rows only through org-scoped queries.

## 15. Notification deadlock / liveness bug
- Files/lines: `app/services/notifications.py:23-32`
- Bug: Booking creation locked email then audit, while cancellation locked audit then email.
- Impact: Concurrent create/cancel requests could deadlock the service, violating the liveness rule.
- Fix: Replace the opposing lock order with a single shared notification lock so both flows acquire synchronization consistently.
