# E-2 — Identity & Playgroups

| | |
|---|---|
| **Phase(s)** | 0 (accounts, authentication, every auth screen on web and mobile, player identity, site admin role) · 1 (profiles, guests, playgroups, playgroup settings, house rules, game defaults and house bans, admin password reset) · 2 (profile privacy) · 5 (data export, account deletion, archiving and deleting playgroups) |
| **Goal** | Give a private playgroup invite-only accounts, a single player identity that works for both members and guests, playgroups with owner, admin and member roles, a global site admin role, per-playgroup settings and house rules (time zone, small-sample threshold, game defaults, house bans and "rule 0" allowances) that stats, deck validation and the game lobby can use, and the data rights from decision C9 (export, account deletion, archiving or deleting a playgroup). |
| **Success measures** | - Nobody can create an account without a valid invite link, and no part of the flow needs an email service.<br>- Sign-in, invite sign-up, password reset and sign-out work on both web and mobile, and every shareable link is an `https` web URL with an "Open in app" button.<br>- A guest who later registers keeps 100% of their game history under one player identity, and their stats are rebuilt to include it.<br>- A playgroup owner or admin can get a locked-out member back in with a one-time reset link in under 2 minutes.<br>- House bans and allowances for a playgroup come back from one service call that F-4.5 can use directly.<br>- A user can export all of their data and delete their account themselves; afterwards their past seats show "Deleted player" and no playgroup's stats change. |
| **Out of scope** | Email delivery (verification, self-service reset), social or OAuth login, public sign-up, two-factor auth, avatar image uploads, a cap on how many playgroups a user can create or join (decision C3: there is none), routing, layout, the auth guard and the API client's token refresh (E-1: S-1.5.3, F-1.6, F-1.7), universal and app links (S-11.5.6), seasons and leagues (E-10), deck legality logic itself (F-4.5), the bracket rule-set and category-list screens the site admin uses (E-5), the profile dashboard layout and widgets (E-8). |

## F-2.1 — Accounts & authentication (invite-only)

Accounts can only be created from an invite link. Sign-in uses a username and password (an email address is optional and never verified) and returns PyJWT access and refresh tokens. Passwords are hashed with argon2id (`argon2-cffi`). With no email service, a playgroup owner or admin issues one-time password reset links, and an operator CLI covers the very first admin, the site admin role and emergency recovery.

E-2 owns every auth screen on web and mobile: sign-in at `/sign-in`, sign-up from an invite, password reset, change password and sign-out. E-1 provides only routing, layout, the auth guard and the API client with token refresh (S-1.5.3); E-2 plugs its session stores into that client. Every shareable link (invite, claim invite, password reset) is an `https` URL on `PUBLIC_WEB_URL`; its web page offers "Open in app" through `mtgstats://…`, and in-app navigation uses `mtgstats://`. Universal and App Links replace the button in Phase 5 (S-11.5.6). Data export and account deletion are in F-2.6.

Phase 0 (admin password reset: phase 1) · Priority: Must

#### S-2.1.1 — Bootstrap the first administrator from the command line

**As an** operator **I want** to create the first admin account and recover passwords from the command line **so that** an invite-only system has someone who can issue the first invite.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-2.2.1, F-1.4 |

**Acceptance criteria**
- **Given** an empty `users` table **When** the operator runs `uv run python -m app.cli create-admin --username hector --display-name "Hector"` (with an optional `--email`) and types a password at the hidden prompt **Then** a User with `is_site_admin = true` and its linked Player are created in one transaction, and the command exits 0.
- **Given** a user with that username already exists (compared case-insensitively) **When** the command runs **Then** it exits non-zero with "username already taken" and no rows change.
- **Given** a username or password that breaks the registration rules (S-2.1.3) **When** the command runs **Then** it is rejected with the same message the API gives.
- **Given** an existing user **When** the operator runs `set-password --username …` **Then** the password is replaced, all of that user's refresh tokens are revoked, and the password never shows up in stdout, logs or shell history.

**Tasks**
- [ ] T-2.1.1.1 (api) `User` model (UUID id, username with a unique index on `lower(username)`, optional unverified email, password_hash, is_site_admin, is_active, created_at, password_changed_at) and its Alembic migration
- [ ] T-2.1.1.2 (api) Password hashing service using `argon2-cffi` (argon2id) with its parameters encoded in the stored hash, and `check_needs_rehash` so hashes are upgraded on the next successful sign-in
- [ ] T-2.1.1.3 (api) `app.cli` entry point (stdlib `argparse`, `getpass`) with `create-admin` and `set-password` commands calling the accounts service
- [ ] T-2.1.1.4 (api) Tests: create, duplicate username, weak password, set-password revokes tokens

#### S-2.1.2 — Create and manage invite links

**As an** admin **I want** to create invite links that expire and can be revoked **so that** only the people I choose can register.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-2.1.1, S-2.1.4 |

**Acceptance criteria**
- **Given** a site admin **When** they `POST /invites` with no body **Then** the response is 201 with an `https` URL `{PUBLIC_WEB_URL}/invite/{token}` (never a `mtgstats://` link), where the token has at least 32 bytes of randomness, `expires_at` is now + 7 days and `max_uses` is 1.
- **Given** `expires_in_days` between 1 and 30 and `max_uses` between 1 and 20 **When** the invite is created **Then** those values are used. Values outside those ranges return 422.
- **Given** any invite **When** the database is inspected **Then** only a SHA-256 hash of the token is stored, never the token itself.
- **Given** a revoked, expired or used-up invite **When** someone checks or uses it **Then** the API returns 410 "invite no longer valid".
- **Given** a user who is not a site admin (and, from phase 1, not the owner or an admin of the playgroup the invite is for, see S-2.4.2) **When** they `POST /invites` **Then** the response is 403. `GET /invites` lists only the caller's invites, with a status of active, used, expired or revoked, and never the token.

**Tasks**
- [ ] T-2.1.2.1 (api) `Invite` model (token_hash, created_by, expires_at, max_uses, use_count, revoked_at) and migration. The playgroup, guest and invited-user columns are added by S-2.4.2, S-2.3.3 and S-2.4.6.
- [ ] T-2.1.2.2 (api) Invite service: create, revoke, validate (`secrets.token_urlsafe`, constant-time hash comparison)
- [ ] T-2.1.2.3 (api) Routes `POST /invites`, `GET /invites`, `POST /invites/{id}/revoke`
- [ ] T-2.1.2.4 (api) Tests for every invite state and the permission rules

#### S-2.1.3 — Register with an invite link (API)

**As an** invited person **I want** to create my account from the invite link **so that** I can join my group's games and stats.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-2.1.2, S-2.2.1 |

**Acceptance criteria**
- **Given** a valid invite **When** `GET /invites/{token}/preview` is called without auth **Then** it returns the inviter's display name and expiry (and the playgroup name once S-2.4.2 exists), and returns 410 for an invalid token.
- **Given** a valid invite **When** `POST /auth/register` is sent with `{invite_token, username, password, display_name}` and an optional `email` **Then** the response is 201. User and Player are created in one transaction, the invite's `use_count` goes up by 1, and the response contains an access token and a refresh token, so the person is signed in.
- **Given** an optional email **When** it is stored **Then** it is not verified, not required to be unique, never used to sign in and never shown to other members.
- **Given** a single-use invite **When** two registrations using it arrive at the same time **Then** exactly one succeeds and the other gets 410, because the invite row is locked.
- **Given** a username that is already registered (case-insensitive) **When** someone registers with it **Then** the response is 409 and the invite is not used up.
- **Given** a username outside 3–24 characters or using characters other than letters, digits, `_`, `-` and `.`, a password outside 12–128 characters, or a display name outside 1–32 characters after trimming **When** someone registers **Then** the response is 422 with field-level errors in the standard error envelope.

**Tasks**
- [ ] T-2.1.3.1 (api) Registration service: check the invite with `SELECT … FOR UPDATE`, create User and Player, increase the invite's use count
- [ ] T-2.1.3.2 (api) Routes for the invite preview and registration, plus Pydantic schemas with validation
- [ ] T-2.1.3.3 (api) Tests covering the concurrency case, a duplicate username and every invalid invite state

#### S-2.1.4 — Sign in with access and refresh tokens

**As a** member **I want** to sign in with my username and password **so that** the apps can call the API for me.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 0 | S-2.1.1, F-1.4 |

**Acceptance criteria**
- **Given** correct credentials **When** `POST /auth/login` is called with `{username, password}` **Then** the response is 200 with a PyJWT access token and a refresh token. The access token uses HS256, lives 15 minutes and carries the claims `sub`, `exp`, `iat`, `jti` and `type=access`. The refresh token lives 30 days. Both lifetimes are settings.
- **Given** an unknown username or a wrong password **When** someone signs in **Then** both cases return 401 with the same generic message, and the unknown-username path still runs a dummy argon2 verification so the timing does not give it away.
- **Given** a stored hash whose argon2 parameters are older than the current settings **When** the user signs in successfully **Then** the hash is replaced with one using the current parameters.
- **Given** a protected endpoint **When** it is called with an expired token, a malformed token, a refresh-type token or a token with `alg=none` **Then** it returns 401. A valid access token returns 200.
- **Given** a user with `is_active = false` **When** they sign in or refresh **Then** they get 401. Access tokens they already hold stop working when they expire, within 15 minutes at most.

**Tasks**
- [ ] T-2.1.4.1 (api) `core/auth`: encode and decode JWTs with PyJWT, an algorithm allow-list, and `JWT_SECRET` read from the environment through pydantic-settings
- [ ] T-2.1.4.2 (api) `get_current_user` FastAPI dependency (the `require_site_admin` dependency is built in S-2.1.11)
- [ ] T-2.1.4.3 (api) Login service and route, including the rehash-on-login step
- [ ] T-2.1.4.4 (api) Tests: expired, tampered, wrong token type, `alg=none`, inactive user, rehash of an outdated hash

#### S-2.1.5 — Refresh token rotation and sign-out

**As a** member **I want** my session to renew quietly and to be able to sign out everywhere **so that** I stay signed in on my phone but a stolen token is not useful for long.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-2.1.4, F-1.9 |

**Acceptance criteria**
- **Given** a valid refresh token **When** `POST /auth/refresh` is called **Then** a new access token and a new refresh token are returned, and the old refresh token is revoked.
- **Given** a refresh token that was already rotated **When** someone uses it again **Then** the response is 401 and every token in that token family is revoked (reuse detection).
- **Given** a web client **When** tokens are issued **Then** the refresh token is set as an `HttpOnly`, `Secure`, `SameSite=Strict` cookie scoped to `/auth` and never appears in the response body. Mobile clients get it in the response body instead and keep it in `expo-secure-store` (S-2.1.7).
- **Given** a signed-in user **When** they call `POST /auth/logout` **Then** the current refresh token is revoked. **When** they call `POST /auth/logout-all` **Then** all of their refresh tokens are revoked.
- **Given** refresh tokens that expired more than 7 days ago **When** the daily cleanup job runs **Then** their rows are deleted.

**Tasks**
- [ ] T-2.1.5.1 (api) `RefreshToken` model (token_hash, user_id, family_id, expires_at, revoked_at, replaced_by) and migration
- [ ] T-2.1.5.2 (api) Rotation, reuse detection and revoke-all in the auth service
- [ ] T-2.1.5.3 (api) Refresh and logout routes, with cookie handling for web clients
- [ ] T-2.1.5.4 (api) Daily arq cron that removes expired tokens
- [ ] T-2.1.5.5 (api) Tests for rotation, reuse and logout-all

#### S-2.1.6 — Sign in and register from an invite on web

**As a** member using a browser **I want** the sign-in page and the invite sign-up page **so that** I can get into the web app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 0 | S-2.1.3, S-2.1.5, F-1.5, F-1.6 |

**Acceptance criteria**
- **Given** a signed-out visitor sent by E-1's auth guard to `/sign-in?next=/decks` **When** they sign in with their username and password (decision C1) **Then** they land on `/decks`. Without `next` they land on the playgroup home. A `next` value that is not a same-origin path is ignored, so the page cannot be used as an open redirect.
- **Given** wrong credentials **When** the form is submitted **Then** the error envelope's message is shown, the password field is cleared and focus returns to it. **Given** a 429 from S-2.1.10 **When** it arrives **Then** the page shows "Too many attempts. Try again in N minutes", using `Retry-After`.
- **Given** a visitor opening `https://<PUBLIC_WEB_URL>/invite/{token}` **When** the invite is valid **Then** the page shows who invited them (and the playgroup name), a registration form (username, display name, password, optional email) and an "Open in app" button that opens `mtgstats://invite/{token}`. **When** the invite is invalid **Then** it shows "This invite has expired or was revoked. Ask your playgroup admin for a new one."
- **Given** registration succeeds **When** the response arrives **Then** the person is signed in and lands on the playgroup home of the playgroup they joined.
- **Given** a signed-in user **When** they reload any page **Then** the session is restored through a cookie refresh before protected content renders, the access token exists only in memory (never in `localStorage` or `sessionStorage`), and the refresh token is only in the HttpOnly cookie (decision B3).
- **Given** the API client from S-1.5.3 **When** the web app creates it **Then** it passes E-2's in-memory session store as the token store (cookie mode), so the refresh-and-retry logic itself stays in `packages/api-client`.
- **Given** a keyboard-only user **When** they fill in either form **Then** every field has a label, Enter submits, and validation errors are announced next to the field they belong to.

**Tasks**
- [ ] T-2.1.6.1 (web) `/sign-in` and `/invite/:token` routes with their forms
- [ ] T-2.1.6.2 (web) Session store (Zustand) holding the access token in memory, implementing the S-1.5.3 token-store interface in cookie mode, plus session restore on page load
- [ ] T-2.1.6.3 (web) Honour the auth guard's `next` parameter on sign-in, with the same-origin check
- [ ] T-2.1.6.4 (web) Vitest tests for `next` handling, the open-redirect guard, error and 429 messages, session restore and form validation
- [ ] T-2.1.6.5 (web) "Open in app" button (`mtgstats://…`) on the invite and reset pages, shown on mobile browsers only

#### S-2.1.7 — Sign in and register from an invite on mobile

**As a** member using a phone **I want** to sign in once and stay signed in **so that** I can open a game at the table without typing my password.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 0 | S-2.1.3, S-2.1.5, F-1.5, F-1.7 |

**Acceptance criteria**
- **Given** a signed-out user on the sign-in screen **When** they enter a valid username and password (decision C1) **Then** both tokens are stored with `expo-secure-store` (decision A10, iOS Keychain or Android Keystore), never in plain async storage, and they land on the playgroup home. Wrong credentials and 429 show the same messages as on web.
- **Given** no network connection **When** the user submits the sign-in or registration form **Then** they see "You're offline" rather than a generic error.
- **Given** a new user on the "I have an invite" screen **When** they paste either a full `https://<PUBLIC_WEB_URL>/invite/{token}` link or just the token **Then** the app extracts the token and shows the invite preview and the registration form. After registering they are signed in.
- **Given** the app is installed **When** the user opens `mtgstats://invite/{token}` or `mtgstats://reset/{token}` (for example from the web page's "Open in app" button) **Then** the app opens the matching screen with the token filled in. Both screens are reachable while signed out.
- **Given** a `mtgstats://` link to a protected screen (for example `mtgstats://join/<code>`) opened while signed out **When** the user signs in **Then** they land on the screen the link pointed to.
- **Given** a user who signed in within the last 30 days **When** they relaunch the app **Then** they land on the home screen already signed in, using the refresh token held in `expo-secure-store`.
- **Given** the API client from S-1.5.3 **When** the mobile app creates it **Then** it passes E-2's secure session store as the token store (body mode), so the refresh-and-retry logic itself stays in `packages/api-client`.

**Tasks**
- [ ] T-2.1.7.1 (mobile) Sign-in and invite screens, with parsing for pasted `https` links and bare tokens
- [ ] T-2.1.7.2 (mobile) Session store on `expo-secure-store` implementing the S-1.5.3 token-store interface in body mode, plus restore on launch
- [ ] T-2.1.7.3 (mobile) Jest tests for token parsing, restore on launch, the offline message and the pending deep link
- [ ] T-2.1.7.4 (mobile) Register the `invite/[token]` and `reset/[token]` Expo Router routes on the `mtgstats` scheme E-1 registers, readable while signed out
- [ ] T-2.1.7.5 (mobile) Keep a pending deep link across sign-in, and detect offline from the client's network-failure error type

#### S-2.1.8 — Change my password

**As a** member **I want** to change my password **so that** I can replace a weak or shared one.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api, web, mobile | 0 | S-2.1.5 |

**Acceptance criteria**
- **Given** a signed-in user **When** they submit the correct current password and a valid new one **Then** the password is updated, all of their other refresh tokens are revoked, and the current session gets new tokens.
- **Given** a wrong current password **When** they submit **Then** the response is 400 with a field error on `current_password` and nothing changes.
- **Given** a new password that breaks the registration rules **When** they submit **Then** the response is 422.

**Tasks**
- [ ] T-2.1.8.1 (api) `POST /auth/change-password` service and route
- [ ] T-2.1.8.2 (web) Change-password form in the account settings
- [ ] T-2.1.8.3 (mobile) Change-password screen

#### S-2.1.9 — Playgroup admin issues a password reset link

**As a** playgroup owner or admin **I want** to give a locked-out member a one-time reset link **so that** they can get back in without an email service.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-2.1.5, S-2.4.3 |

**Acceptance criteria**
- **Given** an owner or admin of playgroup P and a plain member M of P who belongs to no other playgroup **When** the caller chooses "Reset password" for M **Then** a one-time `https` link `{PUBLIC_WEB_URL}/reset/{token}` valid for 24 hours is shown with Copy and Share buttons, and nothing is emailed. Only a hash of the token is stored.
- **Given** M is an admin of P (and belongs to no other playgroup) **When** P's owner requests the reset **Then** it is allowed. **When** another admin requests it **Then** the response is 403, because the owner handles admins.
- **Given** M is also an active member of any other playgroup, or M is P's owner or a site admin **When** anyone requests a reset through this flow **Then** the response is 403 "Ask the site operator", and recovery goes through the CLI `set-password` (S-2.1.1).
- **Given** M opens the link in a browser **When** the page loads **Then** it shows the new-password form and, on a phone, an "Open in app" button that opens `mtgstats://reset/{token}` on the mobile reset screen (S-2.1.7). M can also paste the link into the app's reset screen.
- **Given** M sets a valid new password on web or mobile **When** the reset completes **Then** the new password works, all of M's refresh tokens are revoked, reusing the link returns 410, and M is taken to sign-in with "Password changed. Sign in with your new password." M's old password keeps working until the link is used.
- **Given** an earlier unused reset link for M **When** a new one is issued **Then** the earlier link returns 410.
- **Given** a caller who is not an owner or admin of a playgroup M belongs to, or who targets themselves **When** they request a reset **Then** the response is 403. Self-service goes through S-2.1.8.
- **Given** a reset was issued **When** M next signs in **Then** they see "Your password was reset by <admin> on <date>", and an audit row records the issuer, the target and the time.

**Tasks**
- [ ] T-2.1.9.1 (api) `PasswordResetToken` model (hashed token, target user, issued_by, expires_at, used_at) and `AuditEntry` rows
- [ ] T-2.1.9.2 (api) Routes to issue a link (owner/admin, with the no-other-playgroup and owner-handles-admins rules) and to complete the reset (public, token-based)
- [ ] T-2.1.9.3 (web) "Reset password" action on the member list (hidden where the caller may not reset), plus the public `/reset/:token` page with its "Open in app" button
- [ ] T-2.1.9.4 (mobile) "Reset password" action with the native share sheet, plus a reset screen that accepts a pasted `https` link, a bare token or `mtgstats://reset/{token}`
- [ ] T-2.1.9.5 (api) Tests for every permission rule, expiry, single use and the superseded-link case

#### S-2.1.10 — Throttle failed sign-in attempts

**As an** operator **I want** repeated failed sign-ins to be slowed down **so that** passwords cannot be brute-forced.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 0 | S-2.1.4, F-1.3 |

**Acceptance criteria**
- **Given** 5 failed sign-ins for one username within 15 minutes **When** a 6th attempt arrives **Then** it gets 429 with a `Retry-After` header, even if the password is correct, until the 15-minute window ends.
- **Given** 20 failed attempts from one IP address within 15 minutes, across any accounts **When** another attempt arrives **Then** it gets 429.
- **Given** a successful sign-in **When** it completes **Then** the failure counter for that username is reset.
- **Given** the registration, invite preview and reset-completion endpoints **When** a single IP calls them more than 30 times in 15 minutes **Then** it gets 429. All limits are settings and the counters live in Redis with a TTL.

**Tasks**
- [ ] T-2.1.10.1 (api) Redis fixed-window limiter in `core/` as a FastAPI dependency
- [ ] T-2.1.10.2 (api) Apply the limiter to the auth, invite preview and reset routes
- [ ] T-2.1.10.3 (api) Tests using a test Redis, covering the username, IP and reset-on-success rules

#### S-2.1.11 — Grant and check the site admin role

**As an** operator **I want** to grant and revoke a global site admin role from the command line, and have the API check it on every admin endpoint, **so that** only trusted people can publish bracket rule sets and maintain the category lists (E-5).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-2.1.1, S-2.1.4 |

**Acceptance criteria**
- **Given** an existing user **When** the operator runs `uv run python -m app.cli grant-site-admin --username <name>` **Then** `is_site_admin` becomes true, the change is logged with the old and new value and the time, and the command exits 0. `revoke-site-admin --username <name>` sets it back to false. An unknown username exits non-zero and changes nothing.
- **Given** exactly one site admin **When** `revoke-site-admin` targets them **Then** the command refuses with "at least one site admin is required" and nothing changes.
- **Given** the OpenAPI spec **When** it is inspected **Then** no route grants or revokes the role. It can only be changed from the CLI (and `create-admin` in S-2.1.1).
- **Given** an endpoint protected by `require_site_admin` (for example E-5's rule-set publishing and category lists, and `GET /admin/card-sync/runs` in S-3.1.7) **When** the caller is not a site admin **Then** the response is 403. The dependency reads `is_site_admin` from the database on every request, so a revoke takes effect on the caller's next request rather than when their access token expires.
- **Given** a signed-in user **When** the client calls `GET /auth/me` **Then** the response includes `is_site_admin`, which the web and mobile apps use only to show or hide the admin entry. The API never trusts a client-supplied value.
- **Given** a site admin **When** they call any `/playgroups/{id}/…` endpoint for a playgroup they are not a member of **Then** the response is 404 as for anyone else. The role grants no playgroup access, and no playgroup role grants it.

**Tasks**
- [ ] T-2.1.11.1 (api) `grant-site-admin` and `revoke-site-admin` CLI commands with the last-site-admin rule
- [ ] T-2.1.11.2 (api) `require_site_admin` dependency that re-reads the user row, exported from `core/auth`
- [ ] T-2.1.11.3 (api) `GET /auth/me` returning the user's id, username, display name and `is_site_admin`
- [ ] T-2.1.11.4 (api) Tests: grant, revoke, last site admin, revoke takes effect on the next request, no playgroup access

#### S-2.1.12 — Sign out and session expiry on web and mobile

**As a** member **I want** to sign out on this device or everywhere, and be told clearly when my session has ended **so that** a shared or lost device can't be used as me.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 0 | S-2.1.5, S-2.1.6, S-2.1.7 |

**Acceptance criteria**
- **Given** a signed-in user on web **When** they choose Sign out **Then** the app calls `POST /auth/logout` (which clears the refresh cookie), clears the in-memory access token, the TanStack Query cache and every user-scoped store, and shows `/sign-in`. Other open tabs of the app sign out too, within 1 s.
- **Given** a signed-in user on mobile **When** they tap Sign out (from More or account settings) **Then** the app calls `/auth/logout` and deletes the tokens in `expo-secure-store` and the query cache, even when it is offline (the API call is best effort), and shows the sign-in screen.
- **Given** a signed-in user on either platform **When** they choose "Sign out everywhere" and confirm **Then** the app calls `POST /auth/logout-all`, every device's next refresh fails, and this device signs out as above.
- **Given** the API client calls `onSessionExpired` (S-1.5.3) **When** the user is on a protected page or screen **Then** they are sent to sign-in with "Your session has expired". On web the current path is kept as `next`; on mobile the current screen is kept as the pending deep link.
- **Given** a keyboard-only user on web **When** they use Sign out and "Sign out everywhere" **Then** both work with Tab, Enter and Escape, and the confirmation traps focus.

**Tasks**
- [ ] T-2.1.12.1 (web) Sign-out and "Sign out everywhere" actions in the account menu, cache and store clearing, and cross-tab sign-out with `BroadcastChannel`
- [ ] T-2.1.12.2 (mobile) Sign-out and "Sign out everywhere" actions, secure-store and cache clearing that works offline
- [ ] T-2.1.12.3 (web, mobile) `onSessionExpired` handler that routes to sign-in with the message and keeps the return target
- [ ] T-2.1.12.4 (web, mobile) Vitest and Jest tests: sign-out clears everything, offline sign-out, sign-out everywhere, session-expired redirect

## F-2.2 — Player profiles

A Player is the person at the table. Every User has exactly one Player, and a guest is a Player with no User. All game data refers to Player, never to User, so guests and members work the same way. Avatars are a color with initials, or a card's art crop from Scryfall. There are no image uploads. Visitors see the owner's profile layout (E-8); the owner can hide sections, and spend and collection value are private unless the owner shares them with the playgroup (decision C8, S-2.2.5).

Phase 0 (player model), 1 (profile screens) and 2 (profile privacy) · Priority: Must

#### S-2.2.1 — One player identity for members and guests

**As a** developer **I want** a single Player entity linked to an optional User **so that** games, stats and leagues can refer to members and guests the same way.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | F-1.4 |

**Acceptance criteria**
- **Given** any registered User **When** the database is inspected **Then** that User has exactly one Player, enforced by a unique `user_id`.
- **Given** a guest **When** it is stored **Then** its Player has `user_id = NULL` and `is_guest = true`, and the database rejects a row where `is_guest = true` and `user_id` is not null.
- **Given** a new Player **When** it is created **Then** its `display_name` (1–32 characters, trimmed) is required, and its `avatar_color` is one of 12 palette tokens, assigned from the player id when not given. The default avatar is the player's initials on that color.
- **Given** another domain needs player data **When** it imports from `players` **Then** it can use only `players.services` (`get_player`, `get_players`, `get_player_for_user`), never the models directly.

**Tasks**
- [ ] T-2.2.1.1 (api) `Player` model (id, user_id, is_guest, display_name, avatar_color, home_playgroup_id nullable, merged_into_id nullable, created_at), check constraint and migration. `avatar_printing_id` is added by S-2.2.2.
- [ ] T-2.2.1.2 (api) Public interface in `players/services/__init__.py`
- [ ] T-2.2.1.3 (api) Factories for User, Player and guest Player
- [ ] T-2.2.1.4 (shared) Check the generated `Player` type in `packages/api-client`

#### S-2.2.2 — Edit my profile and avatar

**As a** member **I want** to change my display name and avatar (a color with my initials, or a card's art crop) **so that** my friends recognise me at the table and in stats.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-2.2.1, S-2.1.6, S-2.1.7, S-3.3.1, S-3.3.2 |

**Acceptance criteria**
- **Given** a signed-in member **When** they `PATCH /players/me` with a new display name, avatar color or `avatar_printing_id` **Then** the change is saved, and the next request for any playgroup member list shows it.
- **Given** an `avatar_printing_id` **When** the avatar renders **Then** it shows that printing's Scryfall `art_crop` image, hotlinked from Scryfall and cropped to a circle. If the image fails to load, the initials-on-color avatar is shown instead. Setting `avatar_printing_id` to null goes back to initials on color.
- **Given** an unknown printing id, or a printing with no `art_crop` image **When** they save **Then** the response is 422. No endpoint accepts an uploaded image.
- **Given** a display name outside 1–32 characters after trimming, or an unknown color token **When** they save **Then** the response is 422.
- **Given** a member **When** they try to edit another member's Player **Then** the response is 403. Guests are edited through S-2.3.1.
- **Given** a keyboard-only user on web **When** they choose a color **Then** the palette works as a radio group with the arrow keys, and the card art picker (card autocomplete, then a printing) works without a mouse.

**Tasks**
- [ ] T-2.2.2.1 (api) Profile update service and `PATCH /players/me` route, plus the nullable `avatar_printing_id` column (migration), validated through `cards/services`
- [ ] T-2.2.2.2 (web) `Avatar` component (initials on color, or art crop with fallback) and the profile settings page with a color radio group and a card art picker
- [ ] T-2.2.2.3 (mobile) `Avatar` component and the profile settings screen with the same choices
- [ ] T-2.2.2.4 (web) Vitest tests for the avatar fallback and the picker

#### S-2.2.3 — View a player's profile

**As a** member **I want** to open another player's profile **so that** I can see who they are and how they do.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 1 | S-2.2.1, S-2.4.1 |

**Acceptance criteria**
- **Given** two users who share a playgroup **When** one opens `/players/{id}` for the other **Then** they see the display name, avatar, a guest badge if it applies, "member since", and the playgroups they have in common.
- **Given** a player who shares no playgroup with the viewer **When** the viewer requests that profile **Then** the response is 404, not 403, so it does not reveal that the player exists.
- **Given** a guest that has been merged (`merged_into_id` is set) **When** someone opens the guest's old profile URL **Then** they are redirected to the surviving player's profile.
- **Given** F-7.1 is available **When** the profile loads **Then** it shows games played and win rate. Until then, that section is hidden rather than showing empty values.

**Tasks**
- [ ] T-2.2.3.1 (api) `GET /players/{id}` with a visibility check through the playgroups service, plus the merged-player redirect
- [ ] T-2.2.3.2 (web) Player profile page
- [ ] T-2.2.3.3 (mobile) Player profile screen

#### S-2.2.4 — Pick players from my playgroup

**As a** game host **I want** a quick player picker that lists members and guests **so that** I can fill seats in a few taps.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-2.3.1, S-2.4.1 |

**Acceptance criteria**
- **Given** the active playgroup has 10 members and 3 guests **When** the picker opens **Then** it lists members first, then guests, each group in alphabetical order, with avatars.
- **Given** the user types "jo" **When** the list filters **Then** matching ignores case and accents ("Jöel" matches) and results appear within 200 ms using `GET /playgroups/{id}/players?q=`.
- **Given** a player already chosen for this game **When** the picker opens again **Then** that player is shown as disabled.
- **Given** no match **When** the user chooses "Add guest '<text>'" **Then** a guest is created (S-2.3.1) and selected in one step.
- **Given** a keyboard user on web **When** they use the picker **Then** the arrow keys move through the list, Enter selects and Escape closes.

**Tasks**
- [ ] T-2.2.4.1 (api) Playgroup players query (members and guests, with search) in the playgroups repository
- [ ] T-2.2.4.2 (web) `PlayerPicker` component (accessible listbox)
- [ ] T-2.2.4.3 (mobile) `PlayerPicker` bottom sheet with a search field
- [ ] T-2.2.4.4 (web) Vitest tests for filtering and the disabled state

#### S-2.2.5 — Profile privacy settings

**As a** member **I want** to hide parts of my profile from other members and decide whether my spend and collection value are shared **so that** I control what my playgroup sees about me (decision C8).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 2 | S-2.2.3 |

**Acceptance criteria**
- **Given** a new member **When** their privacy settings are read **Then** every profile section is visible to members who share a playgroup with them, except `spend` and `collection_value`, which are private (`share_spend = false`, `share_collection_value = false`).
- **Given** the owner of a profile **When** they `PATCH /players/me/privacy` with `hidden_sections` (any of `stats`, `recent_games`, `decks`, `activity`, `meta`) and the two share flags **Then** the settings are saved. An unknown section key returns 422.
- **Given** another member views the profile **When** `profile_visibility(owner_player_id, viewer_user_id)` is called **Then** it returns the sections the viewer may see in one query: hidden sections are left out, and `spend` and `collection_value` are included only if the owner opted in and the viewer shares a playgroup with them. The owner always gets every section. E-8 and E-9 call this function rather than reading the settings directly, and S-2.2.3 uses it.
- **Given** a viewer requests data for a section the owner hid **When** the API answers **Then** that section is simply missing from the response; it never returns the hidden data.
- **Given** a guest player **When** their profile is viewed **Then** no settings apply and spend and collection value never exist for them.
- **Given** the privacy settings screen on web **When** a keyboard-only user uses it **Then** every toggle has a label and works with Space.

**Tasks**
- [ ] T-2.2.5.1 (api) `ProfilePrivacy` settings on `Player` (hidden_sections, share_spend, share_collection_value) with migration, the `PATCH /players/me/privacy` route and `profile_visibility` in `players/services/__init__.py`
- [ ] T-2.2.5.2 (web) Privacy section in the profile settings page
- [ ] T-2.2.5.3 (mobile) Privacy section in the profile settings screen
- [ ] T-2.2.5.4 (api) Tests: defaults, opt-in sharing, viewer outside the playgroup, owner sees all, hidden data never returned

## F-2.3 — Guest players

Guests let people play without an account. A guest belongs to the playgroup that created it, can control their own seat from their phone with a game-scoped token, and is counted in stats and standings like a member. A guest can later be claimed or merged into a real account without losing any game history, and the claim or merge triggers a stats rebuild (F-7.1).

Phase 1 · Priority: Must

#### S-2.3.1 — Add a guest player to a playgroup

**As a** playgroup member **I want** to add a friend as a guest **so that** they can play and be counted without signing up.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-2.2.1, S-2.4.1 |

**Acceptance criteria**
- **Given** a member of playgroup P **When** they `POST /playgroups/{id}/guests` with a name of 1–32 characters **Then** a guest Player with `home_playgroup_id = P` is created and returned with status 201.
- **Given** a name that matches an existing member or guest of P, ignoring case and accents **When** they add the guest **Then** the response is 409 "A player named 'X' already exists in this playgroup".
- **Given** a guest **When** the guest's creator or P's owner or an admin renames it **Then** the change is saved. Any other member gets 403.
- **Given** a guest **When** anyone tries to sign in as that guest **Then** it is impossible, because guests have no credentials.

**Tasks**
- [ ] T-2.3.1.1 (api) Guest service: create and rename, with the uniqueness rule inside the playgroup
- [ ] T-2.3.1.2 (api) Routes and tests
- [ ] T-2.3.1.3 (web) "Add guest" dialog in the playgroup's player list
- [ ] T-2.3.1.4 (mobile) "Add guest" sheet

#### S-2.3.2 — Game-scoped access token for a guest's device

**As a** guest at the table **I want** to control my own seat from my phone **so that** I can track my life without an account.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-2.3.1, S-2.1.4 |

**Acceptance criteria**
- **Given** a guest seated in game G **When** the guest-token service is called with that guest and G (from F-6.2's join flow) **Then** it returns a PyJWT token with `type=guest`, `player_id` and `game_id`, and an `exp` 7 days after issue: the hard cap (a setting).
- **Given** a guest token **When** it is used on any REST or WebSocket endpoint other than game G's player actions **Then** the response is 401 or 403.
- **Given** a guest token for G **When** it is used **Then** it is valid until 24 hours after G ends or is abandoned, and never beyond the 7-day hard cap, whichever comes first (the same rule as S-6.2.2). The end time is checked through the games service on every use, because it isn't known when the token is issued. Within those 24 hours the token is still accepted on G's endpoints, and E-6 decides which actions an ended game allows.
- **Given** G was abandoned and the host resumes it within the 7-day cap **When** the guest's token is used **Then** it is accepted again, because G has no end time any more.
- **Given** a guest token **When** it is sent to `/auth/refresh` **Then** it is rejected, because guest tokens cannot be refreshed.

**Tasks**
- [ ] T-2.3.2.1 (api) `issue_guest_token` in `players/services` and a `get_game_actor` auth dependency that accepts either a user token or a guest token
- [ ] T-2.3.2.2 (api) Tests for scoping, the 24-hour-after-end rule, abandoned and resumed games, and the 7-day hard cap winning over a game that never ends
- [ ] T-2.3.2.3 (shared) Document the guest token in the OpenAPI security schemes so `packages/api-client` exposes it

#### S-2.3.3 — Claim a guest when registering

**As a** guest who decides to sign up **I want** my new account to be my guest player **so that** all my past games count as mine.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-2.1.3, S-2.3.1, S-2.3.4, S-2.4.2 |

**Acceptance criteria**
- **Given** the owner or an admin of P and a guest G in P **When** they create a claim invite for G **Then** a single-use invite is created that is bound to G and to P.
- **Given** a new person registers with that invite **When** registration completes **Then** G becomes their Player (`user_id` set, `is_guest = false`, same player id), they become a member of P, and all of G's game seats stay attributed to them without moving any rows.
- **Given** an existing signed-in user opens a claim invite **When** they confirm "This is me" **Then** G is merged into their Player through the S-2.3.4 service, and the invite is used up.
- **Given** either claim path completes **When** the transaction commits **Then** a stats refresh is queued for every game G played (`refresh_game_facts`, S-7.1.1), so G's history shows under the account in stats and standings once the jobs finish.
- **Given** G has already been claimed or merged **When** someone uses the claim invite **Then** the response is 410.

**Tasks**
- [ ] T-2.3.3.1 (api) Add `guest_player_id` to `Invite` (migration) and the claim branch in the registration service
- [ ] T-2.3.3.2 (api) Claim flow for an existing user, which calls the merge service
- [ ] T-2.3.3.3 (web) Invite page variant: "You're claiming <guest name>'s history"
- [ ] T-2.3.3.4 (mobile) Same variant on the invite screen
- [ ] T-2.3.3.5 (api) Tests: claim on registration, claim by an existing user, claim twice, stats refresh queued after commit

#### S-2.3.4 — Merge a guest into an existing account

**As a** playgroup admin **I want** to merge a guest into a member's player **so that** someone who played as a guest before registering has one complete history.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-2.3.1, S-6.3.1, S-6.7.4, S-7.1.1 |

**Acceptance criteria**
- **Given** guest G with 12 game seats and member player M **When** the owner or an admin of G's playgroup runs merge(G → M) **Then** all 12 seats and every game event that names G as actor or target now refer to M, and G gets `merged_into_id = M`.
- **Given** the merge has committed **When** the stats jobs run **Then** a `refresh_game_facts` job (S-7.1.1) was queued for each of the 12 games, and afterwards M's stats and every affected playgroup's standings include those games.
- **Given** the same person played as guest G1 in P and guest G2 in Q **When** G1 and G2 are each merged into M by their own playgroup's owner or admin **Then** both histories end up on M, as long as no game seated both of them.
- **Given** G and M were both seated in the same game **When** the merge runs **Then** it is rejected with 409, the response lists the conflicting game ids, and nothing changes.
- **Given** a failure partway through **When** the merge stops **Then** the whole merge runs in one transaction, so no partial reassignment is left behind and no stats job is queued.
- **Given** a merge has completed **When** the audit log is read **Then** it records who merged, when, and the ids of every reassigned seat, so the merge could be reversed by hand. There is no undo button in the UI.
- **Given** a caller who is not the owner or an admin of G's home playgroup **When** they request the merge **Then** the response is 403, unless the call comes from the claim flow in S-2.3.3.

**Tasks**
- [ ] T-2.3.4.1 (api) `merge_guest` service in `players/services`, called with an explicit list of domains that hold player references (no signals)
- [ ] T-2.3.4.2 (api) Ask the games domain to expose `reassign_player(from_id, to_id)` and `find_shared_games(a, b)` in its public services interface (coordinate with E-6)
- [ ] T-2.3.4.3 (api) `POST /playgroups/{id}/guests/{guest_id}/merge` route with audit entries
- [ ] T-2.3.4.4 (api) Queue `refresh_game_facts` for every reassigned game after commit, through `stats.services` (coordinate with E-7)
- [ ] T-2.3.4.5 (api) Tests: successful merge, two guests into one account, conflict, rollback when the games step fails partway, stats jobs queued only on success

#### S-2.3.5 — Admin screens for claiming and merging guests

**As a** playgroup admin **I want** claim and merge actions on the guest list **so that** I can tidy up guests without using the API.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | web, mobile | 1 | S-2.3.3, S-2.3.4 |

**Acceptance criteria**
- **Given** the owner or an admin viewing the guest list **When** they choose "Invite to claim" **Then** a claim invite link is shown with Copy and Share buttons.
- **Given** the owner or an admin chooses "Merge into member…" and picks a member **When** the confirmation opens **Then** it shows how many games will move, any conflicting games, and that stats will be rebuilt. Confirm stays disabled while there are conflicts.
- **Given** a keyboard-only user on web **When** they use both actions **Then** they can complete them with Tab, Enter and Escape alone.

**Tasks**
- [ ] T-2.3.5.1 (web) Guest admin actions and the merge confirmation dialog
- [ ] T-2.3.5.2 (mobile) Guest admin actions and the merge confirmation sheet
- [ ] T-2.3.5.3 (api) Merge preview endpoint (game count and conflicts) that makes no changes

## F-2.4 — Playgroups & membership

A playgroup is the group of people who play together. Any signed-in user can create one and becomes its owner. Members have the role owner (exactly one), admin or member. The owner and admins invite people and manage members; the owner manages admins. There is no cap on how many playgroups a user can create or join (decision C3). Guests, games, settings (time zone, small-sample threshold), house rules and (later) seasons belong to a playgroup. A member who leaves keeps read-only access to their own games, which stay in the playgroup's stats under their name (decision C7). The owner can archive or delete the playgroup (decision C9, S-2.6.3).

Phase 1 · Priority: Must

#### S-2.4.1 — Create a playgroup

**As a** signed-in user **I want** to create a playgroup **so that** my friends and our games have a home.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-2.1.4, S-2.2.1 |

**Acceptance criteria**
- **Given** any signed-in user **When** they `POST /playgroups` with a name of 3–50 characters and an optional description of up to 500 characters **Then** the playgroup is created and the creator becomes its owner. The database allows exactly one owner per playgroup.
- **Given** the owner or an admin **When** they rename the playgroup or edit its description **Then** the change is saved. A plain member gets 403.
- **Given** a user who is not a member **When** they call any `/playgroups/{id}/…` endpoint **Then** the response is 404, so the playgroup's existence is not revealed.
- **Given** a user in several playgroups **When** they call `GET /playgroups` **Then** it returns every playgroup they belong to, with their role in each, paged with a cursor (decision B2).
- **Given** a user who already owns or belongs to any number of playgroups **When** they create or join another **Then** it succeeds: there is no cap (decision C3).

**Tasks**
- [ ] T-2.4.1.1 (api) `Playgroup` and `Membership` models (role: owner, admin or member, joined_at, left_at), a partial unique index allowing one active owner per playgroup, and migration
- [ ] T-2.4.1.2 (api) `require_member`, `require_playgroup_admin` (owner or admin) and `require_playgroup_owner` dependencies, plus the public `playgroups/services` interface
- [ ] T-2.4.1.3 (api) Routes and tests
- [ ] T-2.4.1.4 (web) "Create playgroup" form
- [ ] T-2.4.1.5 (mobile) "Create playgroup" screen

#### S-2.4.2 — Invite people to a playgroup

**As a** playgroup owner or admin **I want** to share an invite link to my playgroup **so that** new and existing users can join it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-2.4.1, S-2.1.2, S-2.1.6, S-2.1.7 |

**Acceptance criteria**
- **Given** the owner or an admin of P **When** they create an invite for P **Then** the invite is bound to P and its preview shows P's name.
- **Given** a new person registers with that invite **When** registration completes **Then** they are a member of P.
- **Given** an existing signed-in user opens the invite **When** they confirm "Join <P>" **Then** they become a member and the invite's use count goes up. If they are already a member, they see "You're already in <P>" and no use is counted.
- **Given** a plain member of P **When** they try to create an invite for P **Then** the response is 403.
- **Given** the invite link on mobile **When** the owner or admin taps Share **Then** the native share sheet opens with the link.

**Tasks**
- [ ] T-2.4.2.1 (api) Add `playgroup_id` to `Invite` (migration), plus the join-on-register and join-existing-user branches
- [ ] T-2.4.2.2 (web) Invite panel on the playgroup page: create, copy, revoke, list
- [ ] T-2.4.2.3 (mobile) Invite panel using the React Native `Share` API
- [ ] T-2.4.2.4 (api) Tests for the join branches and permissions

#### S-2.4.3 — Manage admins and transfer ownership

**As a** playgroup owner **I want** to make members admins, remove their admin role and hand over ownership **so that** running the group does not depend on one person.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-2.4.1 |

**Acceptance criteria**
- **Given** the owner **When** they promote a member to admin, or demote an admin to member **Then** the role changes and that person's next request uses the new permissions.
- **Given** an admin who is not the owner, or a plain member **When** they try to change any role **Then** the response is 403.
- **Given** the owner **When** they transfer ownership to another member **Then** that member becomes the owner and the previous owner becomes an admin, in one transaction, so the playgroup always has exactly one owner.
- **Given** the owner **When** they try to demote themselves **Then** the response is 409 "Transfer ownership first".

**Tasks**
- [ ] T-2.4.3.1 (api) `PATCH /playgroups/{id}/members/{player_id}` role change (owner only) and `POST /playgroups/{id}/transfer-ownership`
- [ ] T-2.4.3.2 (web) Role and ownership controls on the member list
- [ ] T-2.4.3.3 (mobile) Role and ownership controls on the member list
- [ ] T-2.4.3.4 (api) Tests for the owner-only rule, transfer and self-demotion

#### S-2.4.4 — Remove a member or leave a playgroup

**As a** member **I want** to leave a playgroup, and **as an** owner or admin I want to remove someone, **so that** membership reflects who actually plays.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-2.4.3, S-6.7.4 |

**Acceptance criteria**
- **Given** the owner or an admin removes plain member X, or X leaves **When** X next requests any of P's pages **Then** they get 404. X's games stay in P's stats and standings under X's name (decision C7), and member lists show X as "former member".
- **Given** X has left or been removed from P **When** X opens their own profile **Then** a "Former playgroups" section lists P with the games X was seated in, and X can open each of those games read-only (summary, seats, results and timeline). X cannot see P's other games, member list, stats pages or house rules, and cannot change anything.
- **Given** an admin tries to remove another admin, or anyone tries to remove the owner **When** the request arrives **Then** the response is 403. Only the owner removes admins.
- **Given** X is invited back later **When** X rejoins **Then** the same Player is reused, so X's earlier history connects again and P leaves X's "Former playgroups".
- **Given** the owner of a playgroup that still has other members **When** they try to leave **Then** the response is 409 "Transfer ownership first".
- **Given** the last member leaves **When** the playgroup has no members left **Then** it is archived (read-only, `archived_at` set) rather than deleted. Owner-initiated archiving and deletion are in S-2.6.3.

**Tasks**
- [ ] T-2.4.4.1 (api) Leave and remove services (set `left_at`, keep the Player), the owner and admin rules, and the archive rule
- [ ] T-2.4.4.2 (web) "Leave" and "Remove" actions with confirmation, and the "Former playgroups" section on my profile
- [ ] T-2.4.4.3 (mobile) "Leave" and "Remove" actions with confirmation, and the "Former playgroups" section on my profile
- [ ] T-2.4.4.4 (api) `former_playgroups(player_id)` in `playgroups/services`, and a seated-player read rule `can_view_game(player_id, game_id)` that the games and history endpoints use (coordinate with E-6 and E-7)
- [ ] T-2.4.4.5 (api) Tests for access after removal, read-only access to own games only, rejoining, the owner-leaves rule and who may remove whom

#### S-2.4.5 — Playgroup home and switcher

**As a** member of one or more playgroups **I want** a playgroup home and a quick way to switch between groups **so that** games and stats always show the right group.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-2.4.1, S-2.4.2 |

**Acceptance criteria**
- **Given** a user with no playgroups **When** they sign in **Then** they see an empty state with "Create a playgroup" and "I have an invite".
- **Given** a user in 2 or more playgroups **When** they pick one in the switcher **Then** every playgroup-scoped screen reloads for that group, and the choice is remembered on that device across restarts.
- **Given** the playgroup home **When** it loads **Then** it shows the owner, admins and members with their roles, then guests. Owner-and-admin actions are hidden from plain members, and owner-only actions are hidden from admins.
- **Given** a keyboard user on web **When** they use the switcher **Then** it can be opened, moved through and selected without a mouse.

**Tasks**
- [ ] T-2.4.5.1 (web) Active-playgroup store (Zustand, remembered per device), switcher and home page
- [ ] T-2.4.5.2 (mobile) Active-playgroup store, switcher and home screen
- [ ] T-2.4.5.3 (web) Vitest tests for the switcher and the role-based visibility

#### S-2.4.6 — Targeted invite or invite suggestion after a game with a non-member

**As a** game host **I want** to invite a signed-in non-member who just played with us into the playgroup in one tap **so that** a newcomer who joined by game code can become a regular without a separate invite link.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, web, mobile | 1 | S-2.4.2, S-6.7.3 |

**Acceptance criteria**
- **Given** a finished game in P where signed-in user U, who is not a member of P, played a seat under their own account (seat marked `non_member`, S-6.2.2) **When** E-6's final screen (S-6.7.7) asks `invite_status_for_seats(game_id)` **Then** it learns which seats can be invited. Members, guests and users who already have a pending invite or suggestion for P get none.
- **Given** the host is P's owner or an admin **When** they tap "Invite to <P>" (the button S-6.7.7 shows), which calls `invite_after_game(game_id, player_id, host)` **Then** a single-use invite bound to P and to U's account (`invited_user_id`, 7-day expiry) is created and the button reads "Invited". No link has to be shared.
- **Given** the host is a plain member **When** they tap the same button **Then** an invite suggestion is created instead and the button reads "Suggested to admins". P's owner and admins see "<host> suggests inviting <U> (played on <date>)" with Approve and Dismiss. Approve creates the invite as above, because only the owner and admins can invite (decision C2, C3).
- **Given** U has a pending invite **When** U opens the app **Then** the playgroup switcher and home show "<inviter> invited you to <P>" with Join and Decline. Join makes U a member through the same path as S-2.4.2, and Decline revokes the invite. Any other user who tries to accept it gets 403.
- **Given** the button is tapped twice **When** the second request arrives **Then** no second invite or suggestion is created.

**Tasks**
- [ ] T-2.4.6.1 (api) Add `invited_user_id` to `Invite` (migration), an `InviteSuggestion` model, and services and routes to create a targeted invite, suggest, approve, dismiss, accept and decline; export `invite_status_for_seats` and `invite_after_game` from `playgroups/services` for S-6.7.7
- [ ] T-2.4.6.2 (api) Ask the games domain to expose the non-member seats of a finished game in its public services interface (coordinate with E-6)
- [ ] T-2.4.6.3 (web) Pending-invite banner and the admin suggestion list (the button on the final screen belongs to S-6.7.7)
- [ ] T-2.4.6.4 (mobile) Pending-invite banner and the admin suggestion list
- [ ] T-2.4.6.5 (api) Tests: owner/admin host, plain-member host, duplicate tap, approve and dismiss, accept by the wrong user, decline

#### S-2.4.7 — Playgroup settings: time zone and small-sample threshold

**As a** playgroup owner **I want** to set our playgroup's time zone, and as a member to override it for myself, **so that** days, weeks and streaks line up with where we actually play (decision B8), and stats use our small-sample threshold.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-2.4.3, S-2.2.2 |

**Acceptance criteria**
- **Given** a user creates a playgroup **When** the client sends the device's IANA time zone with `POST /playgroups` **Then** it becomes the playgroup's `time_zone`; without one it is `UTC`. `low_sample_games` starts at 5.
- **Given** P's owner **When** they `PATCH /playgroups/{id}/settings` with a `time_zone` **Then** it is saved with who changed it and when. A name not in `zoneinfo.available_timezones()` returns 422. Admins and plain members get 403, because the owner sets the time zone; every member can read it.
- **Given** the owner or an admin changes the small-sample threshold through S-7.1.8's endpoint **When** it calls `set_low_sample_games(playgroup_id, actor, value)` from `playgroups/services` **Then** a whole number from 1 to 50 is saved and anything else raises a validation error (422). E-2 owns the column; E-7 owns the control and endpoint.
- **Given** a member **When** they set or clear their own `time_zone` in profile settings **Then** it is saved on their account. "Use the playgroup's time zone" is the default.
- **Given** any domain that buckets by day or week **When** it calls `effective_time_zone(user_id, playgroup_id=None)` **Then** it gets the user's override if set; otherwise the playgroup's time zone when a playgroup is given; otherwise the time zone of the user's earliest-joined active playgroup; otherwise `UTC`. `playgroup_settings(playgroup_id)` returns `time_zone` and `low_sample_games` in one query.
- **Given** a time zone change **When** stats or activity are read afterwards **Then** only the bucketing changes; stored timestamps stay in UTC and no stored row is rewritten.

**Tasks**
- [ ] T-2.4.7.1 (api) `time_zone` (default `UTC`) and `low_sample_games` (default 5) columns on `Playgroup`, nullable `time_zone` on `User`, and migration (this replaces the column S-7.1.8 planned to add; coordinate with E-7)
- [ ] T-2.4.7.2 (api) `PATCH /playgroups/{id}/settings` (owner only), `set_low_sample_games`, `playgroup_settings` and `effective_time_zone` in `playgroups/services/__init__.py`
- [ ] T-2.4.7.3 (web) Time zone picker in playgroup settings (owner only) and in my profile settings
- [ ] T-2.4.7.4 (mobile) Time zone picker in playgroup settings (owner only) and in my profile settings
- [ ] T-2.4.7.5 (api) Tests: creation default, invalid zone, owner-only rule, threshold range, every fallback step of `effective_time_zone`

## F-2.5 — House rules & house bans

Each playgroup keeps its own house rules, which apply to games in that playgroup and to decks viewed in it: game defaults that override the official 40 starting life, 21 commander damage and 10 poison; house bans that add to the official Commander banned list; "rule 0" allowances that permit officially banned cards; and a free-text house rules note. F-4.5 gets the bans and allowances through a single service call, and F-6.2 gets the game defaults the same way.

Phase 1 · Priority: Must

#### S-2.5.1 — Maintain the house ban and allow lists

**As a** playgroup owner or admin **I want** to ban specific cards for our group, or allow cards that are officially banned ("rule 0") **so that** deck validation follows our table's rules for everyone.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-2.4.3, S-3.2.1 |

**Acceptance criteria**
- **Given** the owner or an admin of P **When** they `POST /playgroups/{id}/house-bans` with an `oracle_id`, a `kind` of `ban` (the default) or `allow`, and an optional reason of up to 200 characters **Then** the entry is created with the admin and the time recorded.
- **Given** a card that already has an active entry of either kind in P, or an unknown `oracle_id` **When** an admin adds it **Then** the response is 409 or 422 respectively.
- **Given** a `ban` for a card that is already banned in Commander (Scryfall legality `banned`) **When** an admin adds it **Then** the ban is accepted and the response includes `already_banned_in_commander: true`.
- **Given** an `allow` for a card whose Commander legality is not `banned` **When** an admin adds it **Then** the response is 422 "Only officially banned cards can be allowed".
- **Given** an allowed card that is later unbanned officially **When** the list is read **Then** the entry is still there, marked `officially_banned: false`, so admins can tidy it up.
- **Given** an admin removes an entry **When** the list is read **Then** it no longer appears, but it is kept with `removed_at` and `removed_by` for history.
- **Given** a plain member **When** they read the list **Then** they get it, grouped by kind and sorted by card name. **When** they try to add or remove an entry **Then** they get 403.

**Tasks**
- [ ] T-2.5.1.1 (api) `HouseBan` model (playgroup_id, oracle_id, kind `ban`/`allow`, reason, added_by, added_at, removed_by, removed_at) with a partial unique index on active entries per (playgroup_id, oracle_id), and migration
- [ ] T-2.5.1.2 (api) House-ban service: add, remove and list, getting card names and Commander legality through `cards/services`
- [ ] T-2.5.1.3 (api) Routes and tests, including the allow-only-if-banned rule

#### S-2.5.2 — House-ban lookup for deck validation

**As the** deck validation service (F-4.5) **I want** to get a playgroup's house bans and allowances in one call **so that** I can flag house-banned cards and accept "rule 0" cards for the playgroup of the game or view.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 1 | S-2.5.1 |

**Acceptance criteria**
- **Given** playgroup P with 15 active bans and 2 active allowances **When** `house_bans(playgroup_id, at=None)` is called **Then** it returns a mapping from `oracle_id` to `{kind, reason, added_at}` with 17 entries, using exactly one query.
- **Given** an entry added on 1 March and removed on 1 May **When** the function is called with `at=2026-04-01` **Then** that entry is included. **When** it is called with `at=2026-06-01` **Then** it is not.
- **Given** an archived playgroup **When** the function is called **Then** it still returns that playgroup's entries.
- **Given** the function's signature **When** it is reviewed **Then** it takes exactly one playgroup id. There is no variant that combines a user's playgroups: callers pass the playgroup of the game or of the current view.

**Tasks**
- [ ] T-2.5.2.1 (api) Export `house_bans` from `playgroups/services/__init__.py` with full type hints
- [ ] T-2.5.2.2 (api) Tests, including the point-in-time cases, both kinds and a query-count assertion

#### S-2.5.3 — House ban screens

**As a** member **I want** to see my playgroup's house bans and allowances with card images, and as an owner or admin to edit them, **so that** everyone knows what is and isn't allowed before building a deck.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-2.5.1, S-3.3.1, S-3.3.2 |

**Acceptance criteria**
- **Given** a member opens House rules **When** the page loads **Then** it shows two lists, "House bans" and "Allowed despite the official ban", and each entry shows the card name, its mana cost, the reason, and who added it and when. Hovering, focusing or long-pressing an entry shows the card image.
- **Given** the owner or an admin **When** they add an entry through the card autocomplete and choose ban or allow **Then** it appears in the right list without a page reload.
- **Given** the owner or an admin **When** they remove an entry **Then** a confirmation is shown first, and that confirmation can be completed with the keyboard on web.
- **Given** a playgroup with no entries **When** the page loads **Then** it shows "No house bans. The official Commander banned list still applies."

**Tasks**
- [ ] T-2.5.3.1 (web) House rules page: both lists and the add and remove controls for owners and admins
- [ ] T-2.5.3.2 (mobile) House rules screen: both lists and the add and remove controls for owners and admins
- [ ] T-2.5.3.3 (web) Vitest tests for the admin-only controls and the empty state

#### S-2.5.4 — House rules note

**As a** playgroup admin **I want** to write down our house rules **so that** new and guest players can read them before a game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api, web, mobile | 1 | S-2.4.1 |

**Acceptance criteria**
- **Given** the owner or an admin **When** they save house rules text of up to 5,000 characters **Then** members see it, along with "last edited by <name> on <date>". Text over 5,000 characters returns 422.
- **Given** text that contains HTML or script tags **When** it is displayed **Then** it shows as plain text with line breaks kept, and nothing in it is run or rendered as markup.
- **Given** a plain member **When** they try to edit the house rules **Then** the response is 403.

**Tasks**
- [ ] T-2.5.4.1 (api) `house_rules_text` and its edited-by and edited-at columns on `Playgroup`, plus the route
- [ ] T-2.5.4.2 (web) Read and edit view on the House rules page
- [ ] T-2.5.4.3 (mobile) Read and edit view on the House rules screen

#### S-2.5.5 — Add house bans from a pasted list

**As a** playgroup admin **I want** to paste a list of card names to ban or allow **so that** setting up an existing group's house rules takes one step.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web | 1 | S-2.5.1, S-3.2.3 |

**Acceptance criteria**
- **Given** the owner or an admin pastes up to 100 names, one per line, and picks ban or allow **When** they choose Preview **Then** the matched cards and the unmatched names are listed separately, with up to 3 suggestions for each unmatched name. For allow, matched cards that are not officially banned are listed as rejected.
- **Given** the preview **When** the admin confirms **Then** only the accepted cards that have no active entry yet are added, all in one transaction, and the response reports how many were added and skipped.
- **Given** more than 100 lines **When** the admin chooses Preview **Then** the response is 422 and nothing is resolved.

**Tasks**
- [ ] T-2.5.5.1 (api) Bulk preview and bulk add endpoints using `cards/services.resolve_names`
- [ ] T-2.5.5.2 (web) Paste dialog with a ban/allow choice and the preview table
- [ ] T-2.5.5.3 (api) Tests for mixed matched and unmatched input, rejected allowances and the size limit

#### S-2.5.6 — Playgroup game defaults

**As a** playgroup owner or admin **I want** to set our default starting life, commander-damage threshold and poison threshold **so that** new games in our group start with our house values.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-2.4.3, S-2.5.3 |

**Acceptance criteria**
- **Given** a new playgroup **When** its game defaults are read **Then** they are 40 starting life, 21 commander damage and 10 poison.
- **Given** the owner or an admin **When** they `PATCH /playgroups/{id}/game-defaults` with `starting_life` (1–999), `commander_damage_threshold` (1–99) and `poison_threshold` (1–99) **Then** the values are saved with who changed them and when. Values outside those ranges return 422, and a plain member gets 403.
- **Given** F-6.2 creates a lobby in P **When** it calls `game_defaults(playgroup_id)` from `playgroups/services` **Then** it gets P's current values in one query. Changing the defaults affects only games created afterwards, because each game records the values it used and can override them in the lobby (E-6).
- **Given** a member opens House rules **When** the page loads **Then** it shows the three values and marks any that differ from the official default (for example "House rule: 30 life"). Owners and admins also get "Reset to official defaults".

**Tasks**
- [ ] T-2.5.6.1 (api) Game-default columns on `Playgroup` (with edited-by and edited-at) and migration, the route, and `game_defaults` in `playgroups/services/__init__.py`
- [ ] T-2.5.6.2 (web) Game defaults section on the House rules page
- [ ] T-2.5.6.3 (mobile) Game defaults section on the House rules screen
- [ ] T-2.5.6.4 (api) Tests for the defaults, ranges, permissions and the single query

## F-2.6 — Data rights

Users can export their own data as JSON or CSV and delete their own account; a deleted user's past seats show as "Deleted player" so nobody else's stats change. The owner can archive or delete a playgroup (decision C9). Every domain that holds user or playgroup data takes part through explicit calls to its public services, never through signals. Nothing else in the backlog depends on this feature.

Phase 5 · Priority: Must

#### S-2.6.1 — Export my data

**As a** member **I want** to download everything the app holds about me as JSON or CSV **so that** I have my own copy and can take it elsewhere (decision C9).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 5 | S-2.1.4, S-2.2.1, S-2.4.4, S-4.4.1, S-6.7.4, S-9.1.1, S-9.3.1 |

**Acceptance criteria**
- **Given** a signed-in user on the web Account page **When** they choose "Export my data" and JSON **Then** `GET /account/export?format=json` streams one file `mtgstats-export-<username>-<date>.json`. **When** they choose CSV **Then** it streams a ZIP (standard-library `zipfile`) with one CSV per section. Nothing is stored on the server.
- **Given** the export **When** it is opened **Then** it contains: account (username, display name, optional email, created date, time zone, currency), profile and privacy settings, memberships with roles and dates, every game the user's player was seated in (date, playgroup, seat, commander, deck version, placement, outcome, and the other seats' display names), their decks with every version, their collection items, purchases, and the guests and invites they created.
- **Given** the export **When** it is checked **Then** it never contains password or token hashes, other users' private data (their spend, collection value or decks they can't see), or another player's name once that player has deleted their account ("Deleted player").
- **Given** each domain that holds user data **When** the export runs **Then** the exporter calls an explicit list of `export_user_data(user_id)` functions from each domain's public services (no signals). An architecture test lists every table with a `user_id` or `player_id` column and fails if it is neither exported nor listed as excluded with a reason.
- **Given** a user with 500 games, 30 decks and 5,000 collection items **When** they export **Then** the download finishes within 10 s. A second export within 10 minutes returns 429.
- **Given** a mobile user on the Account screen **When** they tap "Export my data" **Then** the web Account page opens in the browser, because the export downloads a file and no new file-sharing dependency is added to the mobile app.

**Tasks**
- [ ] T-2.6.1.1 (api) Export service in `accounts/services` and `GET /account/export` streaming JSON or a ZIP of CSVs, with the rate limit from S-2.1.10
- [ ] T-2.6.1.2 (api) Ask the players, playgroups, games, decks, collection and purchases domains to expose `export_user_data(user_id)` in their public services (coordinate with E-4, E-6 and E-9)
- [ ] T-2.6.1.3 (api) Architecture test covering every user- or player-keyed table
- [ ] T-2.6.1.4 (web) "Export my data" section on the Account page with the format choice
- [ ] T-2.6.1.5 (mobile) "Export my data" row that opens the web Account page
- [ ] T-2.6.1.6 (api) Tests: both formats, excluded fields, another player's deleted name, the 10 s budget on generated data, rate limit

#### S-2.6.2 — Delete my account

**As a** member **I want** to delete my account myself **so that** my personal data is gone while my friends' game records stay correct (decision C9).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 8 | api, web, mobile | 5 | S-2.6.1, S-2.4.3, S-2.4.4, S-4.4.1, S-6.7.4, S-9.1.1, S-9.3.1, S-10.1.1 |

**Acceptance criteria**
- **Given** a signed-in user on web or mobile **When** they choose "Delete account" **Then** a confirmation lists what will be deleted and what is kept anonymously, offers "Export my data first" (S-2.6.1), and requires their current password and their username typed in full. `DELETE /account` then returns 204 and the user is signed out on every device.
- **Given** the user owns a playgroup that still has other members **When** they try to delete **Then** the response is 409 listing those playgroups with "Transfer ownership first". **Given** they are the last site admin **When** they try **Then** the response is 409 "at least one site admin is required".
- **Given** the deletion commits **When** the user's Player is read **Then** it keeps the same id, `user_id` is null, `deleted_at` is set, `display_name` is "Deleted player" and the avatar is the neutral default. Every seat, game event, placement, standing, stats fact and audit entry that named them still points to that Player, so it shows "Deleted player" and no playgroup's stats or standings change. The Player check constraint allows exactly one of member, guest or deleted.
- **Given** the deletion commits **When** the database is inspected **Then** the User row (username, optional email, password hash), refresh and reset tokens, unused invites they created, invite suggestions, profile and privacy settings, dashboard layouts, collection items, purchases, and decks that no game seat refers to are gone. Decks that a game seat refers to are kept without an owner: they disappear from every deck list and appear on those games only by commander and deck version. The username can be registered again.
- **Given** the user's memberships **When** the deletion commits **Then** each is ended (`left_at`), and a playgroup where they were the only member is archived (S-2.4.4).
- **Given** each domain that holds user data **When** the deletion runs **Then** it calls an explicit list of `delete_user_data(user_id)` functions from each domain's public services (no signals) in one transaction. If any step fails, nothing changes and the user stays signed in.
- **Given** a deletion **When** the audit log is read **Then** one entry records that a player's account was deleted and when, without the username or email.

**Tasks**
- [ ] T-2.6.2.1 (api) `delete_account` service and `DELETE /account` route with the password and username confirmation and the owner and last-site-admin rules
- [ ] T-2.6.2.2 (api) `deleted_at` on `Player`, the three-state check constraint (migration) and the anonymisation step; display code resolves names from Player so no stats rebuild is queued
- [ ] T-2.6.2.3 (api) Ask the decks, collection, purchases, stats (dashboard layouts) and leagues domains to expose `delete_user_data(user_id)` in their public services, keeping decks that game seats refer to (coordinate with E-4, E-7, E-9 and E-10)
- [ ] T-2.6.2.4 (web) "Delete account" section and confirmation dialog on the Account page
- [ ] T-2.6.2.5 (mobile) "Delete account" screen with the same confirmation
- [ ] T-2.6.2.6 (api) Tests: owner with members, last site admin, anonymised seats in another member's stats, kept versus deleted decks, sole-member playgroup archived, rollback when a domain step fails

#### S-2.6.3 — Owner archives or deletes a playgroup

**As a** playgroup owner **I want** to archive a playgroup we no longer use, or delete it entirely **so that** an inactive group stops cluttering everyone's app and a group can be removed for good (decision C9).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 8 | api, web, mobile | 5 | S-2.4.3, S-2.4.4, S-6.7.4, S-7.1.1, S-10.1.1 |

**Acceptance criteria**
- **Given** P's owner **When** they `POST /playgroups/{id}/archive` **Then** P becomes read-only: no new lobbies or games, active invites are revoked and no new ones can be created, house rules, settings and guests can't be edited, and no season can start. Members still see its games, stats and standings, under an "Archived on <date> by <owner>" banner. `POST /playgroups/{id}/unarchive` by the owner undoes it. Admins and members get 403.
- **Given** an archived P **When** its owner chooses Delete, re-enters their password and types P's name exactly **Then** the response is 202, P disappears for everyone at once (404 on every `/playgroups/{id}/…` endpoint), and a background job deletes it. A playgroup that is not archived returns 409 "Archive the playgroup first".
- **Given** the deletion job runs **When** it finishes **Then** P's memberships, unclaimed guests, invites and suggestions, house bans, house rules, settings, games with their seats and events, stats facts, seasons and standings are gone. User accounts, their Players, decks and collections are not touched, and a guest who was claimed or merged keeps its account.
- **Given** P's games are deleted **When** stats are read afterwards **Then** every former member's personal, cross-playgroup activity and stats no longer include them, because the job asks the stats domain to drop those games' facts.
- **Given** each domain that holds playgroup data **When** the job runs **Then** it calls an explicit list of `delete_playgroup_data(playgroup_id)` functions from each domain's public services (no signals), each in its own transaction, with the Playgroup row deleted last. The job is idempotent, so a failed run can be retried until it completes.
- **Given** the delete confirmation **When** it opens **Then** it shows the member count and the number of games that will be deleted, warns that former members lose read-only access to those games, and suggests that members export their data first (S-2.6.1).
- **Given** a deletion **When** the audit log is read **Then** one entry records the playgroup id and name, the owner who deleted it and when.

**Tasks**
- [ ] T-2.6.3.1 (api) `archive`, `unarchive` and `delete` services and routes in `playgroups`, with the archived-state checks used by every write path in the domain
- [ ] T-2.6.3.2 (api) Ask the games, stats and leagues domains to expose `delete_playgroup_data(playgroup_id)` and to refuse writes to an archived playgroup through `playgroups.services.is_archived` (coordinate with E-6, E-7 and E-10)
- [ ] T-2.6.3.3 (api) arq deletion job, idempotent and retryable, with the audit entry
- [ ] T-2.6.3.4 (web) Archive, unarchive and delete actions with the confirmation dialog in playgroup settings
- [ ] T-2.6.3.5 (mobile) Archive, unarchive and delete actions with the confirmation sheet
- [ ] T-2.6.3.6 (api) Tests: owner-only rules, writes refused while archived, delete requires archive, 404 immediately, claimed guests kept, personal stats no longer include deleted games, retry after a failed run

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
