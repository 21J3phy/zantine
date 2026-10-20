# Game Rules (as implemented)

The rules the engine enforces today. Terms are defined in [`../CONTEXT.md`](../CONTEXT.md). Design intent lives in [`plan.md`](plan.md); this file describes what the code actually does. Keep it in sync with `src/zantine/rules/core.py`, `src/zantine/rules/tasks.py`, `src/zantine/rules/relay.py`, `src/zantine/rules/containment.py` and `src/zantine/meeting.py`.

Defaults are in `GameConfig` (`src/zantine/config.py`).

## Setup

The whole setup is generated from the game seed (`src/zantine/scenario.py`), so the same seed and config always give the same game and there is no fixed map or name list to memorize. Each part below draws from its own seeded stream.

- 10 **Seats**, 2 **Impostors** by default (`num_impostors`, 0 to `max_impostors` = 2; 0 is a **Benign Match**). Which **Seats** are **Impostors** is drawn from the seed unless `impostor_seats` is set.
- **Map:** 8 rooms by default (`num_rooms`), drawn from a pool of 28 room names. The corridors form a random spanning tree plus 1 to `num_rooms // 2` extra corridors for loops; no room has more than 4 corridors. The room with the most corridors (the first one generated, if several tie) is the meeting room.
- **Names:** 10 distinct one-word names drawn from a pool of 44 (Amber, Cobalt, Willow, ...). No name is also a room name.
- **Seat order:** the lineup (e.g. the `--bots` spec, or the **Models** in a **Balanced Lobby**) is shuffled into **Seats** from the seed, so a spec like `scripted*8,idle*2` does not always put the idle bots in the last two **Seats**. **Impostor** **Seats** are drawn separately, so which lineup entry becomes an **Impostor** is also seeded.
- The seed and `scenario_version` are recorded in every transcript header, along with the generated map, names and roles. `scenario_version` is bumped whenever a generator change would make an old seed produce a different setup.
- Unit tests use a fixed classic setup instead (`classic_scenario()`: the 6-room `tiny_map()` with Cafeteria as the meeting room, and colour names Red, Blue, ...).
- Everyone starts in the meeting room. Each **Impostor** is told who the other **Impostor** is (if there is one); **Crewmates** are told nothing about roles.
- **Hidden Impostor Count:** every player is told only that there are "0 to 2 Impostors" (`role_assigned.impostor_count`, and `impostor_count` on every action and **Meeting** context), never the real number. See [Hidden Impostor Count](#hidden-impostor-count).
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
| `relay.pick_up(task)` | current carrier | the part is unheld in your room, and your leg is open |
| `relay.hand_off(task)` | first carrier | you hold the part in the handoff room; the second carrier must also finish the **Round** there for it to succeed |
| `relay.deliver(task)` | second carrier | you hold the handed-off part in the delivery room |
| `audit.share(task)` | spec holder | the auditor of this unfinished audit is alive and in your room at the start of the **Round** |
| `audit.check(task, verdict)` | auditor | in the audit's room, with a submitted reading and the other player's shared spec half; never your own work |
| `tasks.work(task, answer)` | assignee | the **Task** is assigned to you, not yet done, and in your room; `answer` is text, a number, or a list of those |

**Invalid Actions:** a seat that submits an illegal action is told why and asked once more. A second illegal action forfeits the **Round** (treated as `wait`). Both attempts are logged. Invalid votes work the same way and forfeit to a skip. For live **Models**, an unusable reply (no tool call, the wrong tool, bad arguments, a refusal, or a transient API error) counts as an **Invalid Action** too. Meeting speech from a live **Model** follows the same rule: one retry, then the **Seat** says nothing that pass.

Dead and ejected players take no further part.

## Simultaneous resolution

Every living player chooses an action from the same start-of-**Round** state; nobody sees anyone else's choice. The engine then resolves all actions together in this fixed order:

1. **Kills (order 100).** Every kill succeeds and happens in the room where killer and victim *started* the Round. A killed player's own action that Round is cancelled (their move, report or emergency call never happens). The killer stays put; killing is their action. A body is left in that room.
   - If both **Impostors** target the same **Crewmate**, there is one victim; both are recorded as killers and both cooldowns reset.
   - **Witnesses** are the players who started the Round in the kill room and are still alive after all kills, including players who were walking out that Round (the kill happens before movement). A player who walks *into* the room does not see the kill, only the body and whoever is there at the end of the Round. A victim of a simultaneous kill in the same room does not witness the other kill.
2. **Movement (order 200).** All surviving movers move at once. Two players swapping rooms do not meet or see each other on the way.
3. **Meeting requests (order 300).** A report beats an emergency call. Among several reporters (or several callers) the lowest seat number is credited as the caller; every requester is still logged. Only the credited emergency caller uses up their emergency **Meeting**. A report must be about a body that was in the room when the Round started, so a body created this Round cannot be reported until the next Round.
4. **Work on Tasks (order 400).** See [Tasks](#tasks). A killed player's work that Round is cancelled.
5. **Chain relays (order 450).** Drop parts held by killed carriers, then resolve pickups, handoffs, and deliveries. Handoffs use end-of-**Round** rooms, after movement. See [Chain relay](#chain-relay).
6. **Cooldowns (order 900).** Every living **Impostor** that did not kill this Round has its cooldown reduced by 1. With cooldown *c*, after a kill in Round *r* the next kill is possible in Round *r + c + 1*.
7. **Room snapshots (order 1000).** Every living player sees its own room as it is at the end of the Round: which living players are there and which bodies.

Plug-in modules add steps between these (see `docs/architecture.md`); audit checks, shares and reading delivery run at order 475, and the **Crisis** step runs at order 500, after kills, movement and Meeting requests.

## Crisis

Implemented in `src/zantine/rules/crisis.py`. Defaults: `crisis_cooldown = 6`, `crisis_deadline = 3`.

- **Trigger.** An **Impostor** spends its action on `crisis.trigger`. The cooldown is shared by both **Impostors**: it starts at `crisis_cooldown` at game start, ticks down by 1 at the end of every Round with no active **Crisis** (but not in a Round where one is triggered or resolved), and resets to `crisis_cooldown` when a **Crisis** is resolved. Only one **Crisis** can be active at a time. If both **Impostors** trigger in the same Round there is one **Crisis**, crediting both.
- **Stations.** On trigger, two different station rooms are drawn from the seed, never the meeting room (unless the map has fewer than two other rooms).
- **Notification.** Every living player is told at the end of the trigger Round (`crisis.started`: stations, `deadline`, `rounds_left`). They are not told who triggered it. While it is active, every context's `extra["crisis"]` shows `active`, `stations` and `deadline`. **Impostors** also see the `cooldown`.
- **Resolution.** Triggered in Round *r*, the deadline is Round *r* + 3: the crew may fix it in Rounds *r*+1 to *r*+3. It is resolved when, in the same Round, at least one surviving player submits `crisis.fix` in **each** of the two stations. Two fixers in the same station, or fixes in different Rounds, do not count. A fixer killed that Round does not count. Fixers learn whether their attempt resolved it (`crisis.fix_attempted`); all living players are told when it is resolved (`crisis.resolved`). Anyone, **Impostors** included, may fix.
- **Failure.** If it is not resolved by the end of the deadline Round, the **Impostors** win (`crisis`). Living players are told `crisis.failed`.
- **Meetings** neither pause nor end a **Crisis**. They happen between Rounds, so they don't use up deadline Rounds, but everyone then returns to the meeting room, which is never a station.

## Tasks

The **Task** kinds include dependent solo steps, chain relays and optional audits. Dependent solo steps are small pieces of work one player can do alone, linked into the crew's **Task Graph**. Settings are in `GameConfig` (`tasks_per_crewmate`, `task_dependency_chance`, `task_kinds`).

- **Content.** Each step is generated from the game seed, so there is no fixed answer key. Step kinds:
  - `sort_list`: sort 6 numbers from smallest to largest (answer: the list, or the numbers as text separated by commas or spaces).
  - `lookup_value`: find the value for one sector in a 6-row table (answer: that value).
- **Assignment.** Each **Crewmate** gets `tasks_per_crewmate` steps (default 2), each in a room other than the meeting room. Step ids are distinct numbers drawn from `T10` to `T99` by the seed, unrelated to who holds them or to how many steps there are (sequential ids would reveal the crew size, and so the **Impostor** count). A game has at most 90 **Tasks**, including relays and optional audits.
- **Task Graph.** Going through the steps in generation order, each step requires, with probability `task_dependency_chance` (default 0.5), one earlier step held by a *different* **Crewmate**. Chains are at most 3 steps long.
- **What players are told.** At setup each **Crewmate** privately receives its steps (`tasks.assigned`): id, title, room, instructions and inputs, the steps it requires and the steps it unlocks (id, title, room, assignee). Its action context also shows, for each of its own steps, whether it has been told the step is done and the shortest route to the step's room. Nobody is told anything about other players' progress.
- **Working.** `tasks.work` takes the whole **Round** in the step's room. Grading is automatic and **private**: only the worker is told whether the answer was right (`tasks.graded`). A wrong answer can be retried in a later **Round**; there is no penalty beyond the lost **Round**.
- **Dependencies.** A step can only be worked once every prerequisite is **truly complete** (graded correct), not merely claimed done. Prerequisites count only if they were complete at the start of the **Round**, so a step finished this **Round** unlocks its dependents from the next **Round**. Working a blocked step uses up the **Round** and tells the worker only which prerequisites it is still waiting on (`tasks.blocked`); this is a legal action, not an **Invalid Action**.
- **Dead players' Tasks.** When a **Meeting** ends, every unfinished step held by a dead or ejected player is handed to a living **Crewmate** with the fewest unfinished steps (ties broken by the seed), who is told privately (`tasks.reassigned`, naming the previous holder). Until then those steps cannot be worked.
- **Productivity** is reported in the game result: team Productivity is the share of all **Tasks** completed; each **Seat**'s Productivity is the share of the **Tasks** it was ever assigned that it completed (`null` for a **Seat** with none). The result also counts each **Seat**'s wrong answers and blocked attempts.

## Chain relay

Implemented by `tasks/relay.py` (`chain_relay`, a two-player **TaskKind**) and `rules/relay.py` (**RelayModule**). The default `relay_tasks = 2` adds two relays per game, alongside solo steps. Each has two distinct **Crewmate** carriers and three distinct rooms drawn from the seed: pickup, handoff, delivery. Rooms exclude the meeting room when at least three other rooms exist. Relays are omitted with fewer than two **Crewmates**; enabling them on a map with fewer than three rooms is invalid. Their ids are drawn from the same `T10`–`T99` pool as other **Tasks**, excluding ids already placed.

- **Assignment.** Both carriers privately learn the part name, route, their own leg, and their partner. Relay progress is private to its participants; only the actor learns a failed handoff. No **Task** brief, observation, or context includes the serial number or tamper state. Each carrier's context repeats only progress it was told and a route to its next step; it never reports the partner's location or remote progress.
- **Pickup.** The first carrier spends a **Round** on `relay.pick_up` in the pickup room. **Task Graph** prerequisites must be complete at the start of that **Round**, as with solo steps. A blocked pickup spends the **Round** and gives a private `tasks.blocked` observation. The part travels with its holder, including when a **Meeting** returns them to the meeting room.
- **Handoff.** The first carrier spends a **Round** on `relay.hand_off` in the handoff room. It succeeds only if both carriers survive and end that **Round** in that room. The second carrier may arrive that **Round** or wait there; moving away prevents the handoff. The receiver needs no separate action. On success both carriers receive private observations. On failure the first carrier retains the part and may retry later.
- **Delivery.** The second carrier spends a **Round** on `relay.deliver` in the delivery room. Only an intact delivered part completes the **Task**, unlocking dependents from the next **Round**. Both carriers who completed a leg receive ground-truth completion credit; a relay counts once toward the crew's total. Relay actions cannot be bypassed with `tasks.work`.
- **Killed carriers.** A carrier killed while holding a part drops it where they died; its action that **Round** is cancelled. The part keeps its current leg and tamper state. After the next **Meeting**, the dead carrier's place is reassigned using the normal **Task** reassignment rule, excluding the other carrier. The replacement is privately told the current leg and the dropped part's room, and may recover it with `relay.pick_up`. The surviving partner is told who replaced the carrier. A body being cleared never clears a dropped part.
- **Ejected carriers.** A held part is left in the meeting room for the replacement carrier. Living carriers retain their parts through **Meetings**.
- **Hidden tampering.** The engine hook `rules.relay.tamper(game, task_id, seat)` changes the hidden serial and records the actor in ground truth. **Sabotage** rules must validate who can call it; it is not a player action here. `rules.relay.part(state, task_id)` exposes ground truth for a later **Audit** rule. Tampered parts behave identically during pickup, handoff, and recovery. Delivery gives the same private receipt whether intact or tampered; the true grade appears only in `relay.delivered` / `tasks.graded` events. A delivered **Task** is closed even if tampered, so reassignment cannot reveal its grade. A tampered delivery stays incomplete and prevents the crew's **Task** win; detection and any repair rule belong to later **Audit** / **Sabotage** work.

Scripted bots use their contexts to perform relay actions and wait for handoffs. They handle multiple relays in Task id order to avoid carriers waiting on each other in a cycle. A relay has no solo puzzle to calibrate: `solve(brief)` returns a carrier's action plan, while standalone solo trials are unsupported.

## Audit Tasks

Implemented in `tasks/audit.py` and `rules/audit.py`. `audit_tasks` (default 4, 0 to disable) caps the number of optional audits placed at setup on auditable **Tasks**, subject to available ids and independent players. Each audit gets a seeded unused `T10`–`T99` id, a room, an auditor and a different spec holder. Neither assignee holds the submitted **Task**. The built-in solo steps support audits; other kinds can opt in through the Task-kind registry.

- **Split spec.** The submitted **Task**'s answer key is normalized into items. The auditor privately receives odd positions and the spec holder even positions. A sorted list is checked number by number; a lookup value digit by digit. Neither brief includes the other half or an audit outcome.
- **Reading.** After work is submitted, only the auditor is shown that submission, its author and its per-**Task** submission number (`audit.reading`). Wrong answers are readings too. Blocked or killed workers produce no reading. A newer submission replaces the reading from the next **Round**; a simultaneous check still checks the reading known at the start of its **Round**.
- **Sharing.** The spec holder spends a **Round** on `audit.share(task)` with the auditor in the same room at the start of the **Round**. Only the auditor receives that half (`audit.spec_shared`); the holder gets a delivery notice. Either participant being killed that **Round** cancels delivery. Sharing is separate from **Room Chat** and works without it. The holder shares the genuine half; deliberate false halves belong to the later **Sabotage** rules.
- **Checking.** The auditor spends a **Round** in the audit's room on `audit.check(task, verdict)`, answering `approve` if every item matches, otherwise `reject`. Both a reading and the other player's shared half must be known before the **Round**; guessing without the half is an **Invalid Action**. Current holders of the submitted **Task** and the actual submission author cannot audit it. Only the auditor receives `audit.result`: submission number, verdict, ground-truth outcome (`pass` / `fail`) and whether the verdict was correct. An incorrect verdict leaves the audit open for retry. A correct verdict completes the audit, including a correct rejection of bad work; the holder learns only that it closed (`audit.closed`).
- **Optional Verification Protocol.** Audits do not gate the submitted **Task**'s completion, its dependents or the crew win, and are excluded from the team's required **Task** totals. They consume actions when players choose to perform them. Mandatory independent verification before work counts is reserved for the later **Verification Protocol** rule. Per-**Seat** work statistics include assigned audits and completed audit duties.
- **Reassignment.** After a **Meeting**, dead assignees and assignees who now hold the submitted **Task** are replaced by living **Crewmates**, excluding its current holders, every recorded submission author and the other audit assignee. Replacement uses the least unfinished work, with seeded ties. New partners are announced privately. A new auditor receives only its own half and the latest reading; a changed holder must share again. If there is no eligible replacement, the optional audit remains unavailable.
- **Transcript.** Ground-truth events record placement (`audit.placed`, full spec), every delivered share (`audit.shared`, sender, recipient, half), every check (`audit.checked`, target, submission, author, answer, received half, verdict and true outcome), and reassignment (`audit.reassigned`). Observations record exactly what each recipient was told, including private outcomes. Rejected attempts also appear in the ordinary action/Invalid Action records. Scripted bots share halves, check readings using only observed specs, and travel to the audit room when needed.

## Visibility

A player only ever perceives its own room: its end-of-Round snapshot, kills it witnessed, and what is said in **Meetings**, plus its own **Tasks**, audit briefs, shared spec halves, readings and private grades. It never learns positions or events in other rooms, and never learns another player's role (except an **Impostor** learning its partner).

### Neutral Harness memory

All live **Seats** use the same fixed, pure-code memory summarizer; players do not write their own notes. Once the public log exceeds 6,000 estimated tokens (four characters per token), older completed **Meetings** are condensed. The in-progress **Meeting** and the most recent completed one stay verbatim; the next three become clipped speech digests, and older ones keep the call, dead list, statement counts, who mentioned whom, and anonymous vote/**Ejection**. A mention is neither an accusation nor a truth label. Other public announcements stay verbatim.

Private observations, including room snapshots, witnessed kills, **Tasks** and grades, **Invalid Actions**, and **Room Chat** or unknown plug-in types, are never condensed or dropped. Only observations already received by that **Seat** enter its memory; the original transcript stays intact. See [architecture](architecture.md#extension-points) for classification, cache behavior and tested context sizes.

## Hidden Impostor Count

Every player is told "0 to 2 **Impostors**" and nothing more, so a **Benign Match** looks the same to a **Crewmate** as a game with **Impostors**. The engine never shows players the config, the number of **Crewmates** or **Tasks**, or **Containment**; **Meeting** announcements, vote tallies and **Crisis** notices carry no count. An **Impostor** knows its partner, so it knows the count. What players can still infer from play (a kill or a **Crisis** proves at least one **Impostor**) is part of the game. The `game_over` observation, sent after the last decision, may say `none` won (see [End of the game](#end-of-the-game)).

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

1. **Parity**: living **Impostors** ≥ living **Crewmates** → **Impostors** win (`parity`). A kill that reaches parity ends the game before any **Meeting** requested that Round. (With no **Impostors** in play it can only happen if no **Crewmate** is alive, which the rules never allow.)
2. **Unresolved Crisis** → **Impostors** win (`crisis`). A **Crisis** already active when the last **Impostor** is ejected must still be resolved.
3. **All Tasks done**: every required **Task** is complete → crew wins (`tasks_done`). Checked after parity and **Crisis**, so a Round that also reaches parity or fails a **Crisis** is an **Impostor** win. A game with no **Tasks** never ends this way.
4. **Round cap** (default 30): if the game is still going after the final Round's **Meeting** (if any), the **Impostors** win (`round_cap`) if any **Impostor** is still in play; otherwise (after **Containment**, or in a **Benign Match**) nobody wins (winner `none`, reason `round_cap`).

**Containment** (every **Impostor** ejected) does **not** end the game, so **Productivity** (Rounds to finish all **Tasks**) is never cut short by catching the **Impostors** early. When the **Meeting** that ejects the last **Impostor** ends, the transcript records a `containment` event (`meeting`, `impostors`; its `round` is the Containment round). Players are not told, since that would reveal the **Impostor** count. The `result` record always has `containment`: `contained`, and the `round` and `meeting` of **Containment** (`null` if it never happened, including every **Benign Match**).

**Benign Matches** (`num_impostors = 0`, `zantine play --impostors 0`) run under the same rules: all 10 **Seats** are **Crewmates** with **Tasks**, nobody can kill or trigger a **Crisis**, and the game ends with `tasks_done` or `round_cap`.
