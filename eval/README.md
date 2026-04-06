# eval/

Evaluation suite for JD Intelligence pipeline.

## Folder structure

```
eval/
├── run_eval.py        ← main script, run this
├── ground_truth.json  ← hand-labeled expected outputs for all 9 JDs
├── last_run.json      ← auto-generated after each run (git-ignore this)
└── jds/               ← paste your 9 JD .txt files here
    ├── Java_Full_stack_Development_Lead.txt
    ├── Generative_AI_Engineer.txt
    ├── Full_Stack_Development_Internship_i.txt
    ├── Security_Engineer__University_Gradu.txt
    ├── Application_Developer-AWS_Cloud_Ful.txt
    ├── Software_Engineer_III_-_Java_AWS_Hy.txt
    ├── Senior_Full_Stack_Developer__SSE_.txt
    ├── Android_Display_Dev__CE_-_Engineer.txt
    └── Full_stack_Engineer_-_Java___Angula.txt
```

## Setup (one time)

1. Copy your 9 JD txt files into `eval/jds/`
2. Make sure `.env` has `GROQ_API_KEY` set

## Running

```bash
# from project root (doc-intelligence/)

# Run all 9 JDs
python eval/run_eval.py

# Run one specific JD
python eval/run_eval.py --id jd_02_gen_ai_engineer

# Print full payload for debugging
python eval/run_eval.py --id jd_02_gen_ai_engineer --verbose

# Skip LLM calls — just check file loading + structure
python eval/run_eval.py --dry-run
```

## What each check tests

| Check | What it verifies |
|-------|-----------------|
| extraction | role is not empty, at least one skill list populated |
| recommendation | apply_now / apply_with_caution / not_recommended matches expected |
| match_score | score falls within expected range |
| gaps | expected gap skills actually appear in skill_gaps |
| matched | expected matched skills actually appear in matched_skills |

## How to use this for development

Every time you change a prompt or logic:

```
python eval/run_eval.py
```

If score goes up → keep the change.
If score goes down → revert the change.

You now have a signal instead of guessing.

## The 9 JDs and what they test

| ID | Company | Tests |
|----|---------|-------|
| jd_01 | Crisil | flat required skills, management red flags |
| jd_02 | Techolution | internship, one-of pools, AI/ML matching |
| jd_03 | Hydizo | giant one-of pool, true entry level |
| jd_04 | Google | 1 year experience gate, security domain |
| jd_05 | IBM | 3 separate one-of pools, hybrid work mode |
| jd_06 | JPMorgan | 3+ years hard stop, all hard required |
| jd_07 | Ninja Van | 4+ years hard stop, backend language pool |
| jd_08 | Qualcomm | embedded/display, almost nothing matches |
| jd_09 | Apple | 5-10 years, senior role, hard stop |

## Updating ground truth

If you disagree with an expected output, edit `ground_truth.json` directly.
The file is your source of truth — change it deliberately, not reactively.
