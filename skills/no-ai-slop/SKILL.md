---
name: no-ai-slop
description: "Rules and worked examples for writing prose that does not read like AI-generated slop. Consult before writing or editing any prose."
---

# No AI Slop

Derived from Louis Rossmann's [no_ai_slop_writing_rules](https://github.com/realrossmanngroup/no_ai_slop_writing_rules); the rule numbers below refer to that list. This skill turns the rules that have worked examples into actionable guidance: each shows a WRONG version (the slop) and a RIGHT version (the fix). The pattern behind every fix is the same: replace the vague claim with a specific, checkable fact. The banned-word and pattern lists are in `references/ai-writing-detection.md`.

## Rule 1: No em dashes

Do not write `—` (U+2014), or an en dash `–` (U+2013) used the same way, as a parenthetical or
sentence-level break. Use a hyphen, a comma, a semicolon, parentheses, or a new sentence.

**The reason is provenance, not taste.** There is no em dash key. Someone writing at a keyboard
produces a hyphen, or two of them. An `—` reaches a page by one of three routes: a word
processor autocorrected it, a typesetter or an editor chose it, or a model trained on edited
print reached for it. In anything meant to read as typed, only the third is plausible. That is
what makes the character a reliable tell, and why it reads as unprofessional in work going out
under a person's name.

**Everything an agent is normally asked to write is the typed register**, so treat the rule as
unconditional. Code comments and docstrings, commit messages, PR bodies, issue text, READMEs
and docs kept in a repo, email, chat, terminal output. A README is no exception: look at how
anyone else on GitHub writes one.

**The exception is real and almost never commissioned.** Finished copy that a person sets and
an editor reads, an article, a printed or PDF report, a book, takes ordinary typography, and an
em dash there needs no apology. That is worth knowing only so the rule is not mistaken for the
character being wrong everywhere, which is what leads to hunting them down in other people's
files. If a piece genuinely looks like set, edited copy, ask rather than assume. Length and
formality do not make it so.

- WRONG: "The policy — which affected millions — was later reversed."
- RIGHT: "The policy affected millions of devices. The company reversed it in December 2017."

**A plain hyphen `-` is what a human types, and it is fine**, including as a separator
("Fedora 44 - KDE", "run the check - it takes a second"). Do not avoid the hyphen key trying
to comply with this rule; that overcorrection is what this section exists to prevent.

An en dash inside a numeric range ("1941–2026", "pp. 118–119") is ordinary typography rather
than slop. Leave it, and follow whatever the surrounding project already does.

**Do not go hunting.** This rule governs what you write, in the turn you are writing it. An em
dash already sitting in a file is not a defect to report, and noticing one is not a reason to
offer a cleanup. Change one only in a line you are editing for some other reason, or when you
are asked to. The converse holds too, and matters more: an em dash already in a file is not
permission to add another, because in most repos it is previous agent output rather than a
house style. Check the added lines, not the file: `git diff -U0 | grep '^+' | grep '—'`.

**Do not swap in a colon mechanically.** A colon is syntax in structured text, not just
punctuation. Rewriting `title — scope fixed by measurement` as `title: scope fixed by
measurement` inside YAML frontmatter produced `mapping values are not allowed here`, and the
file silently vanished from the tool that read it. Look at what the line *is* before picking
the replacement, and quote the value if the format needs it.

## Rule 4: No intensifiers

"Significantly", "dramatically", "extremely" and their kin are placeholders for evidence. Replace the word with the number it was standing in for.

- WRONG: "The pricing was significantly higher than the cost of the part."
- RIGHT: "They charged $1,200 for a repair that needed a $5 chip."

## Rule 5: No hollow statements

A sentence that asserts importance without a detail says nothing. End every claim on a concrete fact.

- WRONG: "This practice has had a significant impact on people."
- RIGHT: "The company replaced 11 million batteries in 2018, against the 1 to 2 million it had expected."

## Rule 7: No structural slop (repetitive layouts)

Three sections built from the same template read as machine output, even when each fact is true. Vary paragraph count, sentence rhythm, and how each section opens.

- WRONG (three sections, identical shape):
  ```
  In [year], [party] did [thing]. This affected [number] people. [Party] responded by [action].
  In [year], [party] did [thing]. This affected [number] people. [Party] responded by [action].
  In [year], [party] did [thing]. This affected [number] people. [Party] responded by [action].
  ```
- RIGHT (vary the shape):
  ```
  Section one: a detailed narrative with timeline and context across two paragraphs.
  Section two: a two-sentence summary, because the event is thinly documented.
  Section three: opens with the party's stated justification, then the contradicting evidence.
  ```

## Rule 11: No filler phrases

"In today's world", "It's important to note", "When it comes to" add length, not meaning. Open on the fact.

- WRONG: "In today's world, planned obsolescence affects many devices."
- RIGHT: "Apple, Samsung, and Google have each faced lawsuits alleging planned obsolescence."

## Rule 13: Write like a researcher, not a copywriter

If a sentence could sit on any advocacy or marketing site without changing a word, it is generic. Anchor it to something checkable.

- WRONG: "People deserve the right to repair their own devices."
- RIGHT: "The FTC voted 5-0 in July 2021 to step up enforcement against illegal repair restrictions."

## Rule 15: No weasel words

"May potentially", "can help to", "might be able to" hedge a claim into meaninglessness. Either the thing happens or it does not. Say which.

- WRONG: "Serialization may potentially prevent independent repair."
- RIGHT: "Replacing an iPhone 15 camera module without the manufacturer's calibration software disables optical image stabilization."

## Rule 16: No dramatic headings

A heading names what the section holds. It does not tease, dramatize, or abstract.

- WRONG: "The Hidden Cost of Planned Obsolescence"
- RIGHT: "Economic impact of shortened product lifespans"

## Rule 19: No fabricated attributions

Never put a position in a named person's mouth from inference. State only what they actually did or said, with the real source.

- WRONG: "Senator Smith has argued that the right to repair is essential."
- RIGHT: "Senator Smith co-sponsored the Fair Repair Act in January 2024."

## Root-cause differentiation

When you contrast two things, name the concrete difference that separates them. Do not assert that one is exempt, newer, better, or unaffected without saying what specifically makes it so.

- WRONG: "2020+ Leaf models are unaffected and use the MyNISSAN app instead."
- RIGHT: "2020+ Leaf models shipped with 4G/LTE telematics units connected to a newer cloud platform, replacing the 2G/3G units in earlier models. Those vehicles use the MyNISSAN app, which talks to a different backend."

Whenever you say A differs from B, name the part, the version, the date, the mechanism, or the supply-chain change that makes the difference real. If you do not have that detail, do not imply the difference exists.

## Self-check before returning text

Run this pass on every piece of prose before you hand it back. The full banned lists are in `references/ai-writing-detection.md`; check against them directly.

1. Remove every em dash from text **you wrote this turn**, and every en dash used as a prose break (Rule 1). Do not touch em dashes elsewhere in the file. Leave plain hyphens and numeric-range en dashes alone.
2. Scan for banned verbs (delve, leverage, utilize, foster, bolster, underscore, unveil, streamline) and replace with plain equivalents.
3. Scan for banned adjectives and intensifiers (robust, comprehensive, pivotal, seamless, significantly, extremely, truly) and cut or replace.
4. Scan for banned transitions and openers (Furthermore, Moreover, That being said, In today's world, It's worth noting that).
5. Check every number: is it real and attributable? If not, cut it (Rule 2).
6. Check every sentence ends on a concrete detail, not an assertion of importance (Rule 5).
7. Check headings: does each name the content rather than tease it (Rule 16)?
8. Check for repeated points and repeated section shapes (Rules 6, 7).
9. Count hedging markers per paragraph. More than three is a red flag.
10. Read it aloud. If a phrase would sound unnatural to a colleague, rewrite it.
