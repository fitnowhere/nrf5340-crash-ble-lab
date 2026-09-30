# Contributing

1. Open an issue describing the failure mode or feature.
2. Keep the portable module in `sdk/zephyr/`; isolate board/SoC behavior in the
   reference application.
3. Add deterministic tests. Do not make clean-clone tests depend on ignored
   hardware archives.
4. Run the commands in `docs/TESTING.md` and `git diff --check`.
5. Clearly label synthetic events and unverified claims.

Security-sensitive changes should document trust boundaries, bounded inputs,
failure recovery and hardware assumptions. Hardware claims need a dated
transcript in `LAB.md`; compilation alone is not hardware proof.
