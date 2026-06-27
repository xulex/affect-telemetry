# Ethics, data, and reproducibility notes

## No participant data is included
This repository contains **only the acquisition and analysis code, the task
materials, and configuration**. It contains **no participant data**: no
recordings, no physiological streams, no input logs, no consent records, no
demographics, and no name-to-ID map. Those artifacts stay on local/encrypted
storage and never enter version control (see `.gitignore`). Anyone reproducing
the study collects their own data under their own ethics approval.

## What the instruments capture (and what they do not)
- **Input dynamics** record keystroke and mouse *timing only*. Key *content* is
  never captured. This is a hard privacy guarantee and is grep-verifiable in the
  output: `input.jsonl` carries no `text`, `key_char`, or `chars` fields.
- **osquery** records process and file *events and metadata*, never URLs or
  payloads.
- **Screen + webcam** are recorded for post-session facial Action Unit
  extraction; the recording is personal data and must be handled accordingly.
- **Facial Action Units** are extracted from the recording on a GPU; in the
  study this ran in the EU for data residency.

If you deploy any affect-sensing read built on this pipeline, it must sit behind
an explicit consent and governance layer. The same signal that can protect Flow
can be turned to surveillance; the method deliberately excludes any individual
performance-scoring use.

## Human-subjects approval
The original study ran under an approved ethics protocol for its specific
parameters (a single-session calibration study). This repository does not grant
ethical clearance for new data collection. Obtain your own institutional
approval before recruiting participants.

## Task materials contain the answer key (replication integrity)
`step8_materials/` includes the full "Meridian Consulting" case, **including the
solved spreadsheet `halestrom_pilot.xlsx` with its deliberate analytical catch**
(the Enterprise segment holds the strongest LTV:CAC ratio despite a partner memo
framing it as weak) and the debrief script. These are published for full
reproducibility.

**If you replicate the task, recruit participants who have not seen this
repository.** Exposure to the materials, or to this note, invalidates the
analytical-catch measure.

The debrief script is written to avoid disclosing the affect-detection framing
to participants during a session; preserve that property if you adapt it.
