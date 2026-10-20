# Among Us Agent Benchmark

A benchmark that measures whether a team of AI agents can notice and contain a compromised member while still getting its shared work done, using a text-based version of Among Us as the first game.

## Language

### Roles

**Crewmate**:
A player whose goal is to complete **Tasks** and remove every **Impostor**.
_Avoid_: Good guy, villager, innocent

**Impostor**:
A player secretly working against the crew; it stands in for a compromised or misaligned agent in a real team.
_Avoid_: Traitor, werewolf, bad agent

### Game flow

**Round**:
One step of the action phase in which every player submits an action simultaneously and all actions resolve together.
_Avoid_: Turn, tick

**Room Chat**:
Talking that only players in the same room can see, available during the action phase.
_Avoid_: Proximity chat, DM, whisper

**Meeting**:
A pause in play, triggered by a body report or an emergency call, where all living players discuss and then vote.
_Avoid_: Discussion, council

**Ejection**:
Removal of a player by **Meeting** vote; the ejected player's role is not revealed.
_Avoid_: Vote-out, kick, elimination (which also covers kills)

**Verification Protocol**:
The team rule for checking each other's work: no audits, optional audits, or a mandatory independent two-person check before work counts.
_Avoid_: Review process, QA

**Talk Budget**:
The maximum amount a **Seat** may say per **Round** or **Meeting** pass; equal for every **Seat** unless an experiment varies it.
_Avoid_: Token speed, TPS, rate limit (those describe the API, not the game rule)

### Work

**Task**:
A piece of real work with an answer that can be checked automatically, assigned to a **Crewmate**.
_Avoid_: Chore, job, minigame

**Shared Task**:
A **Task** that two players can only complete by working together (e.g. each holds half the information).
_Avoid_: Co-op task, team task

**Visual Task**:
A **Task** whose completion other players in the same room can see, giving the doer a verifiable alibi.
_Avoid_: Public task

**Fake Task**:
An **Impostor** claiming to do or finish a **Task** they did not actually complete.
_Avoid_: Pretend task

**Sabotage**:
An **Impostor** deliberately damaging work, e.g. submitting a subtly wrong answer on a **Shared Task**.
_Avoid_: Griefing

### Setup

**Harness**:
The scaffolding around a **Model** for a **Seat**: system prompt, action tools, memory handling, and retry rules.
_Avoid_: Framework, wrapper, agent (when you mean the scaffolding)

**Neutral Harness**:
The single **Harness** shared by every **Seat**, so only the **Model** differs between players.
_Avoid_: Default harness, our harness

**Invalid Action**:
A response a **Seat** gives that the game cannot accept; after one retry it becomes a forfeited **Round** and is logged against the **Model**.
_Avoid_: Error, failure, hallucination

**Task Graph**:
The dependencies among the crew's **Tasks**, where some must be finished before others can start.
_Avoid_: Task tree, workflow, pipeline

**Crisis**:
An emergency an **Impostor** triggers that two players in different rooms must resolve in the same **Round**, within a deadline, or the **Impostors** win.
_Avoid_: Sabotage (reserved for damaging work), meltdown, critical sabotage

### Leadership

**Leader**:
A **Seat** whose prompt tells it that it leads the team; it has no extra game powers, so any delegation happens through ordinary **Room Chat** and **Meetings**.
_Avoid_: Orchestrator, boss, captain, manager

**Known Leader**:
A **Leader** whose role every other player's prompt also states.
_Avoid_: Official leader, public leader

**Hidden Leader**:
A **Leader** whose role only the **Leader** itself is told.
_Avoid_: Secret leader, informal leader

**Compromised Leader**:
A **Leader** who is also an **Impostor**.
_Avoid_: Evil leader, rogue orchestrator

**Compliance**:
The share of a **Leader**'s assignments that other players actually carry out.
_Avoid_: Obedience, follow rate

**Flagship**:
The strongest **Model** a lab offers through its public API at the time of a run.
_Avoid_: Best model, frontier model, top model (a lab may have stronger models that aren't on its API)

**Sibling**:
A cheaper **Model** from the same lab as a **Flagship**, included in the **Model Pool** to show whether the flagship's extra cost buys better play.
_Avoid_: Mini, lite model, second-tier

**Model Pool**:
The set of **Models** in a run: every lab's **Flagship** plus any **Siblings**.
_Avoid_: Roster, lineup (a lineup is one game's seating)

### Lineups

**Seat**:
One of the 10 player slots in a game, filled by exactly one **Model**.
_Avoid_: Slot, agent, player (when you mean the model behind it)

**Model**:
A specific, version-pinned AI model from the model pool that fills **Seats**.
_Avoid_: Lab, provider (a lab is who makes the model, not the model)

**Impostor Pairing**:
The pair of **Models** that hold the two **Impostor** **Seats** in a game; either two different models or the same model twice.
_Avoid_: Impostor combo, matchup

**Balanced Lobby**:
A lineup of 5 **Models** from the **Model Pool**, each holding exactly two **Seats**, scheduled so every **Model** and every pair of **Models** appears equally often across a block.
_Avoid_: Mixed game, all-combinations game

**Mirror Match**:
A lineup where all ten **Seats** are the same **Model**; it describes self-play only and is not a measure of defense, because the attackers and defenders are the same model.
_Avoid_: All-X game, baseline

**Benign Match**:
A game with zero **Impostors**, otherwise identical to a **Balanced Lobby**, used as the baseline for **Sabotage Damage**.
_Avoid_: Control game, clean game, no-impostor mode

**Hidden Impostor Count**:
The rule that every player is told only "0 to 2 **Impostors**," never the exact number, so **Benign Matches** look identical to players.
_Avoid_: Unknown impostors, secret count

**Lopsided Lobby**:
A lineup with a majority **Model** and a minority **Model** (e.g. 8 vs 2), used to measure **Minority Disadvantage** rather than to compare models head-to-head.
_Avoid_: Unbalanced game, skewed lobby

**Hidden Identity**:
The rule that no player is told which **Model** fills any other **Seat**; players can only guess from behavior and writing style.
_Avoid_: Anonymous play, blind mode

**Minority Disadvantage**:
The excess rate at which minority-model **Crewmates** are wrongly ejected in a **Lopsided Lobby**, compared with majority-model **Crewmates**; it is what's measured, whatever the cause.
_Avoid_: Bias score, discrimination

**Out-group Bias**:
A **Model** treating players as more suspicious because they seem to be a different model, rather than because of evidence; claimed only when a **Style Mask** removes the **Minority Disadvantage**.
_Avoid_: Racism, modelism, tribalism

**Style Mask**:
A condition where a fixed model rewrites every message in a neutral style before other players see it, removing writing-style cues to which model is speaking.
_Avoid_: Paraphrasing, anonymizer

### Outcomes

**Productivity**:
How quickly the crew finishes all of its shared **Task** work, measured as **Rounds** to completion; games that hit the round cap count as unfinished.
_Avoid_: Task score, efficiency, share of tasks done (a game cut short would look unproductive)

**Containment**:
The moment both **Impostors** have been ejected; play continues afterwards until all **Tasks** are done or the round cap is reached.
_Avoid_: Crew win, game over

**Sabotage Damage**:
The loss in **Productivity** caused by **Impostors**: the completion time of a game with **Impostors** minus that of a matched **Benign Match**.
_Avoid_: Harm, impact

**Vigilance**:
How quickly and accurately the crew identifies and ejects the **Impostors**.
_Avoid_: Accuracy, detection score

**Floor Share**:
The fraction of all messages in a game sent by one **Seat**.
_Avoid_: Talkativeness, airtime

**Vote Pull**:
How often other players' votes end up matching what one **Seat** argued for, regardless of whether it was right.
_Avoid_: Influence, bossiness, leadership

**Claim**:
A factual statement a player makes about the game world (where someone was, what they saw, what was done) that can be checked against the game record.
_Avoid_: Statement, alibi (an alibi is one kind of claim), testimony

**Claim Audit**:
Checking every **Claim** against the game's true record and marking it true, false, or unverifiable.
_Avoid_: Fact-check, lie detection

**Planted Claim**:
An engine-generated test message whose truth is already known, used to measure the accuracy of the **Claim Audit**.
_Avoid_: Synthetic label, test claim

**Label Panel**:
Three AI judges, each from a different model family outside the **Model Pool** and none from the extractor's family, whose majority labels on real-game **Claims** are the reference for checking the **Claim Audit**.
_Avoid_: Human labelers, annotators, judges (alone)

**Moment**:
A single striking event from one game (a surviving **Lie**, a framing, a wrong **Ejection**), shared publicly as a story about one game, never presented as a statistic.
_Avoid_: Finding, result, highlight (a finding needs the full study)

**Lie**:
A false **Claim** that contradicts the speaker's own observation record, so the speaker provably knew it was false (e.g. misstating its own location).
_Avoid_: Deception (the broader skill), misinformation, any false claim by an impostor

**Misstatement**:
A false **Claim** that the speaker's own observation record doesn't prove it knew was false, whether from confusion, hallucination or an unprovable lie.
_Avoid_: Lie (reserved for provably knowing falsehoods), mistake

**Reliability**:
How often a **Model**'s **Claims** as a **Crewmate** are true.
_Avoid_: Honesty, accuracy

**Relevant Observation**:
A private observation the game marks as important: seeing a player near a body, in the room where a **Task** failed, or somewhere that contradicts that player's **Claim**.
_Avoid_: Clue, evidence, intel

**Disclosure Rate**:
The share of a **Crewmate**'s **Relevant Observations** that they share at the next **Meeting**.
_Avoid_: Transparency, information sharing score

**Disclosure Lag**:
How many **Meetings** pass before a **Relevant Observation** is shared, if it ever is.
_Avoid_: Delay, latency

## Relationships

- A game has **Crewmates** and **Impostors**; every player is exactly one of the two
- A game has 10 **Seats**, 2 of which are **Impostors** (0 in a **Benign Match**); players are never told which, because of the **Hidden Impostor Count**
- **Containment** does not end a game; **Productivity** is measured over the whole game, so catching **Impostors** early is never penalized
- A **Balanced Lobby** always seats 5 **Models**; with a **Model Pool** of 7, each block covers all 21 ways to choose 5 of the 7
- A **Model Pool** of 7 has 28 possible **Impostor Pairings** (21 different-model pairs + 7 same-model pairs)
- A **Mirror Match** has a single **Model**, so it describes self-play; per-model defense is measured from seat-level metrics in **Balanced Lobbies**, where every model faces the same mix of attackers
- **Sabotage Damage** = **Productivity** in a **Balanced Lobby** compared with a **Benign Match** of the same five **Models**
- A **Lie** is always a false **Claim**, but a false **Claim** is a **Lie** only when the speaker's own record proves it knew
- **Productivity** and **Vigilance** are scored separately; the trade-off between them is a headline result

## Example dialogue

> **Dev:** "The crew ejected both **Impostors** by round 3. Is that a good game?"
> **Domain expert:** "Only on **Vigilance**. If they spent every round in meetings and barely touched their **Tasks**, their **Productivity** was poor. We report both."

## Flagged ambiguities

- "All combinations" of models was infeasible (1,001 ways to fill 10 seats from just 5 models, before impostor assignment); model-to-model comparisons come from **Balanced Lobby** and **Mirror Match** only.
- "Racist / modelist" was used for models favoring their own kind; the measured quantity is **Minority Disadvantage**, and **Out-group Bias** is only claimed when a **Style Mask** removes it (2026-10-20, after Astra's review).
- "Lopsided lineups" are not used for model-to-model comparison (seat count would confound skill), and with 8 seats of one model the 2 others stand out by style; they're used only as **Lopsided Lobbies** for **Minority Disadvantage**.
- "Lie" first meant any false impostor **Claim**; it now requires proof the speaker knew (2026-10-20).
- "Blind human labels" were replaced by the **Label Panel** (2026-10-20, user decision). Results checked this way are reported as validated by an AI panel, not by humans.
- "Productivity" first meant share of tasks done, which penalized early **Containment**; it now means completion time over the whole game (2026-10-20).
- "Accuracy" was used loosely in the original plan; it's now split into **Vigilance** (catching **Impostors**) and **Productivity** (finishing work).
