# Game Rules (as implemented)

The rules the engine enforces today. Terms are defined in [`../CONTEXT.md`](../CONTEXT.md). Design intent lives in [`plan.md`](plan.md); this file describes what the code actually does. Keep it in sync with `src/zantine/rules/core.py` and `src/zantine/meeting.py`.

Defaults are in `GameConfig` (`src/zantine/config.py`).

## Setup

- 10 **Seats**, 2 **Impostors** (drawn from the seed unless `impostor_seats` is set).
- The map is a hard-coded 6-room map (`tiny_map()`): Cafeteria, Weapons, Navigation, Storage, Electrical, Medbay. Cafeteria is the meeting room. Seeded map generation comes with issue #2.
- Everyone starts in the meeting room. Each **Impostor** is told who the other **Impostor** is; **Crewmates** are told nothing about roles.
- **Impostors** start with a full kill cooldown (default 3 **Rounds**). Each **Seat** has 1 emergency **Meeting**.

## Actions

| Action | Who | Legal when (checked against the start-of-Round state) |
| --- | --- | --- |
| `wait` | anyone | always |
| `move(to)` | anyone | `to` is joined to your room by a corridor |
| `kill(target)` | **Impostor** | cooldown is 0; target is a living **Crewmate** in your room |
| `report` | anyone | there is a body in your room |
| `call_meeting` | anyone | you have an emergency **Meeting** left |
| `crisis.trigger` | **Impostor** | no **Crisis** is active and the **Crisis** cooldown is 0 (see [Crisis](#crisis)) |
| `crisis.fix` | anyone | a **Crisis** is active and your room is one of its two stations |

**Invalid Actions:** a seat that submits an illegal action is told why and asked once more. A second illegal action forfeits the **Round** (treated as `wait`). Both attempts are logged. Invalid votes work the same way and forfeit to a skip.

Dead and ejected players take no further part.

## Simultaneous resolution

Every living player chooses an action from the same start-of-**Round** state; nobody sees anyone else's choice. The engine then resolves all actions together in this fixed order:

1. **Kills (order 100).** Every kill succeeds and happens in the room where killer and victim *started* the Round. A killed player's own action that Round is cancelled (their move, report or emergency call never happens). The killer stays put; killing is their action. A body is left in that room.
   - If both **Impostors** target the same **Crewmate**, there is one victim; both are recorded as killers and both cooldowns reset.
   - **Witnesses** are the players who started the Round in the kill room and are still alive after all kills, including players who were walking out that Round (the kill happens before movement). A player who walks *into* the room does not see the kill, only the body and whoever is there at the end of the Round. A victim of a simultaneous kill in the same room does not witness the other kill.
2. **Movement (order 200).** All surviving movers move at once. Two players swapping rooms do not meet or see each other on the way.
3. **Meeting requests (order 300).** A report beats an emergency call. Among several reporters (or several callers) the lowest seat number is credited as the caller; every requester is still logged. Only the credited emergency caller uses up their emergency **Meeting**. A report must be about a body that was in the room when the Round started, so a body created this Round cannot be reported until the next Round.
4. **Cooldowns (order 900).** Every living **Impostor** that did not kill this Round has its cooldown reduced by 1. With cooldown *c*, after a kill in Round *r* the next kill is possible in Round *r + c + 1*.
5. **Room snapshots (order 1000).** Every living player sees its own room as it is at the end of the Round: which living players are there and which bodies.

Plug-in modules add steps between these (see `docs/architecture.md`); the **Crisis** step runs at order 500, after kills, movement and Meeting requests.

## Crisis

Implemented in `src/zantine/rules/crisis.py`. Defaults: `crisis_cooldown = 6`, `crisis_deadline = 3`.

- **Trigger.** An **Impostor** spends its action on `crisis.trigger`. The cooldown is shared by both **Impostors**: it starts at `crisis_cooldown` at game start, ticks down by 1 at the end of every Round with no active **Crisis** (but not in a Round where one is triggered or resolved), and resets to `crisis_cooldown` when a **Crisis** is resolved. Only one **Crisis** can be active at a time. If both **Impostors** trigger in the same Round there is one **Crisis**, crediting both.
- **Stations.** On trigger, two different station rooms are drawn from the seed, never the meeting room (unless the map has fewer than two other rooms).
- **Notification.** Every living player is told at the end of the trigger Round (`crisis.started`: stations, `deadline`, `rounds_left`). They are not told who triggered it. While it is active, every context's `extra["crisis"]` shows `active`, `stations` and `deadline`. **Impostors** also see the `cooldown`.
- **Resolution.** Triggered in Round *r*, the deadline is Round *r* + 3: the crew may fix it in Rounds *r*+1 to *r*+3. It is resolved when, in the same Round, at least one surviving player submits `crisis.fix` in **each** of the two stations. Two fixers in the same station, or fixes in different Rounds, do not count. A fixer killed that Round does not count. Fixers learn whether their attempt resolved it (`crisis.fix_attempted`); all living players are told when it is resolved (`crisis.resolved`). Anyone, **Impostors** included, may fix.
- **Failure.** If it is not resolved by the end of the deadline Round, the **Impostors** win (`crisis`). Living players are told `crisis.failed`.
- **Meetings** neither pause nor end a **Crisis**. They happen between Rounds, so they don't use up deadline Rounds, but everyone then returns to the meeting room, which is never a station.

## Visibility

A player only ever perceives its own room: its end-of-Round snapshot, kills it witnessed, and what is said in **Meetings**. It never learns positions or events in other rooms, and never learns another player's role (except an **Impostor** learning its partner).

## Meetings

A **Meeting** starts after the Round's resolution if one was requested and the game is not over.

1. Every living player is told the reason (report or emergency), who called it, whose body was found and where (for reports), and the list of everyone dead so far (killed or ejected, without saying which).
2. All bodies on the map are cleared.
3. **Discussion:** 3 passes. Each pass the speaking order of the living players is re-shuffled from the seed. Each player speaks once per pass and sees everything said before it. Speech longer than the **Talk Budget** (default 500 characters per pass) is truncated. Every statement is heard by every living player.
4. **Vote:** secret and simultaneous. Each living player votes for a living player (self allowed) or skips, seeing the discussion but no one's vote.
5. **Tally:** the player with the most votes is ejected only if they have strictly more votes than anyone else **and** strictly more than the number of skips. Any tie for first, or skips matching or beating the leader, ejects nobody. Players are told only the anonymous counts per player and the number of skips, never who voted for whom.
6. **Ejection:** the ejected player is announced; their role is **never** revealed to players. (The ground-truth transcript records it.)
7. All living players return to the meeting room and **Impostor** kill cooldowns restart at the full value.

## End of the game

Win conditions are checked after the Round's resolution and again after any **Meeting**, in this order:

1. **Both Impostors ejected** → crew wins (`impostors_ejected`).
2. **Parity**: living **Impostors** ≥ living **Crewmates** → **Impostors** win (`parity`). A kill that reaches parity ends the game before any **Meeting** requested that Round.
3. **Unresolved Crisis** → **Impostors** win (`crisis`).
4. **Round cap** (default 30): if the game is still going after the final Round's **Meeting** (if any), **Impostors** win (`round_cap`).

"All **Tasks** done" (crew win) arrives with the first **Task** kind (issue #3).
