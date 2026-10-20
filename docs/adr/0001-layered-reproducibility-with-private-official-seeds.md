# Layered reproducibility: private official seeds, public seed set, commit-then-reveal

Official run seeds stay private so future models can't memorize the games (contamination). That breaks exact reproduction of headline numbers, so we use three layers instead:

1. **Public Seed Set.** Anyone can run the open engine on these seeds and should land within the official error bars.
2. **Seed Commitment.** A SHA-256 hash of each official run's seeds and transcripts is published before its results, which proves no games were cherry-picked or changed afterwards.
3. **Retirement release.** A version's official seeds and transcripts are published once it is retired (e.g. v1's when v2 runs).

Publishing all seeds now was rejected because it invites contamination. Keeping everything private was rejected because results couldn't be checked. Don't "fix" the private seeds by publishing them early: once released, they can't be taken back, and that version's results become memorizable.
