#!/usr/bin/env python3
"""
classify.py - Decide which search terms are job-seeker intent.

One call per batch, not per term. The instructions and examples are byte
identical every run and sit behind a cache_control breakpoint; the variable
term list goes after it so the prefix stays cacheable.

The classifier is deliberately biased toward surfacing. A surfaced term that
gets rejected costs one click. A missed term costs money.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import CLASSIFIER_MODEL, call_anthropic, extract_text_from_response

BATCH_SIZE = 40

SYSTEM_PROMPT = """You review Google Ads search terms for Polymer, an applicant tracking \
system sold to employers.

Polymer's customers are employers, founders, recruiters and HR staff who want software to \
manage hiring. Polymer is NOT a tool for job seekers.

Job seekers keep clicking these ads. They search for things like resume checkers, ATS score \
tools, CV templates, and job listings, then land on a page selling recruiting software. That \
click costs money and can never convert.

For each search term, decide whether the person typing it is a JOB SEEKER or an EMPLOYER.

JOB SEEKER signals:
- Checking, scoring, scanning, optimising or formatting a resume or CV
- Looking for jobs, job boards, job listings, applications, vacancies
- Wanting to beat, pass or test against an ATS
- Career advice, interview preparation, salary questions
- Any language centred on "my resume", "my CV", "my application"

EMPLOYER signals:
- Shopping for, comparing or pricing recruiting or hiring software
- Named ATS products evaluated as purchases (Greenhouse, Lever, Workable, Zoho Recruit, \
Recruitee, BambooHR and similar)
- Posting jobs, sourcing candidates, screening applicants, managing a pipeline
- Hiring process, recruiting workflow, HR tooling for a team

AMBIGUOUS TERMS: bare product-category queries such as "ats" or "applicant tracking system" \
can come from either side. Judge from the whole phrase.

BIAS TOWARD FLAGGING. A flagged term a human rejects costs one click of their time. A missed \
job-seeker term keeps spending money every day. When genuinely torn, flag it.

For every term return:
- is_job_seeker: true or false
- case_for: the strongest argument that this SHOULD be excluded as a negative keyword
- case_against: the strongest argument that this should NOT be excluded

Write both cases even when the answer looks obvious. They are what the human reads to decide. \
Each case is one or two plain sentences. No hedging language, no preamble.

Return ONLY a JSON array, one object per term, in the order given:
[{"search_term": "...", "is_job_seeker": true, "case_for": "...", "case_against": "..."}]"""


def classify_terms(search_terms: list[str], model: str = None) -> dict[str, dict]:
    """Classify search terms. Returns {term: {is_job_seeker, case_for, case_against}}."""
    import anthropic

    if not search_terms:
        return {}

    model = model or CLASSIFIER_MODEL
    client = anthropic.Anthropic()
    verdicts = {}

    for start in range(0, len(search_terms), BATCH_SIZE):
        batch = search_terms[start:start + BATCH_SIZE]
        label = f"classify {start + 1}-{start + len(batch)} of {len(search_terms)}"

        response = call_anthropic(
            client,
            model=model,
            # The stable prefix is cached; the term list varies and follows it.
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": "Classify these search terms:\n\n"
                           + "\n".join(f"- {term}" for term in batch),
            }],
            max_tokens=8192,
            label=label,
        )

        text = extract_text_from_response(response).strip()
        verdicts.update(_parse_verdicts(text, batch))

    return verdicts


def _parse_verdicts(text: str, batch: list[str]) -> dict[str, dict]:
    """Parse the model's JSON array, tolerating a fenced code block."""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse classifier output: {exc}", file=sys.stderr)
        print(f"  raw: {text[:500]}", file=sys.stderr)
        return {}

    known = set(batch)
    verdicts = {}
    for item in parsed:
        term = item.get("search_term", "")
        if term not in known:
            print(f"WARN: classifier returned an unrequested term: {term!r}", file=sys.stderr)
            continue
        verdicts[term] = {
            "is_job_seeker": bool(item.get("is_job_seeker")),
            "case_for":      (item.get("case_for") or "").strip(),
            "case_against":  (item.get("case_against") or "").strip(),
        }

    for term in batch:
        if term not in verdicts:
            print(f"WARN: classifier omitted {term!r}; flagging it for review", file=sys.stderr)
            verdicts[term] = {
                "is_job_seeker": True,
                "case_for": "The classifier returned no verdict for this term. "
                            "Surfaced rather than dropped so it is not missed.",
                "case_against": "No classification was produced, so there is no argument "
                                "either way. Judge from the term itself.",
            }
    return verdicts


def main() -> None:
    terms = sys.argv[1:]
    if not terms:
        print("Usage: classify.py <search term> [<search term> ...]")
        sys.exit(1)
    for term, verdict in classify_terms(terms).items():
        flag = "JOB SEEKER" if verdict["is_job_seeker"] else "employer"
        print(f"\n{flag}  {term!r}")
        print(f"  for:     {verdict['case_for']}")
        print(f"  against: {verdict['case_against']}")


if __name__ == "__main__":
    main()
