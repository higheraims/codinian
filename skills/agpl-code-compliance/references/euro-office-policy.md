# Euro-Office AI Contribution Policy & AGPLv3 Obligations (reference)

Source: [Euro-Office/DocumentServer CONTRIBUTING.md](https://github.com/Euro-Office/DocumentServer/blob/main/CONTRIBUTING.md), [release notes](https://github.com/Euro-Office/DocumentServer/releases)

## AI Contribution Policy (summary)

- **Disclosure**: declare AI tool use in the PR description; add `Assisted-by: AGENT_NAME:MODEL_VERSION` as a git trailer to each affected commit.
- **Accountability**: the contributor must be able to explain, defend, and modify every submitted line. "The AI wrote it" is not an acceptable answer in review.
- **Communication**: PR descriptions, review comments, and issue reports must be written in the contributor's own words — not relayed through an AI tool.
- **Quality**: AI output must be reviewed, cleaned up, and tested by a human before submission. New features must be tested on a live instance by the contributor, not an agent. Never-executed code, or code that pushes debugging onto maintainers, is rejected.
- **Licensing**: AI-generated code must contain nothing incompatible with AGPLv3 or the license of any third-party component it touches.

## AGPLv3 obligations (general, not legal advice)

- **Network use clause**: if a modified version is run as a network service, complete corresponding source must be made available to users who interact with it over the network — this is AGPLv3's key departure from plain GPLv3.
- **Notices**: copyright and license notices, and the AGPLv3 license text, must remain intact.
- **Provenance tracking**: know which files/modules originate from upstream ONLYOFFICE vs. Euro-Office's own work, and what license any vendored/third-party component carries.
- **Compatibility**: not all open licenses combine cleanly with AGPLv3 in a single distributed/network-served work (e.g., GPLv2-only without an "or later" clause, non-OSI licenses like SSPL/BUSL, or non-commercial-use-restricted licenses).

## Tooling landscape (for deeper/legal-grade scans beyond this skill's scripts)

- **FOSSA**, **Black Duck**, **ScanCode Toolkit** — commercial/open-source software composition analysis (SCA); scan for license headers, missing notices, and dependency license conflicts.
- **REUSE (FSFE)** — lightweight standard + tool (`reuse lint`) for verifying every file has clear licensing/copyright info.
- **jscpd**, **PMD CPD** — code-clone/duplication detectors (used by `detect_duplication.py` in this skill when available).
- **MOSS (Measure of Software Similarity)** — academic plagiarism/similarity detector, useful for larger-scale duplication checks.
- **GitHub code search / Sourcegraph** — manual verbatim-match lookups for distinctive snippets (function names, unusual literals) that a generation model might have reproduced from training data.
- **GitHub Copilot "public code matching" filter** (or equivalent settings in other AI coding tools) — a generation-time filter that blocks/flags suggestions matching known public code. Reduces but does not eliminate risk.

None of these tools provide a legal guarantee of originality or license compatibility. For a project with real legal exposure, pair automated scanning with human/legal review before releases.
