# Review 2 — Second Review Evaluation Report

School of Mechanical Engineering · Fall 2026-27 · Project I

Source: form received 2026-10-12 with the Review 2 email. To be filled by the students and
signed by the guide **before** the review (the guide is asked to verify the students' PPT
before authorizing). Submit the signed form during the review session. See
`Review_2_Instructions.md` for the rest of the requirements.

## 1. Project details

| Field | Value |
|---|---|
| Batch No | _fill in_ |
| Date | _fill in_ |
| Project Title | _fill in — use the title on the Review 0/1 forms_ |
| Guide Name | Dr. Jegadeeshwaran R (50204) |
| Guide Signature | __________________ |

Team (one column per student on the form):

| Student | Name | Reg No |
|---|---|---|
| 1 | Mithunvel KL | 23BMH1029 |
| 2 | Kanigalpula Teja Pavan Sai Adinarayana Gupta | 23BMH1007 |
| 3 | Madhumitha Vemula | 23BMH1019 |

## 2. Evaluation (filled by reviewers)

Instructions for reviewers: each criterion is scored as an **integer from 1 to 5** per the
rubric below. Each student has a Reviewer 1 and a Reviewer 2 column.

| Criterion | S1 R1 | S1 R2 | S2 R1 | S2 R2 | S3 R1 | S3 R2 |
|---|---|---|---|---|---|---|
| 1. Technical Depth & Domain Knowledge | | | | | | |
| 2. Demonstration & System Performance | | | | | | |
| 3. Innovation & Originality | | | | | | |
| 4. Question Handling & Technical Defense | | | | | | |
| 5. Societal, Industrial & Future Scope Impact | | | | | | |

Reviewer comments, and Reviewer-1 / Reviewer-2 name and signature: left blank for the panel.

## 3. Rubric description

| Criterion | Level 1 | Level 2 | Level 3 | Level 4 | Level 5 |
|---|---|---|---|---|---|
| 1. Technical Depth & Domain Knowledge | Poor understanding | Basic understanding | Moderate clarity | Strong conceptual clarity | Expert-level understanding and justification |
| 2. Demonstration & System Performance | Not working | Partially working | Functional | Stable and reliable | Optimized, industry-ready system |
| 3. Innovation & Originality | No novelty | Minor novelty | Moderate innovation | Good originality | Highly innovative solution |
| 4. Question Handling & Technical Defense | Unable to answer | Partially answers | Answers most | Confident defense | Excellent analytical defense |
| 5. Societal, Industrial & Future Scope Impact | Not addressed | Weak impact | Moderate relevance | Good applicability | Strong societal/industrial relevance with scalability |

## 4. Honest read of where the project sits against the rubric

Not part of the form — a planning aid, from `.CLAUDE/CLAUDE.md`'s status as of 2026-10-12.

- **Demonstration (level 3 "Functional" needs something that runs):** the camera pipeline
  has run on a laptop webcam and 5 recorded belt videos (92 tiles); the station decision
  service and acoustic tooling run on the laptop with synthetic input. Nothing has run on
  the Pi, a real broker, or the tap rig. Demo what is real and say plainly what is
  simulated — level 4-5 ("stable", "industry-ready") is not defensible yet.
- **Question handling:** the weak points a reviewer will find are listed under Known
  Technical Debt (no human-labelled ground truth, crack check flags intact tiles, corner
  threshold is a business trade-off). Better to state them first than be found out.
- **Innovation:** see `documents/project/AI_Software_Novelty.md`.
- **Societal / industrial scope:** SMTW is a real client doing this manually today; tile
  size/weight data from SMTW is still missing.
