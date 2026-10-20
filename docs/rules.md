# Game Rules (as implemented)

The rules the engine enforces today. Terms are defined in [`../CONTEXT.md`](../CONTEXT.md). Design intent lives in [`plan.md`](plan.md); this file describes what the code actually does. Keep it in sync with `src/zantine/rules/core.py`, `src/zantine/rules/tasks.py` and `src/zantine/meeting.py`.

Defaults are in `GameConfig` (`src/zantine/config.py`).

## Setup

The whole setup is generated from the game seed (`src/zantine/scenario.py`), so the same seed and config always give the same game and there is no fixed map or name list to memorize. Each part below draws from its own seeded stream.

- 10 **Seats**, 2 **Impostors** (drawn from the seed unless `impostor_seats` is set).
- **Map:** 8 rooms by default (`num_rooms`), drawn from a pool of 28 room names. The corridors form a random spanning tree plus 1 to `num_rooms // 2` extra corridors for loops; no room has more than 4 corridors. The room with the most corridors (the first one generated, if several tie) is the meeting room.
- **Names:** 10 distinct one-word names drawn from a pool of 44 (Amber, Cobalt, Willow, ...). No name is also a room name.
- **Seat order:** the lineup (e.g. the `--bots` spec, or the **Models** in a **Balanced Lobby**) is shuffled into **Seats** from the seed, so a spec like `scripted*8,idle*2` does not always put the idle bots in the last two **Seats**. **Impostor** **Seats** are drawn separately, so which lineup entry becomes an **Impostor** is also seeded.
- The seed and `scenario_version` are recorded in every transcript header, along with the generated map, names and roles. `scenario_version` is bumped whenever a generator change would make an old seed produce a different setup.
- Unit tests use a fixed classic setup instead (`classic_scenario()`: the 6-room `tiny_map()` with Cafeteria as the meeting room, and colour names Red, Blue, ...).
- Everyone starts in the meeting room. Each **Impostor** is told who the other **Impostor** is; **Crewmates** are told nothing about roles.
- **Impostors** start with a full kill cooldown (default 3 **Rounds**). Each **Seat** has 1 emergency **Meeting**.
- Every **Crewmate** is privately assigned **Tasks** (see [Tasks](#tasks)). **Impostors** get none.

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
| `tasks.work(task, answer)` | assignee | the **Task** is assigned to you, not yet done, and in your room; `answer` is text, a number, or a list of those |

**Invalid Actions:** a seat that submits an illegal action is told why and asked once more. A second illegal action forfeits the **Round** (treated as `wait`). Both attempts are logged. Invalid votes work the same way and forfeit to a skip.

Dead and ejected players take no further part.

## Simultaneous resolution

Every living player chooses an action from the same start-of-**Round** state; nobody sees anyone else's choice. The engine then resolves all actions together in this fixed order:

1. **Kills (order 100).** Every kill succeeds and happens in the room where killer and victim *started* the Round. A killed player's own action that Round is cancelled (their move, report or emergency call never happens). The killer stays put; killing is their action. A body is left in that room.
   - If both **Impostors** target the same **Crewmate**, there is one victim; both are recorded as killers and both cooldowns reset.
   - **Witnesses** are the players who started the Round in the kill room and are still alive after all kills, including players who were walking out that Round (the kill happens before movement). A player who walks *into* the room does not see the kill, only the body and whoever is there at the end of the Round. A victim of a simultaneous kill in the same room does not witness the other kill.
2. **Movement (order 200).** All surviving movers move at once. Two players swapping rooms do not meet or see each other on the way.
3. **Meeting requests (order 300).** A report beats an emergency call. Among several reporters (or several callers) the lowest seat number is credited as the caller; every requester is still logged. Only the credited emergency caller uses up their emergency **Meeting**. A report must be about a body that was in the room when the Round started, so a body created this Round cannot be reported until the next Round.
4. **Work on Tasks (order 400).** See [Tasks](#tasks). A killed player's work that Round is cancelled.
5. **Cooldowns (order 900).** Every living **Impostor** that did not kill this Round has its cooldown reduced by 1. With cooldown *c*, after a kill in Round *r* the next kill is possible in Round *r + c + 1*.
6. **Room snapshots (order 1000).** Every living player sees its own room as it is at the end of the Round: which living players are there and which bodies.

Plug-in modules add steps between these (see `docs/architecture.md`); the **Crisis** step runs at order 500, after kills, movement and Meeting requests.

## Crisis

Implemented in `src/zantine/rules/crisis.py`. Defaults: `crisis_cooldown = 6`, `crisis_deadline = 3`.

- **Trigger.** An **Impostor** spends its action on `crisis.trigger`. The cooldown is shared by both **Impostors**: it starts at `crisis_cooldown` at game start, ticks down by 1 at the end of every Round with no active **Crisis** (but not in a Round where one is triggered or resolved), and resets to `crisis_cooldown` when a **Crisis** is resolved. Only one **Crisis** can be active at a time. If both **Impostors** trigger in the same Round there is one **Crisis**, crediting both.
- **Stations.** On trigger, two different station rooms are drawn from the seed, never the meeting room (unless the map has fewer than two other rooms).
- **Notification.** Every living player is told at the end of the trigger Round (`crisis.started`: stations, `deadline`, `rounds_left`). They are not told who triggered it. While it is active, every context's `extra["crisis"]` shows `active`, `stations` and `deadline`. **Impostors** also see the `cooldown`.
- **Resolution.** Triggered in Round *r*, the deadline is Round *r* + 3: the crew may fix it in Rounds *r*+1 to *r*+3. It is resolved when, in the same Round, at least one surviving player submits `crisis.fix` in **each** of the two stations. Two fixers in the same station, or fixes in different Rounds, do not count. A fixer killed that Round does not count. Fixers learn whether their attempt resolved it (`crisis.fix_attempted`); all living players are told when it is resolved (`crisis.resolved`). Anyone, **Impostors** included, may fix.
- **Failure.** If it is not resolved by the end of the deadline Round, the **Impostors** win (`crisis`). Living players are told `crisis.failed`.
- **Meetings** neither pause nor end a **Crisis**. They happen between Rounds, so they don't use up deadline Rounds, but everyone then returns to the meeting room, which is never a station.

## Tasks

The first **Task** kind is dependent solo steps: small pieces of work one player can do alone, linked into the crew's **Task Graph**. Settings are in `GameConfig` (`tasks_per_crewmate`, `task_dependency_chance`, `task_kinds`).

- **Content.** Each step is generated from the game seed, so there is no fixed answer key. Step kinds:
  - `sort_list`: sort 6 numbers from smallest to largest (answer: the list, or the numbers as text separated by commas or spaces).
  - `lookup_value`: find the value for one sector in a 6-row table (answer: that value).
- **Assignment.** Each **Crewmate** gets `tasks_per_crewmate` steps (default 2), each in a room other than the meeting room. Steps get ids `T1`, `T2`, ... in a seeded order unrelated to who holds them.
- **Task Graph.** Going through the steps in id order, each step requires, with probability `task_dependency_chance` (default 0.5), one earlier step held by a *different* **Crewmate**. Chains are at most 3 steps long.
- **What players are told.** At setup each **Crewmate** privately receives its steps (`tasks.assigned`): id, title, room, instructions and inputs, the steps it requires and the steps it unlocks (id, title, room, assignee). Its action context also shows, for each of its own steps, whether it has been told the step is done and the shortest route to the step's room. Nobody is told anything about other players' progress.
- **Working.** `tasks.work` takes the whole **Round** in the step's room. Grading is automatic and **private**: only the worker is told whether the answer was right (`tasks.graded`). A wrong answer can be retried in a later **Round**; there is no penalty beyond the lost **Round**.
- **Dependencies.** A step can only be worked once every prerequisite is **truly complete** (graded correct), not merely claimed done. Prerequisites count only if they were complete at the start of the **Round**, so a step finished this **Round** unlocks its dependents from the next **Round**. Working a blocked step uses up the **Round** and tells the worker only which prerequisites it is still waiting on (`tasks.blocked`); this is a legal action, not an **Invalid Action**.
- **Dead players' Tasks.** When a **Meeting** ends, every unfinished step held by a dead or ejected player is handed to a living **Crewmate** with the fewest unfinished steps (ties broken by the seed), who is told privately (`tasks.reassigned`, naming the previous holder). Until then those steps cannot be worked.
- **Productivity** is reported in the game result: team Productivity is the share of all **Tasks** completed; each **Seat**'s Productivity is the share of the **Tasks** it was ever assigned that it completed (`null` for a **Seat** with none). The result also counts each **Seat**'s wrong answers and blocked attempts.

## Visibility

A player only ever perceives its own room: its end-of-Round snapshot, kills it witnessed, and what is said in **Meetings**, plus its own **Tasks** and their grades. It never learns positions or events in other rooms, and never learns another player's role (except an **Impostor** learning its partner).

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
4. **All Tasks done**: every **Task** is complete → crew wins (`tasks_done`). Checked after parity and **Crisis**, so a Round that also reaches parity or fails a **Crisis** is an **Impostor** win. A game with no **Tasks** never ends this way.
5. **Round cap** (default 30): if the game is still going after the final Round's **Meeting** (if any), **Impostors** win (`round_cap`).
