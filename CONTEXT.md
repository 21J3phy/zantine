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
A **Seat** designated to assign **Tasks** to other players.
_Avoid_: Orchestrator, boss, captain, manager

**Known Leader**:
A **Leader** whose role every player is told.
_Avoid_: Official leader, public leader

**Hidden Leader**:
A **Leader** whose role the other players are not told.
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
A lineup where every **Model** holds exactly two **Seats**; games differ only in the **Impostor Pairing**.
_Avoid_: Mixed game, all-combinations game

**Mirror Match**:
A lineup where all ten **Seats** are the same **Model**.
_Avoid_: All-X game, self-play

**Lopsided Lobby**:
A lineup with a majority **Model** and a minority **Model** (e.g. 8 vs 2), used to measure **Out-group Bias** rather than to compare models head-to-head.
_Avoid_: Unbalanced game, skewed lobby

**Hidden Identity**:
The rule that no player is told which **Model** fills any other **Seat**; players can only guess from behavior and writing style.
_Avoid_: Anonymous play, blind mode

**Out-group Bias**:
A **Model** treating players as more suspicious because they seem to be a different model, rather than because of evidence.
_Avoid_: Racism, modelism, tribalism

### Outcomes

**Productivity**:
How much of the crew's shared **Task** work gets done.
_Avoid_: Task score, efficiency

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

**Lie**:
A false **Claim** made by an **Impostor**.
_Avoid_: Deception (the broader skill), misinformation

**Misstatement**:
A false **Claim** made by a **Crewmate**, whether from confusion or hallucination.
_Avoid_: Lie (reserved for impostors), mistake

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
- A game has 10 **Seats**, 2 of which are **Impostors**
- A **Balanced Lobby** of 5 **Models** has 15 possible **Impostor Pairings** (10 different-model pairs + 5 same-model pairs)
- A **Mirror Match** has a single **Model**, so it measures how a team of that model behaves, not which model is the better deceiver
- **Productivity** and **Vigilance** are scored separately; the trade-off between them is a headline result

## Example dialogue

> **Dev:** "The crew ejected both **Impostors** by round 3. Is that a good game?"
> **Domain expert:** "Only on **Vigilance**. If they spent every round in meetings and barely touched their **Tasks**, their **Productivity** was poor. We report both."

## Flagged ambiguities

- "All combinations" of models was infeasible (1,001 ways to fill 10 seats from 5 models before impostor assignment); model-to-model comparisons come from **Balanced Lobby** and **Mirror Match** only.
- "Racist / modelist" was used for models favoring their own kind; canonical term is **Out-group Bias**.
- "Lopsided lineups" are not used for model-to-model comparison (seat count would confound skill), but are used as **Lopsided Lobbies** to measure **Out-group Bias**.
- "Accuracy" was used loosely in the original plan; it's now split into **Vigilance** (catching **Impostors**) and **Productivity** (finishing work).
